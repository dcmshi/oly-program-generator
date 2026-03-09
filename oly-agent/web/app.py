# web/app.py
"""FastAPI application factory."""

import sys
from pathlib import Path

# Make shared/ and oly-agent/ importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response as StarletteResponse

from web.deps import get_settings, limiter
from web.routers import dashboard, program, log_session, generate
from web.routers import auth as auth_router

app = FastAPI(title="Oly Agent")

# ── Rate limiting ──────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


# ── Request body size cap (64 KB) ─────────────────────────────
class ContentSizeLimitMiddleware(BaseHTTPMiddleware):
    _MAX_BODY = 64 * 1024  # 64 KB — enough for any session log form

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > self._MAX_BODY:
                return HTMLResponse("Request body too large (max 64 KB)", status_code=413)
        return await call_next(request)


app.add_middleware(ContentSizeLimitMiddleware)


# ── Auth guard — redirects unauthenticated requests to /login ──
_PUBLIC_PATHS = {"/login"}

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/static"):
            return await call_next(request)
        if not request.session.get("athlete_id"):
            if request.headers.get("HX-Request"):
                # Tell HTMX to do a full-page redirect instead of swapping content
                return StarletteResponse("", status_code=200, headers={"HX-Redirect": "/login"})
            return RedirectResponse("/login")
        return await call_next(request)


app.add_middleware(AuthMiddleware)

# ── Session middleware (must be added after AuthMiddleware so it runs first) ──
app.add_middleware(
    SessionMiddleware,
    secret_key=get_settings().secret_key,
    https_only=False,  # set True in production behind HTTPS
    same_site="lax",
)

# ── Static files ──────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

# ── Templates ─────────────────────────────────────────────────
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _fmt_weight(kg) -> str:
    if kg is None:
        return "—"
    return f"{kg:g} kg"

def _fmt_rpe(rpe) -> str:
    if rpe is None:
        return "—"
    return f"RPE {float(rpe):.1f}"

def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    return f"{v:g}%"

def _reps_list(arr) -> str:
    if not arr:
        return "—"
    return ",".join(str(r) for r in arr)

def _status_color(status: str) -> str:
    return {
        "active":     "bg-green-100 text-green-800",
        "draft":      "bg-yellow-100 text-yellow-800",
        "completed":  "bg-blue-100 text-blue-800",
        "superseded": "bg-gray-100 text-gray-500",
        "abandoned":  "bg-red-100 text-red-700",
    }.get(status, "bg-gray-100 text-gray-600")

def _phase_color(phase: str) -> str:
    return {
        "accumulation":    "bg-blue-100 text-blue-800",
        "intensification": "bg-orange-100 text-orange-800",
        "realization":     "bg-red-100 text-red-800",
        "general_prep":    "bg-purple-100 text-purple-800",
    }.get(phase, "bg-gray-100 text-gray-700")

templates.env.filters["fmt_weight"] = _fmt_weight
templates.env.filters["fmt_rpe"] = _fmt_rpe
templates.env.filters["fmt_pct"] = _fmt_pct
templates.env.filters["reps_list"] = _reps_list
templates.env.filters["status_color"] = _status_color
templates.env.filters["phase_color"] = _phase_color

# ── Routers ───────────────────────────────────────────────────
app.include_router(auth_router.router)
app.include_router(dashboard.router)
app.include_router(program.router)
app.include_router(log_session.router)
app.include_router(generate.router)
