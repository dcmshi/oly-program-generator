#!/usr/bin/env python3
"""
Head-to-head principle extraction across models on the same text (MODEL-2 revisit).

Each window is one production-sized extraction window (`_PRINCIPLE_WINDOW`,
8,000 chars) sent through `PrincipleExtractor`'s own request builder and
parser, so the prompt, schema and post-processing are exactly what an ingest
uses; only `llm_model` changes. Windows are chosen by a rule that doesn't look
at any model's output — the most number-dense window (most "%" signs) of five
books/papers, and the most number-dense article of three web sites — because
numbers are where extraction can go wrong.

Per model it reports principles, rows with numbers, numeric claims and how many
of them the *window* itself does not state (`principle_audit` rules, strict:
the window, not the whole book), empty recommendations, enum / condition-key
repairs the parser had to make, failed windows, latency and cost.

Usage (from oly-ingestion/):
    PYTHONUTF8=1 uv run python principle_model_compare.py \\
        --model moonshotai/kimi-k3 --model claude-sonnet-5 --model claude-sonnet-4-6 \\
        --out logs/principle_model_compare.json
"""

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))  # repo root for shared.*

from principle_audit import (
    _source_files,
    audit_principle,
    fetch_web_text,
    file_text,
    match_source_files,
    text_numbers,
)
from processors.principle_extractor import _PRINCIPLE_WINDOW, PrincipleExtractor

from shared.llm import create_message_growing, estimate_cost, usage_tokens

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BOOK_SOURCES = (799, 801, 802, 501, 745)          # Roman, Vorobyev, Bompa, Medvedev, Winwood
WEB_HOSTS = ("catalystathletics.com", "sportivnypress.com", "strongerbyscience.com")


def densest_window(text: str, size: int = _PRINCIPLE_WINDOW) -> str:
    """The `size`-char window (step size/2) with the most '%' signs."""
    if len(text) <= size:
        return text
    starts = range(0, len(text) - size + 1, size // 2)
    best = max(starts, key=lambda i: text.count("%", i, i + size))
    return text[best:best + size]


def pick_windows(cur) -> list[dict]:
    files = _source_files()
    windows = []
    cur.execute("SELECT id, title FROM sources WHERE id = ANY(%s)", (list(BOOK_SOURCES),))
    titles = dict(cur.fetchall())
    for sid in BOOK_SOURCES:
        text = "\n\n".join(file_text(f) for f in match_source_files(sid, files) if f.suffix != ".pdf" or sid != 501)
        windows.append({"label": f"book:{sid}", "source_id": sid, "title": titles[sid], "text": densest_window(text)})
    cur.execute("""
        SELECT s.id, s.url, s.title,
               sum(length(k.raw_content) - length(replace(k.raw_content, '%', ''))) AS pct
        FROM sources s JOIN chunk_sources cs ON cs.source_id = s.id JOIN knowledge_chunks k ON k.id = cs.chunk_id
        WHERE s.source_type::text = 'website'
        GROUP BY s.id ORDER BY pct DESC
    """)
    rows = cur.fetchall()
    for host in WEB_HOSTS:
        for sid, url, title, _pct in rows:
            if host in (url or ""):
                text = fetch_web_text(url)
                if text:
                    windows.append({"label": f"web:{host.split('.')[0]}:{sid}", "source_id": sid,
                                    "title": title, "text": densest_window(text)})
                    break
    return windows


class _RepairCounter(logging.Handler):
    """Counts the parser's repairs (enum fallbacks, dropped condition keys)."""
    def __init__(self):
        super().__init__(logging.WARNING)
        self.count = 0

    def emit(self, record):
        msg = record.getMessage()
        if "is not in the enum" in msg or "Dropping unknown principle condition" in msg or "malformed" in msg:
            self.count += 1


def run_model(model: str, windows: list[dict]) -> dict:
    from config import Settings
    settings = Settings(llm_model=model)
    ext = PrincipleExtractor(settings)
    repairs = _RepairCounter()
    logging.getLogger("processors.principle_extractor").addHandler(repairs)
    per_window = []
    try:
        for w in windows:
            t0 = time.perf_counter()
            try:
                msg = create_message_growing(ext._get_client(), label=f"compare {model} {w['label']}",
                                             **ext._request_params(w["text"], w["title"]))
                principles = ext._parse_response(msg, w["title"])
                u = usage_tokens(msg.usage)
                error = None
            except Exception as e:                       # noqa: BLE001 — a failed window is a result
                principles, u, error = [], {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}, str(e)
            dt = time.perf_counter() - t0
            nums = text_numbers(w["text"])
            claims = unsup = with_numbers = empty = 0
            flagged = []
            for p in principles:
                sup, bad = audit_principle(p.recommendation, p.condition, nums)
                claims += len(sup) + len(bad)
                unsup += len(bad)
                with_numbers += bool(sup or bad)
                empty += not p.recommendation
                flagged += [f"{p.principle_name}: {c.field}.{c.key}={c.value:g}" for c in bad]
            per_window.append({
                "window": w["label"], "principles": len(principles), "with_numbers": with_numbers,
                "claims": claims, "unsupported": unsup, "empty_recommendation": empty,
                "latency_s": round(dt, 1), "input_tokens": u["input"], "output_tokens": u["output"],
                "cost_usd": round(estimate_cost(u["input"], u["output"], settings.llm_model,
                                                cache_read_tokens=u["cache_read"],
                                                cache_creation_tokens=u["cache_creation"]), 4),
                "error": error, "unsupported_claims": flagged,
                "principle_names": [p.principle_name for p in principles],
            })
            logger.info(f"{model} {w['label']}: {len(principles)} principles, {unsup}/{claims} unsupported, {dt:.0f}s")
    finally:
        logging.getLogger("processors.principle_extractor").removeHandler(repairs)
    total = defaultdict(float)
    for r in per_window:
        for k in ("principles", "with_numbers", "claims", "unsupported", "empty_recommendation",
                  "latency_s", "cost_usd", "output_tokens"):
            total[k] += r[k]
    total["failed_windows"] = sum(1 for r in per_window if r["error"])
    total["parser_repairs"] = repairs.count
    return {"model": settings.llm_model, "totals": dict(total), "windows": per_window}


def main() -> None:
    import psycopg2
    from config import Settings

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    conn = psycopg2.connect(Settings().database_url)
    windows = pick_windows(conn.cursor())
    conn.close()
    logger.info("Windows: " + ", ".join(f"{w['label']} ({len(w['text']):,} ch)" for w in windows))

    results = [run_model(m, windows) for m in args.model]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"windows": [{k: v for k, v in w.items() if k != "text"} for w in windows],
                                    "results": results}, indent=1), encoding="utf-8")

    print(f"\n{'model':<32} {'princ':>6} {'w/nums':>7} {'claims':>7} {'unsup':>6} {'rate':>6} {'empty':>6} "
          f"{'repairs':>8} {'failed':>7} {'sec':>6} {'$':>7}")
    for r in results:
        t = r["totals"]
        rate = t["unsupported"] / t["claims"] if t["claims"] else 0.0
        print(f"{r['model']:<32} {int(t['principles']):>6} {int(t['with_numbers']):>7} {int(t['claims']):>7} "
              f"{int(t['unsupported']):>6} {rate:>6.1%} {int(t['empty_recommendation']):>6} {int(t['parser_repairs']):>8} "
              f"{int(t['failed_windows']):>7} {t['latency_s']:>6.0f} {t['cost_usd']:>7.3f}")
    print(f"\nfull results: {args.out}")


if __name__ == "__main__":
    main()
