#!/usr/bin/env python3
"""
Head-to-head vision OCR across models on fixed scanned pages (ING-EVAL-1).

Pages are chosen by a rule that looks at no model's output for this run: in
each scanned book with an OCR cache, the 5-page ingest group (aligned like
`_extract_with_vision`'s groups) whose cached text holds the most digits — the
tables are where a transcription loses a program — and the group nearest the
book's median length with the fewest digits (prose). Each group goes through
`PDFExtractor._ocr_request` / `_ocr_response_pages`, the ingest's own request
and page splitter; only `vision_model` changes.

There is no ground truth for a scan, so pages are scored the way the OCR-QA
gate scores them (processors/ocr_quality.py; never by asking a model to grade
itself):
  - suspects — `assess_pages` flags (blank, short vs neighbours, echo, garbled,
    non-ASCII) over the model's pages;
  - peer agreement — `view_agreement` with every other candidate's text of the
    same page, plus the cached transcription as one more voter. The caches were
    made by Kimi K3, so agreement with the cache alone would favour it; the
    peer mean is the headline;
  - number recall — the share of the page's numbers (as the majority of the
    other transcriptions have them) that this model reproduced;
  - lost pages — a page that came back empty (a dropped `=== Page N ===`).

Usage (from oly-ingestion/):
    PYTHONUTF8=1 uv run python ocr_model_compare.py \\
        --model moonshotai/kimi-k3 --model openai/gpt-6.1-sol --model claude-sonnet-5 \\
        --out logs/ocr_model_compare.json
    PYTHONUTF8=1 uv run python ocr_model_compare.py --model claude-sonnet-5-5 \\
        --peers logs/ocr_model_compare.json --out logs/ocr_model_compare_plus.json
"""

import argparse
import json
import logging
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))  # repo root for shared.*

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GROUP = 5                      # = pdf_extractor._VISION_BATCH_SIZE
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def numbers(text: str) -> Counter:
    return Counter(m.replace(",", ".") for m in _NUMBER_RE.findall(text))


def number_recall(text: str, reference: Counter) -> float | None:
    """Share of `reference`'s numbers (with multiplicity) found in `text`."""
    total = sum(reference.values())
    if not total:
        return None
    return sum((numbers(text) & reference).values()) / total


def majority_numbers(texts: list[str]) -> Counter:
    """Numbers (with multiplicity) that more than half of `texts` contain."""
    if not texts:
        return Counter()
    counts = [numbers(t) for t in texts]
    need = len(texts) // 2 + 1
    out = Counter()
    for n in set().union(*counts):
        ks = sorted((c[n] for c in counts), reverse=True)
        if len(ks) >= need and ks[need - 1] > 0:
            out[n] = ks[need - 1]                        # the count a majority reaches
    return out


# ── Page selection ───────────────────────────────────────────

def scanned_books() -> list[tuple[Path, dict]]:
    from extractors.ocr_cache import CACHE_DIRNAME, file_sha256
    root = Path(__file__).parent / "sources"
    out = []
    for pdf in sorted(root.glob("*.pdf")):
        cache = root / CACHE_DIRNAME / f"{file_sha256(pdf)}.json"
        if cache.exists():
            out.append((pdf, json.loads(cache.read_text(encoding="utf-8"))))
    return out


def pick_groups(cache_pages: dict[str, str]) -> list[tuple[str, int]]:
    """[("tables", start), ("prose", start)] — 0-based group starts."""
    pages = {int(k): v for k, v in cache_pages.items()}
    if not pages:
        return []
    last = max(pages)
    groups = []
    for start in range(0, last + 1, GROUP):
        texts = [pages.get(i, "") for i in range(start, start + GROUP)]
        if all(t.strip() for t in texts):
            groups.append((start, sum(len(t) for t in texts), sum(len(_NUMBER_RE.findall(t)) for t in texts)))
    if not groups:
        return []
    tables = max(groups, key=lambda g: (g[2], -g[0]))
    median_len = statistics.median(g[1] for g in groups)
    prose_pool = sorted(groups, key=lambda g: abs(g[1] - median_len))[:max(3, len(groups) // 5)]
    prose = min((g for g in prose_pool if g[0] != tables[0]), key=lambda g: (g[2], g[0]), default=None)
    return [("tables", tables[0])] + ([("prose", prose[0])] if prose else [])


# ── Running a model ──────────────────────────────────────────

def run_model(model: str, books: list[tuple[Path, dict]], plan: list[dict], reasoning: str = "default") -> dict:
    """`reasoning`: "default" drops the thinking / effort fields so each model
    runs at its own default; "pipeline" keeps what an ingest sends
    (`thinking_kwargs(model, "disabled")`)."""
    import fitz
    from config import Settings
    from extractors.pdf_extractor import PDFExtractor

    from shared.llm import (
        billed_cost,
        create_llm_client,
        create_message_growing,
        estimate_cost,
        served_tier,
        usage_tokens,
    )

    settings = Settings(vision_model=model)
    model_id = settings.vision_model
    ext = PDFExtractor(create_llm_client(settings), vision_model=model_id, ocr_cache=False)
    by_name = {pdf.name: pdf for pdf, _ in books}
    groups = []
    for g in plan:
        start, end = g["start"], g["start"] + GROUP
        t0 = time.perf_counter()
        try:
            with fitz.open(by_name[g["book"]]) as doc:
                params = ext._ocr_request(doc, start, end)
            if reasoning == "default":
                params.pop("thinking", None)
                params.pop("output_config", None)
            msg = create_message_growing(ext._client, label=f"compare OCR {model_id} {g['label']}", **params)
            stop_reason = getattr(msg, "stop_reason", None)
            ext.refused_pages = set()
            pages = ext._ocr_response_pages(msg, start, end)   # a decline comes back as blank pages
            declined = bool(ext.refused_pages)
            u = usage_tokens(msg.usage)
            billed = billed_cost(msg.usage)
            cost = billed if billed is not None else estimate_cost(
                u["input"], u["output"], model_id, service_tier=served_tier(msg.usage),
                cache_read_tokens=u["cache_read"], cache_creation_tokens=u["cache_creation"])
            error = None
        except Exception as e:                           # noqa: BLE001 — a failed group is a result
            pages, u, cost, billed, error = [""] * GROUP, {"input": 0, "output": 0}, 0.0, None, str(e)
            stop_reason, declined = None, False
        dt = time.perf_counter() - t0
        groups.append({"label": g["label"], "pages": pages, "latency_s": round(dt, 1),
                       "stop_reason": stop_reason, "declined": declined,
                       "input_tokens": u["input"], "output_tokens": u["output"],
                       "cost_usd": round(cost, 4), "cost_billed": billed is not None, "error": error})
        logger.info(f"{model_id} {g['label']}: {sum(1 for p in pages if p.strip())}/{GROUP} pages, "
                    f"{sum(len(p) for p in pages):,} chars, {dt:.0f}s, ${cost:.4f}")
    return {"model": model_id, "groups": groups}


def score(results: list[dict], plan: list[dict]) -> None:
    """Adds `totals` to each result in place (needs every model's pages)."""
    from processors.ocr_quality import assess_pages, garbled_ratio, view_agreement

    for r in results:
        peers_all, cache_agree, recalls, suspects, lost, garbled = [], [], [], 0, 0, []
        for gi, g in enumerate(r["groups"]):
            cached = plan[gi]["cached"]
            quality = assess_pages(g["pages"])
            suspects += sum(q.suspect for q in quality)
            for pi, text in enumerate(g["pages"]):
                if not text.strip():
                    lost += 1
                others = [o["groups"][gi]["pages"][pi] for o in results if o is not r] + [cached[pi]]
                peers_all.append(statistics.mean(view_agreement(text, o) for o in others))
                cache_agree.append(view_agreement(text, cached[pi]))
                rec = number_recall(text, majority_numbers(others))
                if rec is not None:
                    recalls.append(rec)
                garbled.append(garbled_ratio(text))
        r["totals"] = {
            "pages": len(peers_all), "lost_pages": lost, "suspect_pages": suspects,
            "failed_groups": sum(1 for g in r["groups"] if g["error"]),
            "declined_groups": sum(1 for g in r["groups"] if g.get("declined")),
            "stop_reasons": dict(Counter(str(g.get("stop_reason")) for g in r["groups"] if not g["error"])),
            "peer_agreement": round(statistics.mean(peers_all), 3) if peers_all else 0.0,
            "cache_agreement": round(statistics.mean(cache_agree), 3) if cache_agree else 0.0,
            "number_recall": round(statistics.mean(recalls), 3) if recalls else None,
            "garbled_mean": round(statistics.mean(garbled), 4) if garbled else 0.0,
            "latency_s": round(sum(g["latency_s"] for g in r["groups"]), 1),
            "output_tokens": sum(g["output_tokens"] for g in r["groups"]),
            "cost_usd": round(sum(g["cost_usd"] for g in r["groups"]), 4),
        }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-groups", type=int, default=0, help="smoke: only the first N page groups")
    ap.add_argument("--reasoning", choices=("default", "pipeline"), default="default",
                    help="default: each model's own reasoning default (no thinking / effort field); "
                         "pipeline: what an ingest sends (thinking disabled, or adaptive + low where mandatory)")
    ap.add_argument("--peers", type=Path, help="a saved --out file whose models' pages join the scoring "
                                                "(re-scored, not re-run) — add a model without re-paying for the rest")
    args = ap.parse_args()

    books = scanned_books()
    plan = []
    for pdf, cache in books:
        for kind, start in pick_groups(cache["pages"]):
            plan.append({"book": pdf.name, "label": f"{pdf.name.split(' - ')[0]}:{kind}:p{start + 1}-{start + GROUP}",
                         "start": start, "cached": [cache["pages"].get(str(i), "") for i in range(start, start + GROUP)],
                         "cache_model": cache.get("model")})
    if args.max_groups:
        plan = plan[:args.max_groups]
    logger.info("Page groups: " + ", ".join(g["label"] for g in plan))

    results = [run_model(m, books, plan, args.reasoning) for m in args.model]
    for r in results:
        r["reasoning"] = args.reasoning
    if args.peers:
        saved = json.loads(args.peers.read_text(encoding="utf-8"))
        if [g["label"] for g in saved["groups"]] != [g["label"] for g in plan]:
            raise SystemExit(f"{args.peers} covers other page groups — re-run every model instead")
        have = {r["model"] for r in results}
        results += [r for r in saved["results"] if r["model"] not in have]
    score(results, plan)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"groups": [{k: v for k, v in g.items() if k != "cached"} for g in plan],
                                    "results": results}, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"\n{'model':<32} {'pages':>5} {'lost':>5} {'susp':>5} {'fail':>5} {'decl':>5} {'peer agr':>9} {'cache agr':>10} "
          f"{'num rec':>8} {'garbled':>8} {'sec':>6} {'$':>7}")
    for r in results:
        t = r["totals"]
        rec = f"{t['number_recall']:.3f}" if t["number_recall"] is not None else "—"
        print(f"{r['model']:<32} {t['pages']:>5} {t['lost_pages']:>5} {t['suspect_pages']:>5} {t['failed_groups']:>5} "
              f"{t['declined_groups']:>5} "
              f"{t['peer_agreement']:>9.3f} {t['cache_agreement']:>10.3f} {rec:>8} {t['garbled_mean']:>8.4f} "
              f"{t['latency_s']:>6.0f} {t['cost_usd']:>7.3f}")
    for r in results:
        print(f"  {r['model']}: stop reasons {r['totals']['stop_reasons']}")
    print(f"\ncache transcriptions by: {sorted({g['cache_model'] for g in plan})}; full results: {args.out}")


if __name__ == "__main__":
    main()
