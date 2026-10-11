"""Coach constraints (COACH-1): a program generated to a coach's brief.

The coach fills a form on the athlete's account — sessions per week, phase,
lift emphasis, strength limiters, technical faults, injuries, exercises to
avoid, a meet date — plus a free-text note. Every field is optional and
overrides the athlete's profile for **this program only**: the constraints
become a copy of the AthleteContext right after ASSESS, so PLAN, RETRIEVE,
the session prompt and the validator honour them through the fields they
already read, and the profile itself is never written. The note has no
structured home; it reaches the session prompt as a `## Coach Instructions`
section (program-level, above the static/dynamic marker) and the EXPLAIN
prompt. The constraints are stored on the program as
`generation_params.coach`.

Pure module: no DB, no web imports.
"""

from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass
from datetime import date

from models import AthleteContext

# Phases a coach may force. The others in the training_phase enum (transmutation,
# competition, deload, transition) have no phase profile to build weeks from.
COACH_PHASES: tuple[str, ...] = ("general_prep", "accumulation", "intensification", "realization")
LIFT_EMPHASES: tuple[str, ...] = ("balanced", "snatch_biased", "cj_biased")
COACH_NOTE_MAX_CHARS = 1500
SESSIONS_PER_WEEK_RANGE = (1, 7)


class CoachConstraintError(ValueError):
    """A constraint outside its vocabulary or range (shown to the coach)."""


@dataclass(frozen=True)
class CoachConstraints:
    """None = keep the profile's value; an empty list = explicitly none."""
    phase: str | None = None
    sessions_per_week: int | None = None
    lift_emphasis: str | None = None
    strength_limiters: list[str] | None = None
    technical_faults: list[str] | None = None
    injuries: list[str] | None = None
    avoid_exercises: list[str] | None = None
    competition_date: date | None = None
    note: str = ""

    def __post_init__(self):
        if self.phase is not None and self.phase not in COACH_PHASES:
            raise CoachConstraintError(f"Phase must be one of {', '.join(COACH_PHASES)}.")
        if self.lift_emphasis is not None and self.lift_emphasis not in LIFT_EMPHASES:
            raise CoachConstraintError(f"Lift emphasis must be one of {', '.join(LIFT_EMPHASES)}.")
        lo, hi = SESSIONS_PER_WEEK_RANGE
        if self.sessions_per_week is not None and not lo <= self.sessions_per_week <= hi:
            raise CoachConstraintError(f"Sessions per week must be between {lo} and {hi}.")
        if len(self.note) > COACH_NOTE_MAX_CHARS:
            raise CoachConstraintError(f"The coach note is limited to {COACH_NOTE_MAX_CHARS} characters.")

    def is_empty(self) -> bool:
        return not self.overrides() and not self.note.strip()

    def overrides(self) -> dict:
        """The structured fields that override the profile (None ones dropped)."""
        out = {}
        for f in ("phase", "sessions_per_week", "lift_emphasis", "strength_limiters",
                  "technical_faults", "injuries", "avoid_exercises", "competition_date"):
            value = getattr(self, f)
            if value is not None:
                out[f] = value
        return out

    def to_json(self) -> dict:
        """The record stored in generation_params.coach (and the ARQ job payload)."""
        out = self.overrides()
        if "competition_date" in out:
            out["competition_date"] = out["competition_date"].isoformat()
        if self.note.strip():
            out["note"] = self.note.strip()
        return out

    @classmethod
    def from_json(cls, data: dict | None) -> CoachConstraints:
        data = dict(data or {})
        if data.get("competition_date"):
            data["competition_date"] = date.fromisoformat(str(data["competition_date"]))
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise CoachConstraintError(f"Unknown coach constraint(s): {', '.join(sorted(unknown))}.")
        return cls(**data)


def apply_constraints(ctx: AthleteContext, c: CoachConstraints | None,
                      today: date | None = None) -> AthleteContext:
    """A copy of `ctx` with the coach's overrides in the places every later step
    reads them — the `athlete` row dict (lift_emphasis, strength_limiters,
    exercise_preferences.avoid, sessions_per_week, injuries, technical_faults)
    and the context fields (sessions_per_week, technical_faults, injuries,
    weeks_to_competition, active_goal.competition_date). `ctx` is not modified.
    """
    if c is None or c.is_empty():
        return ctx
    athlete = copy.deepcopy(ctx.athlete)
    changes: dict = {"coach_note": c.note.strip() or None, "coach_constraints": c.to_json()}

    if c.sessions_per_week is not None:
        athlete["sessions_per_week"] = c.sessions_per_week
        changes["sessions_per_week"] = c.sessions_per_week
    if c.lift_emphasis is not None:
        athlete["lift_emphasis"] = c.lift_emphasis
    if c.strength_limiters is not None:
        athlete["strength_limiters"] = list(c.strength_limiters)
    if c.technical_faults is not None:
        athlete["technical_faults"] = list(c.technical_faults)
        changes["technical_faults"] = list(c.technical_faults)
    if c.injuries is not None:
        athlete["injuries"] = list(c.injuries)
        changes["injuries"] = list(c.injuries)
    if c.avoid_exercises is not None:
        prefs = dict(athlete.get("exercise_preferences") or {})
        prefs["avoid"] = list(c.avoid_exercises)
        athlete["exercise_preferences"] = prefs
    if c.competition_date is not None:
        today = today or date.today()
        days = (c.competition_date - today).days
        if days < 0:
            raise CoachConstraintError("The meet date is in the past.")
        changes["weeks_to_competition"] = days // 7
        changes["active_goal"] = {**(ctx.active_goal or {}), "competition_date": c.competition_date}

    changes["athlete"] = athlete
    return dataclasses.replace(ctx, **changes)


def coach_prompt_section(note: str | None, constraints: dict | None) -> str:
    """`## Coach Instructions` for the session prompt; "" when there is nothing
    to say. Program-level, so it belongs above PROMPT_STATIC_DYNAMIC_MARKER."""
    if not note and not constraints:
        return ""
    lines = ["## Coach Instructions",
             "This program was requested by the athlete's coach. The structured choices below are already "
             "applied to the profile, plan and avoid list above; follow the coach's note as a hard instruction "
             "wherever it does not conflict with the safety rules."]
    shown = {k: v for k, v in (constraints or {}).items() if k != "note"}
    if shown:
        lines.append("Coach choices: " + "; ".join(
            f"{k.replace('_', ' ')}: {', '.join(v) if isinstance(v, list) else v}" for k, v in shown.items()))
    if note:
        lines.append(f"Coach note: {note}")
    return "\n".join(lines)
