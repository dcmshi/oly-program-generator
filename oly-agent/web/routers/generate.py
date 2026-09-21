# web/routers/generate.py
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from web import jobs
from web.auth import get_current_athlete_id
from web.deps import get_db, limiter
from web.queries import program as qp

from shared.constants import BLOCK_WEEKS_MAX_BY_LEVEL, BLOCK_WEEKS_MIN

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/generate")


@router.get("", response_class=HTMLResponse)
async def generate_page(
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    programs = await qp.get_all_programs(conn, athlete_id)
    last = programs[0] if programs else None
    # Pick up a job that is still running from an earlier visit, so leaving the
    # page and coming back resumes polling instead of showing nothing.
    inflight = await jobs.get_inflight_job_id(athlete_id)
    return templates.TemplateResponse(request, "generate.html", {
        "request": request, "last_program": last,
        "job_id": inflight,
        "job": {"status": "running"} if inflight else None,
    })


@router.post("/run", response_class=HTMLResponse)
@limiter.limit("2/minute")
async def run_generation(
    request: Request,
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    form = await request.form()
    dry_run = form.get("dry_run") == "on"
    duration_weeks = None
    raw_weeks = (form.get("weeks") or "").strip()
    if raw_weeks:
        try:
            duration_weeks = int(raw_weeks)
        except ValueError:
            return HTMLResponse('<div class="text-red-700 text-sm">Block length must be a whole number of weeks.</div>', status_code=422)
        if not BLOCK_WEEKS_MIN <= duration_weeks <= max(BLOCK_WEEKS_MAX_BY_LEVEL.values()):
            return HTMLResponse(
                f'<div class="text-red-700 text-sm">Block length must be between {BLOCK_WEEKS_MIN} and '
                f'{max(BLOCK_WEEKS_MAX_BY_LEVEL.values())} weeks.</div>', status_code=422)
    request_id = getattr(request.state, "request_id", "-")
    try:
        job_id = await jobs.submit_generation(athlete_id, dry_run=dry_run, request_id=request_id,
                                              duration_weeks=duration_weeks)
    except jobs.GenerationInFlightError:
        logger.info(f"Generation rejected — already in flight for athlete {athlete_id}")
        return HTMLResponse(
            '<div class="text-amber-700 text-sm">A program generation is already '
            "running for your account — wait for it to finish before starting "
            "another.</div>",
            status_code=409,
        )
    logger.info(f"Generation submitted: job_id={job_id}, athlete={athlete_id}, dry_run={dry_run}")
    return templates.TemplateResponse(request, "partials/generate_result.html", {
        "request": request, "job_id": job_id, "job": {"status": "running"},
    })


@router.get("/status/{job_id}", response_class=HTMLResponse)
async def generation_status(
    job_id: str,
    request: Request,
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    job = await jobs.get_job_status(job_id, athlete_id)
    return templates.TemplateResponse(request, "partials/generate_result.html", {
        "request": request, "job_id": job_id, "job": job,
    })
