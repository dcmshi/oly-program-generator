# web/routers/generate.py
import html
import logging

from coach import COACH_NOTE_MAX_CHARS, COACH_PHASES, INTENSITY_CAP_RANGE, SESSIONS_PER_WEEK_RANGE
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from web import jobs
from web.async_db import async_fetch_all
from web.auth import get_current_athlete_id
from web.coach_form import parse_coach_form, profile_defaults
from web.deps import get_db, limiter
from web.queries import profile as qprof
from web.queries import program as qp

from shared.constants import BLOCK_WEEKS_MAX_BY_LEVEL, BLOCK_WEEKS_MIN, MACROCYCLE_WEEKS_MAX, MACROCYCLE_WEEKS_MIN
from shared.timeutil import today_in_tz

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/generate")


@router.get("", response_class=HTMLResponse)
async def generate_page(
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    programs = await qp.get_all_programs(conn, athlete_id)
    last = programs[0] if programs else None
    # Pick up a job that is still running from an earlier visit, so leaving the
    # page and coming back resumes polling instead of showing nothing.
    inflight = await jobs.get_inflight_job_id(athlete_id)
    macro = await qp.get_macrocycle_view(conn, athlete_id)
    athlete = await qprof.get_athlete(conn, athlete_id)
    goal = await qprof.get_active_goal(conn, athlete_id)
    exercise_names = [r["name"] for r in await async_fetch_all(conn, "SELECT name FROM exercises ORDER BY name")]
    return templates.TemplateResponse(request, "generate.html", {
        "request": request, "last_program": last, "macro": macro,
        "macro_min": MACROCYCLE_WEEKS_MIN, "macro_max": MACROCYCLE_WEEKS_MAX,
        "job_id": inflight,
        "job": {"status": "running"} if inflight else None,
        # COACH-1: the coach form, pre-filled from the profile
        "coach": profile_defaults(athlete, goal), "coach_phases": COACH_PHASES,
        "coach_spw_range": SESSIONS_PER_WEEK_RANGE, "coach_note_max": COACH_NOTE_MAX_CHARS,
        "coach_cap_range": INTENSITY_CAP_RANGE,
        "exercise_names": exercise_names,
    })


@router.post("/run", response_class=HTMLResponse)
@limiter.limit("2/minute")
async def run_generation(
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    form = await request.form()
    dry_run = form.get("dry_run") == "on"
    duration_weeks = None
    raw_weeks = (form.get("weeks") or "").strip()
    if raw_weeks:
        try:
            duration_weeks = int(raw_weeks)
        except ValueError:
            return HTMLResponse('<div class="text-red-700 text-sm">Block length must be a whole number of weeks.</div>', status_code=422)
        if not BLOCK_WEEKS_MIN <= duration_weeks <= max(BLOCK_WEEKS_MAX_BY_LEVEL.values()):
            return HTMLResponse(
                f'<div class="text-red-700 text-sm">Block length must be between {BLOCK_WEEKS_MIN} and '
                f'{max(BLOCK_WEEKS_MAX_BY_LEVEL.values())} weeks.</div>', status_code=422)
    macrocycle = None
    if (form.get("macrocycle_id") or "").strip():
        try:
            macrocycle = {"id": int(form["macrocycle_id"])}
        except ValueError:
            return HTMLResponse('<div class="text-red-700 text-sm">Unknown macrocycle.</div>', status_code=422)
    elif form.get("macrocycle") == "on":
        macrocycle = {"new": True, "weeks": None}
        raw_total = (form.get("macrocycle_weeks") or "").strip()
        if raw_total:
            try:
                macrocycle["weeks"] = int(raw_total)
            except ValueError:
                return HTMLResponse('<div class="text-red-700 text-sm">Macrocycle length must be a whole '
                                    'number of weeks.</div>', status_code=422)
            if not MACROCYCLE_WEEKS_MIN <= macrocycle["weeks"] <= MACROCYCLE_WEEKS_MAX:
                return HTMLResponse(
                    f'<div class="text-red-700 text-sm">Macrocycle length must be between {MACROCYCLE_WEEKS_MIN} '
                    f'and {MACROCYCLE_WEEKS_MAX} weeks.</div>', status_code=422)
    coach = None
    if form.get("coach_mode") == "on":
        athlete = await qprof.get_athlete(conn, athlete_id)
        goal = await qprof.get_active_goal(conn, athlete_id)
        coach, errors = parse_coach_form(form, profile_defaults(athlete, goal),
                                         today_in_tz((athlete or {}).get("timezone")))
        if errors:
            return HTMLResponse('<div class="text-red-700 text-sm">' + " ".join(map(html.escape, errors))
                                + "</div>", status_code=422)
        if coach and coach.get("phase") and macrocycle is not None:
            return HTMLResponse('<div class="text-red-700 text-sm">A macrocycle sets each block\'s phase — '
                                "leave the coach phase on auto.</div>", status_code=422)
    request_id = getattr(request.state, "request_id", "-")
    try:
        job_id = await jobs.submit_generation(athlete_id, dry_run=dry_run, request_id=request_id,
                                              duration_weeks=duration_weeks, macrocycle=macrocycle, coach=coach)
    except jobs.GenerationInFlightError:
        logger.info(f"Generation rejected — already in flight for athlete {athlete_id}")
        return HTMLResponse(
            '<div class="text-amber-700 text-sm">A program generation is already '
            "running for your account — wait for it to finish before starting "
            "another.</div>",
            status_code=409,
        )
    logger.info(f"Generation submitted: job_id={job_id}, athlete={athlete_id}, dry_run={dry_run}, "
                f"macrocycle={macrocycle}, coach={coach}")
    return templates.TemplateResponse(request, "partials/generate_result.html", {
        "request": request, "job_id": job_id, "job": {"status": "running"},
    })


@router.get("/status/{job_id}", response_class=HTMLResponse)
async def generation_status(
    job_id: str,
    request: Request,
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    job = await jobs.get_job_status(job_id, athlete_id)
    return templates.TemplateResponse(request, "partials/generate_result.html", {
        "request": request, "job_id": job_id, "job": job,
    })
