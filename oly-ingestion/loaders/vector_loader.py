# loaders/vector_loader.py
"""
Loads processed chunks into pgvector with embeddings.

Handles:
- Embedding generation (OpenAI text-embedding-3-small)
- Batch inserts with configurable commit size
- Deduplication: skips chunks whose content hash already exists
- Similarity search with pre-filtering for the downstream agent
"""

import hashlib
import logging
from typing import Any

import psycopg2
from pgvector.psycopg2 import register_vector
from processors.chunker import Chunk

logger = logging.getLogger(__name__)


class VectorLoader:
    # OpenAI allows up to 2048 texts per embedding call.
    # We use a smaller batch to avoid context length issues with long chunks.
    EMBED_BATCH_SIZE = 100

    def __init__(self, settings):
        self.settings = settings
        self.conn = psycopg2.connect(settings.database_url)
        register_vector(self.conn)
        self.batch_size = settings.batch_size

        # OpenAI embedding client
        from openai import OpenAI
        if not settings.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is required for embeddings. "
                "Set it in .env or pass via environment variable."
            )
        self.embed_client = OpenAI(api_key=settings.openai_api_key)
        self.last_skipped_count = 0  # dedup-skip count from the most recent load_chunks call

    @staticmethod
    def _partition_new_chunks(chunks, hashes, existing):
        """Split (chunk, hash) pairs into (new_chunks, skipped_count).

        A chunk is skipped if its content hash is already in the DB (`existing`)
        or has already appeared earlier in THIS batch — the intra-batch check
        prevents two identical chunks from both passing and then colliding on the
        UNIQUE(content_hash) insert (I-M9). Pure/static so it's unit-testable
        without a DB or embedding client.
        """
        new_chunks: list[tuple[Chunk, str]] = []  # (chunk, content_hash)
        seen_in_batch: set[str] = set()
        skipped = 0
        for chunk, content_hash in zip(chunks, hashes, strict=True):
            if content_hash in existing or content_hash in seen_in_batch:
                skipped += 1
            else:
                seen_in_batch.add(content_hash)
                new_chunks.append((chunk, content_hash))
        return new_chunks, skipped

    def load_chunks(
        self,
        chunks: list[Chunk],
        source_id: int,
        run_id: int | None = None,
        structured_loader=None,
    ) -> int:
        """Embed and store chunks in pgvector.

        Uses batch embedding (multiple texts per API call) for efficiency.
        A 300-page book produces ~200-300 chunks, so 2-3 API calls total
        instead of 200-300 individual calls.

        Args:
            chunks: Processed Chunk objects from the SemanticChunker.
            source_id: FK to the sources table.
            run_id: Optional ingestion run ID for chunk logging/rollback support.
            structured_loader: Required if run_id is provided (used to log chunks).

        Returns:
            Number of chunks loaded (excludes duplicates).
        """
        cursor = self.conn.cursor()
        loaded = 0
        skipped = 0
        self.last_skipped_count = 0  # exposed so callers can track dedup stats

        # Step 1: Filter out duplicates before hitting the embedding API.
        # One round-trip for all existing hashes (not one SELECT per chunk), plus
        # a local set so two identical chunks WITHIN this call don't both pass the
        # DB check and then collide on the UNIQUE(content_hash) INSERT after
        # embeddings were already paid for (I-M9).
        hashes = [hashlib.sha256(c.raw_content.encode()).hexdigest() for c in chunks]
        existing: set[str] = set()
        if hashes:
            cursor.execute(
                "SELECT content_hash FROM knowledge_chunks WHERE content_hash = ANY(%s)",
                (hashes,),
            )
            existing = {row[0] for row in cursor.fetchall()}

        new_chunks, skipped = self._partition_new_chunks(chunks, hashes, existing)

        # Set for ALL return paths (was only set on the success path, so a
        # fully-deduped section reported 0 skipped — I-M5).
        self.last_skipped_count = skipped
        if skipped:
            logger.info(f"  Skipped {skipped} duplicate chunks (pre-embedding filter)")

        if not new_chunks:
            logger.info("  No new chunks to embed")
            cursor.close()
            return 0

        # Filter out chunks with empty content — OpenAI rejects empty strings.
        empty_count = sum(1 for chunk, _ in new_chunks if not chunk.content.strip())
        if empty_count:
            logger.warning(f"  Skipped {empty_count} chunk(s) with empty content (would cause API error)")
            new_chunks = [(chunk, h) for chunk, h in new_chunks if chunk.content.strip()]
        if not new_chunks:
            cursor.close()
            return 0

        # Step 2: Batch embed all new chunks.
        # Truncate texts that exceed OpenAI's 8192-token limit (~32000 chars at ~4 chars/token).
        # Oversized chunks lose their tail content but still get embedded — better than dropping them.
        EMBED_CHAR_LIMIT = 30000
        texts = [
            chunk.content[:EMBED_CHAR_LIMIT] if len(chunk.content) > EMBED_CHAR_LIMIT else chunk.content
            for chunk, _ in new_chunks
        ]
        if any(len(chunk.content) > EMBED_CHAR_LIMIT for chunk, _ in new_chunks):
            over = sum(1 for chunk, _ in new_chunks if len(chunk.content) > EMBED_CHAR_LIMIT)
            logger.warning(f"  Truncated {over} oversized chunk(s) to {EMBED_CHAR_LIMIT} chars for embedding")
        all_embeddings = self._embed_batch(texts)

        # Step 3: Insert chunks with their embeddings.
        # Collect log entries and write them after committing so the FK is satisfied.
        pending_log: list[tuple] = []  # (chunk_id, page_number, section_title, classification)

        for (chunk, content_hash), embedding in zip(new_chunks, all_embeddings, strict=True):
            cursor.execute(
                """
                INSERT INTO knowledge_chunks
                    (content, raw_content, content_hash, embedding,
                     source_id, chapter, section,
                     chunk_type, topics, athlete_level_relevance,
                     information_density, contains_specific_numbers)
                VALUES (%s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s)
                RETURNING id
                """,
                (
                    chunk.content,
                    chunk.raw_content,
                    content_hash,
                    embedding,
                    source_id,
                    chunk.metadata.get("chapter", ""),
                    chunk.metadata.get("section_title", ""),
                    chunk.metadata.get("chunk_type", "concept"),
                    chunk.topics or [],
                    chunk.metadata.get("athlete_level_relevance"),
                    chunk.information_density,
                    chunk.contains_specific_numbers,
                ),
            )
            chunk_id = cursor.fetchone()[0]
            loaded += 1

            if run_id is not None and structured_loader is not None:
                pending_log.append((
                    chunk_id,
                    chunk.metadata.get("page_number"),
                    chunk.metadata.get("section_title"),
                    chunk.metadata.get("chunk_type"),
                ))

            if loaded % self.batch_size == 0:
                self.conn.commit()
                logger.info(f"  Committed batch: {loaded} chunks loaded so far")
                # Log committed chunks now that the FK exists
                for entry in pending_log:
                    structured_loader.log_chunk(run_id=run_id, chunk_id=entry[0],
                                                page_number=entry[1], section_title=entry[2],
                                                classification=entry[3])
                pending_log.clear()

        self.conn.commit()
        # Log any remaining chunks from the final (possibly partial) batch
        if pending_log and structured_loader is not None:
            for entry in pending_log:
                structured_loader.log_chunk(run_id=run_id, chunk_id=entry[0],
                                            page_number=entry[1], section_title=entry[2],
                                            classification=entry[3])
        cursor.close()
        self.last_skipped_count = skipped
        logger.info(f"  Loaded {loaded} chunks into knowledge_chunks")
        return loaded

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Batch embed multiple texts in as few API calls as possible.

        OpenAI's embedding API accepts multiple texts per call (up to 2048).
        For 200 chunks, this means 2 API calls instead of 200.
        Retries transient errors (rate limits, timeouts, connection drops, 5xx)
        by exception TYPE — matching on the "rate" substring missed 5xx/timeout
        errors and failed the whole section even though earlier batches were
        already paid for (I-L4).
        """
        import time

        from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

        retryable = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), self.EMBED_BATCH_SIZE):
            batch = texts[i : i + self.EMBED_BATCH_SIZE]
            logger.info(
                f"  Embedding batch {i // self.EMBED_BATCH_SIZE + 1} "
                f"({len(batch)} texts)"
            )

            for attempt in range(3):
                try:
                    response = self.embed_client.embeddings.create(
                        model=self.settings.embedding_model,
                        input=batch,
                    )
                    batch_embeddings = [item.embedding for item in response.data]
                    all_embeddings.extend(batch_embeddings)
                    break
                except retryable as e:
                    if attempt < 2:
                        wait = 2 ** attempt
                        logger.warning(f"  Embedding call failed ({type(e).__name__}), retrying in {wait}s...")
                        time.sleep(wait)
                    else:
                        raise

        return all_embeddings

    def _embed(self, text: str) -> list[float]:
        """Embed a single text. Used for query-time similarity search."""
        response = self.embed_client.embeddings.create(
            model=self.settings.embedding_model,
            input=text,
        )
        return response.data[0].embedding

    def similarity_search(
        self,
        query: str,
        top_k: int = 5,
        chunk_types: list[str] | None = None,
        topics: list[str] | None = None,
        athlete_level: str | None = None,
        min_density: str | None = None,
        require_numbers: bool = False,
        min_similarity: float | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve similar chunks with optional pre-filtering.

        Used downstream by the programming agent. Supports filtered
        similarity search: filter by metadata first, then rank by
        vector similarity within the filtered set.

        min_similarity: if set, drops chunks whose cosine similarity falls
        below the threshold before applying top_k. This prevents the agent
        from receiving low-confidence chunks that waste prompt space.
        """
        query_embedding = self._embed(query)
        cursor = self.conn.cursor()

        where_clauses = []
        params: list[Any] = []

        if chunk_types:
            where_clauses.append("chunk_type::text = ANY(%s)")
            params.append(chunk_types)

        if topics:
            where_clauses.append("topics && %s")  # array overlap operator
            params.append(topics)

        if athlete_level:
            where_clauses.append(
                "(athlete_level_relevance IS NULL "
                "OR athlete_level_relevance IN ('all', %s))"
            )
            params.append(athlete_level)

        if min_density:
            density_order = {"low": 0, "medium": 1, "high": 2}
            min_val = density_order.get(min_density, 0)
            allowed = [k for k, v in density_order.items() if v >= min_val]
            where_clauses.append("information_density = ANY(%s)")
            params.append(allowed)

        if require_numbers:
            where_clauses.append("contains_specific_numbers = TRUE")

        if min_similarity is not None:
            where_clauses.append("1 - (embedding <=> %s::vector) >= %s")
            params.extend([query_embedding, min_similarity])

        where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

        cursor.execute(
            f"""
            SELECT id, content, raw_content, chapter, section,
                   chunk_type, topics, information_density,
                   source_id,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM knowledge_chunks
            WHERE {where_sql}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            [query_embedding, *params, query_embedding, top_k],
        )

        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        cursor.close()
        return results

    def close(self):
        self.conn.close()
