# extractors/ocr_cache.py
"""
On-disk cache of vision-OCR page text (ING-M5).

OCR is the one ingestion cost that a re-ingest pays again in full — Medvedev's
146 pages were transcribed three times on 2026-09-20 (≈ $0.75 and 20 min
each) while the chunking bug was being fixed. The cache stores the text per
page under `sources/.ocr_cache/<sha256 of the PDF>.json` (gitignored with the
sources), keyed by the OCR model, so a re-ingest of an unchanged file skips
the vision calls entirely and a model change re-OCRs.

`PDFExtractor._extract_with_vision` consults it page by page: cached pages
are reused, only the missing page groups go to the model, and the result is
written back before returning. `--no-ocr-cache` on pipeline.py bypasses it.
"""

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_DIRNAME = ".ocr_cache"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


class OcrCache:
    def __init__(self, pdf_path: Path, model: str, cache_dir: Path | None = None):
        self.model = model
        self.path = (cache_dir or pdf_path.parent / CACHE_DIRNAME) / f"{file_sha256(pdf_path)}.json"
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"model": self.model, "pages": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning(f"OCR cache unreadable ({e}) — starting fresh: {self.path}")
            return {"model": self.model, "pages": {}}
        if data.get("model") != self.model:
            logger.info(f"OCR cache was made by {data.get('model')!r}, now {self.model!r} — re-OCR")
            return {"model": self.model, "pages": {}}
        return data

    def get(self, page_index: int) -> str | None:
        return self._data["pages"].get(str(page_index))

    def has(self, page_index: int) -> bool:
        return str(page_index) in self._data["pages"]

    def put(self, page_index: int, text: str) -> None:
        self._data["pages"][str(page_index)] = text

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")

    def __len__(self) -> int:
        return len(self._data["pages"])
