# web/routers/profile.py
"""Profile and account settings routes."""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from web.async_db import async_fetch_one
from web.auth import get_current_athlete_id, hash_password, verify_password
from web.deps import get_db, limiter
from web.queries import profile as q

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/profile")


@router.get("", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    athlete = await q.get_athlete(conn, athlete_id)
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "athlete": athlete,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@router.post("/update", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def update_profile(
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
    name: Annotated[str, Form(min_length=1, max_length=200)] = "",
    email: Annotated[str, Form(max_length=200)] = "",
    level: Annotated[str, Form()] = "intermediate",
    biological_sex: Annotated[str, Form()] = "",
    date_of_birth: Annotated[str, Form()] = "",
    bodyweight_kg: Annotated[str, Form()] = "",
    height_cm: Annotated[str, Form()] = "",
    weight_class: Annotated[str, Form()] = "",
    training_age_years: Annotated[str, Form()] = "",
    sessions_per_week: Annotated[str, Form()] = "",
    session_duration_minutes: Annotated[str, Form()] = "",
    injuries: Annotated[str, Form(max_length=1000)] = "",
    notes: Annotated[str, Form(max_length=2000)] = "",
    lift_emphasis: Annotated[str, Form()] = "balanced",
    competition_experience: Annotated[str, Form()] = "none",
):
    from web.app import templates
    # Collect checkbox lists
    form = await request.form()
    available_equipment = form.getlist("available_equipment")
    technical_faults = form.getlist("technical_faults")
    strength_limiters = form.getlist("strength_limiters")

    data = {
        "name": name.strip(),
        "email": email.strip(),
        "level": level,
        "biological_sex": biological_sex,
        "date_of_birth": date_of_birth or None,
        "bodyweight_kg": bodyweight_kg,
        "height_cm": height_cm,
        "weight_class": weight_class,
        "training_age_years": training_age_years,
        "sessions_per_week": sessions_per_week,
        "session_duration_minutes": session_duration_minutes,
        "available_equipment": available_equipment,
        "technical_faults": technical_faults,
        "injuries": injuries.strip() or None,
        "notes": notes.strip() or None,
        "lift_emphasis": lift_emphasis or "balanced",
        "strength_limiters": strength_limiters,
        "competition_experience": competition_experience or "none",
    }

    if not data["name"]:
        athlete = await q.get_athlete(conn, athlete_id)
        return templates.TemplateResponse("profile.html", {
            "request": request,
            "athlete": athlete,
            "error": "Name is required.",
            "profile_error": True,
        }, status_code=422)

    await q.update_profile(conn, athlete_id, data)

    # Refresh session name if it changed
    request.session["athlete_name"] = data["name"]
    logger.info(f"Profile updated: athlete_id={athlete_id}")
    return RedirectResponse("/profile?success=profile", status_code=303)


@router.post("/password", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def update_password(
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
    current_password: Annotated[str, Form(min_length=1, max_length=200)] = "",
    new_password: Annotated[str, Form(min_length=8, max_length=200)] = "",
    confirm_password: Annotated[str, Form(min_length=1, max_length=200)] = "",
):
    from web.app import templates

    athlete = await q.get_athlete(conn, athlete_id)

    def _err(msg):
        return templates.TemplateResponse("profile.html", {
            "request": request,
            "athlete": athlete,
            "error": msg,
            "security_error": True,
        }, status_code=422)

    row = await async_fetch_one(conn, "SELECT password_hash FROM athletes WHERE id = $1", athlete_id)
    if not row or not verify_password(current_password, row["password_hash"]):
        return _err("Current password is incorrect.")

    if new_password != confirm_password:
        return _err("New passwords do not match.")

    if len(new_password) < 8:
        return _err("New password must be at least 8 characters.")

    await q.update_password(conn, athlete_id, hash_password(new_password))
    logger.info(f"Password changed: athlete_id={athlete_id}")
    return RedirectResponse("/profile?success=password", status_code=303)


@router.post("/username", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def update_username(
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
    new_username: Annotated[str, Form(min_length=3, max_length=100)] = "",
    confirm_password_u: Annotated[str, Form(min_length=1, max_length=200)] = "",
):
    from web.app import templates

    athlete = await q.get_athlete(conn, athlete_id)

    def _err(msg):
        return templates.TemplateResponse("profile.html", {
            "request": request,
            "athlete": athlete,
            "error": msg,
            "security_error": True,
        }, status_code=422)

    new_username = new_username.strip()
    if not new_username:
        return _err("Username cannot be empty.")

    row = await async_fetch_one(conn, "SELECT password_hash FROM athletes WHERE id = $1", athlete_id)
    if not row or not verify_password(confirm_password_u, row["password_hash"]):
        return _err("Password is incorrect.")

    try:
        await q.update_username(conn, athlete_id, new_username)
    except ValueError as exc:
        return _err(str(exc))

    logger.info(f"Username changed: athlete_id={athlete_id} -> '{new_username}'")
    return RedirectResponse("/profile?success=username", status_code=303)
