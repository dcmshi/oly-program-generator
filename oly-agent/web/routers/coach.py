# web/routers/coach.py
"""Coach mode across athletes (COACH-2).

/coach lists the athletes this account coaches and the coaches this account
has given access to. A coach switches to an athlete ("Coach as") and every
athlete-scoped page — dashboard, profile, generate, programs, logs, share —
then works on that athlete through `get_current_athlete_id`; "Back to me"
returns to their own account. Links come from the athlete granting a coach's
username, or from the coach adding an athlete who has no login.

Every route here acts on the logged-in account (`get_account_id`), never on
the athlete being coached, so a coach can't grant or revoke on their behalf.
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from web.auth import ACTING_ID_KEY, ACTING_NAME_KEY, get_account_id, stop_acting
from web.deps import get_db, limiter
from web.formparse import parse_float, parse_int
from web.options import FIELD_BOUNDS, SEX_OPTIONS, VALID_SEXES
from web.queries import coach as q

from shared.schema_enums import ATHLETE_LEVELS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/coach")


def _back(msg: str = "", error: str = "") -> RedirectResponse:
    from urllib.parse import urlencode
    params = urlencode({k: v for k, v in (("msg", msg), ("error", error)) if v})
    return RedirectResponse("/coach" + (f"?{params}" if params else ""), status_code=303)


@router.get("", response_class=HTMLResponse)
async def coach_page(request: Request, conn=Depends(get_db), account_id: int = Depends(get_account_id)):
    from web.app import templates
    return templates.TemplateResponse(request, "coach.html", {
        "request": request,
        "athletes": await q.list_coached_athletes(conn, account_id),
        "coaches": await q.list_coaches(conn, account_id),
        "acting_id": request.session.get(ACTING_ID_KEY),
        "levels": ATHLETE_LEVELS,
        "sex_options": SEX_OPTIONS,
        "spw_bounds": FIELD_BOUNDS["sessions_per_week"],
        "msg": request.query_params.get("msg"),
        "error": request.query_params.get("error"),
    })


@router.post("/athletes", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def add_athlete(
    request: Request,
    name: Annotated[str, Form(min_length=1, max_length=200)],
    level: Annotated[str, Form()] = "intermediate",
    sessions_per_week: Annotated[str, Form()] = "4",
    bodyweight_kg: Annotated[str, Form()] = "",
    biological_sex: Annotated[str, Form()] = "",
    conn=Depends(get_db),
    account_id: int = Depends(get_account_id),
):
    name = name.strip()
    if not name:
        return _back(error="Give the athlete a name.")
    if level not in ATHLETE_LEVELS:
        return _back(error="Unknown level.")
    bounds = FIELD_BOUNDS["sessions_per_week"]
    spw = parse_int(sessions_per_week, lo=bounds["min"], hi=bounds["max"]) or 4
    bw = parse_float(bodyweight_kg)
    bw_bounds = FIELD_BOUNDS["bodyweight_kg"]
    if bw is not None and not bw_bounds["min"] <= bw <= bw_bounds["max"]:
        bw = None
    sex = biological_sex if biological_sex in VALID_SEXES else None
    athlete_id = await q.create_managed_athlete(conn, account_id, name=name, level=level, sessions_per_week=spw,
                                                bodyweight_kg=bw, biological_sex=sex)
    logger.info(f"Coach {account_id} added athlete {athlete_id} ({name})")
    return _back(msg=f"Added {name}. Switch to them to add maxes, faults and goals.")


@router.post("/switch", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def switch(
    request: Request,
    athlete_id: Annotated[int, Form()],
    conn=Depends(get_db),
    account_id: int = Depends(get_account_id),
):
    """Coach as `athlete_id`; 0 (or one's own id) goes back to one's own account."""
    if athlete_id in (0, account_id):
        stop_acting(request.session)
        return RedirectResponse("/", status_code=303)
    if not await q.is_linked(conn, account_id, athlete_id):
        return _back(error="You don't coach that athlete.")
    athlete = next((a for a in await q.list_coached_athletes(conn, account_id) if a["id"] == athlete_id), None)
    request.session[ACTING_ID_KEY] = athlete_id
    request.session[ACTING_NAME_KEY] = athlete["name"] if athlete else f"athlete {athlete_id}"
    logger.info(f"Coach {account_id} now acting as athlete {athlete_id}")
    return RedirectResponse("/", status_code=303)


@router.post("/grant", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def grant_access(
    request: Request,
    coach_username: Annotated[str, Form(min_length=1, max_length=100)],
    conn=Depends(get_db),
    account_id: int = Depends(get_account_id),
):
    """The logged-in athlete gives a coach (by username) access to their account."""
    coach = await q.find_account_by_username(conn, coach_username.strip())
    if coach is None:
        return _back(error="No account with that username.")
    if coach["id"] == account_id:
        return _back(error="That's your own account.")
    added = await q.grant(conn, account_id, coach["id"])
    logger.info(f"Athlete {account_id} granted coach access to {coach['id']}")
    return _back(msg=f"{coach['name']} can now coach you." if added else f"{coach['name']} already coaches you.")


@router.post("/revoke", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def revoke_access(
    request: Request,
    coach_id: Annotated[int, Form()],
    conn=Depends(get_db),
    account_id: int = Depends(get_account_id),
):
    """The logged-in athlete removes a coach's access."""
    await q.revoke(conn, coach_id, account_id)
    logger.info(f"Athlete {account_id} revoked coach {coach_id}")
    return _back(msg="Access removed.")


@router.post("/remove", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def remove_athlete(
    request: Request,
    athlete_id: Annotated[int, Form()],
    conn=Depends(get_db),
    account_id: int = Depends(get_account_id),
):
    """The coach drops an athlete: one they added (no login) is deleted with all
    their data; one who granted access is only unlinked."""
    result = await q.remove_athlete(conn, account_id, athlete_id)
    if result is None:
        return _back(error="You don't coach that athlete.")
    if request.session.get(ACTING_ID_KEY) == athlete_id:
        stop_acting(request.session)
    logger.info(f"Coach {account_id} removed athlete {athlete_id}: {result}")
    return _back(msg="Athlete deleted." if result == "deleted" else "Athlete removed from your list.")
