# oly-agent/eval/context_diversity.py
"""
AUD-3: how diverse is the knowledge context generation actually receives?

The golden-set gate scores one query at a time; it cannot see that a program's
sessions are all fed the same few chunks. This reads what the prompts showed —
`generation_log.retrieval_set` (migration 0010: `[{label, id, chunk_type,
source_id, similarity, score, session_query}, …]` per attempt) — and reports,
per program:

- slots: context positions shown across the program's sessions
- distinct chunks and the distinct-chunk ratio (distinct / slots)
- max source share: the largest fraction of slots filled from one source_id
- repeated chunks: chunks shown in >= REPEAT_SESSION_FRACTION of the sessions

One attempt counts per session (week, day): the last successful attempt, else
the last attempt — retries of a session re-show the same context and would
inflate the repeat counts.

    cd oly-agent
    PYTHONUTF8=1 uv run python -m eval.context_diversity 29 12
    PYTHONUTF8=1 uv run python -m eval.context_diversity 29 --json

Read-only (a READ ONLY transaction, rolled back). No LLM or embedding calls.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

REPEAT_SESSION_FRACTION = 0.5    # a chunk in at least this share of sessions is "repeated"

SESSIONS_SQL = """
    SELECT DISTINCT ON (week_number, day_number)
           week_number, day_number, attempt_number, status, retrieval_set
      FROM generation_log
     WHERE program_id = %s AND retrieval_set IS NOT NULL
     ORDER BY week_number, day_number, (status = 'success') DESC, attempt_number DESC, id DESC
"""


def diversity_report(sessions: list[list[dict]],
                     repeat_fraction: float = REPEAT_SESSION_FRACTION) -> dict:
    """Diversity of the composed context over a program's sessions.

    `sessions` is one retrieval_set per session. Pure; no I/O."""
    slots = [entry for rs in sessions for entry in (rs or []) if entry.get("id") is not None]
    n_sessions = len(sessions)
    if not slots:
        return {"sessions": n_sessions, "slots": 0, "distinct_chunks": 0, "distinct_ratio": None,
                "max_source_share": None, "max_source_id": None, "sources": 0,
                "repeated_chunks": [], "repeat_fraction": repeat_fraction}

    sessions_with = Counter()                 # chunk id -> number of sessions showing it
    labels = defaultdict(Counter)             # chunk id -> label positions it took
    meta: dict = {}
    for rs in sessions:
        seen = set()
        for entry in rs or []:
            cid = entry.get("id")
            if cid is None:
                continue
            labels[cid][entry.get("label")] += 1
            meta.setdefault(cid, {"chunk_type": entry.get("chunk_type"), "source_id": entry.get("source_id")})
            if cid not in seen:
                seen.add(cid)
                sessions_with[cid] += 1

    source_counts = Counter(e.get("source_id") for e in slots)
    top_source, top_count = source_counts.most_common(1)[0]
    threshold = repeat_fraction * n_sessions
    repeated = [
        {"id": cid, "sessions": n, "session_share": round(n / n_sessions, 3),
         "labels": dict(labels[cid].most_common()), **meta[cid]}
        for cid, n in sessions_with.most_common() if n >= threshold
    ]
    distinct = len(sessions_with)
    return {
        "sessions": n_sessions,
        "slots": len(slots),
        "distinct_chunks": distinct,
        "distinct_ratio": round(distinct / len(slots), 3),
        "max_source_share": round(top_count / len(slots), 3),
        "max_source_id": top_source,
        "max_source_slots": top_count,
        "sources": len(source_counts),
        "repeated_chunks": repeated,
        "repeat_fraction": repeat_fraction,
    }


def load_sessions(conn, program_id: int) -> list[list[dict]]:
    """One retrieval_set per (week, day) of `program_id`, read-only."""
    from shared.db import fetch_all

    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
    rows = fetch_all(conn, SESSIONS_SQL, (program_id,))
    out = []
    for r in rows:
        rs = r["retrieval_set"]
        out.append(json.loads(rs) if isinstance(rs, str) else (rs or []))
    return out


def format_report(program_id: int, rep: dict) -> str:
    if not rep["slots"]:
        return f"program {program_id}: no logged retrieval sets ({rep['sessions']} sessions)"
    lines = [
        f"program {program_id}: {rep['sessions']} sessions, {rep['slots']} slots, "
        f"{rep['distinct_chunks']} distinct chunks (ratio {rep['distinct_ratio']:.3f}), "
        f"{rep['sources']} sources; max source share {rep['max_source_share']:.3f} "
        f"(source {rep['max_source_id']}: {rep['max_source_slots']} slots)",
        f"  chunks in >= {rep['repeat_fraction']:.0%} of sessions: {len(rep['repeated_chunks'])}",
    ]
    for c in rep["repeated_chunks"]:
        labels = ", ".join(f"{lbl}x{n}" for lbl, n in c["labels"].items())
        lines.append(f"    chunk {c['id']:>6} [{c['chunk_type']}, source {c['source_id']}] "
                     f"in {c['sessions']}/{rep['sessions']} sessions ({labels})")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Composed-context diversity per program (AUD-3)")
    parser.add_argument("program_ids", type=int, nargs="+")
    parser.add_argument("--repeat-fraction", type=float, default=REPEAT_SESSION_FRACTION)
    parser.add_argument("--json", action="store_true", help="print the reports as JSON")
    args = parser.parse_args(argv)

    from shared.config import Settings
    from shared.db import get_connection

    conn = get_connection(Settings().database_url)
    reports = {}
    try:
        for pid in args.program_ids:
            reports[pid] = diversity_report(load_sessions(conn, pid), args.repeat_fraction)
            conn.rollback()
    finally:
        conn.rollback()
        conn.close()

    if args.json:
        print(json.dumps(reports, indent=2))
    else:
        for pid, rep in reports.items():
            print(format_report(pid, rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
