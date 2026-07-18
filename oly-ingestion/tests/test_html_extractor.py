# tests/test_html_extractor.py
"""
Tests for extractors/html_extractor.py.

No API keys or DB needed — uses temporary HTML files written to disk.

Run: python tests/test_html_extractor.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from extractors.html_extractor import block_text, extract_text_from_html

RESULTS = []

def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, str(e)))


def _write_html(content: str) -> Path:
    """Write HTML to a temp file and return its Path."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", encoding="utf-8", delete=False
    )
    f.write(content)
    f.close()
    return Path(f.name)


# ── Basic extraction ─────────────────────────────────────────────────────────

def test_extracts_body_text():
    p = _write_html("<html><body><p>Hello world</p></body></html>")
    text = extract_text_from_html(p)
    assert "Hello world" in text

def test_returns_string():
    p = _write_html("<html><body><p>Test</p></body></html>")
    result = extract_text_from_html(p)
    assert isinstance(result, str)

def test_empty_body_returns_empty_string():
    p = _write_html("<html><body></body></html>")
    result = extract_text_from_html(p)
    assert result.strip() == ""


# ── Boilerplate removal ──────────────────────────────────────────────────────

def test_strips_nav():
    p = _write_html("""
    <html><body>
      <nav>Site Navigation</nav>
      <p>Main content here</p>
    </body></html>""")
    text = extract_text_from_html(p)
    assert "Site Navigation" not in text
    assert "Main content here" in text

def test_strips_header_and_footer():
    p = _write_html("""
    <html><body>
      <header>Page Header</header>
      <p>Article body</p>
      <footer>Page Footer</footer>
    </body></html>""")
    text = extract_text_from_html(p)
    assert "Page Header" not in text
    assert "Page Footer" not in text
    assert "Article body" in text

def test_strips_script_tags():
    p = _write_html("""
    <html><body>
      <script>var x = 1;</script>
      <p>Readable content</p>
    </body></html>""")
    text = extract_text_from_html(p)
    assert "var x" not in text
    assert "Readable content" in text

def test_strips_style_tags():
    p = _write_html("""
    <html><body>
      <style>.foo { color: red; }</style>
      <p>Article text</p>
    </body></html>""")
    text = extract_text_from_html(p)
    assert ".foo" not in text
    assert "Article text" in text


# ── Content element priority ─────────────────────────────────────────────────

def test_prefers_main_element():
    p = _write_html("""
    <html><body>
      <p>Sidebar noise</p>
      <main><p>Main article content</p></main>
    </body></html>""")
    text = extract_text_from_html(p)
    assert "Main article content" in text

def test_falls_back_to_article_element():
    p = _write_html("""
    <html><body>
      <p>Sidebar noise</p>
      <article><p>Article content</p></article>
    </body></html>""")
    text = extract_text_from_html(p)
    assert "Article content" in text

def test_falls_back_to_body_when_no_main_or_article():
    p = _write_html("""
    <html><body>
      <div><p>All we have is body</p></div>
    </body></html>""")
    text = extract_text_from_html(p)
    assert "All we have is body" in text


# ── Whitespace handling ──────────────────────────────────────────────────────

def test_collapses_excessive_blank_lines():
    p = _write_html("""
    <html><body><p>Para one</p>



    <p>Para two</p></body></html>""")
    text = extract_text_from_html(p)
    # Should not have more than 2 consecutive newlines
    assert "\n\n\n" not in text

def test_unicode_content_preserved():
    p = _write_html("<html><body><p>Snatch: 100 kg — техника</p></body></html>")
    text = extract_text_from_html(p)
    assert "100 kg" in text
    assert "техника" in text


# ── Paragraph separation (chunking regression) ───────────────────────────────
# Before the fix, get_text(separator="\n") produced only single newlines, so
# _chunk_section (which splits on \n\n) treated an entire article as one
# paragraph → one oversized chunk per article regardless of length.

def test_paragraphs_separated_by_blank_lines():
    p = _write_html(
        "<html><body><article>"
        + "".join(f"<p>Paragraph {i} with some text.</p>" for i in range(10))
        + "</article></body></html>"
    )
    text = extract_text_from_html(p)
    paragraphs = [b for b in text.split("\n\n") if b.strip()]
    assert len(paragraphs) >= 10, f"Expected >=10 paragraphs, got {len(paragraphs)}"


def test_inline_tags_do_not_break_sentences():
    p = _write_html(
        "<html><body><p>See <em>this</em> point and <a href='#'>that</a> link.</p></body></html>"
    )
    text = extract_text_from_html(p)
    assert "See this point and that link." in text


def test_block_text_on_soup_element():
    # Direct test of the helper ingest_web.fetch_article uses.
    from bs4 import BeautifulSoup

    html = "<div>" + "".join(f"<p>Para {i}.</p>" for i in range(5)) + "</div>"
    soup = BeautifulSoup(html, "lxml")
    text = block_text(soup.find("div"))
    paragraphs = [b for b in text.split("\n\n") if b.strip()]
    assert len(paragraphs) == 5, f"Expected 5 paragraphs, got {len(paragraphs)}"


# ── audit5-M4: block-boundary separators ─────────────────────────────────────

def test_br_separated_lines_not_mashed():
    from bs4 import BeautifulSoup
    html = "<div>Snatch 5x2 @ 80%<br>Clean Pull 4x3 @ 90%<br>Back Squat 5x5</div>"
    text = block_text(BeautifulSoup(html, "lxml").find("div"))
    assert "80%Clean" not in text, f"<br> boundary mashed: {text!r}"
    assert "Clean Pull" in text and "Back Squat" in text


def test_table_cells_not_mashed():
    from bs4 import BeautifulSoup
    html = ("<div><table><tr><td>Snatch</td><td>80%</td><td>3x2</td></tr>"
            "<tr><td>Clean</td><td>85%</td><td>2x2</td></tr></table></div>")
    text = block_text(BeautifulSoup(html, "lxml").find("div"))
    assert "Snatch80%" not in text and "3x2Clean" not in text, f"table cells mashed: {text!r}"


def test_div_paragraphs_not_mashed():
    from bs4 import BeautifulSoup
    html = "<section><div>First paragraph text.</div><div>Second paragraph text.</div></section>"
    text = block_text(BeautifulSoup(html, "lxml").find("section"))
    assert "text.Second" not in text, f"div boundary mashed: {text!r}"
    blocks = [b for b in text.split("\n\n") if b.strip()]
    assert len(blocks) == 2, f"div paragraphs should split into 2 blocks, got {len(blocks)}"


def test_inline_tags_still_not_broken():
    # the M4 separators must NOT split inline emphasis/links inside a paragraph
    from bs4 import BeautifulSoup
    html = "<p>See <em>this</em> point and <a href='#'>that</a> link.</p>"
    text = block_text(BeautifulSoup(html, "lxml").find("p"))
    assert "See this point and that link." in text, text


# ── Runner ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        _test(name, fn)

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    failed = sum(1 for r in RESULTS if r[0] in ("FAIL", "ERROR"))
    for r in RESULTS:
        detail = f"  → {r[2]}" if len(r) > 2 else ""
        print(f"  {r[0]}  {r[1]}{detail}")
    print(f"\n{passed} passed, {failed} failed")
