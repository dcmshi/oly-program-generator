# extractors/epub_extractor.py
"""
Extracts chapter texts from EPUB files.
Requires: ebooklib (uncomment in requirements.txt)
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_text_from_epub(path: Path) -> list[str]:
    """Extract chapter texts from an EPUB file.

    Returns a list of strings, one per chapter/document.
    Requires ebooklib: pip install ebooklib

    Args:
        path: Path to the .epub file.

    Returns:
        List of chapter text strings.
    """
    try:
        import ebooklib
        from ebooklib import epub
        from bs4 import BeautifulSoup
    except ImportError:
        raise ImportError(
            "ebooklib is required for EPUB extraction. "
            "Install it: pip install ebooklib"
        )

    book = epub.read_epub(str(path))
    chapters = []

    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            soup = BeautifulSoup(item.get_content(), "lxml")

            # Remove script/style tags
            for tag in soup(["script", "style"]):
                tag.decompose()

            # Insert double-newline markers after block-level elements so that
            # _chunk_section (which splits on \n\n) can split within long chapters.
            # insert_after adds a sibling NavigableString visible to get_text().
            from bs4 import NavigableString
            for tag in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li"]):
                tag.insert_after(NavigableString("\n\n"))

            import re
            text = soup.get_text(separator="")
            text = re.sub(r"[ \t]+", " ", text)        # collapse horizontal whitespace
            text = re.sub(r"\n{3,}", "\n\n", text)     # normalize 3+ newlines → \n\n

            if text.strip():
                chapters.append(text)

    logger.info(f"Extracted {len(chapters)} chapters from {path.name}")
    return chapters
