#!/usr/bin/env python3
"""
Mine accessory exercises from the corpus into the `exercises` catalogue (DOG-2g).

Migration 0023 seeded 24 accessories by hand because ingestion never
catalogued any. `pipeline._parse_exercise` only reads sections classified
EXERCISE_DESCRIPTION, and the parsed program templates name accessories in
shorthand ("Abs", "GPP exercise 3"). Most accessories are named in prose
instead: "finish with good mornings 3×8", "rows and pull-ups for the upper
back". This script reads that prose:

1. Prefilter: non-quarantined chunks of the current embedding model whose
   text matches an accessory vocabulary (`PREFILTER_SQL_RE`).
2. Extract: a light-model, schema-constrained call per
   ACCESSORY_MINE_PASSAGES_PER_CALL passages lists the accessory exercises
   each passage names: name, movement family, and sets × reps and purpose
   when the passage gives them. A name whose words are not all in the passage
   is dropped (`named_in_text`), so an invented exercise can't enter.
3. Aggregate: names are normalised (plurals, hyphens, "RDL" → "romanian
   deadlift", "hyperextension" → "back extension" …) and counted by distinct
   source (`chunk_sources`, so text shared by two sources counts twice).
4. Report: every candidate, whether the catalogue already has it, and the
   median sets × reps → `--out` (default logs/accessory_candidates.json).
5. `--apply`: candidates named in ≥ ACCESSORY_MINE_MIN_SOURCES sources that the
   catalogue lacks are inserted (`ON CONFLICT (name) DO NOTHING`; complexity 1,
   no typical % — accessories are prescribed by RPE like 0023's rows). Two
   kinds of name are never inserted. One is a name that reads as a lift
   (`names_a_lift`), which would take the week's ceiling and the Prilepin
   budget (the DOG-2o regression). The other is machine or cable work
   (`needs_machine`), because the generator has no equipment check for it.
   Inserted ids are written to the report, along with the raw mentions, so
   `--from-report` can re-aggregate under changed rules without new calls.

New sources then reach the generator with no migration: re-run after an ingest
with `--resume` off and `--apply`.

The extraction pays per call and is resumable (RESUME-1): finished batches go
to `<out>.partial.jsonl`; `--resume` skips them.

Usage (from oly-ingestion/):
    PYTHONUTF8=1 uv run python mine_accessories.py --limit 40             # smoke test → report only
    PYTHONUTF8=1 uv run python mine_accessories.py                        # whole corpus → report
    PYTHONUTF8=1 uv run python mine_accessories.py --resume               # continue an aborted run
    PYTHONUTF8=1 uv run python mine_accessories.py --from-report logs/accessory_candidates.json
                                                                          # re-aggregate under the current rules
    PYTHONUTF8=1 uv run python mine_accessories.py --from-report logs/accessory_candidates.json --apply
                                                                          # …and insert; no LLM calls either way
Cost (2026-11-05): ~1,950 prefiltered chunks / 5 per call ≈ 390 calls; under
$1 on deepseek/deepseek-v4.1-flash.
"""

import argparse
import json
import logging
import re
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.checkpoint import Checkpoint  # noqa: E402
from shared.constants import (  # noqa: E402
    ACCESSORY_MINE_CONCURRENCY,
    ACCESSORY_MINE_MAX_TOKENS,
    ACCESSORY_MINE_MIN_PRESCRIPTIONS,
    ACCESSORY_MINE_MIN_SOURCES,
    ACCESSORY_MINE_PASSAGE_CHARS,
    ACCESSORY_MINE_PASSAGES_PER_CALL,
)
from shared.exercise_mapping import (  # noqa: E402
    ACCESSORY_FAMILIES,
    infer_movement_family,
    is_competition_lift,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = 1

# Families an accessory can have. The lift families are left out: the
# competition lifts and their variants are not what this pass is for.
MINE_FAMILIES: tuple[str, ...] = ("hinge", "row", "press", "core", "carry", "plyometric", "squat", "pull")

# Postgres regex (`~*`, `\m` / `\M` word bounds) for chunks worth a call.
PREFILTER_SQL_RE = (
    r"\m(rows?|pull-?ups?|chin-?ups?|planks?|sit-?ups?|leg raises?|back extensions?|hyperextensions?"
    r"|reverse hyper\w*|good mornings?|glute[- ]ham|rdls?|romanian|stiff[- ]leg\w*|bench press\w*|dips"
    r"|triceps|lunges?|step-?ups?|carr(y|ies)|farmer\w*|shrugs?|curls?|abdominals?|abs|assistance exercises?"
    r"|accessor(y|ies)|auxiliary|supplementary|gpp|lateral raises?|flyes?|face pulls?|pallof|hollow"
    r"|rollouts?|calf raises?|split squats?|nordic|hip thrusts?|kettlebells?)\M"
)

MINE_PROMPT = """\
You list the ACCESSORY exercises named in passages from Olympic weightlifting coaching literature.

An accessory exercise is supporting work that is NOT a competition lift (snatch, clean, jerk, clean & jerk), \
NOT a variant of one (power, hang, block, pause, muscle, deficit versions, snatch / clean pulls), and NOT a \
back or front squat. Examples: rows, pull-ups, good mornings, back extensions, Romanian deadlifts, planks, \
leg raises, strict / bench / behind-the-neck presses, dips, lunges, step-ups, carries, shrugs, box jumps.

Rules:
- Only exercises the passage names. Give the name as written, singular, without loads or set counts \
("Good Morning", not "good mornings 3x8").
- Skip generic categories ("abs", "core work", "GPP", "accessory work", "upper body", "jumps").
- sets_low / sets_high / reps_low / reps_high only when the passage gives them for that exercise.
- purpose: what the passage says the exercise is for, in at most 12 words; leave it out when the passage says nothing.
- movement_family: hinge (hip hinge, back extension), row (rows, pull-ups), press (presses, dips, triceps), \
core (trunk), carry, plyometric (jumps), squat (lunges, step-ups, split squats), pull (shrugs, deadlifts).
- A passage that names no accessory exercise gets an empty list.

PASSAGES:
{passages}

Respond with JSON only: {{"passages": [{{"index": 1, "exercises": [...]}}, ...]}} — one entry per passage, in order."""

MINE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "passages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "exercises": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "movement_family": {"type": "string", "enum": list(MINE_FAMILIES)},
                                "purpose": {"type": "string"},
                                "sets_low": {"type": "integer"},
                                "sets_high": {"type": "integer"},
                                "reps_low": {"type": "integer"},
                                "reps_high": {"type": "integer"},
                            },
                            "required": ["name", "movement_family"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["index", "exercises"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["passages"],
    "additionalProperties": False,
}

# Plausible prescription bounds; a number outside them is a misread (a load,
# a page number) and is dropped rather than averaged in.
_SETS_RANGE = (1, 10)
_REPS_RANGE = (1, 50)
_MAX_NAME_WORDS = 6
_MAX_NAME_CHARS = 60
# A one-word name that only names a movement pattern ("Squat", "Row", "Curl")
# is not a catalogue entry; the model returned these despite the prompt.
_GENERIC_NAMES = frozenset({
    "squat", "row", "curl", "press", "pull", "deadlift", "jump", "carry", "raise", "extension",
    "lift", "hinge", "push", "pulldown", "fly", "abs", "core",
})
# Placeholders the model writes in `purpose` instead of leaving it out.
_EMPTY_PURPOSE_RE = re.compile(r"^\W*(not (stated|specified|given|mentioned)|none|n/?a|unspecified|unknown)\W*$", re.I)

# Whole-phrase synonyms applied after lowercasing / de-hyphenating, so the
# corpus's spellings and the catalogue's names meet on one key.
_SYNONYMS: tuple[tuple[str, str], ...] = (
    (r"\bdb\b", "dumbbell"), (r"\bbb\b", "barbell"), (r"\bkb\b", "kettlebell"),
    (r"\brdls?\b", "romanian deadlift"), (r"\bghrs?\b", "glute ham raise"),
    (r"\bsldls?\b", "stiff leg deadlift"),
    (r"\bhyper ?extensions?\b", "back extension"),
    (r"\bfarmers? (walk|carry)\b", "farmer carry"),
    (r"\b(military|overhead|standing) press\b", "strict press"),
    (r"\bbtn\b", "behind the neck"),
    (r"\bpress(es)? behind (the )?neck\b", "behind the neck press"),
    (r"\bstiff legged\b", "stiff leg"),
    (r"\bbent (over )?rows?\b", "barbell row"),
    (r"\bincline bench press\b", "incline press"),
    (r"\bdead lifts?\b", "deadlift"),
    (r"\bparallel bar dips?\b", "dip"),
    (r"\bpullups?\b", "pull up"), (r"\bchinups?\b", "chin up"), (r"\bsitups?\b", "sit up"),
    (r"\bstepups?\b", "step up"),
)
# Machine and cable work: a weightlifting gym may have none, and the
# generator has no equipment check for it (only blocks), so it is reported
# but never inserted.
_MACHINE_RE = re.compile(
    r"\b(machine|cable|smith|leg press|leg extension|leg curl|lat pull ?downs?|pec deck|hack squat)",
    re.I,
)
_LIFT_WORD_RE = re.compile(r"snatch|clean|jerk", re.I)
_SMALL_WORDS = frozenset({"the", "of", "to", "with", "on", "a", "and", "in", "from"})
_WORD_RE = re.compile(r"[a-z0-9]+")


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"                      # carries → carry
    if len(word) > 4 and word.endswith(("sses", "ches", "shes", "xes")):
        return word[:-2]                            # presses → press, crunches → crunch
    if len(word) > 2 and word.endswith("s") and not word.endswith(("ss", "us")):
        return word[:-1]                            # rows → row, triceps → tricep, ups → up
    return word


def normalize_name(name: str | None) -> str:
    """The aggregation / dedupe key: lowercase, no parenthetical, no
    apostrophes or hyphens, synonyms folded, every word singular."""
    text = (name or "").lower()
    text = re.sub(r"\(.*?\)", " ", text)
    text = text.replace("’", "").replace("'", "")
    text = re.sub(r"[-_/]", " ", text)
    text = " ".join(_WORD_RE.findall(text))
    text = re.sub(r"^the ", "", text)
    for pattern, repl in _SYNONYMS:
        text = re.sub(pattern, repl, text)
    return " ".join(_singular(w) for w in text.split())


def _text_words(text: str) -> set[str]:
    folded = normalize_name(text)
    return set(folded.split())


def named_in_text(name: str, text_words: set[str]) -> bool:
    """True when every word of the normalised name (3+ letters) occurs in the
    passage. This guards against a name the model made up or merged from two
    sentences."""
    words = [w for w in normalize_name(name).split() if len(w) >= 3]
    return bool(words) and all(w in text_words for w in words)


def display_name(surface_forms: Counter) -> str:
    """The commonest spelling (case-insensitive), title-cased: "good morning"
    → "Good Morning", "farmer's carry" → "Farmer's Carry"; a short all-caps
    word ("RDL") keeps its case."""
    folded: Counter = Counter()
    for form, n in surface_forms.items():
        folded[form.lower()] += n
    best = folded.most_common(1)[0][0]
    form = max((f for f in surface_forms if f.lower() == best), key=lambda f: surface_forms[f]).strip()
    words = form.split()
    out = []
    for i, w in enumerate(words):
        if i and w.lower() in _SMALL_WORDS:
            out.append(w.lower())
        elif w.isupper() and len(w) <= 4:
            out.append(w)                           # RDL, DB stay as written
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


def build_prompt(passages: list[tuple[int, str]]) -> str:
    body = "\n\n".join(f"[{i}] {text[:ACCESSORY_MINE_PASSAGE_CHARS]}" for i, text in passages)
    return MINE_PROMPT.format(passages=body)


def _int_in(value, bounds: tuple[int, int]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = int(value)
    return v if bounds[0] <= v <= bounds[1] else None


def parse_reply(raw: dict | list | None, passages: list[tuple[int, str]]) -> dict[int, list[dict]]:
    """{passage index: [clean mention, …]} from the model's JSON.

    Drops entries with an unknown index or family, an over-long name, or a name
    the passage doesn't contain. Sets / reps outside plausible bounds become None,
    and a low above its high drops the pair.
    """
    if isinstance(raw, list):
        raw = {"passages": raw}
    texts = {i: _text_words(t) for i, t in passages}
    out: dict[int, list[dict]] = {i: [] for i, _ in passages}
    for entry in (raw or {}).get("passages") or []:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        if idx not in texts:
            continue
        for ex in entry.get("exercises") or []:
            if not isinstance(ex, dict):
                continue
            name = " ".join(str(ex.get("name") or "").split())
            family = ex.get("movement_family")
            if (not name or family not in MINE_FAMILIES or len(name) > _MAX_NAME_CHARS
                    or len(name.split()) > _MAX_NAME_WORDS or normalize_name(name) in _GENERIC_NAMES
                    or not named_in_text(name, texts[idx])):
                continue
            purpose = " ".join(str(ex.get("purpose") or "").split())
            mention = {"name": name, "movement_family": family,
                       "purpose": None if not purpose or _EMPTY_PURPOSE_RE.match(purpose) else purpose}
            for lo_key, hi_key, bounds in (("sets_low", "sets_high", _SETS_RANGE),
                                           ("reps_low", "reps_high", _REPS_RANGE)):
                lo, hi = _int_in(ex.get(lo_key), bounds), _int_in(ex.get(hi_key), bounds)
                if lo is not None and hi is None:
                    hi = lo
                if hi is not None and lo is None:
                    lo = hi
                if lo is not None and hi is not None and lo > hi:
                    lo = hi = None
                mention[lo_key], mention[hi_key] = lo, hi
            out[idx].append(mention)
    return out


def _median_int(values: list[int]) -> int | None:
    """Median of the given numbers, or None from fewer than
    ACCESSORY_MINE_MIN_PRESCRIPTIONS (one passage's "1 set" is not a typical
    prescription)."""
    return int(round(statistics.median(values))) if len(values) >= ACCESSORY_MINE_MIN_PRESCRIPTIONS else None


def names_a_lift(name: str) -> bool:
    """True when the name holds snatch / clean / jerk and no non-lift marker
    ("Hang Snatch", not "Snatch Grip Shrug"). Such a row would be counted as a
    competition lift against the week's ceiling and Prilepin budget.
    `is_competition_lift(name, ref)` alone is no test here: it is True for
    every name without a non-lift marker ("Lunge", "Plank"), because the
    validator only asks it about rows already loaded on a lift's max."""
    return bool(_LIFT_WORD_RE.search(name or "")) and is_competition_lift(name, "snatch")


def needs_machine(name: str) -> bool:
    return bool(_MACHINE_RE.search(name or ""))


def aggregate(mentions_by_chunk: dict[int, list[dict]], chunk_sources: dict[int, list[int]],
              catalogue_names: list[str]) -> list[dict]:
    """One candidate per normalised name, most sources first."""
    catalogue = {normalize_name(n): n for n in catalogue_names}
    groups: dict[str, dict] = defaultdict(lambda: {
        "forms": Counter(), "families": Counter(), "purposes": Counter(), "chunks": set(),
        "source_chunks": Counter(), "sets_low": [], "sets_high": [], "reps_low": [], "reps_high": [],
    })
    for chunk_id, mentions in mentions_by_chunk.items():
        seen_here: set[str] = set()
        for m in mentions:
            key = normalize_name(m["name"])
            if not key:
                continue
            g = groups[key]
            g["forms"][m["name"]] += 1
            g["families"][m["movement_family"]] += 1
            if m.get("purpose"):
                g["purposes"][m["purpose"]] += 1
            for k in ("sets_low", "sets_high", "reps_low", "reps_high"):
                if m.get(k) is not None:
                    g[k].append(m[k])
            if key not in seen_here:
                seen_here.add(key)
                g["chunks"].add(chunk_id)
                for sid in chunk_sources.get(chunk_id) or []:
                    g["source_chunks"][sid] += 1

    candidates = []
    for key, g in groups.items():
        name = display_name(g["forms"])
        family = infer_movement_family(name) or g["families"].most_common(1)[0][0]
        if family not in MINE_FAMILIES:            # the name reads as a lift ("snatch …")
            family = g["families"].most_common(1)[0][0]
        candidates.append({
            "key": key,
            "name": name,
            "movement_family": family,
            "sources": len(g["source_chunks"]),
            "chunks": len(g["chunks"]),
            "in_catalogue": key in catalogue,
            "catalogue_name": catalogue.get(key),
            "counts_as_lift": names_a_lift(name),
            "needs_machine": needs_machine(name),
            "sets": [_median_int(g["sets_low"]), _median_int(g["sets_high"])],
            "reps": [_median_int(g["reps_low"]), _median_int(g["reps_high"])],
            "purpose": g["purposes"].most_common(1)[0][0] if g["purposes"] else None,
            "primary_source_id": g["source_chunks"].most_common(1)[0][0] if g["source_chunks"] else None,
            "example_chunk_ids": sorted(g["chunks"])[:5],
        })
    candidates.sort(key=lambda c: (-c["sources"], -c["chunks"], c["name"]))
    return candidates


def insertable(candidates: list[dict], min_sources: int = ACCESSORY_MINE_MIN_SOURCES) -> list[dict]:
    """Candidates `--apply` inserts: enough sources, not in the catalogue, not
    a lift, no machine."""
    return [c for c in candidates
            if c["sources"] >= min_sources and not c["in_catalogue"] and not c["counts_as_lift"]
            and not c.get("needs_machine")]


def category_for(family: str) -> str:
    return "accessory" if family in ACCESSORY_FAMILIES else "strength"


# ── DB + LLM ──────────────────────────────────────────────────────────────────

def _load_chunks(conn, embedding_model: str, source_id: int | None, limit: int) -> list[tuple]:
    sql = """
        SELECT k.id, k.raw_content,
               array_remove(array_agg(DISTINCT cs.source_id) || k.source_id, NULL) AS sources
        FROM knowledge_chunks k
        LEFT JOIN chunk_sources cs ON cs.chunk_id = k.id
        WHERE NOT k.quarantined
          AND k.embedding_model = %s
          AND k.raw_content ~* %s
    """
    params: list = [embedding_model, PREFILTER_SQL_RE]
    if source_id:
        sql += " AND k.source_id = %s"
        params.append(source_id)
    sql += " GROUP BY k.id ORDER BY k.id"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [(cid, text, sorted(set(srcs or []))) for cid, text, srcs in cur.fetchall()]


def _catalogue_names(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM exercises")
        return [r[0] for r in cur.fetchall()]


def _extract_batch(client, model: str, batch: list[tuple]) -> tuple[dict[int, list[dict]], float]:
    from shared.llm import (
        billed_cost,
        create_message_growing,
        estimate_cost,
        json_schema_kwargs,
        message_text,
        parse_llm_json,
        thinking_kwargs,
    )

    passages = [(i, text) for i, (_cid, text, _s) in enumerate(batch, start=1)]
    response = create_message_growing(
        client, model=model, max_tokens=ACCESSORY_MINE_MAX_TOKENS, label="Accessory mining",
        messages=[{"role": "user", "content": build_prompt(passages)}],
        **json_schema_kwargs(MINE_SCHEMA, thinking_kwargs(model, "disabled")),
    )
    by_index = parse_reply(parse_llm_json(message_text(response)), passages)
    usage = response.usage
    cost = estimate_cost(getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0,
                         model, billed_usd=billed_cost(usage))
    return {batch[i - 1][0]: mentions for i, mentions in by_index.items()}, cost


def _apply(conn, candidates: list[dict], min_sources: int) -> list[dict]:
    inserted = []
    with conn.cursor() as cur:
        for c in insertable(candidates, min_sources):
            (sets_lo, sets_hi), (reps_lo, reps_hi) = c["sets"], c["reps"]
            cur.execute(
                """
                INSERT INTO exercises (name, category, movement_family, complexity_level, primary_purpose,
                                       faults_addressed, typical_sets_low, typical_sets_high,
                                       typical_reps_low, typical_reps_high, source_id)
                VALUES (%s, %s::exercise_category, %s::movement_family, 1, %s, '{}', %s, %s, %s, %s, %s)
                ON CONFLICT (name) DO NOTHING
                RETURNING id
                """,
                (c["name"], category_for(c["movement_family"]), c["movement_family"], c["purpose"],
                 sets_lo, sets_hi, reps_lo, reps_hi, c["primary_source_id"]),
            )
            row = cur.fetchone()
            if row:
                inserted.append({"id": row[0], "name": c["name"], "movement_family": c["movement_family"],
                                 "sources": c["sources"]})
    conn.commit()
    return inserted


def mine(*, out: Path, model: str | None, source_id: int | None, limit: int, resume: bool,
         concurrency: int, apply: bool, min_sources: int) -> dict:
    import psycopg2
    from config import Settings

    from shared.llm import create_llm_client, light_model_for

    settings = Settings()
    model = light_model_for(settings, model)
    conn = psycopg2.connect(settings.database_url)
    try:
        chunks = _load_chunks(conn, settings.embedding_model, source_id, limit)
        logger.info(f"{len(chunks)} prefiltered chunks (model={model})")
        checkpoint = Checkpoint(out, resume=resume, signature={
            "model": model, "prompt_version": PROMPT_VERSION,
            "passages_per_call": ACCESSORY_MINE_PASSAGES_PER_CALL,
            "passage_chars": ACCESSORY_MINE_PASSAGE_CHARS,
        })
        batches = [chunks[i:i + ACCESSORY_MINE_PASSAGES_PER_CALL]
                   for i in range(0, len(chunks), ACCESSORY_MINE_PASSAGES_PER_CALL)]
        todo = [(f"{b[0][0]}-{b[-1][0]}", b) for b in batches]
        todo = [(k, b) for k, b in todo if k not in checkpoint]
        logger.info(f"{len(batches)} calls, {len(batches) - len(todo)} already done")

        client = create_llm_client(settings) if todo else None
        failed = 0
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {pool.submit(_extract_batch, client, model, b): k for k, b in todo}
            for n, fut in enumerate(as_completed(futures), start=1):
                key = futures[fut]
                try:
                    mentions, cost = fut.result()
                except Exception as e:                  # re-run on --resume
                    failed += 1
                    logger.warning(f"Batch {key} failed: {type(e).__name__}: {e}")
                    continue
                checkpoint.put(key, {"mentions": {str(k): v for k, v in mentions.items()}, "cost": cost})
                if n % 25 == 0:
                    logger.info(f"  {n}/{len(todo)} calls")

        mentions_by_chunk: dict[int, list[dict]] = {}
        cost = 0.0
        for k, _b in [(f"{b[0][0]}-{b[-1][0]}", b) for b in batches]:
            value = checkpoint.get(k)
            if value is None:
                continue
            cost += value.get("cost") or 0.0
            for cid, ms in value["mentions"].items():
                mentions_by_chunk[int(cid)] = ms
        chunk_sources = {cid: srcs for cid, _t, srcs in chunks}
        candidates = aggregate(mentions_by_chunk, chunk_sources, _catalogue_names(conn))
        report = {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "model": model, "chunks": len(chunks), "calls": len(batches), "failed_calls": failed,
            "cost_usd": round(cost, 4), "min_sources": min_sources, "candidates": candidates,
            # kept so `--from-report` re-aggregates under the current rules without new calls
            "mentions": {str(k): v for k, v in mentions_by_chunk.items()},
            "chunk_sources": {str(k): v for k, v in chunk_sources.items()},
        }
        if failed:
            logger.warning(f"{failed} call(s) failed — re-run with --resume before applying")
        elif apply:
            report["inserted"] = _apply(conn, candidates, min_sources)
        _write_report(out, report)
        if not failed:
            checkpoint.finish()
        return report
    finally:
        conn.close()


def _write_report(out: Path, report: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    new = insertable(report["candidates"], report["min_sources"])
    print(f"\n{len(report['candidates'])} candidates from {report['chunks']} chunks "
          f"({report['calls']} calls, ${report['cost_usd']:.2f}) → {out}")
    print(f"Named in ≥ {report['min_sources']} sources and not in the catalogue ({len(new)}):")
    for c in new:
        print(f"  {c['name']:32s} {c['movement_family']:10s} {c['sources']:3d} sources  "
              f"sets {c['sets']} reps {c['reps']}")
    if "inserted" in report:
        print(f"Inserted {len(report['inserted'])}: {[r['name'] for r in report['inserted']]}")


def apply_report(path: Path, min_sources: int | None, apply: bool = True) -> dict:
    import psycopg2
    from config import Settings

    report = json.loads(path.read_text(encoding="utf-8"))
    if min_sources is not None:
        report["min_sources"] = min_sources
    conn = psycopg2.connect(Settings().database_url)
    try:
        # Re-aggregate from the stored mentions under the current rules and the
        # current catalogue (it may have grown since the report).
        report["candidates"] = reaggregate(report, _catalogue_names(conn))
        if apply:
            report["inserted"] = _apply(conn, report["candidates"], report["min_sources"])
    finally:
        conn.close()
    _write_report(path, report)
    return report


def reaggregate(report: dict, catalogue_names: list[str]) -> list[dict]:
    """The report's candidates rebuilt from its stored mentions."""
    if "mentions" not in report:
        raise SystemExit("This report has no stored mentions (written before they were kept) — re-run the extraction")
    mentions = {int(k): v for k, v in report["mentions"].items()}
    sources = {int(k): v for k, v in report["chunk_sources"].items()}
    return aggregate(mentions, sources, catalogue_names)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Mine accessory exercises from the corpus (DOG-2g)")
    parser.add_argument("--out", type=Path, default=Path("logs/accessory_candidates.json"))
    parser.add_argument("--model", default=None, help="default: settings.light_model")
    parser.add_argument("--source-id", type=int, help="Only chunks first seen in this source")
    parser.add_argument("--limit", type=int, default=0, help="Only the first N prefiltered chunks (smoke test)")
    parser.add_argument("--resume", action="store_true", help="Skip calls already in <out>.partial.jsonl")
    parser.add_argument("--concurrency", type=int, default=ACCESSORY_MINE_CONCURRENCY)
    parser.add_argument("--apply", action="store_true",
                        help="Insert candidates named in ≥ --min-sources sources that the catalogue lacks")
    parser.add_argument("--min-sources", type=int, default=None,
                        help=f"default {ACCESSORY_MINE_MIN_SOURCES} (or the report's value with --from-report)")
    parser.add_argument("--from-report", type=Path,
                        help="Re-aggregate an existing report under the current rules (no LLM calls); "
                             "with --apply, insert")
    args = parser.parse_args()
    if args.from_report:
        apply_report(args.from_report, args.min_sources, apply=args.apply)
    else:
        mine(out=args.out, model=args.model, source_id=args.source_id, limit=args.limit, resume=args.resume,
             concurrency=args.concurrency, apply=args.apply,
             min_sources=args.min_sources or ACCESSORY_MINE_MIN_SOURCES)
