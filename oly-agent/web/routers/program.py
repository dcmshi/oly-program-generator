# web/routers/program.py
import base64
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from web.auth import get_current_athlete_id
from web.deps import get_db, limiter
from web.queries import program as q

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/program")


@router.get("", response_class=HTMLResponse)
async def program_list(
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    programs = await q.get_all_programs(conn, athlete_id)
    logger.info(f"Program list: {len(programs)} programs for athlete {athlete_id}")
    return templates.TemplateResponse(request, "program_list.html", {"request": request, "programs": programs})


@lru_cache(maxsize=1)
def _inline_font_css() -> str:
    """static/fonts.css with every woff2 inlined as a data URI, so an exported
    page renders in the app's fonts with no requests (SHARE-1)."""
    static = Path(__file__).resolve().parent.parent / "static"
    css = (static / "fonts.css").read_text(encoding="utf-8")

    def inline(match):
        data = base64.b64encode((static / "fonts" / match.group(1)).read_bytes()).decode()
        return f"url('data:font/woff2;base64,{data}')"
    return re.sub(r"url\('/static/fonts/([\w.-]+\.woff2)'\)", inline, css)


@router.get("/share.html", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def share_page(
    request: Request,
    ids: Annotated[list[int], Query(min_length=1)],
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    """One self-contained HTML file for one or more of the athlete's programs —
    the shareable page, downloaded (SHARE-1). 404 if any id isn't theirs."""
    from web.app import templates
    from web.queries import dashboard as dq
    from web.queries import share as qs

    from shared.timeutil import today_in_tz
    if len(set(ids)) > qs.MAX_SHARED_PROGRAMS:
        raise HTTPException(status_code=422, detail=f"Export at most {qs.MAX_SHARED_PROGRAMS} programs at once")
    data = await qs.build_share_data(conn, athlete_id, ids)
    if data is None:
        raise HTTPException(status_code=404, detail="Program not found")
    programs = data["programs"]
    heading = programs[0]["name"] if len(programs) == 1 else f"{len(programs)} programs, side by side"
    models = {p["model"] for p in programs if p.get("model")}
    today = today_in_tz(await dq.get_athlete_timezone(conn, athlete_id))
    response = templates.TemplateResponse(request, "share_page.html", {
        "request": request, "data": data, "title": f"Oly Agent · {heading}", "heading": heading,
        "exported_on": today.strftime("%d %b %Y"), "athlete_name": data["athlete"].get("name"),
        "model": models.pop() if len(models) == 1 else None, "font_css": _inline_font_css(),
    })
    slug = "-".join(str(p["id"]) for p in programs)
    response.headers["Content-Disposition"] = f'attachment; filename="oly-program-{slug}.html"'
    logger.info(f"Share page exported: athlete {athlete_id}, programs {slug}")
    return response


@router.get("/{program_id}", response_class=HTMLResponse)
async def program_detail(
    program_id: int,
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    program = await q.get_program(conn, program_id)
    if not program:
        logger.warning(f"Program {program_id} not found")
        raise HTTPException(status_code=404, detail="Program not found")
    if program["athlete_id"] != athlete_id:
        logger.warning(f"Athlete {athlete_id} attempted to access program {program_id} owned by {program['athlete_id']}")
        raise HTTPException(status_code=404, detail="Program not found")
    weeks = await q.get_program_weeks(conn, program_id)
    volume_data = await q.get_program_volume_by_week(conn, program_id)
    macro = (await q.get_macrocycle_view(conn, athlete_id, program["macrocycle_id"])
             if program.get("macrocycle_id") else None)
    logger.info(f"Program {program_id} detail: {len(weeks)} weeks, status={program['status']}")
    return templates.TemplateResponse(request, "program.html", {
        "request": request, "program": program, "weeks": weeks, "volume_data": volume_data,
        "macro": macro,
    })


@router.post("/{program_id}/activate", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def activate(
    program_id: int,
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    program = await q.get_program(conn, program_id)
    if not program or program["athlete_id"] != athlete_id:
        raise HTTPException(status_code=404, detail="Program not found")
    # Only a draft can be activated — flipping a completed/abandoned program
    # back to active carries stale outcome_summary/end_date (audit5 web-L1)
    if program["status"] != "draft":
        raise HTTPException(status_code=409, detail="Only a draft program can be activated")
    await q.activate_program(conn, program_id, athlete_id)
    program = await q.get_program(conn, program_id)
    logger.info(f"Program {program_id} activated for athlete {athlete_id}")
    return templates.TemplateResponse(request, "partials/program_actions_update.html", {
        "request": request, "program": program,
    })


@router.post("/{program_id}/complete", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def complete(
    program_id: int,
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    program = await q.get_program(conn, program_id)
    if not program or program["athlete_id"] != athlete_id:
        logger.warning(f"Complete requested for missing/unowned program {program_id}")
        raise HTTPException(status_code=404, detail="Program not found")
    # Only an active program can be completed — re-completing an old one
    # recomputes maxes_delta against today's maxes and re-stamps updated_at,
    # which then wins assess()'s previous-program pick (AGT-H2 class; audit5 web-L1)
    if program["status"] != "active":
        raise HTTPException(status_code=409, detail="Only an active program can be completed")
    logger.info(f"Completing program {program_id} for athlete {athlete_id}")
    outcome = await q.complete_program(conn, program_id, athlete_id)
    logger.info(f"Program {program_id} completed: adherence={outcome.adherence_pct}%, make_rate={outcome.avg_make_rate:.0%}")
    macro_next = await _advance_macrocycle(program, outcome, athlete_id, request) if program.get("macrocycle_id") else None
    return templates.TemplateResponse(request, "partials/outcome_summary.html", {
        "request": request, "outcome": outcome, "program_id": program_id, "macro_next": macro_next,
        # The partial refreshes the action buttons out-of-band, so it needs the
        # post-completion status rather than the row we read above.
        "program": {**dict(program), "status": "completed"},
    })


async def _advance_macrocycle(program, outcome, athlete_id: int, request: Request) -> dict | None:
    """PLAN-3e: re-flow the macrocycle after a block completes and queue the
    next block. Returns {"message", "finished", "queued", "error"} for the
    outcome card; a failure here never fails the completion itself."""
    from web import jobs
    try:
        info = await q.advance_macrocycle(program["id"], outcome)
    except Exception as e:
        logger.error(f"Macrocycle re-flow failed for program {program['id']}: {e}")
        return {"message": "", "finished": False, "queued": False,
                "error": "The macrocycle could not be updated — generate the next block from the Generate page."}
    if not info:
        return None
    result = {"message": info["message"], "finished": info["finished"], "queued": False, "error": ""}
    if info["finished"]:
        return result
    try:
        await jobs.submit_generation(athlete_id, request_id=getattr(request.state, "request_id", "-"),
                                     macrocycle={"id": info["macrocycle_id"]})
        result["queued"] = True
    except jobs.GenerationInFlightError:
        result["error"] = "Another generation is running — generate the next block from the Generate page when it finishes."
    except Exception as e:
        logger.error(f"Could not queue the next macrocycle block: {e}")
        result["error"] = "The next block could not be queued — generate it from the Generate page."
    return result


@router.delete("/{program_id}", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def delete_program(
    program_id: int,
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    program = await q.get_program(conn, program_id)
    if not program or program["athlete_id"] != athlete_id:
        logger.warning(f"Delete requested for missing/unowned program {program_id} by athlete {athlete_id}")
        raise HTTPException(status_code=404, detail="Program not found")
    await q.delete_program(conn, program_id, athlete_id)
    logger.info(f"Program {program_id} deleted by athlete {athlete_id}")
    return HTMLResponse("")


@router.post("/{program_id}/abandon", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def abandon(
    program_id: int,
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.app import templates
    program = await q.get_program(conn, program_id)
    if not program or program["athlete_id"] != athlete_id:
        raise HTTPException(status_code=404, detail="Program not found")
    # Same guard as activate and complete: without it, abandoning a completed
    # program flipped its status and stranded the computed outcome_summary.
    if program["status"] not in ("draft", "active"):
        raise HTTPException(status_code=409, detail="Only a draft or active program can be abandoned")
    await q.abandon_program(conn, program_id, athlete_id)
    logger.info(f"Program {program_id} abandoned by athlete {athlete_id}")
    return templates.TemplateResponse(request, "partials/program_actions_update.html", {
        "request": request, "program": {**dict(program), "status": "abandoned"},
    })


@router.post("/maxes/delete", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def delete_max(
    request: Request,
    exercise_name: Annotated[str, Form(min_length=1, max_length=200)],
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    try:
        await q.delete_athlete_max(conn, athlete_id, exercise_name)
        logger.info(f"Max deleted: athlete {athlete_id}, {exercise_name}")
        return await _maxes_response(request, conn, athlete_id,
                                     success=f"{exercise_name} max removed — using estimated value")
    except ValueError as e:
        logger.warning(f"Max delete failed: {e}")
        return await _maxes_response(request, conn, athlete_id, error=str(e))


LB_TO_KG = 0.45359237
MAX_KG = 500


async def _maxes_response(request: Request, conn, athlete_id: int, **extra):
    """The maxes partial, plus the dashboard's Goal Progress and Lift Ratios as
    out-of-band swaps — both read the maxes and showed the old value until a
    reload (QA-1)."""
    from web.app import templates
    from web.queries import dashboard as dq
    return templates.TemplateResponse(request, "partials/maxes_table.html", {
        "request": request, "maxes": await q.get_athlete_maxes(conn, athlete_id),
        "goal_progress": await dq.get_goal_progress(conn, athlete_id),
        "ratios": await dq.get_lift_ratios(conn, athlete_id),
        "oob": True, **extra,
    })


@router.post("/maxes/update", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def update_max(
    request: Request,
    exercise_name: Annotated[str, Form(min_length=1, max_length=200)],
    weight_kg: Annotated[float, Form(gt=0, le=MAX_KG / LB_TO_KG)],
    unit: Annotated[str, Form(pattern="^(kg|lb)$")] = "kg",
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from web.queries import dashboard as dq

    from shared.timeutil import today_in_tz
    # QA-1: `weight_kg` is in the chosen unit; lb converts to kg (0.1 kg) here
    entered = weight_kg
    if unit == "lb":
        weight_kg = round(entered * LB_TO_KG, 1)
    if weight_kg > MAX_KG:
        return await _maxes_response(request, conn, athlete_id, unit=unit,
                                     error=f"A max above {MAX_KG} kg isn't plausible — check the value and unit.")
    shown = f"{weight_kg:g} kg" + (f" ({entered:g} lb)" if unit == "lb" else "")
    try:
        # dated in the athlete's timezone, like every other athlete-facing date (QA-1)
        today = today_in_tz(await dq.get_athlete_timezone(conn, athlete_id))
        is_pr, prev_kg = await q.upsert_athlete_max(conn, athlete_id, exercise_name, weight_kg, today)
        logger.info(f"Max updated: athlete {athlete_id}, {exercise_name} = {shown}, PR={is_pr}")
        return await _maxes_response(
            request, conn, athlete_id, unit=unit,
            success=f"{exercise_name} updated to {shown}",
            is_pr=is_pr, pr_exercise=exercise_name, pr_kg=weight_kg, pr_prev_kg=prev_kg,
        )
    except ValueError as e:
        logger.warning(f"Max update failed: {e}")
        return await _maxes_response(request, conn, athlete_id, unit=unit, error=str(e))
