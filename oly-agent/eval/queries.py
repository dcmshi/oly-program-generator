# oly-agent/eval/queries.py
"""
The evaluation query set (RAG-M6).

Two families:

* **production-shaped** — the exact strings `retrieve.py` sends, built with its
  own builders (`build_session_query`, `build_fault_query`, `build_limiter_query`)
  from the UI vocabularies (`web/options.py`) for an intermediate athlete: one
  session query per (phase × session template), one fault query per
  `FAULT_OPTIONS` value, one limiter query per `STRENGTH_LIMITER_OPTIONS` value.
  Each carries the same `preferred_chunk_types` production uses, so an eval run
  measures what generation actually receives (production parity — the legacy
  report searched unfiltered).

* **legacy** — the 22 free-form coach questions from the design doc
  (`oly-ingestion/eval_queries.py`), scored under the session preference.
"""

import sys
from pathlib import Path

_AGENT = Path(__file__).resolve().parent.parent
_REPO = _AGENT.parent
for p in (str(_REPO), str(_AGENT), str(_REPO / "oly-ingestion")):
    if p not in sys.path:
        sys.path.insert(0, p)

from models import AthleteContext, ProgramPlan, SessionTemplate, WeekTarget
from phase_profiles import PHASE_PROFILES, build_weekly_targets
from retrieve import (
    FAULT_PREFERRED_TYPES,
    SESSION_PREFERRED_TYPES,
    build_fault_query,
    build_limiter_query,
    build_session_query,
)
from session_templates import get_session_templates

EVAL_LEVEL = "intermediate"
EVAL_SESSIONS_PER_WEEK = 4


def _ctx(level: str = EVAL_LEVEL, faults=None, limiters=None) -> AthleteContext:
    return AthleteContext(
        athlete={"name": "eval", "level": level, "lift_emphasis": "balanced",
                 "strength_limiters": limiters or []},
        level=level, maxes={}, active_goal=None, previous_program=None, recent_logs=[],
        technical_faults=faults or [], injuries=[], sessions_per_week=EVAL_SESSIONS_PER_WEEK,
        weeks_to_competition=None,
    )


def _plan(phase: str) -> tuple[ProgramPlan, WeekTarget]:
    raw = build_weekly_targets(phase, 4, EVAL_LEVEL)
    templates = [
        SessionTemplate(day_number=t["day_number"], label=t["label"], primary_movement=t["primary_movement"],
                        secondary_movements=t["secondary_movements"], session_volume_share=t["session_volume_share"])
        for t in get_session_templates(EVAL_SESSIONS_PER_WEEK)
    ]
    first = raw[0]
    wt = WeekTarget(week_number=1, volume_modifier=first["volume_modifier"], intensity_floor=first["intensity_floor"],
                    intensity_ceiling=first["intensity_ceiling"], total_competition_lift_reps=20,
                    reps_per_set_range=first["reps_per_set_range"], is_deload=first["is_deload"])
    plan = ProgramPlan(phase=phase, duration_weeks=4, sessions_per_week=EVAL_SESSIONS_PER_WEEK, deload_week=4,
                       weekly_targets=[wt], session_templates=templates, active_principles=[], supporting_chunks=[])
    return plan, wt


def production_queries() -> list[dict]:
    """[{id, kind, query, preferred_chunk_types, expected_topics}] — the strings production sends."""
    from web.options import FAULT_OPTIONS, STRENGTH_LIMITER_OPTIONS  # UI vocabularies (dev tool; not pipeline code)

    out: list[dict] = []
    for phase in PHASE_PROFILES:
        plan, wt = _plan(phase)
        for tmpl in plan.session_templates:
            out.append({
                "id": f"session:{phase}:d{tmpl.day_number}:{tmpl.primary_movement}",
                "kind": "session",
                "query": build_session_query(_ctx(), plan, tmpl, wt),
                "preferred_chunk_types": list(SESSION_PREFERRED_TYPES),
                "expected_topics": [f"{tmpl.primary_movement}_programming"],
            })
    for _label, fault in FAULT_OPTIONS:
        out.append({
            "id": f"fault:{fault}",
            "kind": "fault",
            "query": build_fault_query(fault, EVAL_LEVEL),
            "preferred_chunk_types": list(FAULT_PREFERRED_TYPES),
            "expected_topics": ["fault_correction"],
        })
    for _label, limiter in STRENGTH_LIMITER_OPTIONS:
        out.append({
            "id": f"limiter:{limiter}",
            "kind": "limiter",
            "query": build_limiter_query(limiter, EVAL_LEVEL),
            "preferred_chunk_types": list(SESSION_PREFERRED_TYPES),
            "expected_topics": ["exercise_selection_rationale"],
        })
    return out


def legacy_queries() -> list[dict]:
    from eval_queries import RETRIEVAL_EVAL_QUERIES

    return [
        {
            "id": f"legacy:{i:02d}",
            "kind": "legacy",
            "query": q["query"],
            "preferred_chunk_types": list(SESSION_PREFERRED_TYPES),
            "expected_topics": list(q.get("expected_topics", [])),
            "note": q.get("note", ""),
        }
        for i, q in enumerate(RETRIEVAL_EVAL_QUERIES, 1)
    ]


def all_queries() -> list[dict]:
    queries = production_queries() + legacy_queries()
    ids = [q["id"] for q in queries]
    assert len(ids) == len(set(ids)), "duplicate query ids"
    return queries
