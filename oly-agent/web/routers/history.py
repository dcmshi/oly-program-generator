# web/routers/history.py
"""Exercise history view."""

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from web.auth import get_current_athlete_id
from web.deps import get_db
from web.queries.history import get_exercise_history, compute_history_summary

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/history")


def _safe_back(url: str) -> str:
    """Return url only if it is a safe relative path, else fall back to /."""
    if url.startswith("/") and "://" not in url:
        return url
    return "/"


@router.get("", response_class=HTMLResponse)
async def exercise_history(
    request: Request,
    exercise: str = Query(default="", max_length=200),
    back: str = Query(default="/"),
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    if not exercise:
        return RedirectResponse("/", status_code=302)

    from web.app import templates

    rows = await get_exercise_history(conn, athlete_id, exercise)
    summary = compute_history_summary(rows)
    logger.info(f"History: athlete={athlete_id}, exercise={exercise!r}, rows={len(rows)}")

    return templates.TemplateResponse("history.html", {
        "request":  request,
        "exercise": exercise,
        "rows":     rows,
        "summary":  summary,
        "back":     _safe_back(back),
    })
