# web/routers/program.py
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from web.deps import ATHLETE_ID, get_db
from web.queries import program as q

router = APIRouter(prefix="/program")


@router.get("", response_class=HTMLResponse)
async def program_list(request: Request, conn=Depends(get_db)):
    from web.app import templates
    programs = q.get_all_programs(conn, ATHLETE_ID)
    return templates.TemplateResponse("program_list.html", {"request": request, "programs": programs})


@router.get("/{program_id}", response_class=HTMLResponse)
async def program_detail(program_id: int, request: Request, conn=Depends(get_db)):
    from web.app import templates
    program = q.get_program(conn, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")
    weeks = q.get_program_weeks(conn, program_id)
    return templates.TemplateResponse("program.html", {
        "request": request, "program": program, "weeks": weeks,
    })


@router.post("/{program_id}/activate", response_class=HTMLResponse)
async def activate(program_id: int, request: Request, conn=Depends(get_db)):
    from web.app import templates
    q.activate_program(conn, program_id, ATHLETE_ID)
    program = q.get_program(conn, program_id)
    return templates.TemplateResponse("partials/status_badge.html", {
        "request": request, "status": program["status"],
    })
