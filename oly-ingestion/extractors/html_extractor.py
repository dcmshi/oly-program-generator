# extractors/html_extractor.py
"""
Extracts clean text from HTML files (web articles, blog posts).
Strips navigation, headers, footers, and other boilerplate.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


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

    text = main.get_text(separator="\n", strip=True)

    # Collapse excessive blank lines
    import re
    text = re.sub(r"\n{3,}", "\n\n", text)

    logger.info(f"Extracted {len(text):,} characters from {path.name}")
    return text
