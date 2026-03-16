# web/jobs.py
"""In-process background job queue for program generation."""

import logging
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1)  # one generation at a time
_jobs: dict[str, dict] = {}  # job_id → {status, athlete_id, program_id, error, ...}


def submit_generation(athlete_id: int, dry_run: bool = False) -> str:
    job_id = str(uuid.uuid4())[:8]
    started_at = datetime.now(timezone.utc).isoformat()
    _jobs[job_id] = {
        "status": "running",
        "athlete_id": athlete_id,
        "program_id": None,
        "error": None,
        "started_at": started_at,
        "completed_at": None,
        "duration_seconds": None,
    }
    logger.info(f"Job {job_id}: submitted generation for athlete {athlete_id} (dry_run={dry_run})")
    _executor.submit(_run_generation, job_id, athlete_id, dry_run)
    return job_id


def get_job(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def get_job_for_athlete(job_id: str, athlete_id: int) -> dict | None:
    """Return the job only if it belongs to the given athlete."""
    job = _jobs.get(job_id)
    if job is None or job.get("athlete_id") != athlete_id:
        return None
    return job


def _run_generation(job_id: str, athlete_id: int, dry_run: bool):
    start = datetime.now(timezone.utc)
    logger.info(f"Job {job_id}: starting orchestrator for athlete {athlete_id}")
    try:
        from shared.config import Settings
        import orchestrator
        settings = Settings()
        program_id = orchestrator.run(athlete_id, settings, dry_run=dry_run)
        completed_at = datetime.now(timezone.utc)
        duration = round((completed_at - start).total_seconds(), 1)
        athlete_id = _jobs[job_id].get("athlete_id")
        _jobs[job_id] = {
            "status": "done",
            "athlete_id": athlete_id,
            "program_id": program_id,
            "error": None,
            "started_at": start.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": duration,
        }
        logger.info(f"Job {job_id}: completed in {duration}s — program_id={program_id}")
    except Exception as e:
        import traceback
        completed_at = datetime.now(timezone.utc)
        duration = round((completed_at - start).total_seconds(), 1)
        athlete_id = _jobs[job_id].get("athlete_id")
        logger.error(f"Job {job_id}: failed after {duration}s — {e}\n{traceback.format_exc()}")
        _jobs[job_id] = {
            "status": "failed",
            "athlete_id": athlete_id,
            "program_id": None,
            "error": "Program generation failed. Check server logs for details.",
            "started_at": start.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": duration,
        }
