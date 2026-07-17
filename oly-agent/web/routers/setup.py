# web/routers/setup.py
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from web.auth import hash_password, password_too_long
from web.deps import get_db, limiter
from web.queries import setup as q

logger = logging.getLogger(__name__)
router = APIRouter()

# Canonical vocabularies live in web/options.py (WEB-M3) — re-exported here
# because retrieve.py's fault mapping and existing tests reference this module.
from web.options import (  # noqa: F401  (re-export)
    EQUIPMENT_OPTIONS,
    FAULT_OPTIONS,
    MAX_EXERCISES,
    STRENGTH_LIMITER_OPTIONS,
)


def _template_ctx(request, errors=None, form=None):
    return {
        "request": request,
        "errors": errors or [],
        "form": form or {},
        "equipment_options": EQUIPMENT_OPTIONS,
        "fault_options": FAULT_OPTIONS,
        "max_exercises": MAX_EXERCISES,
    }


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    from web.app import templates
    return templates.TemplateResponse(request, "setup.html", _template_ctx(request))


@router.post("/setup", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def setup_submit(request: Request, conn=Depends(get_db)):
    from web.app import templates
    raw_form = await request.form()
    form = dict(raw_form)

    errors = []

    # ── Account validation ────────────────────────────────────
    username = form.get("username", "").strip()
    password = form.get("password", "")
    confirm  = form.get("confirm_password", "")

    if not username:
        errors.append("Username is required.")
    elif len(username) > 100:
        errors.append("Username must be 100 characters or fewer.")
    elif await q.username_taken(conn, username):
        errors.append(f"Username '{username}' is already taken.")

    if not password:
        errors.append("Password is required.")
    elif len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    elif password_too_long(password):
        errors.append("Password must be at most 72 bytes (bcrypt limit).")
    elif password != confirm:
        errors.append("Passwords do not match.")

    # ── Profile validation ────────────────────────────────────
    name  = form.get("name", "").strip()
    level = form.get("level", "").strip()

    if not name:
        errors.append("Name is required.")
    elif len(name) > 200:
        errors.append("Name must be 200 characters or fewer.")

    if level not in ("beginner", "intermediate", "advanced", "elite"):
        errors.append("Please select a training level.")

    if errors:
        # Multi-select fields collapse to their last value in dict(raw_form);
        # the template needs the full list to re-check every selected box, and
        # calling .getlist on the plain dict crashed the re-render (WEB-H2).
        form["strength_limiters"] = raw_form.getlist("strength_limiters")
        return templates.TemplateResponse(request,
            "setup.html", _template_ctx(request, errors, form), status_code=422
        )

    # ── Collect equipment + faults from individual checkbox fields ──
    equipment = [val for _, val in EQUIPMENT_OPTIONS if form.get(f"equip_{val}") == "on"]
    faults    = [val for _, val in FAULT_OPTIONS    if form.get(f"fault_{val}") == "on"]

    athlete_data = {
        "name": name,
        "email": form.get("email", "").strip() or None,
        "level": level,
        "biological_sex": form.get("biological_sex") or None,
        "bodyweight_kg": form.get("bodyweight_kg"),
        "height_cm": form.get("height_cm"),
        "date_of_birth": form.get("date_of_birth") or None,
        "weight_class": form.get("weight_class", "").strip() or None,
        "training_age_years": form.get("training_age_years"),
        "sessions_per_week": form.get("sessions_per_week") or "4",
        "session_duration_minutes": form.get("session_duration_minutes") or "90",
        "available_equipment": equipment,
        "injuries": form.get("injuries", "").strip() or None,
        "technical_faults": faults,
        "username": username,
        "notes": form.get("notes", "").strip() or None,
        "lift_emphasis": form.get("lift_emphasis") or "balanced",
        "strength_limiters": raw_form.getlist("strength_limiters"),
        "competition_experience": form.get("competition_experience") or "none",
    }

    athlete_id = await q.create_athlete(conn, athlete_data, hash_password(password))

    # ── Maxes ─────────────────────────────────────────────────
    from web.formparse import parse_float
    maxes = []
    for exercise_name in MAX_EXERCISES:
        field = f"max_{exercise_name.lower().replace(' ', '_').replace('&', 'and')}"
        weight = parse_float(form.get(field, "").strip())  # finite + bounded (WEB-L4)
        if weight and weight > 0:
            maxes.append((exercise_name, weight))
    await q.create_maxes(conn, athlete_id, maxes)

    # ── Goal ──────────────────────────────────────────────────
    goal_type = form.get("goal_type", "").strip()
    if goal_type:
        await q.create_goal(
            conn,
            athlete_id,
            goal_type,
            competition_date=form.get("competition_date"),
            target_snatch_kg=form.get("target_snatch_kg"),
            target_cj_kg=form.get("target_cj_kg"),
        )

    logger.info(f"New athlete created: id={athlete_id}, username={username}, level={level}")

    request.session["athlete_id"] = athlete_id
    request.session["athlete_name"] = name
    return RedirectResponse("/", status_code=303)
