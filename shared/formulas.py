# shared/formulas.py
"""Small numeric formulas shared across the agent pipeline.

Centralized so the pipeline can't drift between duplicate copies (e.g. the
0.5 kg rounding that lived in three modules, or the session-duration estimate
that lived in both orchestrator and validate).
"""

from shared.constants import (
    DEFAULT_REST_SECONDS,
    SECONDS_PER_SET,
    WEIGHT_ROUND_INCREMENT,
)


def round_kg(weight: float, increment: float = WEIGHT_ROUND_INCREMENT) -> float:
    """Round a load to the nearest plate increment (default 0.5 kg)."""
    return round(round(weight / increment) * increment, 1)


def estimate_session_minutes(exercises: list[dict]) -> float:
    """Rough working + rest duration for a session, in minutes.

    Each set costs SECONDS_PER_SET of work plus its rest (DEFAULT_REST_SECONDS
    when unspecified). Returns the raw float; callers floor/round as needed.
    """
    seconds = sum(
        (ex.get("sets") or 0) * (SECONDS_PER_SET + (ex.get("rest_seconds") or DEFAULT_REST_SECONDS))
        for ex in exercises
    )
    return seconds / 60
