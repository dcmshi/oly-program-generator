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


# Known placeholder values shipped in .env.example — never valid signing keys
_PLACEHOLDER_SECRET_KEYS = frozenset({"change_me_to_a_random_64_char_hex_string"})

# Default model per role. Sonnet-class for anything that reasons over long text;
# Haiku-class for label/summary tasks (classifier fallback, chunk context,
# relabelling, eval grading) where the extra capability buys nothing.
#
# The agent's generation / explanation roles run on Sonnet 5 with thinking
# disabled: the 2026-09-16 baseline (eval/model_baseline, athlete 1, 8 sessions)
# had it 8/8 first-try like Sonnet 4.6 at 16% lower cost and 27% lower latency,
# while adaptive thinking at a 4,096 output budget produced no text at all
# (TODO MODEL-1). Ingestion followed on 2026-09-20 (MODEL-1b): every ingestion
# call site passes thinking_kwargs(..., "disabled"), and on one Catalyst article
# + one Everett window Sonnet 5 extracted principles with no parse errors at
# −45% cost and −60% latency, folding 4.6's near-duplicate rules into fewer,
# broader ones (13+12 → 7+6). Sonnet 5 is also $2/$10 vs 4.6's $3/$15.
DEFAULT_LLM_MODEL = "claude-sonnet-5"
DEFAULT_LIGHT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_GENERATION_MODEL = "claude-sonnet-5"
DEFAULT_GENERATION_THINKING = "disabled"


@dataclass
class Settings:
    # ── Database ──────────────────────────────────────────────
    database_url: str = ""
    db_pool_min: int = 1
    db_pool_max: int = 10

    # ── Embedding model ───────────────────────────────────────
    # EMBEDDING_PROVIDER (oly-ingestion/loaders/embedders.py):
    #   openai         OpenAI (OPENAI_API_KEY)                              — the default
    #   openai_compat  any OpenAI-shaped /v1/embeddings server: Together, Ollama,
    #                  text-embeddings-inference … (EMBEDDING_BASE_URL, EMBEDDING_API_KEY)
    #   local          a sentence-transformers model on this machine (CPU is enough)
    # EMBEDDING_MODEL names the model in that provider's namespace; every provider
    # fits vectors to embedding_dim (the vector(N) column), and the per-row
    # embedding_model tag keeps spaces apart. Switching = reembed.py + golden/baseline
    # rebuild (docs/RETRIEVAL_EVAL.md).
    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_dim: int = 1536

    # ── Model roles ───────────────────────────────────────────
    # Blank defaults resolve in __post_init__ (explicit arg > env > default) so a
    # model can be switched per run without a code edit — LLM_MODEL,
    # GENERATION_MODEL, EXPLANATION_MODEL, LIGHT_MODEL.
    #   llm_model         ingestion heavy lifting: principle extraction, program-template
    #                     parsing, vision OCR (needs a Sonnet-class model)
    #   light_model       cheap label/summary tasks: classifier fallback, --contextualize,
    #                     relabel_chunk_types.py, golden-set grading (a Haiku-class model)
    #   generation_model  the agent's per-session generation call
    #   explanation_model the agent's program rationale call
    llm_model: str = ""
    llm_max_tokens: int = 4096
    light_model: str = ""
    # TEMPLATE_MODEL: program-template parsing only; blank = llm_model. Split out
    # on 2026-09-20 because Kimi K3 handles principle extraction and OCR but
    # returned one empty template parse in four (a silently lost program), and
    # DeepSeek's long template outputs came back as malformed JSON — templates
    # stay on Sonnet 5 while the rest of the role moves.
    template_model: str = ""

    # ── Agent LLM settings ────────────────────────────────────
    generation_model: str = ""
    generation_max_tokens: int = 4096
    generation_temperature: float = 0.3   # dropped automatically on models that reject it (Sonnet 5+)
    explanation_model: str = ""
    explanation_max_tokens: int = 2048   # Sonnet 5 rationales overran 1024 (baseline: stop_reason=max_tokens)
    explanation_temperature: float = 0.7
    # Thinking mode ("adaptive" / "disabled"; blank resolves to
    # DEFAULT_GENERATION_THINKING — set "adaptive" explicitly for the model's own
    # default, and raise generation_max_tokens with it) and effort ("" = model
    # default, low … max) per role — env GENERATION_THINKING / GENERATION_EFFORT /
    # EXPLANATION_THINKING / EXPLANATION_EFFORT. Resolved by
    # shared.llm.thinking_kwargs(), which no-ops on models that lack the field.
    generation_thinking: str = ""
    generation_effort: str = ""
    explanation_thinking: str = ""
    explanation_effort: str = ""

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

    # ── API keys / provider ───────────────────────────────────
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    # LLM_PROVIDER: "anthropic" (default) or "openrouter" — OpenRouter's
    # Anthropic-compatible endpoint with OPENROUTER_API_KEY; the model roles
    # below are rewritten to OpenRouter ids in __post_init__. LLM_BASE_URL
    # overrides the endpoint (blank = the provider's default).
    llm_provider: str = ""
    openrouter_api_key: str = ""
    llm_base_url: str = ""

    # ── Web / deployment ──────────────────────────────────────
    secret_key: str = ""   # session signing key; MUST be set in production via SECRET_KEY env var
    redis_url: str = ""    # optional Redis URL for rate limiter (e.g. redis://localhost:6379)
    https_only: bool = False  # set HTTPS_ONLY=true in production to enable Secure cookie flag
    # Blank defaults resolve in __post_init__ (explicit arg > env > default) so
    # env vars can't silently override an explicitly passed value (INF-L8).
    log_format: str = ""      # "json" for production — set via LOG_FORMAT env var (default "text")
    log_level: str = ""       # set via LOG_LEVEL env var (default "INFO")

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
        self.embedding_provider = (self.embedding_provider or os.getenv("EMBEDDING_PROVIDER", "openai")).strip().lower()
        if self.embedding_provider not in ("openai", "openai_compat", "local"):
            raise ValueError(f"EMBEDDING_PROVIDER must be openai, openai_compat or local, got {self.embedding_provider!r}")
        _default_embedding = {"openai": "text-embedding-3-small", "local": "Qwen/Qwen3-Embedding-0.6B"}.get(self.embedding_provider, "")
        self.embedding_model = self.embedding_model or os.getenv("EMBEDDING_MODEL", _default_embedding)
        self.embedding_base_url = self.embedding_base_url or os.getenv("EMBEDDING_BASE_URL", "")
        self.embedding_api_key = self.embedding_api_key or os.getenv("EMBEDDING_API_KEY", "")
        if self.embedding_provider == "openai_compat" and not (self.embedding_model and self.embedding_base_url):
            raise ValueError("EMBEDDING_PROVIDER=openai_compat needs EMBEDDING_MODEL and EMBEDDING_BASE_URL")
        self.anthropic_api_key = self.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.llm_provider = (self.llm_provider or os.getenv("LLM_PROVIDER", "anthropic")).strip().lower()
        self.openrouter_api_key = self.openrouter_api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.llm_base_url = self.llm_base_url or os.getenv("LLM_BASE_URL", "")
        if self.llm_provider not in ("anthropic", "openrouter"):
            raise ValueError(f"LLM_PROVIDER must be 'anthropic' or 'openrouter', got {self.llm_provider!r}")

        if not self.openai_api_key and self.embedding_provider == "openai":
            _log.warning("OPENAI_API_KEY is not set — embeddings and vector search will fail")
        if self.llm_provider == "openrouter":
            if not self.openrouter_api_key:
                _log.warning("OPENROUTER_API_KEY is not set — LLM calls will fail")
        elif not self.anthropic_api_key:
            _log.warning("ANTHROPIC_API_KEY is not set — LLM calls will fail")

        self.redis_url = self.redis_url or os.getenv("REDIS_URL", "")

        # Model roles: explicit arg > env > default (see the field comments)
        self.llm_model = self.llm_model or os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL)
        self.light_model = self.light_model or os.getenv("LIGHT_MODEL", DEFAULT_LIGHT_MODEL)
        self.template_model = self.template_model or os.getenv("TEMPLATE_MODEL", "") or self.llm_model
        self.generation_model = self.generation_model or os.getenv("GENERATION_MODEL", DEFAULT_GENERATION_MODEL)
        self.explanation_model = self.explanation_model or os.getenv("EXPLANATION_MODEL", DEFAULT_GENERATION_MODEL)
        self.generation_thinking = (self.generation_thinking
                                    or os.getenv("GENERATION_THINKING", DEFAULT_GENERATION_THINKING))
        self.generation_effort = self.generation_effort or os.getenv("GENERATION_EFFORT", "")
        self.explanation_thinking = (self.explanation_thinking
                                     or os.getenv("EXPLANATION_THINKING", DEFAULT_GENERATION_THINKING))
        self.explanation_effort = self.explanation_effort or os.getenv("EXPLANATION_EFFORT", "")
        if self.llm_provider == "openrouter":
            from shared.llm import openrouter_model_id
            self.llm_model = openrouter_model_id(self.llm_model)
            self.template_model = openrouter_model_id(self.template_model)
            self.light_model = openrouter_model_id(self.light_model)
            self.generation_model = openrouter_model_id(self.generation_model)
            self.explanation_model = openrouter_model_id(self.explanation_model)

        self.https_only = self.https_only or os.getenv("HTTPS_ONLY", "").lower() in ("1", "true", "yes")

        # Explicit arg > env > default — matches every other field (INF-L8)
        self.log_format = self.log_format or os.getenv("LOG_FORMAT", "text")
        self.log_level  = self.log_level or os.getenv("LOG_LEVEL", "INFO")

        self.secret_key = self.secret_key or os.getenv("SECRET_KEY", "")
        if self.secret_key in _PLACEHOLDER_SECRET_KEYS:
            # A copied-but-unedited .env would otherwise sign sessions with a
            # public string from the repo (INF-L9)
            _log.warning(
                "SECRET_KEY is the committed .env.example placeholder — ignoring it. "
                "Generate a real key: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
            self.secret_key = ""
        if not self.secret_key:
            import secrets
            self.secret_key = secrets.token_hex(32)
            _log.warning(
                "SECRET_KEY is not set — sessions will be invalidated on every restart. "
                "Set SECRET_KEY in environment for production deployments."
            )

    def ensure_working_dirs(self) -> None:
        """Create the ingestion working directories.

        Called explicitly by the ingestion entry points — NOT in __post_init__,
        so importing Settings from the web app, agent, or tests doesn't scatter
        empty ./sources and ./logs dirs into whatever the CWD happens to be.
        """
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
