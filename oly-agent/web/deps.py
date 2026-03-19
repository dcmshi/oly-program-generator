# web/deps.py
"""FastAPI shared dependencies."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from shared.config import Settings
from web.async_db import get_async_pool

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


async def get_db():
    pool = get_async_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn


def require_admin(request: Request) -> None:
    """Dependency that restricts a route to admin users.

    Reads is_admin from the session (set at login time). Raises 403 for
    non-admin authenticated users so the nav link can be hidden in templates
    without relying solely on UI-level access control.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
