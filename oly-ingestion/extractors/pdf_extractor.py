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
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.constants import OCR_GARBLED_RATIO_MAX, OCR_SECOND_VIEW_DPI, OCR_VIEW_ROTATIONS_DEG
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
                 batch: bool = False, ocr_cache: bool = True, force_vision: bool = False,
                 ocr_postcorrect: bool = False, postcorrect_model: str | None = None):
        """
        Args:
            anthropic_client: Optional Anthropic client instance. When provided,
                              used as a last-resort OCR fallback for image-only PDFs.
            vision_model:     Model id for vision OCR (threaded from settings).
            batch:            Send every page group of the document as one Message
                              Batch (half price, COST-1) instead of sequential calls.
            ocr_cache:        Reuse page text from sources/.ocr_cache/<sha256>.json
                              and write new pages back (ING-M5).
            force_vision:     Skip the text layer and OCR every page. For scans
                              whose embedded OCR is one block per line (paragraphs
                              lost) or full of spaced digits ("1 9 7 3").
            ocr_postcorrect:  After the views agree, send a page that is still
                              garbled to a text model for OCR error correction
                              (ICDAR 2026 HIPE-OCRepair setting). Guarded: the
                              corrected text is kept only if the garbled share
                              drops and the length stays within ±10 %. Off by
                              default — post-correction can also hallucinate.
        """
        self._client = anthropic_client
        self._vision_model = vision_model
        self._batch = batch
        self._ocr_cache = ocr_cache
        self._force_vision = force_vision
        self._ocr_postcorrect = ocr_postcorrect
        self._postcorrect_model = postcorrect_model
        self.last_ocr_report: dict | None = None   # OCR-QA report of the last _extract_with_vision

    def extract(self, path: Path, max_pages: int = 0) -> list[str]:
        """Extract text from a PDF, returning a list of page texts.

        Returns one string per page (empty pages are omitted).
        Falls back through the chain until enough text is found.

        Args:
            max_pages: If > 0, only process the first N pages (useful for test runs).
        """
        if self._force_vision:
            if not self._client:
                raise ValueError("force_vision needs an LLM client (pass --vision)")
            logger.info("force_vision: skipping the text layer, OCR-ing every page")
            pages = self._extract_with_vision(path, max_pages=max_pages)
            logger.info(f"Extracted {len(pages)} pages, {sum(len(p) for p in pages):,} total characters")
            return pages
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

        # OCR quality gate (OCR-QA). No reference exists for a scan, so every
        # page is scored on signals that need none (processors/ocr_quality.py:
        # blank, short against neighbours, echo of the previous page, garbled
        # or non-ASCII share) and each suspect is re-OCR'd on its own as a second
        # view at another zoom; the two views are kept where they agree and the
        # page stays flagged where they don't. Roman pp. 86–90 were stored blank
        # on 2026-09-20 with only a warning — this is what turns that into a
        # recovery, and into a page list in the run stats when it can't recover.
        # The second-view calls are single synchronous requests, so the gate
        # runs for --batch documents too.
        self.last_ocr_report = None
        if True:
            from processors.ocr_quality import assess_pages, choose_view, garbled_ratio, resolve_views, summarize
            texts = [pages.get(i, "") for i in range(n_pages)]
            ink = [self._page_has_ink(doc, i) for i in range(n_pages)]
            qualities = assess_pages(texts, ink)
            suspects = [q for q in qualities if q.suspect]
            if suspects:
                logger.warning(f"Vision OCR: {len(suspects)} suspect page(s) after the group pass — "
                               f"second view at {OCR_SECOND_VIEW_DPI} DPI: "
                               f"{[(q.index + 1, q.reasons[0]) for q in suspects][:12]}")
            verdicts: dict[int, str] = {}
            for q in suspects:
                second = self._ocr_batch(doc, q.index, q.index + 1, dpi=OCR_SECOND_VIEW_DPI, view=1)[0]
                text, agreement, verdict = choose_view(texts[q.index], second)
                if verdict == "disagree":
                    # tie-break with a third independent view: keep the pair that agrees
                    third = self._ocr_batch(doc, q.index, q.index + 1, dpi=OCR_SECOND_VIEW_DPI, view=2)[0]
                    text, agreement, verdict = resolve_views([texts[q.index], second, third])
                if verdict != "disagree" and self._ocr_postcorrect and garbled_ratio(text) > OCR_GARBLED_RATIO_MAX:
                    text = self._postcorrect(text, q.index) or text
                verdicts[q.index + 1] = f"{verdict} ({agreement:.2f})"
                if text != texts[q.index]:
                    pages[q.index] = text
                    fresh.append((q.index, text))
                    if cache is not None:
                        cache.put(q.index, text)
            report = summarize(qualities)
            report["verdicts"] = verdicts
            report["pages_unresolved"] = sorted(
                p for p, v in verdicts.items() if v.startswith("disagree") or not pages.get(p - 1, "").strip()
            )
            self.last_ocr_report = report
            if cache is not None:                       # sits beside the page cache for ocr_audit.py
                report_path = cache.path.with_suffix(".report.json")
                report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
            if report["pages_unresolved"]:
                logger.error(f"Vision OCR: {len(report['pages_unresolved'])} page(s) unresolved after the second view "
                             f"(blank or the views disagree) — check these in the scan: {report['pages_unresolved']}")
            elif suspects:
                logger.info(f"  Vision OCR: all {len(suspects)} suspect page(s) resolved by the second view")
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

    def _ocr_batch(self, doc, start: int, end: int, dpi: int = 150, view: int = 0) -> list[str]:
        """Send a batch of pages to Claude vision and return extracted text per page."""
        response = create_message_growing(
            self._client, label=f"Vision OCR pages {start + 1}–{end}",
            **self._ocr_request(doc, start, end, dpi=dpi, view=view),
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

    @staticmethod
    def _render_png(doc, index: int, dpi: int = 150, view: int = 0) -> bytes:
        """Render one page. `view` > 0 applies a small geometric transform (a
        different zoom and a slight rotation) so a re-transcription is an
        independent probe of the same page, not a re-run of the same pixels
        (risk-controlled VLM OCR, arXiv 2603.19790)."""
        import fitz
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        if view:
            mat = mat.prerotate(OCR_VIEW_ROTATIONS_DEG[(view - 1) % len(OCR_VIEW_ROTATIONS_DEG)])
        return doc[index].get_pixmap(matrix=mat).tobytes("png")

    @staticmethod
    def _page_has_ink(doc, index: int) -> bool:
        """Cheap blank-page test on a 30-DPI grayscale render: any pixel darker
        than mid-grey on more than 0.2 % of the page."""
        import fitz
        pix = doc[index].get_pixmap(matrix=fitz.Matrix(30 / 72, 30 / 72), colorspace=fitz.csGRAY)
        dark = sum(1 for b in pix.samples if b < 128)
        return dark > 0.002 * len(pix.samples)

    def _postcorrect(self, text: str, index: int) -> str | None:
        """LLM OCR post-correction with an acceptance guard (see __init__)."""
        from processors.ocr_quality import garbled_ratio
        model = self._postcorrect_model or self._vision_model
        try:
            response = self._client.messages.create(
                model=model, max_tokens=4096,
                **thinking_kwargs(model, "disabled"),
                messages=[{"role": "user", "content": (
                    "The text below is OCR output from a scanned book page and contains recognition errors. "
                    "Correct ONLY obvious OCR errors (broken words, wrong characters, spaced digits). "
                    "Do not add, remove, reorder or paraphrase anything; keep all numbers and line breaks. "
                    "Return only the corrected text.\n\n" + text
                )}],
            )
            fixed = message_text(response).strip()
        except Exception as e:                       # noqa: BLE001 — never fail a page on post-correction
            logger.warning(f"Vision OCR: post-correction of page {index + 1} failed: {e}")
            return None
        before, after = garbled_ratio(text), garbled_ratio(fixed)
        ratio = len(fixed) / max(1, len(text))
        if after < before and 0.9 <= ratio <= 1.1:
            logger.info(f"  Vision OCR: page {index + 1} post-corrected (garbled {before:.0%} → {after:.0%})")
            return fixed
        logger.info(f"  Vision OCR: page {index + 1} post-correction rejected (garbled {before:.0%} → {after:.0%}, length ×{ratio:.2f})")
        return None

    def _ocr_request(self, doc, start: int, end: int, dpi: int = 150, view: int = 0) -> dict:
        """messages.create kwargs for one group of rendered pages."""
        # Build the message content: alternating page-number labels and images
        content = []
        page_indices = list(range(start, end))

        for i in page_indices:
            # Render at 150 DPI — good balance of legibility vs token cost
            b64 = base64.standard_b64encode(self._render_png(doc, i, dpi, view)).decode()

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
        # Assign text by the page NUMBER in each header, not by position: a
        # model that repeats a header, drops one or splits a page in two used
        # to shift every following page of the group (OCR-QA). Text under a
        # repeated header is concatenated; a number outside the group is ignored.
        by_number: dict[int, list[str]] = {}
        parts = re.split(r"===\s*Page\s+(\d+)\s*===", raw)
        for k in range(1, len(parts) - 1, 2):
            by_number.setdefault(int(parts[k]), []).append(parts[k + 1].strip())
        if by_number:
            wanted = {i + 1 for i in page_indices}
            hits = [num for num in by_number if num in wanted]
            if hits:
                if len(hits) < n or set(by_number) - wanted:
                    logger.warning(
                        f"Vision OCR: pages {page_indices[0] + 1}–{page_indices[-1] + 1}: headers for "
                        f"{sorted(by_number)} — assigning by page number, {n - len(hits)} page(s) blank"
                    )
                return ["\n\n".join(t for t in by_number.get(i + 1, []) if t) for i in page_indices]
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
