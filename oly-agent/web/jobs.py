# web/jobs.py
"""In-process background job queue for program generation."""

import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

_executor = ThreadPoolExecutor(max_workers=1)  # one generation at a time
_jobs: dict[str, dict] = {}


def submit_generation(athlete_id: int, dry_run: bool = False) -> str:
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {"status": "running", "program_id": None, "error": None}
    _executor.submit(_run_generation, job_id, athlete_id, dry_run)
    return job_id


def get_job(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def _run_generation(job_id: str, athlete_id: int, dry_run: bool):
    try:
        from shared.config import Settings
        import orchestrator
        settings = Settings()
        program_id = orchestrator.run(athlete_id, settings, dry_run=dry_run)
        _jobs[job_id] = {"status": "done", "program_id": program_id, "error": None}
    except Exception as e:
        import traceback
        _jobs[job_id] = {
            "status": "failed",
            "program_id": None,
            "error": f"{e}\n{traceback.format_exc()}",
        }
