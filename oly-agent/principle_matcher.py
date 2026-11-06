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

Ranking (AUD-1): with a session ``query``, the kept rules are ordered by
``priority + PRINCIPLE_RELEVANCE_WEIGHT × overlap`` — overlap is the number of
distinct query terms (lowercased, stop-worded, crudely de-pluralised) found in
the principle's name, rationale and recommended/avoided exercise names — ties by
id, and at most ``MAX_PRINCIPLES_PER_CATEGORY`` of one category are taken. No
embeddings: it runs per session with no API call.
"""

import logging
import operator
import re

from shared.constants import (
    INJURY_PRINCIPLE_TERMS,
    MAX_PRINCIPLES_PER_CATEGORY,
    PRINCIPLE_RELEVANCE_WEIGHT,
    PROMPT_HIDDEN_RECOMMENDATION_KEYS,
    YOUTH_PRINCIPLE_AGE_BANDS,
    YOUTH_PRINCIPLE_NAME_PATTERNS,
)
from shared.exercise_mapping import COMP_LIFT_AVOID_NAMES
from shared.schema_enums import NOVICE_LEVELS

logger = logging.getLogger(__name__)

_YOUTH_NAME_RE = re.compile("|".join(YOUTH_PRINCIPLE_NAME_PATTERNS), re.I)

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
        # not condition keys — they gate injury / youth / novice-foundation
        # principles in select_principles
        "has_injuries": bool(athlete_context.injuries),
        "is_youth": getattr(athlete_context, "age_band", None) in YOUTH_PRINCIPLE_AGE_BANDS,
        "olympic_lifting_experience": bool(athlete_context.athlete.get("olympic_lifting_experience")),
    }


def is_injury_specific(principle: dict) -> bool:
    """True when the principle's name or rationale is about injury / rehab
    (INJURY_PRINCIPLE_TERMS) — rules the condition vocabulary can't gate."""
    text = f"{principle.get('principle_name') or ''} {principle.get('rationale') or ''}".lower()
    return any(term in text for term in INJURY_PRINCIPLE_TERMS)


def is_youth_specific(principle: dict) -> bool:
    """True when the principle's name is about youth / junior athletes
    (YOUTH_PRINCIPLE_NAME_PATTERNS) — the condition vocabulary has no age key."""
    return bool(_YOUTH_NAME_RE.search(principle.get("principle_name") or ""))


_EMPTY_VALUES = (None, "", [], {})


def prompt_recommendation(principle: dict):
    """The recommendation as the session prompt shows it: without
    PROMPT_HIDDEN_RECOMMENDATION_KEYS (DOG-2l). A non-dict recommendation is
    returned unchanged."""
    rec = principle.get("recommendation")
    if not isinstance(rec, dict):
        return rec
    return {k: v for k, v in rec.items() if k not in PROMPT_HIDDEN_RECOMMENDATION_KEYS}


def only_hidden_recommendation(principle: dict) -> bool:
    """True when every actionable value in the recommendation is under a
    PROMPT_HIDDEN_RECOMMENDATION_KEYS key, e.g. `{"total_reps_max": 10}` alone.
    Such a rule would reach the prompt with nothing to recommend, so it is
    not selected (DOG-2l). `plan._load_principles` has already dropped rules
    with nothing actionable at all."""
    rec = principle.get("recommendation")
    if not isinstance(rec, dict):
        return False
    hidden = any(rec.get(k) not in _EMPTY_VALUES for k in PROMPT_HIDDEN_RECOMMENDATION_KEYS)
    return hidden and all(v in _EMPTY_VALUES for v in prompt_recommendation(principle).values())


def is_novice_lift_ban(principle: dict) -> bool:
    """A "learn the basics first" rule: it recommends avoiding a competition
    lift and its condition is a novice gate (beginner / novice level or a
    training-age bound). Irrelevant to an athlete who has already trained the
    lifts; phase-, injury- or movement-gated bans don't qualify."""
    rec = principle.get("recommendation")
    avoid = rec.get("avoid_exercises") if isinstance(rec, dict) else None
    if not isinstance(avoid, list) or not any(str(v).strip().lower() in COMP_LIFT_AVOID_NAMES for v in avoid):
        return False
    cond = principle.get("condition")
    if not isinstance(cond, dict):
        return False
    levels = cond.get("athlete_level")
    levels = [levels] if isinstance(levels, str) else (levels or [])
    return "training_age_years" in cond or any(str(lv).lower() in NOVICE_LEVELS for lv in levels)


# Words from `retrieve.build_session_query`'s template and plain English that
# say nothing about which rule applies ("session", "phase", "athlete", "with").
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into",
    "is", "it", "of", "on", "or", "the", "to", "with", "without", "during", "no",
    "not", "this", "that", "their", "your", "should", "can", "will", "than",
    "session", "support", "phase", "athlete", "addressing", "focus", "work",
    "week", "training", "program", "exercise",
})
_WORD_RE = re.compile(r"[a-z][a-z'&-]*")


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def query_terms(text: str | None) -> set[str]:
    """Lowercased, stop-worded, de-pluralised terms of ``text`` (hyphens split)."""
    if not text:
        return set()
    terms = set()
    for raw in _WORD_RE.findall(text.lower().replace("_", " ")):
        for word in re.split(r"[-&']", raw):
            if len(word) > 2 and word not in _STOPWORDS:
                terms.add(_stem(word))
    return terms


def _principle_terms(p: dict) -> set[str]:
    rec = p.get("recommendation")
    names: list[str] = []
    if isinstance(rec, dict):
        for key in ("prefer_exercises", "avoid_exercises"):
            vals = rec.get(key)
            if isinstance(vals, list):
                names.extend(str(v) for v in vals)
    return query_terms(" ".join([p.get("principle_name") or "", p.get("rationale") or "", *names]))


def relevance_overlap(p: dict, terms: set[str]) -> int:
    """How many distinct session-query terms the principle mentions."""
    return len(terms & _principle_terms(p)) if terms else 0


def select_principles(
    candidates: list[dict],
    state: dict,
    limit: int | None = None,
    query: str | None = None,
) -> list[dict]:
    """Principles whose condition holds for ``state``, best first.

    Without ``query``: priority order (ties by id). With it: priority +
    PRINCIPLE_RELEVANCE_WEIGHT × overlap with the session query. Either way at
    most MAX_PRINCIPLES_PER_CATEGORY per category (rows without a category are
    uncapped), then ``limit``.
    """
    terms = query_terms(query)
    kept = [p for p in candidates
            if condition_matches(p.get("condition"), state) and not only_hidden_recommendation(p)]
    if state.get("has_injuries") is False:           # absent (older callers) → no filter
        kept = [p for p in kept if not is_injury_specific(p)]
    if state.get("is_youth") is False:               # unknown age counts as adult
        kept = [p for p in kept if not is_youth_specific(p)]
    if state.get("olympic_lifting_experience"):
        kept = [p for p in kept if not is_novice_lift_ban(p)]

    def score(p: dict) -> float:
        return (p.get("priority") or 0) + PRINCIPLE_RELEVANCE_WEIGHT * relevance_overlap(p, terms)

    kept.sort(key=lambda p: (-score(p), p.get("id") or 0))

    chosen: list[dict] = []
    per_category: dict[str, int] = {}
    for p in kept:
        cat = p.get("category")
        if cat:
            if per_category.get(cat, 0) >= MAX_PRINCIPLES_PER_CATEGORY:
                continue
            per_category[cat] = per_category.get(cat, 0) + 1
        chosen.append(p)
        if limit and len(chosen) >= limit:
            break
    return chosen
