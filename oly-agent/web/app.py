# web/app.py
"""FastAPI application factory."""

import logging
import sys
import uuid
from contextlib import asynccontextmanager
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
from web.async_db import close_async_pool, init_async_pool
from web.deps import get_settings, limiter
from web.jobs import close_arq_pool, init_arq_pool
from web.logging_config import configure_logging, request_id_var
from web.routers import admin as admin_router
from web.routers import auth as auth_router
from web.routers import dashboard, generate, log_session, program
from web.routers import export as export_router
from web.routers import health as health_router
from web.routers import history as history_router
from web.routers import profile as profile_router
from web.routers import setup as setup_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    try:
        await init_async_pool(s.database_url, s.db_pool_min, s.db_pool_max)
    except Exception as e:
        logger.warning(f"DB pool init failed ({e}) — running without async pool")
    try:
        await init_arq_pool(s.redis_url)
    except Exception as e:
        logger.warning(f"ARQ Redis pool init failed ({e}) — background jobs unavailable")
    yield
    await close_async_pool()
    await close_arq_pool()


app = FastAPI(title="Oly Agent", lifespan=lifespan)

# ── Rate limiting ──────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


# ── Request body size cap (64 KB) ─────────────────────────────
class ContentSizeLimitMiddleware:
    """64 KB request-body cap (pure ASGI).

    Checks Content-Length up front AND pre-reads streamed chunks up to the cap
    — a chunked request carries no Content-Length, and the old header-only
    check let it buffer unbounded in request.form() (WEB-L6). The body is
    buffered here (bounded by the cap) and replayed to the app, so downstream
    parsing is untouched; oversized requests are answered 413 before the app
    ever sees them.
    """

    _MAX_BODY = 64 * 1024  # enough for any session log form

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)

        content_length = next(
            (v for k, v in scope.get("headers", []) if k == b"content-length"), None
        )
        if content_length is not None:
            try:
                if int(content_length) > self._MAX_BODY:
                    return await self._reject(send)
            except ValueError:
                pass

        buffered: list[dict] = []
        received = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break
            received += len(message.get("body", b""))
            if received > self._MAX_BODY:
                return await self._reject(send)
            if not message.get("more_body"):
                break

        async def replay():
            if buffered:
                return buffered.pop(0)
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _reject(send):
        body = b"Request body too large (max 64 KB)"
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"text/html; charset=utf-8"),
                        (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})


app.add_middleware(ContentSizeLimitMiddleware)


# ── Same-origin enforcement on state-changing requests (WEB-L5) ─
class OriginCheckMiddleware(BaseHTTPMiddleware):
    """Reject cross-origin POST/PUT/PATCH/DELETE.

    Browsers attach an Origin header to cross-site requests; a mismatch with
    Host means CSRF (or a broken proxy). Requests without an Origin header
    (curl, tests) pass — this is defense-in-depth on top of SameSite=Lax
    cookies, not the only line.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin:
                from urllib.parse import urlsplit
                host = request.headers.get("host", "")
                if origin == "null" or urlsplit(origin).netloc != host:
                    logger.warning(f"Cross-origin {request.method} {request.url.path} rejected: origin={origin}")
                    return HTMLResponse("Cross-origin request rejected", status_code=403)
        return await call_next(request)


app.add_middleware(OriginCheckMiddleware)


# ── Security headers ───────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if get_settings().https_only:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


app.add_middleware(SecurityHeadersMiddleware)


# ── Auth guard — redirects unauthenticated requests to /login ──
_PUBLIC_PATHS = {"/login", "/setup", "/health"}

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
_settings = get_settings()
configure_logging(_settings.log_format, _settings.log_level)
app.add_middleware(
    SessionMiddleware,
    secret_key=_settings.secret_key,
    https_only=_settings.https_only,  # set HTTPS_ONLY=true in production
    same_site="lax",
)

# ── Request ID — added last so it runs outermost (before all other middleware) ──
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = req_id
        token = request_id_var.set(req_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = req_id
        return response


app.add_middleware(RequestIDMiddleware)

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
    return f"{float(v):g}%"

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

def _parse_rationale(text: str) -> list[dict]:
    """Split rationale text into {heading, body} sections on # lines."""
    sections: list[dict] = []
    heading: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if heading is not None or lines:
                sections.append({"heading": heading, "body": "\n".join(lines).strip()})
            heading = line.lstrip("#").strip()
            lines = []
        else:
            lines.append(line)
    if heading is not None or lines:
        sections.append({"heading": heading, "body": "\n".join(lines).strip()})
    return sections

from urllib.parse import quote_plus

templates.env.filters["urlencode"]        = quote_plus
templates.env.filters["fmt_weight"]       = _fmt_weight
templates.env.filters["fmt_rpe"]          = _fmt_rpe
templates.env.filters["fmt_pct"]          = _fmt_pct
templates.env.filters["reps_list"]        = _reps_list
templates.env.filters["status_color"]     = _status_color
templates.env.filters["phase_color"]      = _phase_color
templates.env.filters["parse_rationale"]  = _parse_rationale

# Canonical option vocabularies as template globals so setup and profile can
# never drift apart again (WEB-M3). Values come from web/options.py.
from web.options import EQUIPMENT_OPTIONS, FAULT_OPTIONS, STRENGTH_LIMITER_OPTIONS

templates.env.globals["equipment_options"] = EQUIPMENT_OPTIONS
templates.env.globals["fault_options"]     = FAULT_OPTIONS
templates.env.globals["limiter_options"]   = STRENGTH_LIMITER_OPTIONS

# ── Error pages ───────────────────────────────────────────────
# HTMX requests get a small text fragment (a full page would be swapped into
# the target element); normal navigation gets the styled error page.
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

_ERROR_MESSAGES = {
    404: "That page doesn't exist — the bar might have been loaded somewhere else.",
    500: "Something went wrong on our end. The error has been logged.",
}


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code in _ERROR_MESSAGES:
        if request.headers.get("HX-Request"):
            return HTMLResponse(exc.detail or "Error", status_code=exc.status_code)
        return templates.TemplateResponse(request, "error.html", {
            "request": request,
            "status_code": exc.status_code,
            "message": exc.detail or _ERROR_MESSAGES[exc.status_code],
        }, status_code=exc.status_code)
    return HTMLResponse(exc.detail or "Error", status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error on {request.method} {request.url.path}: {exc}")
    if request.headers.get("HX-Request"):
        return HTMLResponse("Internal server error", status_code=500)
    return templates.TemplateResponse(request, "error.html", {
        "request": request,
        "status_code": 500,
        "message": _ERROR_MESSAGES[500],
    }, status_code=500)


# ── Favicon redirect (browsers that request /favicon.ico directly) ─────────
@app.get("/favicon.ico", include_in_schema=False)
async def favicon_redirect():
    return RedirectResponse("/static/favicon.svg", status_code=301)


# ── Routers ───────────────────────────────────────────────────
app.include_router(auth_router.router)
app.include_router(setup_router.router)
app.include_router(profile_router.router)
app.include_router(dashboard.router)
app.include_router(program.router)
app.include_router(log_session.router)
app.include_router(generate.router)
app.include_router(export_router.router)
app.include_router(history_router.router)
app.include_router(health_router.router)
app.include_router(admin_router.router)
