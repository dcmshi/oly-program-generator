# extractors/pdf_extractor.py
"""
Extracts text from PDF files.

Fallback chain:
  1. PyMuPDF (fitz)     — fast, handles most text-layer PDFs
  2. pdfplumber         — better layout handling for complex PDFs
  3. Claude vision API  — for image-only / scanned PDFs (no local OCR needed)

Most weightlifting programming books are text-based, so PyMuPDF handles the
majority of cases. The vision fallback is used for scanned Soviet-era books
(e.g. Laputin) where no text layer exists.
"""

import base64
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.llm import create_message_with_retries

logger = logging.getLogger(__name__)

# Pages with fewer than this many chars are treated as image-only
_TEXT_THRESHOLD = 100

# How many pages to send in one Claude call (balance latency vs cost)
_VISION_BATCH_SIZE = 5

# Output token budget per vision batch. 5 dense book pages routinely exceed the
# old 4096, truncating (and losing) the tail pages of the batch.
_VISION_MAX_TOKENS = 8192

_DEFAULT_VISION_MODEL = "claude-sonnet-4-6"


class PDFExtractor:
    """Extract text from PDFs with a three-stage fallback chain."""

    def __init__(self, anthropic_client=None, vision_model: str = _DEFAULT_VISION_MODEL):
        """
        Args:
            anthropic_client: Optional Anthropic client instance. When provided,
                              used as a last-resort OCR fallback for image-only PDFs.
            vision_model:     Model id for vision OCR (threaded from settings).
        """
        self._client = anthropic_client
        self._vision_model = vision_model

    def extract(self, path: Path, max_pages: int = 0) -> list[str]:
        """Extract text from a PDF, returning a list of page texts.

        Returns one string per page (empty pages are omitted).
        Falls back through the chain until enough text is found.

        Args:
            max_pages: If > 0, only process the first N pages (useful for test runs).
        """
        # Each stage is guarded so a raised exception (corrupt xref, encrypted
        # PDF, …) falls THROUGH to the next stage instead of aborting the run —
        # the chain was previously only a low-yield chain, not an error chain (I-M7).
        try:
            pages = self._extract_with_pymupdf(path)
        except Exception as e:
            logger.warning(f"PyMuPDF extraction failed ({type(e).__name__}: {e}) — trying pdfplumber...")
            pages = []
        if max_pages:
            pages = pages[:max_pages]

        total_chars = sum(len(p) for p in pages)
        if total_chars < _TEXT_THRESHOLD:
            logger.warning(
                f"PyMuPDF extracted only {total_chars} chars from {len(pages)} pages — "
                "trying pdfplumber..."
            )
            try:
                pages = self._extract_with_pdfplumber(path)
            except Exception as e:
                logger.warning(f"pdfplumber extraction failed ({type(e).__name__}: {e})")
                pages = []
            if max_pages:
                pages = pages[:max_pages]

        total_chars = sum(len(p) for p in pages)
        if total_chars < _TEXT_THRESHOLD:
            if self._client:
                logger.warning(
                    f"pdfplumber also extracted only {total_chars} chars — "
                    "falling back to Claude vision OCR..."
                )
                pages = self._extract_with_vision(path, max_pages=max_pages)
            else:
                logger.warning(
                    f"pdfplumber also extracted only {total_chars} chars. "
                    "This appears to be a scanned PDF. "
                    "Pass an Anthropic client to PDFExtractor to enable vision OCR."
                )

        total_chars = sum(len(p) for p in pages)
        logger.info(f"Extracted {len(pages)} pages, {total_chars:,} total characters")
        return pages

    # ── Stage 1: PyMuPDF ─────────────────────────────────────

    @staticmethod
    def _extract_with_pymupdf(path: Path) -> list[str]:
        """Primary extraction using PyMuPDF (fast, handles most PDFs)."""
        import fitz
        doc = fitz.open(str(path))
        pages = []
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                pages.append(text)
        doc.close()
        return pages

    # ── Stage 2: pdfplumber ───────────────────────────────────

    @staticmethod
    def _extract_with_pdfplumber(path: Path) -> list[str]:
        """Fallback extraction using pdfplumber (better for complex layouts)."""
        import pdfplumber
        pages = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(text)
        return pages

    # ── Stage 3: Claude vision OCR ────────────────────────────

    def _extract_with_vision(self, path: Path, max_pages: int = 0) -> list[str]:
        """OCR via Claude vision — renders each page as a PNG and sends to the API.

        Pages are processed in batches of _VISION_BATCH_SIZE to balance
        latency and cost. A short delay between batches avoids rate limits.

        Args:
            max_pages: If > 0, only OCR the first N pages.

        Cost: ~$0.002–0.004 per page at current Claude pricing.
        """
        import fitz

        doc = fitz.open(str(path))
        n_pages = len(doc)
        if max_pages:
            n_pages = min(n_pages, max_pages)
        logger.info(f"Vision OCR: processing {n_pages} pages in batches of {_VISION_BATCH_SIZE}")

        all_pages: list[str] = []

        for batch_start in range(0, n_pages, _VISION_BATCH_SIZE):
            batch_end = min(batch_start + _VISION_BATCH_SIZE, n_pages)
            batch_texts = self._ocr_batch(doc, batch_start, batch_end)
            all_pages.extend(batch_texts)

            logger.info(
                f"  Vision OCR: pages {batch_start + 1}–{batch_end}/{n_pages} done "
                f"({sum(len(t) for t in batch_texts):,} chars)"
            )

            # Brief pause between batches to stay within rate limits
            if batch_end < n_pages:
                time.sleep(1.0)

        doc.close()
        return [t for t in all_pages if t.strip()]

    def _ocr_batch(self, doc, start: int, end: int) -> list[str]:
        """Send a batch of pages to Claude vision and return extracted text per page."""
        import fitz

        # Build the message content: alternating page-number labels and images
        content = []
        page_indices = list(range(start, end))

        for i in page_indices:
            page = doc[i]
            # Render at 150 DPI — good balance of legibility vs token cost
            mat = fitz.Matrix(150 / 72, 150 / 72)
            pix = page.get_pixmap(matrix=mat)
            png_bytes = pix.tobytes("png")
            b64 = base64.standard_b64encode(png_bytes).decode()

            content.append({
                "type": "text",
                "text": f"Page {i + 1}:",
            })
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": b64,
                },
            })

        content.append({
            "type": "text",
            "text": (
                "Extract all text from each page above exactly as it appears. "
                "Preserve paragraph breaks with blank lines. "
                "For each page, start with a line '=== Page N ===' then the text. "
                "Do not summarise, interpret, or add any commentary — only the raw text."
            ),
        })

        response = create_message_with_retries(
            self._client,
            model=self._vision_model,
            max_tokens=_VISION_MAX_TOKENS,
            messages=[{"role": "user", "content": content}],
        )

        # A truncated response silently drops the tail pages of the batch —
        # surface it loudly (and hint at shrinking the batch) rather than
        # embedding partial/blank pages (I-H2).
        if getattr(response, "stop_reason", None) == "max_tokens":
            logger.warning(
                f"Vision OCR: response hit max_tokens ({_VISION_MAX_TOKENS}) for "
                f"pages {start + 1}–{end}; text may be truncated — consider a "
                f"smaller _VISION_BATCH_SIZE (currently {_VISION_BATCH_SIZE})."
            )

        raw = response.content[0].text
        return self._split_page_responses(raw, page_indices)

    @staticmethod
    def _split_page_responses(raw: str, page_indices: list[int]) -> list[str]:
        """Split Claude's batched response into per-page strings.

        Expects sections delimited by '=== Page N ===' headers.
        Falls back to returning the whole response as one page if parsing fails.
        """
        import re
        n = len(page_indices)
        sections = re.split(r"===\s*Page\s+\d+\s*===", raw)
        # First element is any text before the first header — discard it
        sections = [s.strip() for s in sections[1:]]

        if len(sections) == n:
            return sections

        # No headers parsed at all — return the whole blob as the first page.
        if not sections:
            logger.warning(
                f"Vision OCR: no '=== Page N ===' headers found — returning raw "
                f"response as the first of {n} page(s)"
            )
            return [raw.strip()] + [""] * (n - 1)

        # Partial parse — KEEP the sections that parsed (pad/truncate to the batch
        # size) instead of discarding them into one blob (I-H2).
        logger.warning(
            f"Vision OCR: expected {n} page sections, got {len(sections)} — "
            f"keeping the {min(len(sections), n)} parsed section(s)"
        )
        if len(sections) < n:
            sections = sections + [""] * (n - len(sections))
        return sections[:n]
