# web/routers/health.py
"""Health check endpoint for load balancers and container orchestrators.

GET /health — unauthenticated, returns 200 OK or 503 Service Unavailable.
"""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    checks: dict[str, str] = {}
    healthy = True

    # Postgres check
    try:
        from web.async_db import get_async_pool
        pool = get_async_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["db"] = "ok"
    except Exception as e:
        logger.warning(f"Health check: DB failed — {e}")
        checks["db"] = "error"
        healthy = False

    # Redis check
    try:
        from web.jobs import _arq_pool
        if _arq_pool is None:
            raise RuntimeError("pool not initialised")
        await _arq_pool.ping()
        checks["redis"] = "ok"
    except Exception as e:
        logger.warning(f"Health check: Redis failed — {e}")
        checks["redis"] = "error"
        healthy = False

    status = "ok" if healthy else "degraded"
    return JSONResponse({"status": status, "checks": checks}, status_code=200 if healthy else 503)
