# web/deps.py
"""FastAPI shared dependencies."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from slowapi import Limiter
from slowapi.util import get_remote_address

from shared.config import Settings
from shared.db import init_pool, pooled_connection

logger = logging.getLogger(__name__)

# Singleton — parsed once at startup, not on every request
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def _init_limiter() -> Limiter:
    """Create rate limiter, using Redis storage if REDIS_URL is configured."""
    s = get_settings()
    if s.redis_url:
        try:
            return Limiter(key_func=get_remote_address, storage_uri=s.redis_url)
        except Exception as e:
            logger.warning(f"Redis rate limiter unavailable ({e}), falling back to in-memory")
    return Limiter(key_func=get_remote_address)


limiter = _init_limiter()


def get_db():
    s = get_settings()
    init_pool(s.database_url, s.db_pool_min, s.db_pool_max)
    with pooled_connection() as conn:
        yield conn
