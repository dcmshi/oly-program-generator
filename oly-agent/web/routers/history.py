# web/routers/history.py
"""Exercise history view."""

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from web.auth import get_current_athlete_id
from web.deps import get_db
from web.queries.history import get_exercise_history, compute_history_summary

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/history")


@router.get("", response_class=HTMLResponse)
async def exercise_history(
    request: Request,
    exercise: str = Query(..., min_length=1, max_length=200),
    back: str = Query(default="/"),
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates

    rows = await get_exercise_history(conn, athlete_id, exercise)
    summary = compute_history_summary(rows)
    logger.info(f"History: athlete={athlete_id}, exercise={exercise!r}, rows={len(rows)}")

    return templates.TemplateResponse("history.html", {
        "request":  request,
        "exercise": exercise,
        "rows":     rows,
        "summary":  summary,
        "back":     back,
    })
