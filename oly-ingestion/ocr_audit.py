# ocr_audit.py
"""
Audit vision-OCR output already on disk (OCR-QA).

    uv run python ocr_audit.py                      # every sources/.ocr_cache/*.json
    uv run python ocr_audit.py --pdf "sources/R.A. Roman - ….pdf"   # one book, with ink check
    uv run python ocr_audit.py --clear-suspects --pdf …             # drop suspect pages from the cache
                                                                    # so the next ingest re-OCRs them

Runs the same no-reference checks the pipeline applies at ingest time
(processors/ocr_quality.py) over cached page text: blank pages, pages far
shorter than their neighbours, echoes of the previous page, garbled or
non-ASCII text. With --pdf the rendered page is checked for ink so a genuinely
blank page is not reported. Prints one line per suspect page and the summary;
exit code 1 when any page is unresolved.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from extractors.ocr_cache import file_sha256  # noqa: E402
from processors.ocr_quality import assess_pages, summarize  # noqa: E402

CACHE_DIR = Path(__file__).parent / "sources" / ".ocr_cache"


def audit(cache_file: Path, pdf: Path | None, clear: bool) -> int:
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    n = max((int(k) for k in data["pages"]), default=-1) + 1
    pages = [data["pages"].get(str(i), "") for i in range(n)]
    ink = None
    if pdf is not None:
        import fitz
        from extractors.pdf_extractor import PDFExtractor
        doc = fitz.open(str(pdf))
        ink = [PDFExtractor._page_has_ink(doc, i) for i in range(min(n, len(doc)))] + [True] * max(0, n - len(doc))
    qualities = assess_pages(pages, ink)
    report = summarize(qualities)
    label = pdf.name if pdf else cache_file.name
    print(f"== {label}: {report['pages']} pages, {report['pages_suspect']} suspect ({data.get('model')})")
    for page, reasons in report["reasons"].items():
        print(f"   p{page:<4} {len(pages[page - 1]):5d} ch  {'; '.join(reasons)}")
    if clear and report["suspect_pages"]:
        for page in report["suspect_pages"]:
            data["pages"].pop(str(page - 1), None)
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"   cleared {len(report['suspect_pages'])} page(s) from the cache — re-run the ingest to re-OCR them")
    return 1 if report["pages_suspect"] else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", type=Path, help="audit this PDF's cache (enables the ink check)")
    ap.add_argument("--clear-suspects", action="store_true", help="remove suspect pages from the cache")
    args = ap.parse_args()
    if args.pdf:
        cache_file = CACHE_DIR / f"{file_sha256(args.pdf)}.json"
        if not cache_file.exists():
            sys.exit(f"no OCR cache for {args.pdf.name} ({cache_file.name})")
        return audit(cache_file, args.pdf, args.clear_suspects)
    rc = 0
    for cache_file in sorted(CACHE_DIR.glob("*.json")):
        if cache_file.name.endswith(".report.json"):
            continue
        rc |= audit(cache_file, None, False)
    return rc


if __name__ == "__main__":
    sys.exit(main())
