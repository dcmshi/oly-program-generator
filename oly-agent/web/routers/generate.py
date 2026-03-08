# web/routers/generate.py
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from web.deps import ATHLETE_ID, get_db
from web import jobs
from web.queries import program as qp

router = APIRouter(prefix="/generate")


@router.get("", response_class=HTMLResponse)
async def generate_page(request: Request, conn=Depends(get_db)):
    from web.app import templates
    programs = qp.get_all_programs(conn, ATHLETE_ID)
    last = programs[0] if programs else None
    return templates.TemplateResponse("generate.html", {
        "request": request, "last_program": last,
    })


@router.post("/run", response_class=HTMLResponse)
async def run_generation(request: Request):
    from web.app import templates
    form = await request.form()
    dry_run = form.get("dry_run") == "on"
    job_id = jobs.submit_generation(ATHLETE_ID, dry_run=dry_run)
    return templates.TemplateResponse("partials/generate_result.html", {
        "request": request, "job_id": job_id, "job": {"status": "running"},
    })


@router.get("/status/{job_id}", response_class=HTMLResponse)
async def generation_status(job_id: str, request: Request):
    from web.app import templates
    job = jobs.get_job(job_id)
    if not job:
        job = {"status": "failed", "error": "Job not found", "program_id": None}
    return templates.TemplateResponse("partials/generate_result.html", {
        "request": request, "job_id": job_id, "job": job,
    })
