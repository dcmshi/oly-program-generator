# oly-agent/principle_matcher.py
"""
Evaluate `programming_principles.condition` against the state of ONE session (RAG-H3).

The extractor (`oly-ingestion/processors/principle_extractor.py`) records WHEN a
rule applies as a JSONB condition with these keys — string, list, or comparison
object (`{"lte": 2}`, `{"between": [3, 5]}`):

    phase, athlete_level, weeks_out_from_competition, training_age_years,
    week_of_block, movement_family, recent_make_rate, rpe_average_last_week

`plan._load_principles` only ever evaluated `phase` (string equality, which also
dropped array-valued phases) and `athlete_level`. On the live corpus 73 of 161
principles carry a condition nobody checked — `movement_family` on 60 of them —
so snatch-only rules were shown as "Active Principles" on clean & jerk days and
enforced by `validate.py` Check 5, and a "≤ 2 weeks out" taper rule was served in
week 1 of a 12-week-out block.

`plan.py` now loads a phase/level SUPERSET from SQL; the orchestrator calls
`select_principles(candidates, build_session_state(...))` for every session, so
each prompt and each validation sees only the rules whose every condition holds
for that week and that day's primary movement.

Semantics:
- a condition on a fact the state does not know (`None`) is NOT satisfied — a
  make-rate-gated rule is not enforced on a cold start, a weeks-out rule not when
  no competition is set;
- keys outside the extraction schema (schema drift, RAG-L9) are ignored so those
  rules keep today's behaviour rather than vanishing;
- `None`/missing condition → always applies.
"""

import logging
import operator

logger = logging.getLogger(__name__)

KNOWN_CONDITION_KEYS: frozenset[str] = frozenset({
    "phase", "athlete_level", "weeks_out_from_competition", "training_age_years",
    "week_of_block", "movement_family", "recent_make_rate", "rpe_average_last_week",
})

_OPS = {
    "lte": operator.le,
    "gte": operator.ge,
    "lt": operator.lt,
    "gt": operator.gt,
    "eq": operator.eq,
}


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compare(expected, actual) -> bool:
    """Does ``actual`` satisfy ``expected``?

    expected: scalar (equality; numeric when both parse), list (membership),
    or a dict of comparison operators (all must hold). ``actual is None`` → False.
    """
    if actual is None:
        return False

    if isinstance(expected, dict):
        a = _as_float(actual)
        for op, val in expected.items():
            if op == "between":
                if not (isinstance(val, list | tuple) and len(val) == 2):
                    return False
                lo, hi = _as_float(val[0]), _as_float(val[1])
                if a is None or lo is None or hi is None or not (lo <= a <= hi):
                    return False
            elif op in _OPS:
                v = _as_float(val)
                if a is None or v is None or not _OPS[op](a, v):
                    return False
            else:
                logger.debug(f"Unknown condition operator {op!r} — treating as unsatisfied")
                return False
        return True

    if isinstance(expected, list | tuple | set):
        return str(actual).lower() in {str(x).lower() for x in expected}

    a, e = _as_float(actual), _as_float(expected)
    if a is not None and e is not None:
        return a == e
    return str(actual).lower() == str(expected).lower()


def condition_matches(condition, state: dict) -> bool:
    """True when every known key in ``condition`` is satisfied by ``state``."""
    if not condition or not isinstance(condition, dict):
        return True
    for key, expected in condition.items():
        if key not in KNOWN_CONDITION_KEYS:
            continue  # extraction drift (RAG-L9) — cannot be evaluated, don't drop the rule
        if expected is None:
            continue
        if not compare(expected, state.get(key)):
            return False
    return True


def build_session_state(athlete_context, plan, week_number: int, session_template) -> dict:
    """The facts a condition can reference, for one session of one week."""
    weeks_out = athlete_context.weeks_to_competition
    if weeks_out is not None:
        weeks_out = max(0, weeks_out - (week_number - 1))

    outcome = (athlete_context.previous_program or {}).get("outcome_summary") or {}
    make_rate = outcome.get("avg_make_rate") if isinstance(outcome, dict) else None

    rpes = [
        float(e["rpe"]) for e in (athlete_context.recent_logs or [])
        if isinstance(e, dict) and e.get("rpe") is not None
    ]

    return {
        "phase": plan.phase,
        "athlete_level": athlete_context.level,
        "weeks_out_from_competition": weeks_out,
        "training_age_years": athlete_context.athlete.get("training_age_years"),
        "week_of_block": week_number,
        "movement_family": session_template.primary_movement,
        "recent_make_rate": make_rate,
        "rpe_average_last_week": (sum(rpes) / len(rpes)) if rpes else None,
    }


def select_principles(candidates: list[dict], state: dict, limit: int | None = None) -> list[dict]:
    """Principles whose condition holds for ``state``, highest priority first."""
    kept = [p for p in candidates if condition_matches(p.get("condition"), state)]
    kept.sort(key=lambda p: -(p.get("priority") or 0))
    return kept[:limit] if limit else kept
