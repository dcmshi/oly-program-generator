# web/routers/admin.py
"""Admin pages — visible to any authenticated user."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from web.deps import get_db, get_settings, require_admin
from web.queries.admin import get_recent_jobs, get_job_detail

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
logger = logging.getLogger(__name__)


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(
    request: Request,
    settings=Depends(get_settings),
    conn=Depends(get_db),
):
    jobs = await get_recent_jobs(conn)
    return templates.TemplateResponse(
        "admin_jobs.html",
        {"request": request, "jobs": jobs},
    )


@router.get("/jobs/{program_id}", response_class=HTMLResponse)
async def job_detail(
    request: Request,
    program_id: int,
    conn=Depends(get_db),
):
    rows = await get_job_detail(conn, program_id)
    return templates.TemplateResponse(
        "admin_job_detail.html",
        {"request": request, "program_id": program_id, "rows": rows},
    )
