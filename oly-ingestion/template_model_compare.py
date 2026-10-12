#!/usr/bin/env python3
"""
Head-to-head program-template parsing across models on fixed sections (ING-EVAL-1).

Each section goes through `pipeline.parse_program_template_text` — the ingest's
own prompts, schemas and 5k/500 continuation windows; only the model changes —
and is scored against `eval_refs/template_parse_reference.json`, hand-checked
against the text: weeks found (required ones present, none outside the allowed
set), training sessions and exercises inside the reference ranges, spot facts
(an exercise with the right sets / reps / % in the right week, or a lift that
must not be invented), and kg loads read as percentages. Failures (empty or
malformed replies), latency and billed cost are reported per model.

The section text is copyrighted book text, so it is not committed: `--build`
re-derives it from `sources/` (the same extraction + heuristic classifier an
ingest runs, LLM fallback stubbed out) into `sources/eval/template_sections.json`
and checks each section's sha256 against the reference (`--pin` records them).

Usage (from oly-ingestion/):
    PYTHONUTF8=1 uv run python template_model_compare.py --build [--pin]
    PYTHONUTF8=1 uv run python template_model_compare.py \\
        --model moonshotai/kimi-k3 --model openai/gpt-6.1-sol --model claude-sonnet-5 \\
        --out logs/template_model_compare.json
    PYTHONUTF8=1 uv run python template_model_compare.py --rescore logs/template_model_compare.json
"""

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))  # repo root for shared.*

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
REFERENCE = HERE / "eval_refs" / "template_parse_reference.json"
SECTIONS = HERE / "sources" / "eval" / "template_sections.json"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Building the fixed sections ──────────────────────────────

def _document_pages(path: Path) -> list[str]:
    """What `pipeline.ingest` hands the classifier for this file, without a
    model call: EPUB chapters, a text PDF's pages joined (RAG-H1), a scan's
    cached OCR text, plain text."""
    from pipeline import IngestionPipeline
    if path.suffix == ".epub":
        from extractors.epub_extractor import extract_text_from_epub
        return extract_text_from_epub(path)
    if path.suffix == ".txt":
        return [path.read_text(encoding="utf-8")]
    from extractors.pdf_extractor import PDFExtractor
    from principle_audit import file_text
    pages = PDFExtractor(vision_model="unused").extract(path)
    if sum(len(p) for p in pages) < 100 * max(1, len(pages)):
        return [file_text(path)]                         # a scan: the OCR cache
    return IngestionPipeline._prepare_pdf_pages(pages)


def build_sections(reference: dict) -> list[dict]:
    """The reference's sections, re-derived from sources/."""
    from config import Settings
    from principle_audit import source_files
    from processors.classifier import ContentClassifier, ContentType

    classifier = ContentClassifier(Settings())
    classifier._llm_classify = lambda text, title: (ContentType.PROSE, 0.55)   # deterministic, free
    files = source_files()
    cache: dict[str, list[str]] = {}
    out = []
    for ref in reference["sections"]:
        prefix = ref["file_prefix"]
        if prefix not in cache:
            matches = sorted(f for f in files if f.name.startswith(prefix) and f.suffix in (".pdf", ".epub"))
            if not matches:
                raise SystemExit(f"No source file starts with {prefix!r}")
            programs = []
            for page in _document_pages(matches[0]):
                if page.strip():
                    programs += [s.content for s in classifier.classify_sections(page, matches[0].stem)
                                 if s.content_type == ContentType.PROGRAM_TEMPLATE]
            cache[prefix] = programs
        programs = cache[prefix]
        if ref["ordinal"] >= len(programs):
            raise SystemExit(f"{ref['id']}: {prefix!r} has only {len(programs)} program sections")
        text = programs[ref["ordinal"]]
        out.append({"id": ref["id"], "sha256": sha256(text), "text": text})
    return out


# ── Scoring (pure) ───────────────────────────────────────────

def _weeks(parsed: dict) -> list[dict]:
    return [w for w in (parsed.get("weeks") or []) if isinstance(w, dict)]


def _sessions(week: dict) -> list[dict]:
    return [s for s in (week.get("sessions") or []) if isinstance(s, dict)]


def _exercises(session: dict) -> list[dict]:
    return [e for e in (session.get("exercises") or []) if isinstance(e, dict)]


def _same(a, b) -> bool:
    return str(a).strip() == str(b).strip()


def spot_passes(parsed: dict, spot: dict) -> bool:
    """Some exercise in `spot['week']` (None = any) whose name matches
    `name_re` with every given field equal — or, with `absent`, none."""
    rx = re.compile(spot["name_re"], re.IGNORECASE)
    found = False
    for w in _weeks(parsed):
        if spot.get("week") is not None and w.get("week_number") != spot["week"]:
            continue
        for s in _sessions(w):
            for e in _exercises(s):
                if not rx.search(str(e.get("name") or "").strip()):
                    continue
                if all(_same(e.get(k), spot[k]) for k in ("sets", "reps", "intensity_pct") if k in spot):
                    found = True
    return not found if spot.get("absent") else found


def distinct_exercises(session: dict) -> list[dict]:
    """One row per exercise name: a wave written '70% x 3 x 3, 75% x 3 x 2' is
    one exercise whether a model gives it one row or one per load step."""
    seen, out = set(), []
    for e in _exercises(session):
        key = " ".join(str(e.get("name") or "").lower().split())
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def score_section(parsed: dict, ref: dict) -> dict:
    weeks = _weeks(parsed)
    week_numbers = {w.get("week_number") for w in weeks}
    sessions = [s for w in weeks for s in _sessions(w) if _exercises(s)]
    exercises = [e for s in sessions for e in distinct_exercises(s)]
    missing = sorted(set(ref["weeks_required"]) - week_numbers)
    extra = sorted((n for n in week_numbers if n not in set(ref["weeks_allowed"])), key=str)
    lo_s, hi_s = ref["sessions"]
    lo_e, hi_e = ref["exercises"]
    n_s, n_e = len(sessions), len(exercises)
    over_cap = 0
    if ref.get("intensity_pct_max") is not None:
        for e in (row for s in sessions for row in _exercises(s)):
            try:
                over_cap += float(e.get("intensity_pct")) > ref["intensity_pct_max"]
            except (TypeError, ValueError):
                pass
    spots = [spot_passes(parsed, sp) for sp in ref["spot"]]
    result = {
        "weeks_found": sorted(week_numbers, key=str), "weeks_missing": missing, "weeks_extra": extra,
        "weeks_ok": not missing and not extra,
        "sessions": n_s, "sessions_ok": lo_s <= n_s <= hi_s,
        "exercises": n_e, "exercises_ok": lo_e <= n_e <= hi_e,
        "exercises_off_by": max(lo_e - n_e, n_e - hi_e, 0),
        "intensity_over_cap": over_cap,
        "spot_passed": sum(spots), "spot_total": len(spots),
    }
    result["all_ok"] = (result["weeks_ok"] and result["sessions_ok"] and result["exercises_ok"]
                        and not over_cap and all(spots))
    return result


# ── Running a model ──────────────────────────────────────────

def run_model(model: str, sections: list[dict], reference: dict, checkpoint=None) -> dict:
    """Parse every section with `model`; a clean section goes to `checkpoint`
    as it lands and one already there (same model and section text) is not
    re-run (RESUME-1)."""
    from config import Settings
    from pipeline import parse_program_template_text

    from shared.llm import (
        LLMRefusal,
        billed_cost,
        create_llm_client,
        estimate_cost,
        is_refusal,
        message_text,
        served_tier,
        usage_tokens,
    )

    settings = Settings(llm_model=model)
    model_id = settings.llm_model
    client = create_llm_client(settings)
    refs = {r["id"]: r for r in reference["sections"]}
    rows = []
    for sec in sections:
        ref = refs[sec["id"]]
        key = f"{model_id}|{sec['id']}|{sec.get('sha256', '')}"
        if checkpoint is not None and key in checkpoint:
            rows.append(checkpoint.get(key))
            continue
        usage = {"input": 0, "output": 0, "cost": 0.0, "calls": 0, "billed": True, "stops": [], "declined": 0}

        def on_message(msg, usage=usage):
            u = usage_tokens(msg.usage)
            billed = billed_cost(msg.usage)
            usage["calls"] += 1
            usage["stops"].append(str(getattr(msg, "stop_reason", None)))
            try:
                usage["declined"] += is_refusal(msg, message_text(msg))
            except LLMRefusal:
                usage["declined"] += 1
            usage["input"] += u["input"]
            usage["output"] += u["output"]
            if billed is None:
                usage["billed"] = False
            usage["cost"] += billed if billed is not None else estimate_cost(
                u["input"], u["output"], model_id, service_tier=served_tier(msg.usage),
                cache_read_tokens=u["cache_read"], cache_creation_tokens=u["cache_creation"])

        errors: list[str] = []
        t0 = time.perf_counter()
        parsed = parse_program_template_text(client, model_id, settings.llm_max_tokens, sec["text"],
                                             f"compare {model_id} {sec['id']}", on_message=on_message,
                                             errors=errors)
        dt = time.perf_counter() - t0
        score = score_section(parsed, ref)
        rows.append({"section": sec["id"], **score, "errors": errors,
                     "empty": not _weeks(parsed) and bool(ref["weeks_required"]),
                     "latency_s": round(dt, 1), "calls": usage["calls"],
                     "stop_reasons": usage["stops"], "declined": usage["declined"],
                     "input_tokens": usage["input"], "output_tokens": usage["output"],
                     "cost_usd": round(usage["cost"], 4), "cost_billed": usage["billed"],
                     "parsed": parsed})
        if checkpoint is not None and not errors:        # a section with a failed call is re-run on --resume
            checkpoint.put(key, rows[-1])
        logger.info(f"{model_id} {sec['id']}: weeks {'ok' if score['weeks_ok'] else 'X'} "
                    f"sessions {score['sessions']} exercises {score['exercises']} "
                    f"spot {score['spot_passed']}/{score['spot_total']} errors {len(errors)} {dt:.0f}s")
    return {"model": model_id, "totals": _totals(rows), "sections": rows}


def rescore(result: dict, reference: dict) -> dict:
    """Re-score a saved run's parses against the current reference (no model calls)."""
    refs = {r["id"]: r for r in reference["sections"]}
    rows = []
    for row in result["sections"]:
        ref = refs[row["section"]]
        rows.append({**row, **score_section(row["parsed"], ref),
                     "empty": not _weeks(row["parsed"]) and bool(ref["weeks_required"])})
    return {**result, "totals": _totals(rows), "sections": rows}


def _totals(rows: list[dict]) -> dict:
    return {
        "sections_all_ok": sum(r["all_ok"] for r in rows),
        "weeks_ok": sum(r["weeks_ok"] for r in rows),
        "sessions_ok": sum(r["sessions_ok"] for r in rows),
        "exercises_ok": sum(r["exercises_ok"] for r in rows),
        "exercises_off_by": sum(r["exercises_off_by"] for r in rows),
        "spot_passed": sum(r["spot_passed"] for r in rows),
        "spot_total": sum(r["spot_total"] for r in rows),
        "intensity_over_cap": sum(r["intensity_over_cap"] for r in rows),
        "failed_calls": sum(len(r["errors"]) for r in rows),
        "declined_calls": sum(r.get("declined", 0) for r in rows),
        "stop_reasons": dict(Counter(s for r in rows for s in r.get("stop_reasons", []))),
        "empty_sections": sum(r["empty"] for r in rows),
        "latency_s": round(sum(r["latency_s"] for r in rows), 1),
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--build", action="store_true", help="re-derive the sections from sources/")
    ap.add_argument("--pin", action="store_true", help="with --build: record the sha256s in the reference")
    ap.add_argument("--model", action="append", default=[])
    ap.add_argument("--section", action="append", help="limit to these section ids")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--resume", action="store_true",
                    help="with --out: continue an aborted run — sections in <out>.partial.jsonl are not re-sent")
    ap.add_argument("--rescore", type=Path, help="re-score a saved --out file against the current reference")
    args = ap.parse_args()

    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    if args.build:
        sections = build_sections(reference)
        SECTIONS.parent.mkdir(parents=True, exist_ok=True)
        SECTIONS.write_text(json.dumps(sections, indent=1, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Wrote {len(sections)} sections to {SECTIONS}")
        if args.pin:
            by_id = {s["id"]: s["sha256"] for s in sections}
            for ref in reference["sections"]:
                ref["sha256"] = by_id[ref["id"]]
            REFERENCE.write_text(json.dumps(reference, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            logger.info(f"Pinned sha256s in {REFERENCE}")
    if args.rescore:
        saved = json.loads(args.rescore.read_text(encoding="utf-8"))
        results = [rescore(r, reference) for r in saved["results"]]
        print_table(results, len(saved["sections"]), None)
        return
    if not args.model:
        return

    if not SECTIONS.exists():
        raise SystemExit(f"{SECTIONS} missing — run with --build first")
    sections = json.loads(SECTIONS.read_text(encoding="utf-8"))
    pinned = {r["id"]: r.get("sha256") for r in reference["sections"]}
    for s in sections:
        if pinned.get(s["id"]) and pinned[s["id"]] != s["sha256"]:
            raise SystemExit(f"{s['id']}: section text changed since the reference was checked "
                             "(extraction or classifier change) — re-check the reference, then --build --pin")
    if args.section:
        sections = [s for s in sections if s["id"] in set(args.section)]

    from shared.checkpoint import Checkpoint
    if args.resume and not args.out:
        raise SystemExit("--resume needs --out (the partial run lives beside it)")
    checkpoint = (Checkpoint(args.out, resume=args.resume, signature={"script": "template_model_compare"})
                  if args.out else None)
    results = [run_model(m, sections, reference, checkpoint) for m in args.model]
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"sections": [s["id"] for s in sections], "results": results},
                                       indent=1, ensure_ascii=False), encoding="utf-8")
        checkpoint.finish()
    print_table(results, len(sections), args.out)


def print_table(results: list[dict], n: int, out: Path | None) -> None:
    print(f"\n{'model':<32} {'all ok':>7} {'weeks':>6} {'sess':>5} {'exer':>5} {'off by':>7} "
          f"{'spot':>7} {'kg>%':>5} {'fail':>5} {'empty':>6} {'sec':>6} {'$':>7}")
    for r in results:
        t = r["totals"]
        print(f"{r['model']:<32} {t['sections_all_ok']:>4}/{n:<2} {t['weeks_ok']:>3}/{n:<2} {t['sessions_ok']:>2}/{n:<2}"
              f" {t['exercises_ok']:>2}/{n:<2} {t['exercises_off_by']:>7} {t['spot_passed']:>3}/{t['spot_total']:<3}"
              f" {t['intensity_over_cap']:>5} {t['failed_calls']:>5} {t['empty_sections']:>6} {t['latency_s']:>6.0f}"
              f" {t['cost_usd']:>7.3f}")
    for r in results:
        t = r["totals"]
        print(f"  {r['model']}: declined {t.get('declined_calls', 0)}, stop reasons {t.get('stop_reasons', {})}")
    if out:
        print(f"\nfull results: {out}")


if __name__ == "__main__":
    main()
