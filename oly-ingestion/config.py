# config.py
"""
Pipeline configuration. Load from environment variables or .env file.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    # ── Database ──────────────────────────────────────────────
    database_url: str = ""

    # ── Embedding model ───────────────────────────────────────
    # Using OpenAI text-embedding-3-small (1536 dims).
    # At ~$0.02/1M tokens, the total cost for all source books is under $1
    # even with multiple re-embedding passes during iteration.
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # ── LLM (for principle extraction, content classification, topic tagging) ──
    llm_model: str = "claude-sonnet-4-20250514"
    llm_max_tokens: int = 4096

    # ── API keys (loaded from env vars) ───────────────────────
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # ── Paths ─────────────────────────────────────────────────
    sources_dir: Path = Path("./sources")
    logs_dir: Path = Path("./logs")

    # ── Ingestion behavior ────────────────────────────────────
    batch_size: int = 50               # chunks per DB commit
    skip_existing_sources: bool = True  # skip if source title+author already in DB
    validate_chunks: bool = True        # run chunk validation pass before loading
    quarantine_invalid_chunks: bool = False  # if True, skip invalid chunks; if False, load with warnings

    def __post_init__(self):
        """Load sensitive values from environment variables."""
        self.database_url = self.database_url or os.getenv(
            "DATABASE_URL", "postgresql://oly:oly@localhost:5432/oly_programming"
        )
        self.openai_api_key = self.openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.anthropic_api_key = self.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
