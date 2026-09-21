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
        # Under pytest, use its skip mechanism; the standalone runner catches _Skip.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            import pytest

            pytest.skip("set INTEGRATION_TESTS=1 to enable (needs ANTHROPIC_API_KEY)")
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


def test_split_page_responses_partial_keeps_sections():
    """I-H2: a partial parse keeps the sections that parsed (padding the missing
    tail) instead of collapsing everything into one blob on page 0."""
    raw = "=== Page 1 ===\nAAA\n=== Page 2 ===\nBBB"  # 2 sections for a 3-page batch
    result = PDFExtractor._split_page_responses(raw, [0, 1, 2])
    assert result == ["AAA", "BBB", ""]


def test_split_page_responses_more_sections_truncated():
    """I-H2: extra sections beyond the batch size are truncated, not blobbed."""
    raw = "=== Page 1 ===\nA\n=== Page 2 ===\nB\n=== Page 3 ===\nC"
    result = PDFExtractor._split_page_responses(raw, [0, 1])
    assert result == ["A", "B"]


# ── Fallback chain — mocked fitz / pdfplumber ────────────────────────────────

def _mock_fitz(pages: list[str]):
    """Build a mock fitz module returning the given page texts.

    The extractor reads ``get_text("blocks")`` (RAG-H1), so each page text is
    served as one text block; ``get_text("text")`` still returns the raw string.
    """
    mock_fitz = MagicMock()
    mock_pages = []
    for text in pages:
        p = MagicMock()
        blocks = [(0, 0, 0, 0, text, 0, 0)]
        p.get_text.side_effect = lambda mode="text", _t=text, _b=blocks: _b if mode == "blocks" else _t
        mock_pages.append(p)
    mock_doc = MagicMock()
    mock_doc.__iter__ = lambda self: iter(mock_pages)
    mock_fitz.open.return_value = mock_doc
    return mock_fitz


def test_pymupdf_blocks_become_paragraphs_images_skipped():
    """RAG-H1: text blocks are joined with a blank line (the chunker's paragraph
    separator), image blocks are dropped, and line-end hyphenation is repaired.
    Plain-text mode never produced a blank line, so chunk = page."""
    page = MagicMock()
    blocks = [
        (0, 0, 0, 0, "First para line one\nhy-\nphenated.", 0, 0),
        (0, 0, 0, 0, "<image: x.png>", 1, 1),
        (0, 0, 0, 0, "Second para.", 2, 0),
    ]
    page.get_text.side_effect = lambda mode="text": blocks if mode == "blocks" else "unused"
    doc = MagicMock()
    doc.__iter__ = lambda self: iter([page])
    mock_fitz = MagicMock()
    mock_fitz.open.return_value = doc

    with patch.dict(sys.modules, {"fitz": mock_fitz}):
        pages = PDFExtractor._extract_with_pymupdf(Path("x.pdf"))

    assert pages == ["First para line one\nhyphenated.\n\nSecond para."]


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
    assert _LONG_TEXT.strip() in result[0]
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


def test_pymupdf_exception_falls_through_to_pdfplumber():
    """I-M7: a raising PyMuPDF stage falls through to pdfplumber, not aborts."""
    mock_fitz = MagicMock()
    mock_fitz.open.side_effect = RuntimeError("corrupt xref")
    mock_plumber = _mock_pdfplumber([_LONG_TEXT])

    with patch.dict(sys.modules, {"fitz": mock_fitz, "pdfplumber": mock_plumber}):
        result = PDFExtractor().extract(Path("test.pdf"))

    assert len(result) == 1 and _LONG_TEXT.strip() in result[0]
    mock_plumber.open.assert_called_once()


def test_both_extractors_raise_no_crash():
    """I-M7: both stages raising (no client) returns [] instead of propagating."""
    mock_fitz = MagicMock()
    mock_fitz.open.side_effect = RuntimeError("bad")
    mock_plumber = MagicMock()
    mock_plumber.open.side_effect = RuntimeError("also bad")

    with patch.dict(sys.modules, {"fitz": mock_fitz, "pdfplumber": mock_plumber}):
        result = PDFExtractor(anthropic_client=None).extract(Path("test.pdf"))

    assert result == []


# ── Vision OCR — integration only ────────────────────────────────────────────

def test_vision_batch_mode_sends_one_message_batch_and_keeps_page_order():
    """With batch=True every page group is one request in a single Message
    Batch; results come back in page order and a failed group yields blank
    pages instead of aborting the document."""
    from types import SimpleNamespace

    from shared.llm import BatchRequestFailed

    extractor = PDFExtractor(anthropic_client=MagicMock(), vision_model="claude-sonnet-5", batch=True)
    doc = MagicMock()
    captured = {}

    def fake_request(doc, start, end):
        return {"model": "claude-sonnet-5", "max_tokens": 8192, "messages": [{"pages": (start, end)}]}

    def fake_batch(client, requests, **kw):
        captured.update(requests)
        assert "ceiling" not in kw          # truncated groups regrow up to LLM_MAX_TOKENS_CEILING
        return {
            "pages-1-5": SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(
                type="text", text="".join(f"=== Page {i} ===\np{i}\n" for i in range(1, 6)))]),
            "pages-6-7": BatchRequestFailed("pages-6-7", "errored"),
        }

    with patch.object(extractor, "_ocr_request", side_effect=fake_request), \
         patch("extractors.pdf_extractor.run_message_batch", side_effect=fake_batch):
        pages = extractor._ocr_groups_batched(doc, [(0, 5), (5, 7)])

    assert list(captured) == ["pages-1-5", "pages-6-7"]
    assert captured["pages-6-7"]["messages"] == [{"pages": (5, 7)}]
    assert pages == ["p1", "p2", "p3", "p4", "p5", "", ""]


def test_vision_sync_path_grows_the_budget_on_truncation():
    """A group that stops on max_tokens is re-sent with a doubled budget (five
    dense pages overran 8,192 twice on the 2026-09-20 Medvedev run); the
    ceiling reply is still used, with the truncation warning."""
    from types import SimpleNamespace

    cut = SimpleNamespace(stop_reason="max_tokens", content=[SimpleNamespace(type="text", text="=== Page 1 ===\nhel")])
    ok = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="=== Page 1 ===\nhello")])
    client = MagicMock()
    client.messages.create.side_effect = [cut, ok]
    extractor = PDFExtractor(anthropic_client=client, vision_model="claude-sonnet-5")
    with patch.object(extractor, "_ocr_request", return_value={"model": "claude-sonnet-5", "max_tokens": 8192, "messages": []}):
        assert extractor._ocr_batch(MagicMock(), 0, 1) == ["hello"]
    assert [c.kwargs["max_tokens"] for c in client.messages.create.call_args_list] == [8192, 16384]
    assert client.messages.create.call_args.kwargs["model"] == "claude-sonnet-5"


def test_vision_ocr_requires_anthropic_client():
    """Calling _extract_with_vision without a client raises AttributeError."""
    _integration_only()  # gate — this test is a placeholder
    # Full OCR tests require a real PDF and API key
    extractor = PDFExtractor(anthropic_client=None)
    try:
        extractor._extract_with_vision(Path("nonexistent.pdf"))
        raise AssertionError("Should have failed without fitz or client")
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


def test_vision_uses_the_page_cache_and_only_transcribes_missing_groups(tmp_path):
    """ING-M5: pages cached for this file + model are reused; only groups with a
    missing page reach the model; new pages are written back; a different model
    invalidates the cache."""

    from extractors.ocr_cache import OcrCache, file_sha256

    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    cache = OcrCache(pdf, "claude-sonnet-5", cache_dir=tmp_path / "cache")
    for i in range(5):                      # first group fully cached, second group missing
        cache.put(i, f"cached page {i + 1}")
    cache.save()
    assert cache.path.name == file_sha256(pdf) + ".json"

    extractor = PDFExtractor(anthropic_client=MagicMock(), vision_model="claude-sonnet-5")
    doc = MagicMock()
    doc.__len__ = lambda self: 7
    calls = []

    def fake_ocr(d, start, end, dpi=150, view=0, images=None):
        calls.append((start, end))
        return [f"fresh page {i + 1}" for i in range(start, end)]

    fz = MagicMock()
    fz.open.return_value = doc
    with patch.dict(sys.modules, {"fitz": fz}), \
         patch("extractors.ocr_cache.CACHE_DIRNAME", "cache"), \
         patch.object(extractor, "_ocr_batch", side_effect=fake_ocr):
        pages = extractor._extract_with_vision(pdf)
    assert calls == [(5, 7)]                                       # cached group skipped
    assert pages == [f"cached page {i}" for i in range(1, 6)] + ["fresh page 6", "fresh page 7"]
    reloaded = OcrCache(pdf, "claude-sonnet-5", cache_dir=tmp_path / "cache")
    assert len(reloaded) == 7 and reloaded.get(6) == "fresh page 7"
    assert len(OcrCache(pdf, "moonshotai/kimi-k3", cache_dir=tmp_path / "cache")) == 0   # model change → re-OCR

    # --no-ocr-cache: everything is transcribed and nothing is written
    extractor = PDFExtractor(anthropic_client=MagicMock(), vision_model="claude-sonnet-5", ocr_cache=False)
    calls.clear()
    with patch.dict(sys.modules, {"fitz": fz}), patch.object(extractor, "_ocr_batch", side_effect=fake_ocr):
        extractor._extract_with_vision(pdf)
    assert calls == [(0, 5), (5, 7)]


def test_force_vision_skips_the_text_layer(tmp_path):
    """--force-vision: a PDF with a usable (but paragraph-less) text layer still
    goes straight to vision OCR; without a client it raises instead of silently
    using the layer."""
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    extractor = PDFExtractor(anthropic_client=MagicMock(), vision_model="m", ocr_cache=False, force_vision=True)
    with patch.object(extractor, "_extract_with_pymupdf") as pymupdf, \
         patch.object(extractor, "_extract_with_vision", return_value=["ocr page"]) as vision:
        assert extractor.extract(pdf) == ["ocr page"]
    pymupdf.assert_not_called()
    vision.assert_called_once()
    import pytest
    with pytest.raises(ValueError):
        PDFExtractor(anthropic_client=None, force_vision=True).extract(pdf)


def test_vision_retries_blank_pages_singly(tmp_path):
    """OCR-QA: a page that came back blank from its group is re-OCR'd on its own;
    the recovered text is used and cached, a page still blank is reported."""
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    extractor = PDFExtractor(anthropic_client=MagicMock(), vision_model="m", ocr_cache=False)
    doc = MagicMock()
    doc.__len__ = lambda self: 5
    calls = []

    body = "The lifter pulls the bar and drops under it in one continuous movement. " * 6

    def fake_ocr(d, start, end, dpi=150, view=0, images=None):
        calls.append((start, end, dpi, view))
        if end - start > 1:                      # group pass: pages 2 and 4 lost
            return [f"page {i + 1} {body}" if i not in (1, 3) else "" for i in range(start, end)]
        return [f"recovered page 2 {body}"] if start == 1 else [""]

    fz = MagicMock()
    fz.open.return_value = doc
    with patch.dict(sys.modules, {"fitz": fz}), patch.object(extractor, "_ocr_batch", side_effect=fake_ocr),          patch.object(extractor, "_render_group", return_value=[b"png"] * 5),          patch.object(extractor, "_page_has_ink", return_value=True):
        pages = extractor._extract_with_vision(pdf)
    # suspects get a second view (200 DPI, rotated); page 4 stays blank so a third view is tried too
    assert calls == [(0, 5, 150, 0), (1, 2, 200, 1), (3, 4, 200, 1), (3, 4, 200, 2)]
    assert [p.split(" The")[0] for p in pages] == ["page 1", "recovered page 2", "page 3", "page 5"]  # page 4 omitted
    assert extractor.last_ocr_report["pages_unresolved"] == [4]
    assert extractor.last_ocr_report["verdicts"][2].startswith("recovered")


def test_split_page_responses_assigns_by_header_number():
    """OCR-QA: a repeated, missing or out-of-range header no longer shifts the
    following pages; text under a repeated header is joined."""
    raw = "=== Page 6 ===\nsix\n=== Page 8 ===\neight-a\n=== Page 8 ===\neight-b\n=== Page 9 ===\nnine\n=== Page 42 ===\nstray"
    assert PDFExtractor._split_page_responses(raw, [5, 6, 7, 8, 9]) == ["six", "", "eight-a\n\neight-b", "nine", ""]


def test_postcorrect_is_guarded():
    """Post-correction output is kept only when the garbled share drops and the
    length stays within ±10 %."""
    from types import SimpleNamespace
    ex = PDFExtractor(anthropic_client=MagicMock(), vision_model="m", ocr_postcorrect=True)
    garbled = "The sn atch is per formed frm the flr with spd undr the bar xq tbxrq vvvvvv zzzzzz " * 6
    clean = "The snatch is performed from the floor with speed under the bar and a solid catch. " * 6
    ex._client.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(type="text", text=clean)])
    assert ex._postcorrect(garbled, 3) == clean.strip()
    ex._client.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(type="text", text=clean * 3)])
    assert ex._postcorrect(garbled, 3) is None          # length ×3 → rejected
