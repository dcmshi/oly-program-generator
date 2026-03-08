# web/deps.py
"""FastAPI shared dependencies."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.config import Settings
from shared.db import connection

ATHLETE_ID = 1  # single-athlete tool; change here to switch athletes

# Singleton — parsed once at startup, not on every request
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_db():
    settings = get_settings()
    with connection(settings.database_url) as conn:
        yield conn
