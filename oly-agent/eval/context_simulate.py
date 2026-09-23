# oly-agent/eval/context_simulate.py
"""
AUD-3: replay a program's session queries through the real context composition.

`context_diversity.py` measures what a program's prompts *did* receive. This
measures what they *would* receive under the current retrieval + composition
code, without generating anything: it reads the program's per-session
`session_query` from `generation_log.retrieval_set` (the last attempt per
(week, day), as context_diversity does), re-runs the session searches and the
athlete's fault searches with the production settings, and composes every
session in (week, day) order — the order the orchestrator composes in (AUD-4
prefetch) — twice:

- `stateless`: compose_session_context with no program state (the pre-AUD-3
  behaviour: fault chunks C1-C2 identical in every session);
- `stateful`: one ContextDiversityState for the program (fault rotation +
  program-level source cap).

For each it prints context_diversity's report plus the mean retrieval score of
the composed session chunks, so a diversity gain that costs relevance shows.

    cd oly-agent
    PYTHONUTF8=1 uv run python -m eval.context_simulate 29 12
    PYTHONUTF8=1 uv run python -m eval.context_simulate 29 --share 0.3 --json

Read-only on the DB (READ ONLY transaction, rolled back); embeds each distinct
query once (fractions of a cent). No LLM calls unless --rerank.
"""

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT = _HERE.parent
_REPO = _AGENT.parent
for p in (str(_REPO), str(_AGENT), str(_REPO / "oly-ingestion")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.context_diversity import diversity_report, format_report

SESSIONS_SQL = """
    SELECT DISTINCT ON (week_number, day_number)
           week_number, day_number, retrieval_set
      FROM generation_log
     WHERE program_id = %s AND retrieval_set IS NOT NULL
     ORDER BY week_number, day_number, (status = 'success') DESC, attempt_number DESC, id DESC
"""
ATHLETE_SQL = """
    SELECT a.technical_faults, a.level
      FROM generated_programs p JOIN athletes a ON a.id = p.athlete_id
     WHERE p.id = %s
"""


def session_queries(rows: list[dict]) -> list[tuple[int, int, str | None]]:
    """(week, day, session_query) per logged session, in (week, day) order."""
    out = []
    for r in sorted(rows, key=lambda r: (r["week_number"], r["day_number"])):
        rs = r["retrieval_set"]
        rs = json.loads(rs) if isinstance(rs, str) else (rs or [])
        query = next((e.get("session_query") for e in rs if e.get("session_query")), None)
        out.append((r["week_number"], r["day_number"], query))
    return out


def simulate(queries: list[str | None], search, fault_chunks: list[dict], has_faults: bool,
             state=None, **compose_kwargs) -> list[list[dict]]:
    """Compose every session in order. `search(query) -> candidates` (cached by
    the caller). Returns one composed list per session. Pure apart from `search`."""
    from retrieve import compose_session_context

    out = []
    for q in queries:
        candidates = search(q) if q else []
        composed = compose_session_context(candidates, fault_chunks, has_faults=has_faults,
                                           state=state, **compose_kwargs)
        out.append([{**c, "label": f"C{i}"} for i, c in enumerate(composed, 1)])
    return out


def mean_session_score(sessions: list[list[dict]]) -> float | None:
    """Mean retrieval similarity of the non-fault chunks shown."""
    vals = [float(c.get("similarity") or 0.0) for s in sessions for c in s if "fault" not in c]
    return round(sum(vals) / len(vals), 4) if vals else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Replay a program's session queries through the composer (AUD-3)")
    parser.add_argument("program_ids", type=int, nargs="+")
    parser.add_argument("--share", type=float, default=None, help="override MAX_SOURCE_SHARE_IN_PROGRAM")
    parser.add_argument("--rerank", nargs="?", const="", default=None, metavar="MODEL",
                        help="rerank the searches first (costs money)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from loaders.vector_loader import VectorLoader
    from retrieve import (
        SESSION_PREFERRED_TYPES,
        ContextDiversityState,
        fetch_fault_chunks,
        rerank_candidates,
    )

    from shared.config import Settings
    from shared.constants import (
        HYBRID_SEARCH_ENABLED,
        RERANK_TOP_N,
        VECTOR_SEARCH_DEFAULT_TOP_K,
        VECTOR_SEARCH_MIN_SIMILARITY,
    )
    from shared.db import fetch_all, get_connection

    settings = Settings()
    reranker = None
    if args.rerank is not None:
        from rerank import ListwiseReranker
        reranker = ListwiseReranker.from_settings(settings, args.rerank or None)
    top_k = settings.vector_search_top_k or VECTOR_SEARCH_DEFAULT_TOP_K
    compose_kwargs = {} if args.share is None else {"program_source_share": args.share}

    conn = get_connection(settings.database_url)
    loader = VectorLoader(settings)
    reports = {}
    try:
        for pid in args.program_ids:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
            rows = fetch_all(conn, SESSIONS_SQL, (pid,))
            athlete = fetch_all(conn, ATHLETE_SQL, (pid,))
            conn.rollback()
            if not rows or not athlete:
                reports[pid] = None
                continue
            faults = list(athlete[0]["technical_faults"] or [])
            level = athlete[0]["level"]
            queries = [q for _, _, q in session_queries(rows)]
            fault_chunks = fetch_fault_chunks(loader, faults, level, top_k, reranker) if faults else []
            loader.conn.rollback()
            cache: dict = {}

            def search(q, _cache=cache):
                if q not in _cache:
                    keep = top_k * 2
                    found = loader.similarity_search(
                        query=q, top_k=max(keep, RERANK_TOP_N) if reranker else keep,
                        preferred_chunk_types=SESSION_PREFERRED_TYPES,
                        min_similarity=VECTOR_SEARCH_MIN_SIMILARITY, hybrid=HYBRID_SEARCH_ENABLED,
                    )
                    loader.conn.rollback()
                    _cache[q] = rerank_candidates(q, found, reranker, top_k=keep) if reranker else found
                return _cache[q]

            stateless = simulate(queries, search, fault_chunks, bool(faults))
            stateful = simulate(queries, search, fault_chunks, bool(faults),
                                state=ContextDiversityState(), **compose_kwargs)
            reports[pid] = {
                "faults": faults,
                "distinct_session_queries": len(set(q for q in queries if q)),
                "stateless": {**diversity_report(stateless), "mean_session_similarity": mean_session_score(stateless)},
                "stateful": {**diversity_report(stateful), "mean_session_similarity": mean_session_score(stateful)},
            }
    finally:
        conn.rollback()
        conn.close()
        loader.close()

    if args.json:
        print(json.dumps(reports, indent=2, default=str))
    else:
        for pid, rep in reports.items():
            if rep is None:
                print(f"program {pid}: no logged retrieval sets")
                continue
            print(f"program {pid}: faults {rep['faults']}, {rep['distinct_session_queries']} distinct session queries")
            for mode in ("stateless", "stateful"):
                print(f"  [{mode}] mean session-chunk similarity {rep[mode]['mean_session_similarity']}")
                print("  " + format_report(pid, rep[mode]).replace("\n", "\n  "))
    if reranker is not None:
        print(f"rerank ({reranker.model}): {reranker.calls} calls, ${reranker.cost_usd:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
