# web/routers/admin.py
"""Admin pages — visible to any authenticated user."""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from web.deps import get_db, get_settings, require_admin
from web.queries.admin import get_job_detail, get_recent_jobs

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(
    request: Request,
    settings=Depends(get_settings),
    conn=Depends(get_db),
):
    # Use the app's shared templates instance — a locally-built one lacks the
    # custom filters (phase_color/status_color) these pages rely on (WEB-M1).
    from web.app import templates
    jobs = await get_recent_jobs(conn)
    return templates.TemplateResponse(request,
        "admin_jobs.html",
        {"request": request, "jobs": jobs},
    )


@router.get("/jobs/{program_id}", response_class=HTMLResponse)
async def job_detail(
    request: Request,
    program_id: int,
    conn=Depends(get_db),
):
    from web.app import templates
    rows = await get_job_detail(conn, program_id)
    return templates.TemplateResponse(request,
        "admin_job_detail.html",
        {"request": request, "program_id": program_id, "rows": rows},
    )
