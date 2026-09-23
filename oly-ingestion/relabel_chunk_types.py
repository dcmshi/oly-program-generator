#!/usr/bin/env python3
"""
Relabel knowledge_chunks.chunk_type with an LLM (RAG-H2, step 3).

`chunk_type` is assigned at ingest by a first-match keyword scan
(pipeline._infer_chunk_type). On the live corpus that left `concept` at 61% and
put most periodisation text under `recovery_adaptation`. Retrieval now treats the
label as a soft preference (so a wrong label costs rank, not recall), but a better
label still improves ranking. This script asks a small model to label passages in
batches and rewrites the column where the model is confident and disagrees.

No re-embedding: `content`/`embedding` are untouched. Safe to re-run.

Usage (from oly-ingestion/):
    PYTHONUTF8=1 uv run python relabel_chunk_types.py --dry-run             # distribution shift only
    PYTHONUTF8=1 uv run python relabel_chunk_types.py --source-id 51        # one source
    PYTHONUTF8=1 uv run python relabel_chunk_types.py [--model anthropic/claude-haiku-4.5]   # default: settings.light_model
    PYTHONUTF8=1 uv run python relabel_chunk_types.py --limit 200           # smoke test
    PYTHONUTF8=1 uv run python relabel_chunk_types.py --batch               # Batch API, half price (COST-1)
    PYTHONUTF8=1 uv run python relabel_chunk_types.py --judge jev           # Jev (typesafe.ai), ~$0.08 corpus-wide

Cost: ~3.4k chunks × ~400 input tokens (1,500-char passage cap) in batches of 10;
a Haiku-class model does the whole corpus for a few dollars.
"""

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).parent))
from config import Settings

from shared.constants import RELABEL_MAX_TOKENS
from shared.llm import (
    BatchRequestFailed,
    create_llm_client,
    create_message_with_retries,
    json_schema_kwargs,
    light_model_for,
    message_text,
    parse_llm_json,
    run_message_batch,
    supports_batches,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from shared.schema_enums import CHUNK_TYPES  # mirrors the DB enum; test_schema_enums asserts it

DEFAULT_BATCH_SIZE = 10
DEFAULT_MIN_CONFIDENCE = 0.6
PASSAGE_CHARS = 1500  # enough to decide the type; keeps the batch under ~4k input tokens

RELABEL_PROMPT = """\
You label passages from Olympic weightlifting coaching literature with exactly ONE content type.

TYPES (pick the single best fit):
- periodization: how training is organised over time — phases, blocks, cycles, accumulation/intensification/realization, deloads, tapering, peaking, annual plans
- programming_rationale: WHY a prescription is made — exercise selection reasoning, volume/intensity trade-offs, how a coach decides what goes in a session
- methodology: a named training method or system described as a whole (e.g. Bulgarian, Soviet, conjugate) and how it is applied
- fault_correction: diagnosing and fixing technical errors in the lifts — missed positions, bar path faults, drills to correct them
- biomechanics: anatomy, physics and mechanics of the lifts — positions, forces, bar path analysis, muscle involvement
- recovery_adaptation: fatigue, supercompensation, sleep, restoration, overtraining, how the body adapts to load
- competition_strategy: meet day — attempt selection, openers, warm-up room timing, weigh-in tactics
- nutrition_bodyweight: diet, making weight, weight classes, hydration, body composition
- case_study: a specific athlete's or team's training history told as an example
- concept: general explanation or history that fits none of the above

PASSAGES:
{passages}

Respond with JSON only: {{"labels": [{{"index": 1, "chunk_type": "<type>", "confidence": <0.0-1.0>}}, ...]}} — one entry per passage, in order."""

# Constrains the reply (STRUCT-1): `chunk_type` can only be a DB enum value.
RELABEL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "chunk_type": {"type": "string", "enum": list(CHUNK_TYPES)},
                    "confidence": {"type": "number"},
                },
                "required": ["index", "chunk_type", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["labels"],
    "additionalProperties": False,
}


def build_prompt(batch: list[tuple[int, str]]) -> str:
    """batch = [(index, passage_text), ...]; passages are truncated to PASSAGE_CHARS."""
    passages = "\n\n".join(f"[{idx}] {text[:PASSAGE_CHARS]}" for idx, text in batch)
    return RELABEL_PROMPT.format(passages=passages)


def parse_labels(raw_text: str, expected_indexes: set[int]) -> dict[int, tuple[str, float]]:
    """Parse the model's JSON array → {index: (chunk_type, confidence)}.

    Drops entries with an unknown chunk_type, an index outside the batch, or an
    unparseable confidence; confidence is clamped to [0, 1]. Raises on non-JSON
    so the caller can skip the batch.
    """
    items = parse_llm_json(raw_text)
    if isinstance(items, dict):
        items = items.get("labels", [items])   # schema wrapper, or a bare single object
    labels: dict[int, tuple[str, float]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
            conf = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        ctype = str(item.get("chunk_type", "")).strip()
        if idx not in expected_indexes or ctype not in CHUNK_TYPES:
            continue
        labels[idx] = (ctype, min(1.0, max(0.0, conf)))
    return labels


def plan_updates(
    rows: list[tuple[int, str]],
    labels: dict[int, tuple[str, float]],
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> list[tuple[int, str, str, float]]:
    """rows = [(chunk_id, current_type), ...] in batch order (index = position + 1).

    Returns (chunk_id, old_type, new_type, confidence) for every chunk whose new
    label differs from the current one at or above min_confidence.
    """
    updates = []
    for pos, (chunk_id, current) in enumerate(rows, start=1):
        if pos not in labels:
            continue
        new_type, conf = labels[pos]
        if new_type != current and conf >= min_confidence:
            updates.append((chunk_id, current, new_type, conf))
    return updates


def relabel(
    source_id: int | None,
    dry_run: bool,
    batch_size: int = DEFAULT_BATCH_SIZE,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    model: str | None = None,
    limit: int = 0,
    use_batch: bool = False,
    judge: str = "llm",
    min_id: int = 0,
) -> Counter:
    """Relabel the corpus (or one source). Returns a Counter of old→new transitions.

    `judge="llm"` asks the light model in batches of `batch_size` passages;
    `judge="jev"` asks TypeSafe's Jev one passage at a time (calibrated
    confidence, ~$0.08 for the corpus; needs TYPESAFE_API_KEY).
    """
    settings = Settings()
    if judge not in ("llm", "jev"):
        raise ValueError(f"judge must be 'llm' or 'jev', got {judge!r}")
    client = create_llm_client(settings) if judge == "llm" else None
    model = light_model_for(settings, model) if judge == "llm" else "jev"
    if use_batch and judge == "jev":
        use_batch = False
    if use_batch and not supports_batches(settings):
        logger.warning(f"--batch ignored: provider {settings.llm_provider!r} has no Message Batches")
        use_batch = False

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    sql = "SELECT id, chunk_type::text, raw_content FROM knowledge_chunks"
    params: list = []
    where = []
    if source_id:
        where.append("source_id = %s")
        params.append(source_id)
    if min_id:
        where.append("id >= %s")            # only chunks ingested since a known id (new sources)
        params.append(min_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    cur.execute(sql, params)
    rows = cur.fetchall()
    logger.info(f"Loaded {len(rows)} chunks{f' for source_id={source_id}' if source_id else ''} (model={model})")

    before = Counter(r[1] for r in rows)
    transitions: Counter = Counter()
    updated = 0

    offsets = list(range(0, len(rows), batch_size))

    def _params(start: int) -> dict:
        batch = rows[start:start + batch_size]
        prompt = build_prompt([(i, text) for i, (_id, _t, text) in enumerate(batch, start=1)])
        return dict(model=model, max_tokens=RELABEL_MAX_TOKENS, messages=[{"role": "user", "content": prompt}],
                    **json_schema_kwargs(RELABEL_SCHEMA))

    # COST-1: one Message Batch for the whole corpus at half price, instead of
    # one synchronous call per group of chunks.
    batched: dict[str, object] = {}
    if use_batch:
        batched = run_message_batch(
            client, {str(start): _params(start) for start in offsets}, label="Relabel",
        )

    for start in offsets:
        batch = rows[start:start + batch_size]
        try:
            if judge == "jev":
                from processors.jev_judge import label_chunk_types
                labels = label_chunk_types({i: text[:PASSAGE_CHARS] for i, (_id, _t, text) in enumerate(batch, start=1)})
            elif use_batch:
                message = batched.get(str(start))
                if isinstance(message, BatchRequestFailed) or message is None:
                    raise RuntimeError(str(message))
            else:
                message = create_message_with_retries(client, **_params(start))
            if judge == "llm":
                labels = parse_labels(message_text(message), set(range(1, len(batch) + 1)))
        except Exception as e:
            logger.warning(f"Batch at offset {start} skipped: {type(e).__name__}: {e}")
            continue

        for chunk_id, old, new, _conf in plan_updates([(r[0], r[1]) for r in batch], labels, min_confidence):
            transitions[(old, new)] += 1
            if dry_run:
                continue
            cur.execute(
                "UPDATE knowledge_chunks SET chunk_type = %s::chunk_type WHERE id = %s",
                (new, chunk_id),
            )
            updated += 1
        if not dry_run and (start // batch_size) % 10 == 9:
            conn.commit()

    if not dry_run:
        conn.commit()

    after = Counter(before)
    for (old, new), n in transitions.items():
        after[old] -= n
        after[new] += n

    print(f"\n{'DRY RUN — ' if dry_run else ''}chunk_type distribution ({len(rows)} chunks):")
    for ctype in sorted(set(before) | set(after), key=lambda t: -after[t]):
        print(f"  {ctype:24s} {before[ctype]:5d} → {after[ctype]:5d}")
    print(f"\nTransitions ({sum(transitions.values())} chunks{'' if dry_run else f', {updated} written'}):")
    for (old, new), n in transitions.most_common(15):
        print(f"  {old} → {new}: {n}")

    cur.close()
    conn.close()
    return transitions


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Relabel knowledge_chunks.chunk_type with an LLM (RAG-H2)")
    parser.add_argument("--source-id", type=int, help="Limit to one source")
    parser.add_argument("--min-id", type=int, default=0, help="Only chunks with id >= N (relabel just the new rows after an ingest)")
    parser.add_argument("--dry-run", action="store_true", help="Show the distribution shift without writing")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE,
                        help="Only rewrite when the model's confidence is at least this (default 0.6)")
    parser.add_argument("--model", default=None,
                        help="Anthropic model id (default: settings.light_model)")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N chunks (smoke test)")
    parser.add_argument("--batch", action="store_true",
                        help="One Message Batch for the whole run (half price, minutes of latency; COST-1)")
    parser.add_argument("--judge", choices=("llm", "jev"), default="llm",
                        help="llm = the light model (default); jev = TypeSafe's Jev, calibrated confidence, "
                             "~$0.08 for the corpus (needs TYPESAFE_API_KEY)")
    args = parser.parse_args()
    relabel(args.source_id, args.dry_run, args.batch_size, args.min_confidence, args.model, args.limit,
            use_batch=args.batch, judge=args.judge, min_id=args.min_id)
