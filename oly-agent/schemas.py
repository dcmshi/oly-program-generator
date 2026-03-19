# oly-agent/schemas.py
"""Pydantic models for JSONB columns.

These models validate data at the DB boundary — at write time (model_dump_json)
and read time (model_validate). They are the canonical schema for structured
JSONB fields that feed the planning and generation pipeline.

Most critical: OutcomeSummary — silently corrupted data here causes future
programs to be planned with wrong phase, volume, or intensity targets.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TrendLiteral = Literal["ascending", "stable", "descending"]


class PhaseCheck(BaseModel):
    metric: str
    value: float
    display: str
    threshold: str
    passed: bool


class PhaseVerdict(BaseModel):
    prev_phase: str | None = None
    next_phase: str
    prev_label: str
    next_label: str
    advanced: bool
    reason: str
    checks: list[PhaseCheck] = Field(default_factory=list)
    adjustments: list[str] = Field(default_factory=list)


class OutcomeSummary(BaseModel):
    """Schema for generated_programs.outcome_summary JSONB column.

    Written by feedback.save_outcome(), read by plan._apply_outcome_adjustments()
    and generate.build_session_prompt().

    Defaults are "permissive" (assume good performance when a field is absent)
    to match the existing .get() fallback behaviour in plan.py — avoids spurious
    volume/intensity cuts on old records that pre-date this schema.
    """

    adherence_pct: float = 100.0
    avg_rpe_deviation: float = 0.0
    avg_make_rate: float = 1.0
    make_rate_by_lift: dict[str, float] = Field(default_factory=dict)
    avg_weekly_reps: float = 0.0
    rpe_trend: TrendLiteral = "stable"
    make_rate_trend: TrendLiteral = "stable"
    maxes_delta: dict[str, float] = Field(default_factory=dict)
    athlete_feedback: str | None = None
    phase_verdict: PhaseVerdict | None = None
