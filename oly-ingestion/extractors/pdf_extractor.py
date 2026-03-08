# extractors/pdf_extractor.py
"""
Extracts text from PDF files.

Uses PyMuPDF (fitz) as the primary extractor. Falls back to pdfplumber
for scanned PDFs that need layout-aware extraction. OCR via pytesseract
is available as a last resort for image-only PDFs.

Most weightlifting programming books are text-based PDFs, so the
PyMuPDF path handles the majority of cases.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class PDFExtractor:
    """Extract text from PDFs with fallback chain."""

    def extract(self, path: Path) -> list[str]:
        """Extract text from a PDF, returning a list of page texts.

        Falls back through extraction methods if the primary method
        yields very little text (suggesting a scanned/image PDF).
        """
        pages = self._extract_with_pymupdf(path)

        # If PyMuPDF extracted very little text (or no pages at all), try pdfplumber
        total_chars = sum(len(p) for p in pages)
        if total_chars < 100:
            logger.warning(
                f"PyMuPDF extracted only {total_chars} chars from {len(pages)} pages. "
                "Trying pdfplumber..."
            )
            pages = self._extract_with_pdfplumber(path)

        # If still very little text, this might be a scanned PDF
        total_chars = sum(len(p) for p in pages)
        if total_chars < 100:
            logger.warning(
                f"pdfplumber also extracted only {total_chars} chars. "
                "This may be a scanned PDF requiring OCR. "
                "Run with --ocr flag or pre-process with pytesseract."
            )

        logger.info(f"Extracted {len(pages)} pages, {total_chars:,} total characters")
        return pages

    @staticmethod
    def _extract_with_pymupdf(path: Path) -> list[str]:
        """Primary extraction using PyMuPDF (fast, handles most PDFs)."""
        import fitz  # PyMuPDF

        doc = fitz.open(str(path))
        pages = []
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                pages.append(text)
        doc.close()
        return pages

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
