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
    session = await q.get_session_with_exercises(conn, session_id)
    if not session:
        logger.warning(f"Session {session_id} not found")
        raise HTTPException(status_code=404, detail="Session not found")
    existing_log = await q.get_existing_log(conn, session_id)
    logged_exercises = await q.get_logged_exercises(conn, existing_log["id"]) if existing_log else []
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
    session = await q.get_session_with_exercises(conn, session_id)
    if not session:
        logger.warning(f"Session log submit: session {session_id} not found")
        raise HTTPException(status_code=404, detail="Session not found")

    existing = await q.get_existing_log(conn, session_id)
    if existing:
        log_id = existing["id"]
        await q.update_session_log(conn, log_id, dict(form))
        logger.info(f"Session {session_id} log updated (log_id={log_id})")
    else:
        log_id = await q.create_session_log(conn, athlete_id, session_id, dict(form))
        logger.info(f"Session {session_id} logged for athlete {athlete_id} (log_id={log_id})")

    log = await q.get_existing_log(conn, session_id)
    logged_exercises = await q.get_logged_exercises(conn, log_id)
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
    tle_id = await q.create_exercise_log(conn, log_id, dict(form))
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
            promoted = await q.maybe_promote_max(conn, athlete_id, se_id, weight_kg, _date.today())
            if promoted:
                logger.info(
                    f"New max auto-promoted: athlete={athlete_id}, "
                    f"{exercise_name} {weight_kg}kg"
                )
    except Exception as e:
        logger.warning(f"Max promotion check failed (non-fatal): {e}")

    log = await q.get_log_by_id(conn, log_id)
    session = await q.get_session_with_exercises(conn, log["session_id"])
    logged_exercises = await q.get_logged_exercises(conn, log_id)
    return templates.TemplateResponse("partials/exercise_log_section.html", {
        "request": request,
        "session": session,
        "log": log,
        "log_id": log_id,
        "logged_exercises": logged_exercises,
    })


@router.delete("/{log_id}/exercise/{tle_id}", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def delete_exercise_log(
    log_id: int,
    tle_id: int,
    request: Request,
    conn=Depends(get_db),
):
    from web.app import templates
    await q.delete_exercise_log(conn, tle_id)
    logger.info(f"Exercise deleted: tle_id={tle_id}, log_id={log_id}")
    log = await q.get_log_by_id(conn, log_id)
    session = await q.get_session_with_exercises(conn, log["session_id"])
    logged_exercises = await q.get_logged_exercises(conn, log_id)
    return templates.TemplateResponse("partials/exercise_log_section.html", {
        "request": request,
        "session": session,
        "log": log,
        "log_id": log_id,
        "logged_exercises": logged_exercises,
    })


@router.post("/{log_id}/exercise/{tle_id}", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def update_exercise_log(
    log_id: int,
    tle_id: int,
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    from datetime import date as _date
    form = await request.form()
    await q.update_exercise_log(conn, tle_id, dict(form))
    logger.info(f"Exercise updated: tle_id={tle_id}, log_id={log_id}")

    # Auto-promote new max if this is a max attempt exercise
    try:
        weight_kg = float(form.get("weight_kg")) if form.get("weight_kg") else None
        tle = await q.get_exercise_log_entry(conn, tle_id)
        if tle and tle["session_exercise_id"] and weight_kg:
            promoted = await q.maybe_promote_max(
                conn, athlete_id, tle["session_exercise_id"], weight_kg, _date.today()
            )
            if promoted:
                logger.info(f"New max auto-promoted on edit: athlete={athlete_id}, {weight_kg}kg")
    except Exception as e:
        logger.warning(f"Max promotion check on edit failed (non-fatal): {e}")

    tle = await q.get_exercise_log_entry(conn, tle_id)
    log = await q.get_log_by_id(conn, log_id)
    return templates.TemplateResponse("partials/exercise_log_entry.html", {
        "request": request,
        "tle": tle,
        "log_id": log_id,
        "session_id": log["session_id"] if log else None,
    })
