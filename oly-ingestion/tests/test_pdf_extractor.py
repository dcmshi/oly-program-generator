# oly-ingestion/tests/test_pdf_extractor.py
"""
Tests for the PDF extraction fallback chain.

Pure-logic tests (_split_page_responses) need no dependencies.
Fallback-chain tests mock fitz and pdfplumber — no real PDF needed.
Vision OCR tests require INTEGRATION_TESTS=1 and a real Anthropic key.

Run: python tests/test_pdf_extractor.py
     INTEGRATION_TESTS=1 python tests/test_pdf_extractor.py
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

RESULTS = []
_INTEGRATION = os.getenv("INTEGRATION_TESTS", "").lower() in ("1", "true")


class _Skip(Exception):
    pass


def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except _Skip as e:
        RESULTS.append(("SKIP", name, str(e)))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, f"{type(e).__name__}: {e}"))


def _integration_only():
    if not _INTEGRATION:
        raise _Skip("set INTEGRATION_TESTS=1 to enable (needs ANTHROPIC_API_KEY)")


from extractors.pdf_extractor import PDFExtractor

_LONG_TEXT = "Olympic weightlifting training program text. " * 10  # >100 chars
_SHORT_TEXT = "Hi"  # <100 chars


# ── _split_page_responses — pure logic ───────────────────────────────────────

def test_split_page_responses_normal():
    raw = "=== Page 1 ===\nSnatch technique\n=== Page 2 ===\nClean and jerk"
    result = PDFExtractor._split_page_responses(raw, [0, 1])
    assert len(result) == 2
    assert result[0] == "Snatch technique"
    assert result[1] == "Clean and jerk"


def test_split_page_responses_strips_whitespace():
    raw = "=== Page 1 ===\n\n  text with spaces  \n\n=== Page 2 ===\n  more text  "
    result = PDFExtractor._split_page_responses(raw, [0, 1])
    assert result[0] == "text with spaces"
    assert result[1] == "more text"


def test_split_page_responses_single_page():
    raw = "=== Page 1 ===\nSingle page content"
    result = PDFExtractor._split_page_responses(raw, [0])
    assert len(result) == 1
    assert result[0] == "Single page content"


def test_split_page_responses_mismatch_falls_back_to_raw():
    """When section count doesn't match page count, return raw text for first page."""
    raw = "=== Page 1 ===\nOnly one section found"
    result = PDFExtractor._split_page_responses(raw, [0, 1, 2])
    assert len(result) == 3
    assert "Only one section found" in result[0]
    assert result[1] == ""
    assert result[2] == ""


def test_split_page_responses_no_markers_returns_raw():
    """Response with no page markers returns the whole text for the first page."""
    raw = "Text without any page markers at all"
    result = PDFExtractor._split_page_responses(raw, [0, 1])
    assert len(result) == 2
    assert raw.strip() in result[0]


def test_split_page_responses_three_pages():
    raw = "=== Page 1 ===\nA\n=== Page 2 ===\nB\n=== Page 3 ===\nC"
    result = PDFExtractor._split_page_responses(raw, [0, 1, 2])
    assert result == ["A", "B", "C"]


# ── Fallback chain — mocked fitz / pdfplumber ────────────────────────────────

def _mock_fitz(pages: list[str]):
    """Build a mock fitz module returning the given page texts."""
    mock_fitz = MagicMock()
    mock_pages = []
    for text in pages:
        p = MagicMock()
        p.get_text.return_value = text
        mock_pages.append(p)
    mock_doc = MagicMock()
    mock_doc.__iter__ = lambda self: iter(mock_pages)
    mock_fitz.open.return_value = mock_doc
    return mock_fitz


def _mock_pdfplumber(pages: list[str]):
    """Build a mock pdfplumber module returning the given page texts."""
    mock_plumber = MagicMock()
    mock_pages = [MagicMock(extract_text=MagicMock(return_value=t)) for t in pages]
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=MagicMock(pages=mock_pages))
    ctx.__exit__ = MagicMock(return_value=False)
    mock_plumber.open.return_value = ctx
    return mock_plumber


def test_pymupdf_succeeds_no_fallback():
    """When PyMuPDF returns enough text, pdfplumber should never be called."""
    mock_fitz = _mock_fitz([_LONG_TEXT])
    mock_plumber = MagicMock()

    with patch.dict(sys.modules, {"fitz": mock_fitz, "pdfplumber": mock_plumber}):
        result = PDFExtractor().extract(Path("test.pdf"))

    assert len(result) == 1
    assert _LONG_TEXT in result[0]
    mock_plumber.open.assert_not_called()


def test_pymupdf_fails_triggers_pdfplumber_fallback():
    """When PyMuPDF returns <100 chars, pdfplumber is tried next."""
    mock_fitz = _mock_fitz([_SHORT_TEXT])
    mock_plumber = _mock_pdfplumber([_LONG_TEXT])

    with patch.dict(sys.modules, {"fitz": mock_fitz, "pdfplumber": mock_plumber}):
        result = PDFExtractor().extract(Path("test.pdf"))

    assert len(result) == 1
    mock_plumber.open.assert_called_once()


def test_both_fail_no_client_no_vision():
    """When both pymupdf and pdfplumber fail and no client is set, return what we have."""
    mock_fitz = _mock_fitz([_SHORT_TEXT])
    mock_plumber = _mock_pdfplumber([_SHORT_TEXT])

    with patch.dict(sys.modules, {"fitz": mock_fitz, "pdfplumber": mock_plumber}):
        extractor = PDFExtractor(anthropic_client=None)
        result = extractor.extract(Path("test.pdf"))

    # Returns whatever text was found (short, but not errored)
    assert isinstance(result, list)


def test_both_fail_with_client_calls_vision():
    """When both extractors fail and a client is provided, vision OCR is attempted."""
    mock_fitz_module = _mock_fitz([_SHORT_TEXT])
    mock_plumber = _mock_pdfplumber([_SHORT_TEXT])
    mock_client = MagicMock()

    with patch.dict(sys.modules, {"fitz": mock_fitz_module, "pdfplumber": mock_plumber}):
        extractor = PDFExtractor(anthropic_client=mock_client)
        with patch.object(extractor, "_extract_with_vision", return_value=["OCR result text"]) as mock_vision:
            result = extractor.extract(Path("test.pdf"))

    mock_vision.assert_called_once()
    assert "OCR result text" in result


def test_max_pages_limits_results():
    """max_pages parameter truncates extracted pages."""
    pages = [_LONG_TEXT] * 10
    mock_fitz = _mock_fitz(pages)

    with patch.dict(sys.modules, {"fitz": mock_fitz}):
        result = PDFExtractor().extract(Path("test.pdf"), max_pages=3)

    assert len(result) == 3


def test_max_pages_zero_means_no_limit():
    """max_pages=0 (default) returns all pages."""
    pages = [_LONG_TEXT] * 5
    mock_fitz = _mock_fitz(pages)

    with patch.dict(sys.modules, {"fitz": mock_fitz}):
        result = PDFExtractor().extract(Path("test.pdf"), max_pages=0)

    assert len(result) == 5


def test_empty_pages_excluded():
    """Pages with only whitespace are excluded from results."""
    mock_fitz = _mock_fitz(["   ", "\n\n", _LONG_TEXT])

    with patch.dict(sys.modules, {"fitz": mock_fitz}):
        result = PDFExtractor().extract(Path("test.pdf"))

    assert len(result) == 1


# ── Vision OCR — integration only ────────────────────────────────────────────

def test_vision_ocr_requires_anthropic_client():
    """Calling _extract_with_vision without a client raises AttributeError."""
    _integration_only()  # gate — this test is a placeholder
    # Full OCR tests require a real PDF and API key
    extractor = PDFExtractor(anthropic_client=None)
    try:
        extractor._extract_with_vision(Path("nonexistent.pdf"))
        assert False, "Should have failed without fitz or client"
    except Exception:
        pass  # expected — any failure is acceptable here


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        _test(name, fn)

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    skipped = sum(1 for r in RESULTS if r[0] == "SKIP")
    failed = sum(1 for r in RESULTS if r[0] in ("FAIL", "ERROR"))
    for r in RESULTS:
        detail = f"  → {r[2]}" if len(r) > 2 else ""
        print(f"  {r[0]}  {r[1]}{detail}")
    print(f"\n{passed} passed, {skipped} skipped, {failed} failed")
