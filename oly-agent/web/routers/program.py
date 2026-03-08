# web/routers/program.py
from datetime import date

from fastapi import APIRouter, Depends, Form, Request, HTTPException
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


@router.post("/{program_id}/complete", response_class=HTMLResponse)
async def complete(program_id: int, request: Request, conn=Depends(get_db)):
    from web.app import templates
    program = q.get_program(conn, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")
    outcome = q.complete_program(conn, program_id, ATHLETE_ID)
    return templates.TemplateResponse("partials/outcome_summary.html", {
        "request": request, "outcome": outcome, "program_id": program_id,
    })


@router.post("/{program_id}/abandon", response_class=HTMLResponse)
async def abandon(program_id: int, request: Request, conn=Depends(get_db)):
    from web.app import templates
    q.abandon_program(conn, program_id)
    return templates.TemplateResponse("partials/status_badge.html", {
        "request": request, "status": "abandoned",
    })


@router.post("/maxes/update", response_class=HTMLResponse)
async def update_max(
    request: Request,
    exercise_name: str = Form(...),
    weight_kg: float = Form(...),
    conn=Depends(get_db),
):
    from web.app import templates
    try:
        q.upsert_athlete_max(conn, ATHLETE_ID, exercise_name, weight_kg, date.today())
        maxes = q.get_athlete_maxes(conn, ATHLETE_ID)
        return templates.TemplateResponse("partials/maxes_table.html", {
            "request": request, "maxes": maxes, "success": f"{exercise_name} updated to {weight_kg} kg",
        })
    except ValueError as e:
        maxes = q.get_athlete_maxes(conn, ATHLETE_ID)
        return templates.TemplateResponse("partials/maxes_table.html", {
            "request": request, "maxes": maxes, "error": str(e),
        })
