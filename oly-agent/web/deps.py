# web/deps.py
"""FastAPI shared dependencies."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from slowapi import Limiter
from slowapi.util import get_remote_address

from shared.config import Settings
from shared.db import init_pool, pooled_connection

ATHLETE_ID = 1  # single-athlete tool; change here to switch athletes

# Singleton — parsed once at startup, not on every request
_settings: Settings | None = None

# Rate limiter — shared across all routers
limiter = Limiter(key_func=get_remote_address)


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_db():
    s = get_settings()
    init_pool(s.database_url, s.db_pool_min, s.db_pool_max)
    with pooled_connection() as conn:
        yield conn
