# web/routers/log_session.py
from datetime import date
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse

from web.deps import ATHLETE_ID, get_db
from web.queries import log_session as q

router = APIRouter(prefix="/log")


@router.get("/{session_id}", response_class=HTMLResponse)
async def log_form(session_id: int, request: Request, conn=Depends(get_db)):
    from web.app import templates
    session = q.get_session_with_exercises(conn, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    existing_log = q.get_existing_log(conn, session_id)
    logged_exercises = q.get_logged_exercises(conn, existing_log["id"]) if existing_log else []
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
async def submit_session_log(session_id: int, request: Request, conn=Depends(get_db)):
    from web.app import templates
    form = await request.form()
    session = q.get_session_with_exercises(conn, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Idempotent: if already logged, return existing log
    existing = q.get_existing_log(conn, session_id)
    if existing:
        log_id = existing["id"]
    else:
        log_id = q.create_session_log(conn, ATHLETE_ID, session_id, dict(form))

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
async def submit_exercise_log(log_id: int, request: Request, conn=Depends(get_db)):
    from web.app import templates
    form = await request.form()
    q.create_exercise_log(conn, log_id, dict(form))
    # Return a confirmation row for HTMX to append
    exercise_name = form.get("exercise_name", "Exercise")
    weight_kg = form.get("weight_kg", "")
    rpe = form.get("rpe", "")
    return templates.TemplateResponse("partials/exercise_logged_row.html", {
        "request": request,
        "exercise_name": exercise_name,
        "weight_kg": weight_kg,
        "rpe": rpe,
    })
