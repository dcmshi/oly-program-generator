# web/routers/dashboard.py
import logging
from datetime import date
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from web.auth import get_current_athlete_id
from web.deps import get_db
from web.queries import dashboard as q
from web.queries import program as pq

logger = logging.getLogger(__name__)
router = APIRouter()


def _current_week(start_date, duration_weeks: int) -> int:
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    days_in = (date.today() - start_date).days
    return max(1, min(duration_weeks, (days_in // 7) + 1))


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    program = q.get_active_program(conn, athlete_id)
    maxes = pq.get_athlete_maxes(conn, athlete_id)
    ctx: dict = {"request": request, "program": None, "sessions": [],
                 "adherence": {}, "warnings": [], "current_week": 1, "maxes": maxes}

    if program:
        week = _current_week(program["start_date"], program["duration_weeks"])
        sessions = q.get_current_week_sessions(conn, program["id"], week)
        adherence = q.get_adherence(conn, program["id"], week)
        warnings = q.get_warnings(conn, athlete_id)
        ctx.update({
            "program": program,
            "current_week": week,
            "sessions": sessions,
            "adherence": adherence,
            "warnings": warnings,
        })

    logger.info(f"Dashboard: athlete {athlete_id}, program={program['id'] if program else None}, week={ctx['current_week']}")
    return templates.TemplateResponse("dashboard.html", ctx)
