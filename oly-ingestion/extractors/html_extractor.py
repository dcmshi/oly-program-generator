# extractors/html_extractor.py
"""
Extracts clean text from HTML files (web articles, blog posts).
Strips navigation, headers, footers, and other boilerplate.
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def block_text(element) -> str:
    """Extract text with real paragraph breaks from a BeautifulSoup element.

    ``get_text(separator="\\n")`` yields only single newlines between text
    nodes, but the chunker splits paragraphs on ``\\n\\n`` — without this fix
    an entire article collapses into one oversized chunk (same bug fixed in
    epub_extractor). Inserts ``\\n\\n`` markers after block-level tags before
    extracting. Mutates the element (call after any other get_text() reads).
    """
    from bs4 import NavigableString

    # Paragraph-level breaks (\n\n) after block containers so the chunker can
    # split within long articles.
    for tag in element.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li",
                                 "div", "blockquote", "table", "tr"]):
        tag.insert_after(NavigableString("\n\n"))
    # Line breaks (\n) after inline row/cell/break boundaries — without a
    # separator, get_text("") mashed adjacent tokens together
    # ("...80%Clean Pull...", "Snatch80%3x2..."), corrupting embeddings and
    # keep-together heuristics for program listings and tables (audit5-M4)
    for tag in element.find_all(["br", "td", "th"]):
        tag.insert_after(NavigableString("\n"))

    text = element.get_text(separator="")
    text = re.sub(r"[ \t]+", " ", text)         # collapse horizontal whitespace
    text = re.sub(r" ?\n ?", "\n", text)        # trim spaces around newlines
    text = re.sub(r"\n{3,}", "\n\n", text)      # normalize 3+ newlines → \n\n
    return text.strip()


def extract_text_from_html(path: Path) -> str:
    """Extract body text from an HTML file.

    Args:
        path: Path to the .html or .htm file.

    Returns:
        Clean text string with boilerplate removed.
    """
    from bs4 import BeautifulSoup

    html = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")

    # Remove navigation, headers, footers, scripts, styles
    for tag in soup(["nav", "header", "footer", "script", "style", "aside"]):
        tag.decompose()

    # Try to find the main content element
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(id="content")
        or soup.find(class_="content")
        or soup.find(class_="post-content")
        or soup.find(class_="entry-content")
        or soup.body
    )

    if main is None:
        logger.warning(f"Could not find main content element in {path.name}, using full body")
        main = soup

    text = block_text(main)

    logger.info(f"Extracted {len(text):,} characters from {path.name}")
    return text
