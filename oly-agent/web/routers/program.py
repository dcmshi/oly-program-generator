# web/routers/program.py
import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from web.deps import ATHLETE_ID, get_db, limiter
from web.queries import program as q

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/program")


@router.get("", response_class=HTMLResponse)
async def program_list(request: Request, conn=Depends(get_db)):
    from web.app import templates
    programs = q.get_all_programs(conn, ATHLETE_ID)
    logger.info(f"Program list: {len(programs)} programs for athlete {ATHLETE_ID}")
    return templates.TemplateResponse("program_list.html", {"request": request, "programs": programs})


@router.get("/{program_id}", response_class=HTMLResponse)
async def program_detail(program_id: int, request: Request, conn=Depends(get_db)):
    from web.app import templates
    program = q.get_program(conn, program_id)
    if not program:
        logger.warning(f"Program {program_id} not found")
        raise HTTPException(status_code=404, detail="Program not found")
    weeks = q.get_program_weeks(conn, program_id)
    logger.info(f"Program {program_id} detail: {len(weeks)} weeks, status={program['status']}")
    return templates.TemplateResponse("program.html", {
        "request": request, "program": program, "weeks": weeks,
    })


@router.post("/{program_id}/activate", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def activate(program_id: int, request: Request, conn=Depends(get_db)):
    from web.app import templates
    q.activate_program(conn, program_id, ATHLETE_ID)
    program = q.get_program(conn, program_id)
    logger.info(f"Program {program_id} activated for athlete {ATHLETE_ID}")
    return templates.TemplateResponse("partials/status_badge.html", {
        "request": request, "status": program["status"],
    })


@router.post("/{program_id}/complete", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def complete(program_id: int, request: Request, conn=Depends(get_db)):
    from web.app import templates
    program = q.get_program(conn, program_id)
    if not program:
        logger.warning(f"Complete requested for missing program {program_id}")
        raise HTTPException(status_code=404, detail="Program not found")
    logger.info(f"Completing program {program_id} for athlete {ATHLETE_ID}")
    outcome = q.complete_program(conn, program_id, ATHLETE_ID)
    logger.info(f"Program {program_id} completed: adherence={outcome.adherence_pct}%, make_rate={outcome.avg_make_rate:.0%}")
    return templates.TemplateResponse("partials/outcome_summary.html", {
        "request": request, "outcome": outcome, "program_id": program_id,
    })


@router.post("/{program_id}/abandon", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def abandon(program_id: int, request: Request, conn=Depends(get_db)):
    from web.app import templates
    q.abandon_program(conn, program_id)
    logger.info(f"Program {program_id} abandoned")
    return templates.TemplateResponse("partials/status_badge.html", {
        "request": request, "status": "abandoned",
    })


@router.post("/maxes/update", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def update_max(
    request: Request,
    exercise_name: Annotated[str, Form(min_length=1, max_length=200)],
    weight_kg: Annotated[float, Form(gt=0, le=500)],
    conn=Depends(get_db),
):
    from web.app import templates
    try:
        q.upsert_athlete_max(conn, ATHLETE_ID, exercise_name, weight_kg, date.today())
        logger.info(f"Max updated: athlete {ATHLETE_ID}, {exercise_name} = {weight_kg} kg")
        maxes = q.get_athlete_maxes(conn, ATHLETE_ID)
        return templates.TemplateResponse("partials/maxes_table.html", {
            "request": request, "maxes": maxes, "success": f"{exercise_name} updated to {weight_kg} kg",
        })
    except ValueError as e:
        logger.warning(f"Max update failed: {e}")
        maxes = q.get_athlete_maxes(conn, ATHLETE_ID)
        return templates.TemplateResponse("partials/maxes_table.html", {
            "request": request, "maxes": maxes, "error": str(e),
        })
