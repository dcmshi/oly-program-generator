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
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.llm import (
    BatchRequestFailed,
    create_message_growing,
    message_text,
    run_message_batch,
    thinking_kwargs,
)

logger = logging.getLogger(__name__)

# Pages with fewer than this many chars are treated as image-only
_TEXT_THRESHOLD = 100

# How many pages to send in one Claude call (balance latency vs cost)
_VISION_BATCH_SIZE = 5

# Output token budget per vision batch. 5 dense book pages routinely exceed the
# old 4096, truncating (and losing) the tail pages of the batch.
_VISION_MAX_TOKENS = 8192

_DEFAULT_VISION_MODEL = "claude-sonnet-5"


class PDFExtractor:
    """Extract text from PDFs with a three-stage fallback chain."""

    def __init__(self, anthropic_client=None, vision_model: str = _DEFAULT_VISION_MODEL,
                 batch: bool = False, ocr_cache: bool = True):
        """
        Args:
            anthropic_client: Optional Anthropic client instance. When provided,
                              used as a last-resort OCR fallback for image-only PDFs.
            vision_model:     Model id for vision OCR (threaded from settings).
            batch:            Send every page group of the document as one Message
                              Batch (half price, COST-1) instead of sequential calls.
            ocr_cache:        Reuse page text from sources/.ocr_cache/<sha256>.json
                              and write new pages back (ING-M5).
        """
        self._client = anthropic_client
        self._vision_model = vision_model
        self._batch = batch
        self._ocr_cache = ocr_cache

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
        """Primary extraction using PyMuPDF (fast, handles most PDFs).

        Uses ``get_text("blocks")`` rather than ``"text"``: plain-text mode
        separates blocks with a single ``\\n``, so the chunker (which splits
        paragraphs on ``\\n\\n``) saw every page as one paragraph and chunk = page
        for all five PyMuPDF sources (RAG-H1). Text blocks (type 0) are joined
        with a blank line; image blocks are skipped; line-end hyphenation inside
        a block is repaired.
        """
        import fitz

        from extractors.page_text import dehyphenate
        doc = fitz.open(str(path))
        pages = []
        for page in doc:
            blocks = [
                dehyphenate(b[4].strip())
                for b in page.get_text("blocks")
                if len(b) > 6 and b[6] == 0 and str(b[4]).strip()
            ]
            text = "\n\n".join(blocks)
            if text.strip():
                pages.append(text)
        doc.close()
        return pages

    # ── Stage 2: pdfplumber ───────────────────────────────────

    @staticmethod
    def _extract_with_pdfplumber(path: Path) -> list[str]:
        """Fallback extraction using pdfplumber (better for complex layouts).

        ``layout=True`` keeps the vertical whitespace between paragraphs, which
        becomes the ``\\n\\n`` the chunker needs; the horizontal padding it adds
        is collapsed again.
        """
        import pdfplumber

        from extractors.page_text import dehyphenate
        pages = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text(layout=True) or ""
                text = re.sub(r"[ \t]{2,}", " ", text)
                text = "\n".join(ln.strip() for ln in text.splitlines())
                text = re.sub(r"\n{3,}", "\n\n", text)
                if text.strip():
                    pages.append(dehyphenate(text.strip()))
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

        # ING-M5: pages already transcribed for this exact file + model come
        # from the cache; only page groups with a missing page go to the model.
        cache = None
        if self._ocr_cache:
            from extractors.ocr_cache import OcrCache
            cache = OcrCache(path, self._vision_model)
        pages: dict[int, str] = {i: cache.get(i) for i in range(n_pages) if cache and cache.has(i)}
        groups = [
            (start, min(start + _VISION_BATCH_SIZE, n_pages))
            for start in range(0, n_pages, _VISION_BATCH_SIZE)
            if any(i not in pages for i in range(start, min(start + _VISION_BATCH_SIZE, n_pages)))
        ]
        logger.info(
            f"Vision OCR: {n_pages} pages, {len(pages)} from cache, "
            f"{len(groups)} group(s) of {_VISION_BATCH_SIZE} to transcribe"
        )

        fresh: list[tuple[int, str]] = []
        if groups and self._batch:
            texts = self._ocr_groups_batched(doc, groups)
            fresh = list(zip([i for start, end in groups for i in range(start, end)], texts, strict=True))
        elif groups:
            for start, end in groups:
                batch_texts = self._ocr_batch(doc, start, end)
                fresh.extend(zip(range(start, end), batch_texts, strict=True))
                logger.info(
                    f"  Vision OCR: pages {start + 1}–{end}/{n_pages} done "
                    f"({sum(len(t) for t in batch_texts):,} chars)"
                )
                # Brief pause between batches to stay within rate limits
                if end < n_pages:
                    time.sleep(1.0)
        for i, text in fresh:
            pages[i] = text
            if cache is not None:
                cache.put(i, text)
        if cache is not None and fresh:
            cache.save()
            logger.info(f"  OCR cache: {len(cache)} page(s) stored at {cache.path}")

        doc.close()
        return [pages[i] for i in range(n_pages) if pages.get(i, "").strip()]

    def _ocr_groups_batched(self, doc, groups: list[tuple[int, int]]) -> list[str]:
        """OCR every page group through one Message Batch (COST-1).

        A group whose request failed yields empty pages and a warning — the
        same outcome as an unparseable synchronous reply — rather than
        aborting the document.
        """
        requests = {
            f"pages-{start + 1}-{end}": self._ocr_request(doc, start, end)
            for start, end in groups
        }
        # A group that stops on max_tokens is re-sent with a doubled budget
        # (up to LLM_MAX_TOKENS_CEILING) in a follow-up batch — five dense
        # pages overran 8,192 twice on the 2026-09-20 Medvedev run.
        responses = run_message_batch(self._client, requests, label="Vision OCR")
        pages: list[str] = []
        for start, end in groups:
            response = responses.get(f"pages-{start + 1}-{end}")
            if isinstance(response, BatchRequestFailed) or response is None:
                logger.warning(f"Vision OCR: pages {start + 1}–{end} failed in batch: {response}")
                pages.extend([""] * (end - start))
                continue
            texts = self._ocr_response_pages(response, start, end)
            pages.extend(texts)
            logger.info(
                f"  Vision OCR: pages {start + 1}–{end} done ({sum(len(t) for t in texts):,} chars)"
            )
        return pages

    def _ocr_batch(self, doc, start: int, end: int) -> list[str]:
        """Send a batch of pages to Claude vision and return extracted text per page."""
        response = create_message_growing(
            self._client, label=f"Vision OCR pages {start + 1}–{end}", **self._ocr_request(doc, start, end)
        )
        return self._ocr_response_pages(response, start, end)

    def _ocr_response_pages(self, response, start: int, end: int) -> list[str]:
        """Per-page texts from one OCR reply (shared by the sync and batch paths)."""
        # Both paths already re-sent with a doubled budget up to the ceiling;
        # a reply still stopping on max_tokens has lost its tail pages —
        # surface it loudly rather than embedding partial/blank pages (I-H2).
        if getattr(response, "stop_reason", None) == "max_tokens":
            logger.warning(
                f"Vision OCR: response still hit max_tokens at the ceiling for "
                f"pages {start + 1}–{end}; text is truncated — use a smaller "
                f"_VISION_BATCH_SIZE (currently {_VISION_BATCH_SIZE})."
            )

        raw = message_text(response)
        return self._split_page_responses(raw, list(range(start, end)))

    def _ocr_request(self, doc, start: int, end: int) -> dict:
        """messages.create kwargs for one group of rendered pages."""
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

        return dict(
            model=self._vision_model,
            max_tokens=_VISION_MAX_TOKENS,
            messages=[{"role": "user", "content": content}],
            **thinking_kwargs(self._vision_model, "disabled"),   # transcription — no thinking
        )

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
