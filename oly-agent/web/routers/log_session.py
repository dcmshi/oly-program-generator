# web/routers/log_session.py
import logging
from datetime import date
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse

from web.auth import get_current_athlete_id
from web.deps import get_db, limiter
from web.queries import log_session as q

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/log")


@router.get("/{session_id}", response_class=HTMLResponse)
async def log_form(session_id: int, request: Request, conn=Depends(get_db)):
    from web.app import templates
    session = q.get_session_with_exercises(conn, session_id)
    if not session:
        logger.warning(f"Session {session_id} not found")
        raise HTTPException(status_code=404, detail="Session not found")
    existing_log = q.get_existing_log(conn, session_id)
    logged_exercises = q.get_logged_exercises(conn, existing_log["id"]) if existing_log else []
    logger.info(f"Log form: session {session_id}, already_logged={existing_log is not None}")
    return templates.TemplateResponse("log_session.html", {
        "request": request,
        "session": session,
        "existing_log": existing_log,
        "logged_exercises": logged_exercises,
        "log": existing_log,
        "log_id": existing_log["id"] if existing_log else None,
        "today": str(date.today()),
    })


@router.post("/{session_id}", response_class=HTMLResponse)
@limiter.limit("30/minute")
async def submit_session_log(
    session_id: int,
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    form = await request.form()
    session = q.get_session_with_exercises(conn, session_id)
    if not session:
        logger.warning(f"Session log submit: session {session_id} not found")
        raise HTTPException(status_code=404, detail="Session not found")

    # Idempotent: if already logged, return existing log
    existing = q.get_existing_log(conn, session_id)
    if existing:
        log_id = existing["id"]
        logger.info(f"Session {session_id} already logged (log_id={log_id}), returning existing")
    else:
        log_id = q.create_session_log(conn, athlete_id, session_id, dict(form))
        logger.info(f"Session {session_id} logged for athlete {athlete_id} (log_id={log_id})")

    log = q.get_existing_log(conn, session_id)
    logged_exercises = q.get_logged_exercises(conn, log_id)
    return templates.TemplateResponse("partials/exercise_log_section.html", {
        "request": request,
        "session": session,
        "log": log,
        "log_id": log_id,
        "logged_exercises": logged_exercises,
    })


@router.post("/{log_id}/exercise", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def submit_exercise_log(
    log_id: int,
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    from datetime import date as _date
    form = await request.form()
    q.create_exercise_log(conn, log_id, dict(form))
    exercise_name = form.get("exercise_name", "Exercise")
    weight_kg_raw = form.get("weight_kg", "")
    rpe = form.get("rpe", "")
    logger.info(f"Exercise logged: log_id={log_id}, {exercise_name} {weight_kg_raw}kg RPE={rpe}")

    # Auto-promote new max if this is a max attempt exercise
    try:
        se_id_raw = form.get("session_exercise_id")
        se_id = int(se_id_raw) if se_id_raw else None
        weight_kg = float(weight_kg_raw) if weight_kg_raw else None
        if se_id and weight_kg:
            promoted = q.maybe_promote_max(conn, athlete_id, se_id, weight_kg, _date.today())
            if promoted:
                logger.info(
                    f"New max auto-promoted: athlete={athlete_id}, "
                    f"{exercise_name} {weight_kg}kg"
                )
    except Exception as e:
        logger.warning(f"Max promotion check failed (non-fatal): {e}")

    return templates.TemplateResponse("partials/exercise_logged_row.html", {
        "request": request,
        "exercise_name": exercise_name,
        "weight_kg": weight_kg_raw,
        "rpe": rpe,
    })
