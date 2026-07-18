# web/jobs.py
"""ARQ-backed job queue for program generation."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

_arq_pool = None

_DEFAULT_REDIS_URL = "redis://127.0.0.1:6379"

# In-flight guard TTL — a bit above the worker's job_timeout so a crashed
# worker can never wedge the guard for longer than a job could actually run.
INFLIGHT_TTL_SECONDS = 660


class GenerationInFlightError(RuntimeError):
    """The athlete already has a queued/running generation job (WEB-L2)."""


def _inflight_key(athlete_id: int) -> str:
    return f"gen_inflight:{athlete_id}"


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
    # One in-flight generation per athlete — double-clicks otherwise queue N
    # serial paid jobs producing duplicate drafts while the UI polls only the
    # newest (WEB-L2). SET NX + TTL: atomic, and self-releasing if a worker dies.
    if not await _arq_pool.set(_inflight_key(athlete_id), "1", nx=True, ex=INFLIGHT_TTL_SECONDS):
        raise GenerationInFlightError(f"Athlete {athlete_id} already has a generation in flight")
    # athlete_id is the job's first positional arg, so ownership travels with the
    # job payload itself — no separate owner key that could race the enqueue or
    # outlive/under-live the job (W-L4).
    try:
        job = await _arq_pool.enqueue_job("run_generation", athlete_id, dry_run=dry_run, request_id=request_id)
    except BaseException:
        # No job exists to release the guard via the status poll — without this
        # the athlete is locked out for the full TTL (audit2-L5). BaseException:
        # a cancelled request (CancelledError) leaked it too (audit3-L3). The
        # delete itself is guarded so a Redis outage doesn't mask the original
        # error; the TTL remains the backstop.
        try:
            await _arq_pool.delete(_inflight_key(athlete_id))
        except Exception as cleanup_err:
            logger.warning(f"In-flight guard cleanup failed (TTL will expire it): {cleanup_err}")
        raise
    # Stamp the guard with THIS job's id (refreshing the TTL) so a terminal
    # status poll of an OLD job can't release the guard a NEW job holds
    # (audit5 web-L3).
    try:
        await _arq_pool.set(_inflight_key(athlete_id), job.job_id, ex=INFLIGHT_TTL_SECONDS)
    except Exception as e:
        logger.warning(f"Could not stamp in-flight guard with job id (TTL still applies): {e}")
    logger.info(f"Job {job.job_id}: submitted for athlete {athlete_id} (dry_run={dry_run})")
    return job.job_id


async def get_job_status(job_id: str, athlete_id: int) -> dict:
    """Return a normalised job dict.  Returns a 'failed / not found' sentinel on any error."""
    _not_found = {"status": "failed", "error": "Job not found", "program_id": None, "duration_seconds": None}

    if _arq_pool is None:
        return _not_found

    from arq.jobs import Job, JobStatus
    job = Job(job_id, _arq_pool)

    # Ownership check — read the athlete_id from the job's own embedded args
    # (info().args[0]), which is set atomically at enqueue time.
    info = await job.info()
    if info is None or not info.args or int(info.args[0]) != athlete_id:
        logger.warning(f"Status poll for unknown/unauthorized job {job_id} by athlete {athlete_id}")
        return _not_found

    status = await job.status()

    if status in (JobStatus.queued, JobStatus.deferred, JobStatus.in_progress):
        return {"status": "running", "program_id": None, "error": None, "duration_seconds": None}

    # Terminal state (complete/expired/not_found) with ownership verified —
    # release the in-flight guard so the athlete can generate again (WEB-L2),
    # but only if THIS job still holds it: an old job's terminal poll must not
    # free the guard a newer in-flight job now owns (audit5 web-L3).
    held = await _arq_pool.get(_inflight_key(athlete_id))
    held_id = held.decode() if isinstance(held, bytes) else held
    if held_id is None or held_id == job_id:
        await _arq_pool.delete(_inflight_key(athlete_id))

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
