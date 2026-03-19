# web/worker.py
"""ARQ worker — executes program generation jobs from the Redis queue.

Run with:
    cd oly-agent
    PYTHONUTF8=1 uv run arq web.worker.WorkerSettings
"""

import asyncio
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from arq.connections import RedisSettings
from web.logging_config import configure_logging, request_id_var

logger = logging.getLogger(__name__)

# One generation at a time — orchestrator is CPU/IO bound and uses its own DB connection
_executor = ThreadPoolExecutor(max_workers=1)


async def _on_startup(ctx) -> None:
    """Configure logging when the worker process starts."""
    from shared.config import Settings
    s = Settings()
    configure_logging(s.log_format, s.log_level)
    logger.info("ARQ worker started")


async def run_generation(ctx, athlete_id: int, dry_run: bool = False, request_id: str = "-") -> dict:
    """Generate a program for the given athlete.

    Runs the synchronous orchestrator in a thread so the event loop stays free.
    Return value is stored in Redis by ARQ for the web server to fetch.
    """
    token = request_id_var.set(request_id)
    start = datetime.now(timezone.utc)
    logger.info(
        f"Worker: starting generation for athlete {athlete_id} (dry_run={dry_run})",
        extra={"athlete_id": athlete_id, "dry_run": dry_run},
    )

    def _sync():
        from shared.config import Settings
        import orchestrator
        return orchestrator.run(athlete_id, Settings(), dry_run=dry_run)

    try:
        loop = asyncio.get_event_loop()
        program_id = await loop.run_in_executor(_executor, _sync)
        duration = round((datetime.now(timezone.utc) - start).total_seconds(), 1)
        logger.info(
            f"Worker: completed in {duration}s — program_id={program_id}",
            extra={"athlete_id": athlete_id, "program_id": program_id, "duration_seconds": duration},
        )
        return {"program_id": program_id, "duration_seconds": duration, "athlete_id": athlete_id}
    finally:
        request_id_var.reset(token)


class WorkerSettings:
    on_startup = _on_startup
    functions = [run_generation]
    max_jobs = 1          # one generation at a time
    job_timeout = 600     # 10 minute hard limit per job
    keep_result = 86400   # keep results in Redis for 24 hours

    @classmethod
    def redis_settings(cls) -> RedisSettings:
        from shared.config import Settings
        url = Settings().redis_url or "redis://localhost:6379"
        return RedisSettings.from_dsn(url)
