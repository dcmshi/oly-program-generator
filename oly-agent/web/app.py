# web/app.py
"""FastAPI application factory."""

import sys
from pathlib import Path

# Make shared/ and oly-agent/ importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from web.routers import dashboard, program, log_session, generate

app = FastAPI(title="Oly Agent")

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
app.include_router(dashboard.router)
app.include_router(program.router)
app.include_router(log_session.router)
app.include_router(generate.router)
