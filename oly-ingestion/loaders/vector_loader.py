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
import re
import sys
from pathlib import Path
from typing import Any

import psycopg2
from pgvector.psycopg2 import register_vector
from processors.chunker import Chunk
from processors.tokens import truncate_to_tokens

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # repo root for shared.*
from shared.constants import (
    CHUNK_TYPE_PREFERENCE_BOOST,
    CHUNK_TYPE_PREFERENCE_BOOST_RRF,
    EMBED_MAX_TOKENS,
    HNSW_EF_SEARCH,
    HNSW_ITERATIVE_SCAN,
    HYBRID_CANDIDATES_PER_LEG,
    RRF_K,
    VECTOR_SEARCH_CANDIDATE_MULTIPLIER,
    VECTOR_SEARCH_MIN_CANDIDATES,
)

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
        # None = not yet probed; False = this pgvector has no hnsw.iterative_scan
        # (pre-0.8), so skip the SET on later calls instead of erroring each time.
        self._hnsw_settings_supported: bool | None = None

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
        # Cap each text at the model's input limit by TOKENS (RAG-M2) — the old
        # 30k-char cap assumed ~4 chars/token, which numeric notation breaks.
        # Oversized chunks lose their tail but still get embedded — better than dropping them.
        texts = [truncate_to_tokens(chunk.content, EMBED_MAX_TOKENS) for chunk, _ in new_chunks]
        over = sum(1 for (chunk, _), t in zip(new_chunks, texts, strict=True) if t != chunk.content)
        if over:
            logger.warning(f"  Truncated {over} oversized chunk(s) to {EMBED_MAX_TOKENS} tokens for embedding")
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
                     chunk_type, topics,
                     information_density, contains_specific_numbers,
                     embedding_model, context_prefix)
                VALUES (%s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s,
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
                    chunk.information_density,
                    chunk.contains_specific_numbers,
                    self.settings.embedding_model,  # which vector space this row lives in (RAG-M8)
                    chunk.metadata.get("context_prefix"),  # LLM-written retrieval context (RAG-M3)
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
                        **self._embed_kwargs(),
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

    def _embed_kwargs(self) -> dict:
        """Extra embedding-API arguments derived from settings.

        `text-embedding-3-*` models accept `dimensions` (Matryoshka truncation),
        which is how `text-embedding-3-large` fits the existing vector(1536)
        column without a schema change (roadmap #22). Older models reject the
        argument, so it is only sent for the models that support it.
        """
        model = self.settings.embedding_model or ""
        dim = getattr(self.settings, "embedding_dim", None)
        if model.startswith("text-embedding-3") and dim:
            return {"dimensions": int(dim)}
        return {}

    def _embed(self, text: str) -> list[float]:
        """Embed a single text. Used for query-time similarity search."""
        response = self.embed_client.embeddings.create(
            model=self.settings.embedding_model,
            input=text,
            **self._embed_kwargs(),
        )
        return response.data[0].embedding

    def _apply_hnsw_query_settings(self, cursor) -> None:
        """Set the HNSW scan knobs for the current transaction (RAG-H5).

        A filtered HNSW scan collects ``hnsw.ef_search`` candidates and only then
        applies the WHERE clause, so a selective ``chunk_type`` + ``min_similarity``
        filter can return fewer than ``top_k`` rows — or none — while good matches
        exist just outside the candidate set. Measured with the index forced on the
        local corpus: 46/60 fault queries returned 0 rows; with
        ``iterative_scan = relaxed_order`` 59/60 returned the full top_k.

        ``set_config(..., is_local=true)`` is transaction-scoped, so this runs on
        every search (the loader's connection commits between ingestion writes).
        Wrapped in a SAVEPOINT: on pgvector < 0.8 the GUC doesn't exist and the
        error would otherwise abort the transaction the SELECT runs in.
        """
        if self._hnsw_settings_supported is False:
            return
        try:
            cursor.execute("SAVEPOINT hnsw_cfg")
            cursor.execute("SELECT set_config('hnsw.iterative_scan', %s, true)", (HNSW_ITERATIVE_SCAN,))
            cursor.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(HNSW_EF_SEARCH),))
            cursor.execute("RELEASE SAVEPOINT hnsw_cfg")
            self._hnsw_settings_supported = True
        except psycopg2.Error as e:
            self._hnsw_settings_supported = False
            logger.warning(
                f"hnsw.iterative_scan/ef_search unavailable ({type(e).__name__}: {e}) — "
                "filtered ANN queries may return fewer than top_k rows; upgrade pgvector to >= 0.8"
            )
            try:
                cursor.execute("ROLLBACK TO SAVEPOINT hnsw_cfg")
            except psycopg2.Error as rb_err:
                logger.debug(f"ROLLBACK TO SAVEPOINT hnsw_cfg failed (non-fatal): {rb_err}")

    def similarity_search(
        self,
        query: str,
        top_k: int = 5,
        chunk_types: list[str] | None = None,
        topics: list[str] | None = None,
        min_density: str | None = None,
        require_numbers: bool = False,
        min_similarity: float | None = None,
        preferred_chunk_types: list[str] | None = None,
        hybrid: bool = False,
    ) -> list[dict[str, Any]]:
        """Retrieve similar chunks with optional pre-filtering.

        hybrid: fuse the vector leg with a lexical leg over the `tsv` column
        (migration 0009) by reciprocal rank — score = Σ 1/(RRF_K + rank) over
        the top HYBRID_CANDIDATES_PER_LEG of each leg, plus
        CHUNK_TYPE_PREFERENCE_BOOST_RRF for preferred types; `min_similarity`
        applies to the vector leg only (RAG-M1). Rows carry `similarity`,
        `lex_score`, `rrf` and `score`. Production (`retrieve.py`) passes
        HYBRID_SEARCH_ENABLED; the eval and tests default to dense-only.

        Used downstream by the programming agent. Supports filtered
        similarity search: filter by metadata first, then rank by
        vector similarity within the filtered set.

        min_similarity: if set, drops chunks whose cosine similarity falls
        below the threshold before applying top_k. This prevents the agent
        from receiving low-confidence chunks that waste prompt space.

        preferred_chunk_types: a SOFT preference (RAG-H2). `chunk_type` is a
        first-match keyword label and `concept` is ~61% of the corpus, so the
        hard `chunk_types` filter left session generation 15% of the chunks.
        With a preference, a candidate pool of
        max(top_k * VECTOR_SEARCH_CANDIDATE_MULTIPLIER, VECTOR_SEARCH_MIN_CANDIDATES)
        is taken by pure similarity (index-friendly), then
        CHUNK_TYPE_PREFERENCE_BOOST is added to preferred types and the pool is
        re-ranked; each row carries `score` (= similarity + boost) alongside the
        raw `similarity`. `chunk_types` remains available as a hard filter.
        """
        query_embedding = self._embed(query)
        cursor = self.conn.cursor()
        self._apply_hnsw_query_settings(cursor)

        # Only rows in the query's embedding space: a cosine distance between
        # vectors from two models is meaningless, so a half-finished re-embed
        # (reembed.py) must never mix into one ranking (RAG-M8).
        where_clauses = ["embedding_model = %s"]
        params: list[Any] = [self.settings.embedding_model]

        if chunk_types:
            where_clauses.append("chunk_type::text = ANY(%s)")
            params.append(chunk_types)

        if topics:
            where_clauses.append("topics && %s")  # array overlap operator
            params.append(topics)

        # (the `athlete_level` filter was removed in RAG-M7: athlete_level_relevance
        # was NULL on every row, so the predicate was always true)

        if min_density:
            density_order = {"low": 0, "medium": 1, "high": 2}
            min_val = density_order.get(min_density, 0)
            allowed = [k for k, v in density_order.items() if v >= min_val]
            where_clauses.append("information_density = ANY(%s)")
            params.append(allowed)

        if require_numbers:
            where_clauses.append("contains_specific_numbers = TRUE")

        # Metadata filters apply to both legs; the similarity floor only to the
        # vector leg (a lexical-only hit is allowed to sit below it — RAG-M1).
        filter_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"
        vec_where, vec_params = filter_sql, list(params)
        if min_similarity is not None:
            vec_where += " AND 1 - (embedding <=> %s::vector) >= %s"
            vec_params += [query_embedding, min_similarity]

        lex_query = self._lexical_tsquery(query) if hybrid else None
        if lex_query:
            cursor.execute(
                f"""
                WITH vec AS (
                    SELECT id, 1 - (embedding <=> %s::vector) AS similarity,
                           row_number() OVER (ORDER BY embedding <=> %s::vector) AS rnk
                    FROM knowledge_chunks
                    WHERE {vec_where}
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                ), lex AS (
                    SELECT id, ts_rank_cd(tsv, q) AS lex_score,
                           row_number() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS rnk
                    FROM knowledge_chunks, to_tsquery('english', %s) q
                    WHERE tsv @@ q AND {filter_sql}
                    ORDER BY lex_score DESC
                    LIMIT %s
                ), fused AS (
                    SELECT coalesce(v.id, l.id) AS id,
                           (coalesce(1.0 / (%s + v.rnk), 0) + coalesce(1.0 / (%s + l.rnk), 0))::float8 AS rrf,
                           v.similarity, l.lex_score::float8 AS lex_score
                    FROM vec v FULL OUTER JOIN lex l ON v.id = l.id
                )
                SELECT k.id, k.content, k.raw_content, k.chapter, k.section,
                       k.chunk_type, k.topics, k.information_density, k.source_id,
                       coalesce(f.similarity, 1 - (k.embedding <=> %s::vector)) AS similarity,
                       f.lex_score, f.rrf,
                       (f.rrf + CASE WHEN k.chunk_type::text = ANY(%s) THEN %s ELSE 0 END)::float8 AS score
                FROM fused f JOIN knowledge_chunks k ON k.id = f.id
                ORDER BY score DESC, similarity DESC
                LIMIT %s
                """,
                [
                    query_embedding, query_embedding, *vec_params, query_embedding, HYBRID_CANDIDATES_PER_LEG,
                    lex_query, *params, HYBRID_CANDIDATES_PER_LEG,
                    RRF_K, RRF_K,
                    query_embedding, list(preferred_chunk_types or []), CHUNK_TYPE_PREFERENCE_BOOST_RRF, top_k,
                ],
            )
            columns = [desc[0] for desc in cursor.description]
            results = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
            cursor.close()
            return results

        base_select = f"""
            SELECT id, content, raw_content, chapter, section,
                   chunk_type, topics, information_density,
                   source_id,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM knowledge_chunks
            WHERE {vec_where}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """

        if preferred_chunk_types:
            pool = max(top_k * VECTOR_SEARCH_CANDIDATE_MULTIPLIER, VECTOR_SEARCH_MIN_CANDIDATES)
            cursor.execute(
                f"""
                WITH candidates AS ({base_select})
                SELECT *,
                       similarity + CASE WHEN chunk_type::text = ANY(%s) THEN %s ELSE 0 END AS score
                FROM candidates
                ORDER BY score DESC, similarity DESC
                LIMIT %s
                """,
                [query_embedding, *vec_params, query_embedding, pool,
                 list(preferred_chunk_types), CHUNK_TYPE_PREFERENCE_BOOST, top_k],
            )
        else:
            cursor.execute(base_select, [query_embedding, *vec_params, query_embedding, top_k])

        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        cursor.close()
        return results

    _LEX_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")
    # Domain boilerplate that appears in most chunks AND in every production
    # query template. ts_rank_cd has no IDF, so with an OR query these terms
    # would let "reps"/"sets"/"training" chunks crowd out the one that mentions
    # "Prilepin" (measured: 1/5 vs 2/5 dense-only before the stoplist).
    _LEXICAL_STOPLIST = frozenset({
        "exercise", "exercises", "selection", "session", "sessions", "support", "during",
        "phase", "intensity", "athlete", "athletes", "lifter", "lifters", "weightlifter",
        "weightlifting", "training", "program", "programming", "workout", "week", "weeks",
        "reps", "rep", "sets", "set", "per", "with", "for", "and", "the", "optimal",
        "development", "strength", "addressing", "focus", "level", "beginner",
        "intermediate", "advanced", "elite", "correcting", "work",
    })

    @classmethod
    def _lexical_tsquery(cls, query: str) -> str | None:
        """OR-of-terms tsquery text for a natural-language query.

        Production queries are sentences ("exercise selection for a snatch
        session … at 70-80% intensity, intermediate athlete"); `plainto_` /
        `websearch_to_tsquery` AND every term and would match nothing. Terms are
        alphanumeric tokens of 3+ chars minus the domain stoplist, deduped, joined
        with `|`; the 'english' config drops its own stopwords and stems at query
        time. Returns None when nothing distinctive is left (the caller then runs
        the vector-only path).
        """
        seen: list[str] = []
        for tok in cls._LEX_TOKEN_RE.findall(query.lower()):
            if tok not in seen and tok not in cls._LEXICAL_STOPLIST:
                seen.append(tok)
        return " | ".join(seen) if seen else None

    def close(self):
        self.conn.close()
