# web/deps.py
"""FastAPI shared dependencies."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.config import Settings
from shared.db import connection

ATHLETE_ID = 1  # single-athlete tool; change here to switch athletes


def get_settings() -> Settings:
    return Settings()


def get_db():
    settings = Settings()
    with connection(settings.database_url) as conn:
        yield conn
