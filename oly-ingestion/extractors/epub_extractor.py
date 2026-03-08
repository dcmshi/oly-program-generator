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

            text = soup.get_text(separator="\n", strip=True)

            import re
            text = re.sub(r"\n{3,}", "\n\n", text)

            if text.strip():
                chapters.append(text)

    logger.info(f"Extracted {len(chapters)} chapters from {path.name}")
    return chapters
