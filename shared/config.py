# shared/config.py
"""
Unified settings for both the ingestion pipeline and the programming agent.
Loads from environment variables / .env file.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Search for .env upward from the repo root, then fall back to oly-ingestion/
_here = Path(__file__).parent
for _candidate in [
    _here.parent / ".env",              # repo root
    _here.parent / "oly-ingestion" / ".env",  # ingestion dir (where keys live)
    Path(".env"),                        # cwd fallback
]:
    if _candidate.exists():
        load_dotenv(_candidate)
        break


@dataclass
class Settings:
    # ── Database ──────────────────────────────────────────────
    database_url: str = ""
    db_pool_min: int = 1
    db_pool_max: int = 10

    # ── Embedding model ───────────────────────────────────────
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # ── LLM (ingestion: principle extraction, classification) ──
    llm_model: str = "claude-sonnet-4-6"
    llm_max_tokens: int = 4096

    # ── Agent LLM settings ────────────────────────────────────
    generation_model: str = "claude-sonnet-4-6"
    generation_max_tokens: int = 4096
    generation_temperature: float = 0.3
    explanation_model: str = "claude-sonnet-4-6"
    explanation_temperature: float = 0.7

    # ── Retry / error handling ───────────────────────────────
    max_generation_retries: int = 2
    max_parse_retries: int = 2
    retry_delay_seconds: float = 1.0

    # ── Retrieval ────────────────────────────────────────────
    vector_search_top_k: int = 5
    max_principles_per_session: int = 10
    max_template_references: int = 3

    # ── Generation constraints ───────────────────────────────
    max_exercises_per_session: int = 6
    min_exercises_per_session: int = 3
    max_sessions_per_week: int = 6
    max_program_weeks: int = 12

    # ── Cost tracking ────────────────────────────────────────
    track_token_usage: bool = True
    cost_limit_per_program: float = 1.00

    # ── API keys ──────────────────────────────────────────────
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # ── Web / deployment ──────────────────────────────────────
    secret_key: str = ""   # session signing key; MUST be set in production via SECRET_KEY env var
    redis_url: str = ""    # optional Redis URL for rate limiter (e.g. redis://localhost:6379)
    https_only: bool = False  # set HTTPS_ONLY=true in production to enable Secure cookie flag

    # ── Paths (ingestion pipeline) ────────────────────────────
    sources_dir: Path = Path("./sources")
    logs_dir: Path = Path("./logs")

    # ── Ingestion behavior ────────────────────────────────────
    batch_size: int = 50
    skip_existing_sources: bool = True
    validate_chunks: bool = True
    quarantine_invalid_chunks: bool = False

    def __post_init__(self):
        import logging
        _log = logging.getLogger(__name__)

        env_db = os.getenv("DATABASE_URL", "")
        self.database_url = self.database_url or env_db or "postgresql://oly:oly@localhost:5432/oly_programming"
        if not env_db and "localhost" in self.database_url:
            _log.warning(
                "DATABASE_URL is not set — using localhost fallback. "
                "Set DATABASE_URL in environment for production deployments."
            )

        self.openai_api_key = self.openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.anthropic_api_key = self.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")

        if not self.openai_api_key:
            _log.warning("OPENAI_API_KEY is not set — embeddings and vector search will fail")
        if not self.anthropic_api_key:
            _log.warning("ANTHROPIC_API_KEY is not set — LLM calls will fail")

        self.redis_url = self.redis_url or os.getenv("REDIS_URL", "")

        self.https_only = self.https_only or os.getenv("HTTPS_ONLY", "").lower() in ("1", "true", "yes")

        self.secret_key = self.secret_key or os.getenv("SECRET_KEY", "")
        if not self.secret_key:
            import secrets
            self.secret_key = secrets.token_hex(32)
            _log.warning(
                "SECRET_KEY is not set — sessions will be invalidated on every restart. "
                "Set SECRET_KEY in environment for production deployments."
            )

        self.sources_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
