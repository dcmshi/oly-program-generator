# web/coach_form.py
"""Coach-mode form → coach constraints (COACH-1).

The form is pre-filled from the athlete's profile, so a submitted field that
still equals the profile value is not a constraint: only what the coach
changed (plus the note) becomes `generation_params.coach`, which keeps the
stored brief readable and leaves an untouched form a normal run.
"""

from datetime import date

from coach import (
    COACH_NOTE_MAX_CHARS,
    COACH_PHASES,
    INTENSITY_CAP_RANGE,
    LIFT_EMPHASES,
    SESSIONS_PER_WEEK_RANGE,
    CoachConstraintError,
    CoachConstraints,
)
from web.options import FAULT_OPTIONS, STRENGTH_LIMITER_OPTIONS, parse_list_text

_FAULTS = {v for _, v in FAULT_OPTIONS}
_LIMITERS = {v for _, v in STRENGTH_LIMITER_OPTIONS}


def profile_defaults(athlete: dict | None, goal: dict | None) -> dict:
    """The form's pre-fill: the athlete's current values for every coach field."""
    athlete = athlete or {}
    prefs = athlete.get("exercise_preferences") or {}
    return {
        "sessions_per_week": athlete.get("sessions_per_week") or 4,
        "lift_emphasis": athlete.get("lift_emphasis") or "balanced",
        "strength_limiters": list(athlete.get("strength_limiters") or []),
        "technical_faults": list(athlete.get("technical_faults") or []),
        "injuries": list(athlete.get("injuries") or []),
        "avoid_exercises": list(prefs.get("avoid") or []),
        "competition_date": (goal or {}).get("competition_date"),
        "intensity_cap": None,   # no profile value — blank means the phase's own ceilings
    }


def _same_list(a: list[str], b: list[str]) -> bool:
    return sorted(x.lower() for x in a) == sorted(x.lower() for x in b)


def parse_coach_form(form, profile: dict, today: date) -> tuple[dict | None, list[str]]:
    """(constraints JSON or None for "nothing changed", errors). `form` is a
    Starlette FormData (getlist for the checkbox groups)."""
    errors: list[str] = []
    fields: dict = {}

    phase = (form.get("coach_phase") or "").strip()
    if phase:
        if phase not in COACH_PHASES:
            errors.append("Unknown phase.")
        else:
            fields["phase"] = phase

    raw_spw = (form.get("coach_sessions_per_week") or "").strip()
    if raw_spw:
        lo, hi = SESSIONS_PER_WEEK_RANGE
        try:
            spw = int(raw_spw)
        except ValueError:
            spw = None
        if spw is None or not lo <= spw <= hi:
            errors.append(f"Sessions per week must be a whole number between {lo} and {hi}.")
        elif spw != profile["sessions_per_week"]:
            fields["sessions_per_week"] = spw

    emphasis = (form.get("coach_lift_emphasis") or "").strip()
    if emphasis:
        if emphasis not in LIFT_EMPHASES:
            errors.append("Unknown lift emphasis.")
        elif emphasis != profile["lift_emphasis"]:
            fields["lift_emphasis"] = emphasis

    limiters = form.getlist("coach_strength_limiters")
    if set(limiters) - _LIMITERS:
        errors.append("Unknown strength limiter.")
    elif not _same_list(limiters, profile["strength_limiters"]):
        fields["strength_limiters"] = limiters

    faults = form.getlist("coach_technical_faults")
    if set(faults) - _FAULTS:
        errors.append("Unknown technical fault.")
    elif not _same_list(faults, profile["technical_faults"]):
        fields["technical_faults"] = faults

    for key in ("injuries", "avoid_exercises"):
        values = parse_list_text(form.get(f"coach_{key}"))
        if not _same_list(values, profile[key]):
            fields[key] = values

    raw_date = (form.get("coach_competition_date") or "").strip()
    if raw_date:
        try:
            meet = date.fromisoformat(raw_date)
        except ValueError:
            errors.append("Meet date must be a date (YYYY-MM-DD).")
        else:
            if meet < today:
                errors.append("The meet date is in the past.")
            elif meet != profile["competition_date"]:
                fields["competition_date"] = meet

    raw_cap = (form.get("coach_intensity_cap") or "").strip().rstrip("%").strip()
    if raw_cap:
        lo, hi = INTENSITY_CAP_RANGE
        try:
            cap = int(raw_cap)
        except ValueError:
            cap = None
        if cap is None or not lo <= cap <= hi:
            errors.append(f"Intensity cap must be a whole percentage between {lo} and {hi}.")
        else:
            fields["intensity_cap"] = cap

    note = (form.get("coach_note") or "").strip()
    if len(note) > COACH_NOTE_MAX_CHARS:
        errors.append(f"The coach note is limited to {COACH_NOTE_MAX_CHARS} characters.")
    elif note:
        fields["note"] = note

    if errors:
        return None, errors
    try:
        constraints = CoachConstraints(**fields)
    except CoachConstraintError as e:
        return None, [str(e)]
    return (None if constraints.is_empty() else constraints.to_json()), []
