"""OCR-ROLE part 2b: two-page spread detection and the split OCR request."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from extractors.spreads import find_gutter, is_landscape, spread_gutters

W, H = 400, 280


def _page(blocks: list[tuple[float, float, float, float]]) -> bytes:
    """A white W×H grayscale render with 'text' — a dark line every 4th row —
    in each (x0, x1, y0, y1) block, all fractions of the page."""
    px = bytearray([255]) * (W * H)
    for x0, x1, y0, y1 in blocks:
        for y in range(int(y0 * H), int(y1 * H), 4):
            for x in range(int(x0 * W), int(x1 * W)):
                px[y * W + x] = 40
    return bytes(px)


def test_a_spread_splits_in_its_gutter():
    gutter = find_gutter(_page([(0.06, 0.45, 0.1, 0.9), (0.55, 0.94, 0.1, 0.9)]), W, H)
    assert gutter is not None and 0.45 <= gutter <= 0.55


def test_text_across_the_middle_is_not_a_spread():
    # a single page centred on a landscape canvas, or a table running across
    assert find_gutter(_page([(0.25, 0.75, 0.1, 0.9)]), W, H) is None


def test_a_blank_half_is_not_split():
    # Kono's section openers: blank left page, content on the right
    assert find_gutter(_page([(0.55, 0.94, 0.1, 0.9)]), W, H) is None


def test_a_half_empty_page_splits_at_the_middle_not_inside_its_blank_area():
    # the right page holds three lines at the top: its blank area joins the
    # gutter, and the middle of that run would cut through the right page's text
    gutter = find_gutter(_page([(0.06, 0.45, 0.1, 0.9), (0.55, 0.94, 0.1, 0.15)]), W, H)
    assert gutter is not None and 0.45 <= gutter <= 0.55


def test_a_shadow_tick_in_the_gutter_is_tolerated():
    px = bytearray(_page([(0.06, 0.45, 0.1, 0.9), (0.55, 0.94, 0.1, 0.9)]))
    for y in range(int(0.08 * H), int(0.09 * H)):        # a short dark mark on the fold
        px[y * W + W // 2] = 0
    assert find_gutter(bytes(px), W, H) is not None


class _Doc:
    """Pages with a size only; `get_pixmap` must not be reached for a portrait book."""
    def __init__(self, sizes):
        self._pages = [SimpleNamespace(rect=SimpleNamespace(width=w, height=h)) for w, h in sizes]

    def __getitem__(self, i):
        return self._pages[i]


def test_a_mostly_portrait_book_never_splits():
    # Vorobyev: 7 landscape diagram pages in 319
    sizes = [(600, 800)] * 312 + [(800, 620)] * 7
    assert is_landscape(800, 620) and not is_landscape(600, 800)
    assert spread_gutters(_Doc(sizes), len(sizes)) == {}


def _extractor(gutters=None):
    from extractors.pdf_extractor import PDFExtractor
    ext = PDFExtractor(vision_model="claude-sonnet-5")
    ext._gutters = gutters or {}
    return ext


def test_request_sends_a_spread_as_two_halves_under_one_page_label():
    ext = _extractor()
    req = ext._ocr_request(None, 9, 11, images=[(b"L", b"R"), b"P"])
    content = req["messages"][0]["content"]
    texts = [c["text"] for c in content if c["type"] == "text"]
    assert texts[:3] == ["Page 10 (two-page spread), left half:", "Page 10 (two-page spread), right half:", "Page 11:"]
    assert sum(1 for c in content if c["type"] == "image") == 3
    assert "write its left half in full, then its right half" in texts[-1]


def test_request_without_a_spread_is_unchanged():
    req = _extractor()._ocr_request(None, 0, 2, images=[b"A", b"B"])
    instruction = req["messages"][0]["content"][-1]["text"]
    assert instruction == ("Extract all text from each page above exactly as it appears. "
                           "Preserve paragraph breaks with blank lines. "
                           "For each page, start with a line '=== Page N ===' then the text. "
                           "Do not summarise, interpret, or add any commentary — only the raw text.")


def test_render_group_renders_only_gutter_pages_as_halves():
    from unittest.mock import patch
    ext = _extractor({1: 0.5})
    with patch.object(ext, "_render_png", return_value=b"P"), \
         patch.object(ext, "_render_spread", return_value=(b"L", b"R")):
        assert ext._render_group(None, 0, 3) == [b"P", (b"L", b"R"), b"P"]
