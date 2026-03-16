# web/routers/generate.py
import logging
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from web.auth import get_current_athlete_id
from web.deps import get_db, limiter
from web import jobs
from web.queries import program as qp

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
    return templates.TemplateResponse("generate.html", {
        "request": request, "last_program": last,
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
    request_id = getattr(request.state, "request_id", "-")
    job_id = await jobs.submit_generation(athlete_id, dry_run=dry_run, request_id=request_id)
    logger.info(f"Generation submitted: job_id={job_id}, athlete={athlete_id}, dry_run={dry_run}")
    return templates.TemplateResponse("partials/generate_result.html", {
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
    return templates.TemplateResponse("partials/generate_result.html", {
        "request": request, "job_id": job_id, "job": job,
    })
