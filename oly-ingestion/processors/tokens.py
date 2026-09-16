# processors/tokens.py
"""
Token counting for chunk sizing and the embedding input cap (RAG-M2).

`SemanticChunker` sized chunks with `len(text.split()) * 1.3`. For prose that is
close; for the notation this corpus is full of — `(85%/4)4 20:108:280`,
`70%/3x3 75%/3x2`, `Sn. Pu.` — cl100k_base produces 4–5× more tokens than words
(measured: 24 vs 5), so a "900-token" Soviet chunk could be 1,500+ real tokens
while a prose chunk under-filled. The embedding call was capped by characters
for the same reason.

`count_tokens` uses tiktoken's cl100k_base (the tokenizer for
text-embedding-3-*) when it can be loaded — the BPE table is fetched once and
cached by tiktoken — and falls back to the word estimate otherwise, so the
pipeline and tests still run offline.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # repo root for shared.*
from shared.constants import TOKENS_PER_WORD_FALLBACK

logger = logging.getLogger(__name__)

ENCODING_NAME = "cl100k_base"

_encoder = None
_encoder_tried = False


def _get_encoder():
    """Lazily load the tiktoken encoder once; None when unavailable (no tiktoken,
    or the BPE table could not be fetched)."""
    global _encoder, _encoder_tried
    if _encoder_tried:
        return _encoder
    _encoder_tried = True
    try:
        import tiktoken
        _encoder = tiktoken.get_encoding(ENCODING_NAME)
    except Exception as e:  # ImportError, network failure fetching the BPE table, …
        logger.warning(
            f"tiktoken {ENCODING_NAME} unavailable ({type(e).__name__}: {e}) — "
            f"falling back to words × {TOKENS_PER_WORD_FALLBACK} for token counts"
        )
        _encoder = None
    return _encoder


def count_tokens(text: str) -> int:
    """Token count under the embedding model's tokenizer (word estimate offline)."""
    if not text:
        return 0
    enc = _get_encoder()
    if enc is None:
        return int(len(text.split()) * TOKENS_PER_WORD_FALLBACK)
    return len(enc.encode(text, disallowed_special=()))


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Return the longest prefix of ``text`` within ``max_tokens`` tokens."""
    if not text:
        return text
    enc = _get_encoder()
    if enc is None:
        words = text.split()
        keep = int(max_tokens / TOKENS_PER_WORD_FALLBACK)
        return text if len(words) <= keep else " ".join(words[:keep])
    ids = enc.encode(text, disallowed_special=())
    if len(ids) <= max_tokens:
        return text
    return enc.decode(ids[:max_tokens])
