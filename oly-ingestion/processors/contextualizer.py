# processors/contextualizer.py
"""
Contextual retrieval: an LLM-written prefix that situates each chunk in its
document before it is embedded and lexically indexed (RAG-M3).

The chunker's static preamble (`[Source | Author]\\n[Chapter | Section]`) is the
metadata half of "contextual retrieval"; on the live corpus it was just
`[Source | Author]` for 97% of chunks. The other half — one or two sentences
saying what this passage is about *within its document* ("From the chapter on
the preparatory period; gives weekly lift counts for Class I lifters …") — is
what moved the published retrieval-failure numbers (−35% alone, −49% with a
lexical leg, −67% with reranking).

Cost: one short call per chunk. The enclosing section is sent as a cached
system block, so a 40-chunk chapter pays for its text once. A Haiku-class model
is plenty for this task.

Storage: the prefix is written to `knowledge_chunks.context_prefix` (migration
0009) and inserted between the preamble and the body of `content`, which is what
gets embedded; `raw_content` (and therefore the dedup hash and what the prompt
shows) is untouched. The generated `tsv` column already includes it.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # repo root for shared.*
from processors.chunker import Chunk
from shared.llm import create_message_with_retries, message_text

logger = logging.getLogger(__name__)

MAX_DOCUMENT_CHARS = 60_000   # cached system block; a chapter comfortably fits
MAX_CHUNK_CHARS = 6_000       # the chunk itself is always sent whole in practice
MAX_CONTEXT_TOKENS = 160      # 1–2 sentences

DOCUMENT_PROMPT = """\
You write short retrieval context for passages from Olympic weightlifting coaching literature.

The document below is one section of "{source_title}". You will be shown one passage from it at a time.

<document>
{document}
</document>"""

CHUNK_PROMPT = """\
Here is the passage:

<passage>
{chunk}
</passage>

In one or two sentences (at most 60 words), state what this passage is about within the document: the topic or heading it belongs to, the population or training phase it concerns, and any concrete prescription it makes. Write it so that a search for the passage's subject would match. Do not quote the passage, do not start with "This passage", and output only the sentences."""


def build_document_block(document: str, source_title: str) -> str:
    return DOCUMENT_PROMPT.format(source_title=source_title or "Untitled", document=document[:MAX_DOCUMENT_CHARS])


def build_chunk_message(chunk_text: str) -> str:
    return CHUNK_PROMPT.format(chunk=chunk_text[:MAX_CHUNK_CHARS])


def apply_context(chunk: Chunk, context: str) -> Chunk:
    """Insert the context between the preamble and the body of `content`."""
    context = " ".join(context.split()).strip()
    if not context:
        return chunk
    body = chunk.raw_content
    if chunk.content.endswith(body):
        preamble = chunk.content[: len(chunk.content) - len(body)]
    else:  # defensive: unknown layout — prepend rather than lose the context
        preamble, body = "", chunk.content
    chunk.content = f"{preamble}{context}\n\n{body}"
    chunk.metadata["context_prefix"] = context
    chunk.token_count = SemanticChunkerTokens.count(chunk.content)
    return chunk


class SemanticChunkerTokens:
    """Indirection so tests can run without tiktoken; mirrors SemanticChunker._estimate_tokens."""

    @staticmethod
    def count(text: str) -> int:
        from processors.tokens import count_tokens
        return count_tokens(text)


def contextualize(
    chunks: list[Chunk],
    document: str,
    source_title: str,
    client,
    model: str,
) -> list[Chunk]:
    """Add an LLM-written context prefix to every chunk of one document section.

    Failures are per chunk: a chunk whose call fails keeps its original content
    (and gets no context_prefix) so ingestion never stops for this step.
    """
    if not chunks:
        return chunks
    system_block = [{
        "type": "text",
        "text": build_document_block(document, source_title),
        "cache_control": {"type": "ephemeral"},   # the section is reused across its chunks
    }]
    done = 0
    for chunk in chunks:
        try:
            message = create_message_with_retries(
                client,
                model=model,
                max_tokens=MAX_CONTEXT_TOKENS,
                system=system_block,
                messages=[{"role": "user", "content": build_chunk_message(chunk.raw_content)}],
            )
            text = message_text(message).strip()
            if text:
                apply_context(chunk, text)
                done += 1
        except Exception as e:
            logger.warning(f"Contextualizer skipped a chunk ({type(e).__name__}: {e})")
    logger.info(f"  Contextualized {done}/{len(chunks)} chunk(s) with {model}")
    return chunks
