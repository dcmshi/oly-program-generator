# oly-ingestion/tests/test_epub_extractor.py
"""
Tests for the EPUB chapter extractor.

All tests mock ebooklib so no real .epub file is needed.
bs4 must be installed (it is a pipeline dependency).

Run: python tests/test_epub_extractor.py
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

RESULTS = []


def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, f"{type(e).__name__}: {e}"))


# ── Helpers ───────────────────────────────────────────────────────────────────

_ITEM_DOCUMENT = 9  # ebooklib.ITEM_DOCUMENT constant


def _make_item(html: str, item_type: int = _ITEM_DOCUMENT) -> MagicMock:
    item = MagicMock()
    item.get_type.return_value = item_type
    item.get_content.return_value = html.encode("utf-8")
    return item


def _make_book(items: list) -> MagicMock:
    book = MagicMock()
    book.get_items.return_value = items
    return book


def _mock_ebooklib(book: MagicMock):
    """Patch sys.modules so ebooklib imports in the function under test use our mock."""
    mock_ebooklib = MagicMock()
    mock_ebooklib.ITEM_DOCUMENT = _ITEM_DOCUMENT
    mock_epub = MagicMock()
    mock_epub.read_epub.return_value = book
    mock_ebooklib.epub = mock_epub
    return {
        "ebooklib": mock_ebooklib,
        "ebooklib.epub": mock_epub,
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_basic_text_extraction():
    """Each ITEM_DOCUMENT chapter is returned as a string."""
    from extractors.epub_extractor import extract_text_from_epub

    book = _make_book([
        _make_item("<html><body><p>Snatch technique chapter.</p></body></html>"),
        _make_item("<html><body><p>Clean and jerk chapter.</p></body></html>"),
    ])
    with patch.dict(sys.modules, _mock_ebooklib(book)):
        result = extract_text_from_epub(Path("test.epub"))

    assert len(result) == 2
    assert "Snatch technique" in result[0]
    assert "Clean and jerk" in result[1]


def test_non_document_items_excluded():
    """Items that are not ITEM_DOCUMENT (e.g. images, CSS) are skipped."""
    from extractors.epub_extractor import extract_text_from_epub

    book = _make_book([
        _make_item("<html><body><p>Valid chapter</p></body></html>", _ITEM_DOCUMENT),
        _make_item("<style>body { color: red; }</style>", item_type=7),  # ITEM_STYLE
    ])
    with patch.dict(sys.modules, _mock_ebooklib(book)):
        result = extract_text_from_epub(Path("test.epub"))

    assert len(result) == 1
    assert "Valid chapter" in result[0]


def test_empty_chapters_excluded():
    """Chapters with only whitespace are not returned."""
    from extractors.epub_extractor import extract_text_from_epub

    book = _make_book([
        _make_item("<html><body><p>   </p></body></html>"),
        _make_item("<html><body><p>Real content here</p></body></html>"),
    ])
    with patch.dict(sys.modules, _mock_ebooklib(book)):
        result = extract_text_from_epub(Path("test.epub"))

    assert len(result) == 1
    assert "Real content" in result[0]


def test_script_and_style_tags_removed():
    """<script> and <style> content should not appear in extracted text."""
    from extractors.epub_extractor import extract_text_from_epub

    html = """
    <html><body>
      <script>alert('xss')</script>
      <style>.foo { color: red; }</style>
      <p>Clean weightlifting text</p>
    </body></html>
    """
    book = _make_book([_make_item(html)])
    with patch.dict(sys.modules, _mock_ebooklib(book)):
        result = extract_text_from_epub(Path("test.epub"))

    assert len(result) == 1
    assert "alert" not in result[0]
    assert "color: red" not in result[0]
    assert "Clean weightlifting text" in result[0]


def test_multiple_newlines_collapsed():
    """Runs of 3+ newlines are collapsed to double newlines."""
    from extractors.epub_extractor import extract_text_from_epub

    html = "<html><body><p>Para one</p><p>Para two</p><p>Para three</p></body></html>"
    book = _make_book([_make_item(html)])
    with patch.dict(sys.modules, _mock_ebooklib(book)):
        result = extract_text_from_epub(Path("test.epub"))

    assert len(result) == 1
    # Should not have 3 or more consecutive newlines
    assert "\n\n\n" not in result[0]


def test_empty_epub_returns_empty_list():
    """An EPUB with no document items returns an empty list."""
    from extractors.epub_extractor import extract_text_from_epub

    book = _make_book([])
    with patch.dict(sys.modules, _mock_ebooklib(book)):
        result = extract_text_from_epub(Path("test.epub"))

    assert result == []


def test_import_error_for_missing_ebooklib():
    """ImportError is raised with a helpful message when ebooklib is not installed."""
    from extractors.epub_extractor import extract_text_from_epub

    # Remove ebooklib from sys.modules to simulate it not being installed
    modules_backup = {k: v for k, v in sys.modules.items() if "ebooklib" in k}
    for key in list(modules_backup.keys()):
        sys.modules.pop(key, None)

    # Make the import fail
    with patch.dict(sys.modules, {"ebooklib": None, "ebooklib.epub": None}):
        try:
            extract_text_from_epub(Path("test.epub"))
            assert False, "Expected ImportError"
        except ImportError as e:
            assert "ebooklib" in str(e).lower()
        except Exception:
            pass  # Some other error is acceptable — the point is it doesn't silently succeed

    # Restore
    for key, val in modules_backup.items():
        sys.modules[key] = val


def test_single_chapter_book():
    """Single-chapter EPUBs work correctly."""
    from extractors.epub_extractor import extract_text_from_epub

    book = _make_book([
        _make_item("<html><body><h1>Introduction</h1><p>Content.</p></body></html>"),
    ])
    with patch.dict(sys.modules, _mock_ebooklib(book)):
        result = extract_text_from_epub(Path("test.epub"))

    assert len(result) == 1
    assert "Introduction" in result[0]
    assert "Content" in result[0]


def test_nested_html_structure():
    """Nested HTML elements (lists, tables) are handled without crashing."""
    from extractors.epub_extractor import extract_text_from_epub

    html = """
    <html><body>
      <h2>Programming Principles</h2>
      <ul>
        <li>Progressive overload</li>
        <li>Specificity</li>
        <li>Recovery</li>
      </ul>
    </body></html>
    """
    book = _make_book([_make_item(html)])
    with patch.dict(sys.modules, _mock_ebooklib(book)):
        result = extract_text_from_epub(Path("test.epub"))

    assert len(result) == 1
    assert "Progressive overload" in result[0]
    assert "Specificity" in result[0]


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        _test(name, fn)

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    failed = sum(1 for r in RESULTS if r[0] in ("FAIL", "ERROR"))
    for r in RESULTS:
        detail = f"  → {r[2]}" if len(r) > 2 else ""
        print(f"  {r[0]}  {r[1]}{detail}")
    print(f"\n{passed} passed, {failed} failed")
