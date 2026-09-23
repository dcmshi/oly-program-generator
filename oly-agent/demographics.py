# oly-agent/demographics.py
"""Athlete demographics for the pipeline (AUD-5).

The web UI collects biological sex, date of birth, bodyweight and weight
class; these helpers turn them into what ASSESS stores on AthleteContext, the
Athlete Profile line of the session prompt, and the session-query qualifier.
Pure functions — no DB, no web imports.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.constants import (
    AGE_BAND_MASTERS,
    AGE_BAND_QUERY_QUALIFIERS,
    AGE_BANDS,
    SEX_QUERY_QUALIFIERS,
)


def age_from_dob(date_of_birth, today: date) -> int | None:
    """Whole years between ``date_of_birth`` and ``today``; None when unknown
    or not a date, or when the date is in the future."""
    if not isinstance(date_of_birth, date):
        return None
    years = today.year - date_of_birth.year - (
        (today.month, today.day) < (date_of_birth.month, date_of_birth.day)
    )
    return years if years >= 0 else None


def age_band(age_years: int | None) -> str | None:
    """youth / junior / senior / masters per shared.constants.AGE_BANDS."""
    if age_years is None:
        return None
    for upper, band in AGE_BANDS:
        if age_years < upper:
            return band
    return AGE_BAND_MASTERS


def _as_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def demographics_line(athlete_context) -> str:
    """One concise prompt line, e.g. ``Demographics: female, age 38 (masters),
    bodyweight 63.5 kg, 64 kg class``. Unknown fields are left out; an athlete
    with none of them gets an empty string (the line is omitted)."""
    parts = []
    sex = getattr(athlete_context, "biological_sex", None)
    if sex:
        parts.append(str(sex))
    age = getattr(athlete_context, "age_years", None)
    if age is not None:
        band = getattr(athlete_context, "age_band", None) or age_band(age)
        parts.append(f"age {age} ({band})")
    bw = _as_float(getattr(athlete_context, "bodyweight_kg", None))
    if bw is not None:
        parts.append(f"bodyweight {bw:g} kg")
    wc = getattr(athlete_context, "weight_class", None)
    if wc:
        parts.append(f"{wc} kg class")
    return ("Demographics: " + ", ".join(parts)) if parts else ""


def query_qualifiers(athlete_context) -> list[str]:
    """Session-query phrases that change what should be retrieved: female,
    youth and masters athletes. Everyone else gets [] so their query is
    byte-identical to the pre-AUD-5 builder."""
    out = []
    sex = getattr(athlete_context, "biological_sex", None)
    if sex in SEX_QUERY_QUALIFIERS:
        out.append(SEX_QUERY_QUALIFIERS[sex])
    band = getattr(athlete_context, "age_band", None)
    if band in AGE_BAND_QUERY_QUALIFIERS:
        out.append(AGE_BAND_QUERY_QUALIFIERS[band])
    return out
