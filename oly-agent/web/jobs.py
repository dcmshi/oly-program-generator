# web/jobs.py
"""ARQ-backed job queue for program generation."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

_arq_pool = None
_JOB_OWNER_TTL = 86400  # seconds — matches WorkerSettings.keep_result (24 hours)

_DEFAULT_REDIS_URL = "redis://127.0.0.1:6379"


def resolve_redis_dsn(redis_url: str = "") -> str:
    """Normalize a Redis DSN for ARQ, forcing an IPv4 loopback.

    On Windows, `localhost` resolves to IPv6 `::1` first, which Docker's default
    port mapping (`127.0.0.1:6379`) doesn't bind; arq's 1s connect timeout
    expires before the IPv4 fallback, so background jobs never start. A blank
    URL (dev default) and any DSN whose host is `localhost` are rewritten to
    `127.0.0.1`, preserving scheme, userinfo, port, and path. Production sets an
    explicit non-localhost REDIS_URL, which passes through unchanged.
    """
    from urllib.parse import urlsplit, urlunsplit

    url = redis_url or _DEFAULT_REDIS_URL
    parts = urlsplit(url)
    if parts.hostname != "localhost":
        return url
    userinfo = ""
    if parts.username is not None:
        userinfo = parts.username
        if parts.password is not None:
            userinfo += f":{parts.password}"
        userinfo += "@"
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{userinfo}127.0.0.1{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


async def init_arq_pool(redis_url: str = "") -> None:
    global _arq_pool
    from arq import create_pool
    from arq.connections import RedisSettings

    settings = RedisSettings.from_dsn(resolve_redis_dsn(redis_url))
    _arq_pool = await create_pool(settings)
    logger.info("ARQ Redis pool initialised")


async def close_arq_pool() -> None:
    global _arq_pool
    if _arq_pool:
        await _arq_pool.aclose()
        _arq_pool = None


async def submit_generation(athlete_id: int, dry_run: bool = False, request_id: str = "-") -> str:
    if _arq_pool is None:
        raise RuntimeError("ARQ pool not initialised — is Redis running?")
    job = await _arq_pool.enqueue_job("run_generation", athlete_id, dry_run=dry_run, request_id=request_id)
    # Store ownership alongside the job so status polls can verify the requester
    await _arq_pool.set(f"job_owner:{job.job_id}", str(athlete_id), ex=_JOB_OWNER_TTL)
    logger.info(f"Job {job.job_id}: submitted for athlete {athlete_id} (dry_run={dry_run})")
    return job.job_id


async def get_job_status(job_id: str, athlete_id: int) -> dict:
    """Return a normalised job dict.  Returns a 'failed / not found' sentinel on any error."""
    _not_found = {"status": "failed", "error": "Job not found", "program_id": None, "duration_seconds": None}

    if _arq_pool is None:
        return _not_found

    # Ownership check — fast Redis GET before deserialising the full job
    owner = await _arq_pool.get(f"job_owner:{job_id}")
    if owner is None or int(owner) != athlete_id:
        logger.warning(f"Status poll for unknown/unauthorized job {job_id} by athlete {athlete_id}")
        return _not_found

    from arq.jobs import Job, JobStatus
    job = Job(job_id, _arq_pool)
    status = await job.status()

    if status in (JobStatus.queued, JobStatus.deferred, JobStatus.in_progress):
        return {"status": "running", "program_id": None, "error": None, "duration_seconds": None}

    if status == JobStatus.complete:
        try:
            result = await job.result(timeout=None)
            return {
                "status": "done",
                "program_id": result.get("program_id"),
                "error": None,
                "duration_seconds": result.get("duration_seconds"),
            }
        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}")
            return {
                "status": "failed",
                "program_id": None,
                "error": "Program generation failed. Check server logs for details.",
                "duration_seconds": None,
            }

    # JobStatus.not_found or expired
    return _not_found
