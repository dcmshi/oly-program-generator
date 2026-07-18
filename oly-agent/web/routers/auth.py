# web/routers/auth.py
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from web.async_db import async_fetch_one
from web.auth import hash_password, verify_password
from web.deps import get_db, limiter

logger = logging.getLogger(__name__)
router = APIRouter()

# Unknown usernames must burn the same bcrypt cost as a wrong password, or the
# ~100 ms difference is a username-existence oracle (WEB-L7).
_TIMING_DUMMY_HASH = hash_password("timing-equalizer-dummy")


# Map ?error= codes to messages instead of reflecting arbitrary query text into
# the trusted login card (audit5 web-L8). Autoescaping already blocks XSS; this
# stops attacker-chosen copy from being painted inside a styled error box.
_LOGIN_ERROR_MESSAGES = {
    "session_expired": "Your session expired — please sign in again.",
    "logged_out": "You have been signed out.",
}


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    from web.app import templates
    code = request.query_params.get("error")
    return templates.TemplateResponse(request, "login.html", {
        "request": request,
        "error": _LOGIN_ERROR_MESSAGES.get(code),
    })


@router.post("/login")
@limiter.limit("10/minute")
async def login_submit(
    request: Request,
    username: Annotated[str, Form(min_length=1, max_length=100)],
    password: Annotated[str, Form(min_length=1, max_length=200)],
    conn=Depends(get_db),
):
    from web.app import templates
    row = await async_fetch_one(
        conn,
        "SELECT id, name, password_hash, is_admin FROM athletes WHERE username = $1",
        username.strip(),
    )
    if not verify_password(password, row["password_hash"] if row else _TIMING_DUMMY_HASH) or not row:
        logger.warning("Failed login attempt — invalid credentials")
        return templates.TemplateResponse(request,
            "login.html",
            {"request": request, "error": "Invalid username or password"},
            status_code=401,
        )
    request.session["athlete_id"] = row["id"]
    request.session["athlete_name"] = row["name"]
    request.session["is_admin"] = bool(row["is_admin"])
    logger.info(f"Login: athlete_id={row['id']} ({row['name']}) is_admin={row['is_admin']}")
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    athlete_name = request.session.get("athlete_name", "unknown")
    request.session.clear()
    logger.info(f"Logout: {athlete_name}")
    return RedirectResponse("/login", status_code=303)
