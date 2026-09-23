# processors/principle_extractor.py
"""
Uses an LLM to extract structured programming principles from prose text.

This is the most valuable part of the pipeline — converting sentences like
"During the final two weeks before competition, volume should be reduced
by 40-60% while maintaining intensity above 90%" into queryable rules.
"""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # repo root for shared.*
from shared.constants import PRINCIPLE_EMPTY_RETRIES, PRINCIPLE_EMPTY_RETRY_MIN_CHARS
from shared.llm import (
    BatchRequestFailed,
    create_llm_client,
    create_message_growing,
    json_schema_kwargs,
    message_text,
    parse_llm_json,
    run_message_batch,
    thinking_kwargs,
)
from shared.schema_enums import (
    ATHLETE_LEVELS,
    MOVEMENT_FAMILIES,
    PRINCIPLE_CATEGORIES,
    RULE_TYPES,
    TRAINING_PHASES,
)

logger = logging.getLogger(__name__)

# Large sections are scanned in overlapping windows so principles past the first
# window aren't silently dropped (I-M8). EPUB chapters routinely reach 50k+ chars.
_PRINCIPLE_WINDOW = 8000
_PRINCIPLE_OVERLAP = 500


@dataclass
class ExtractedPrinciple:
    principle_name: str
    category: str
    rule_type: str
    condition: dict
    recommendation: dict
    rationale: str
    priority: int


EXTRACTION_PROMPT = """You are an expert Olympic weightlifting coach analyzing programming literature.

Extract structured programming principles from the following text. Each principle should be a concrete, actionable rule that a program generator could follow.

For each principle found, provide:
- principle_name: Short descriptive name
- category: One of [volume, intensity, frequency, exercise_selection, periodization, peaking, recovery, technique, load_progression, deload]
- rule_type: One of [hard_constraint, guideline, heuristic]
  - hard_constraint: Violating this would be dangerous or clearly counterproductive
  - guideline: Strongly recommended but situationally flexible
  - heuristic: Rules of thumb, useful defaults
- condition: JSON object describing WHEN this applies. Use these fields (all optional):
    - "phase": training phase string or array, e.g. "intensification" or ["intensification", "realization"]
    - "weeks_out_from_competition": comparison, e.g. {{"op": "lte", "values": [2]}}
    - "athlete_level": array, e.g. ["intermediate", "advanced"]
    - "training_age_years": comparison, e.g. {{"op": "gte", "values": [2]}}
    - "week_of_block": comparison, e.g. {{"op": "gte", "values": [3]}}
    - "movement_family": string, e.g. "snatch"
    - "recent_make_rate": comparison, e.g. {{"op": "lt", "values": [0.7]}}
    - "rpe_average_last_week": comparison, e.g. {{"op": "gte", "values": [9.0]}}
    A comparison is {{"op": <lte|gte|lt|gt|eq|between>, "values": [<number>]}} — "between" takes two values [low, high].
- recommendation: JSON object describing WHAT to do. Use these fields (all optional):
    - "volume_modifier": float (e.g. 0.6 means reduce to 60%)
    - "total_reps_max": int (hard cap on total reps)
    - "intensity_floor": int (minimum % of 1RM)
    - "intensity_ceiling": int (maximum % of 1RM)
    - "sessions_per_week_max": int
    - "competition_lift_frequency": int or "every_session"
    - "prefer_exercises": array of exercise names
    - "avoid_exercises": array of exercise names
    - "rest_between_sets_min": int (seconds)
    - "include_deload_week": boolean
    - "deload_frequency_weeks": int
    - "competition_lifts_first": boolean
- rationale: Brief explanation of WHY this rule exists
- priority: 1-10 (10 = most critical, use 10 only for safety constraints)

Respond with a JSON object {{"principles": [...]}}. If no clear principles are found, return {{"principles": []}}.

TEXT TO ANALYZE:
{text}

SOURCE: {source}

Respond ONLY with valid JSON, no other text."""


# The condition keys EXTRACTION_PROMPT defines — the only ones the agent's
# principle_matcher can evaluate. The model occasionally invents others
# (athlete_characteristics, exercise_type, delay_minutes, training_focus were
# found in the corpus); those are dropped at parse time so a rule never carries
# a condition nobody can check (RAG-L9). test_principle_schema asserts this set
# equals oly-agent/principle_matcher.KNOWN_CONDITION_KEYS.
CONDITION_KEYS: frozenset[str] = frozenset({
    "phase", "weeks_out_from_competition", "athlete_level", "training_age_years",
    "week_of_block", "movement_family", "recent_make_rate", "rpe_average_last_week",
})


# ── Output schema (STRUCT-1) ──────────────────────────────────────────────
# Sent as `output_config.format`, so the reply is guaranteed to parse and to
# use only these keys/values: conditions can't carry an invented key or an
# athlete level the matcher can't evaluate, recommendations can't hold
# `null`, and `category` can't be a value the DB enum rejects.
# A comparison is `{"op": …, "values": [...]}` with both keys required rather
# than the matcher's `{"lte": 2}` form: the API caps a schema at 24 optional
# properties (a 400 above that), and six optional operators × five comparison
# keys alone would be 30. `normalize_comparisons` converts back at parse time.
COMPARISON_OPS: tuple[str, ...] = ("lte", "gte", "lt", "gt", "eq", "between")
_COMPARISON_KEYS: frozenset[str] = frozenset({
    "weeks_out_from_competition", "training_age_years", "week_of_block",
    "recent_make_rate", "rpe_average_last_week",
})
_COMPARISON = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": list(COMPARISON_OPS)},
        "values": {"type": "array", "items": {"type": "number"}},
    },
    "required": ["op", "values"],
    "additionalProperties": False,
}
_PHASE = {"type": "string", "enum": list(TRAINING_PHASES)}
_LEVEL = {"type": "string", "enum": list(ATHLETE_LEVELS)}
_STRINGS = {"type": "array", "items": {"type": "string"}}
PRINCIPLE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "principles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "principle_name": {"type": "string"},
                    "category": {"type": "string", "enum": list(PRINCIPLE_CATEGORIES)},
                    "rule_type": {"type": "string", "enum": list(RULE_TYPES)},
                    "condition": {
                        "type": "object",
                        "properties": {
                            "phase": {"anyOf": [_PHASE, {"type": "array", "items": _PHASE}]},
                            "weeks_out_from_competition": _COMPARISON,
                            "athlete_level": {"type": "array", "items": _LEVEL},
                            "training_age_years": _COMPARISON,
                            "week_of_block": _COMPARISON,
                            "movement_family": {"type": "string", "enum": list(MOVEMENT_FAMILIES)},
                            "recent_make_rate": _COMPARISON,
                            "rpe_average_last_week": _COMPARISON,
                        },
                        "additionalProperties": False,
                    },
                    "recommendation": {
                        "type": "object",
                        "properties": {
                            "volume_modifier": {"type": "number"},
                            "total_reps_max": {"type": "integer"},
                            "intensity_floor": {"type": "integer"},
                            "intensity_ceiling": {"type": "integer"},
                            "sessions_per_week_max": {"type": "integer"},
                            "competition_lift_frequency": {
                                "anyOf": [{"type": "integer"}, {"type": "string", "const": "every_session"}],
                            },
                            "prefer_exercises": _STRINGS,
                            "avoid_exercises": _STRINGS,
                            "rest_between_sets_min": {"type": "integer"},
                            "include_deload_week": {"type": "boolean"},
                            "deload_frequency_weeks": {"type": "integer"},
                            "competition_lifts_first": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                    "rationale": {"type": "string"},
                    "priority": {"type": "integer", "enum": list(range(1, 11))},
                },
                "required": ["principle_name", "category", "rule_type", "condition",
                             "recommendation", "rationale", "priority"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["principles"],
    "additionalProperties": False,
}
assert set(PRINCIPLE_SCHEMA["properties"]["principles"]["items"]["properties"]["condition"]["properties"]) == CONDITION_KEYS


def normalize_comparisons(condition: dict) -> dict:
    """`{"op": "lte", "values": [2]}` → `{"lte": 2}`, `{"op": "between",
    "values": [3, 5]}` → `{"between": [3, 5]}` — the form principle_matcher
    evaluates. Pre-schema operator dicts pass through unchanged; a malformed
    comparison is dropped so the rule stays unconditional on that key."""
    out = {}
    for key, value in condition.items():
        if key in _COMPARISON_KEYS and isinstance(value, dict) and "op" in value:
            op, values = value.get("op"), value.get("values")
            if not isinstance(values, list) or op not in COMPARISON_OPS:
                logger.warning(f"Dropping malformed comparison {key}={value!r}")
                continue
            if op == "between":
                if len(values) != 2:
                    logger.warning(f"Dropping malformed comparison {key}={value!r}")
                    continue
                out[key] = {"between": values[:2]}
            elif values:
                out[key] = {op: values[0]}
            else:
                logger.warning(f"Dropping malformed comparison {key}={value!r}")
            continue
        out[key] = value
    return out


def sanitize_condition(condition) -> dict:
    """Keep only schema condition keys and normalise comparisons; None /
    non-dict → {} (unconditional)."""
    if not isinstance(condition, dict):
        return {}
    dropped = sorted(set(condition) - CONDITION_KEYS)
    if dropped:
        logger.warning(f"Dropping unknown principle condition key(s): {dropped}")
    return normalize_comparisons({k: v for k, v in condition.items() if k in CONDITION_KEYS})


class PrincipleExtractor:
    def __init__(self, settings):
        self.settings = settings
        self._client = None  # lazy-initialized when first needed

    def _get_client(self):
        """Lazy-init the LLM client (the provider's key must be in .env)."""
        if self._client is None:
            self._client = create_llm_client(self.settings)
        return self._client

    @staticmethod
    def _windows(text: str) -> list[str]:
        """Split text into overlapping windows so nothing past the first window
        is silently dropped (I-M8)."""
        if len(text) <= _PRINCIPLE_WINDOW:
            return [text]
        step = _PRINCIPLE_WINDOW - _PRINCIPLE_OVERLAP
        return [text[i:i + _PRINCIPLE_WINDOW] for i in range(0, len(text), step)]

    def extract(self, text: str, source_title: str, source_id: int) -> list[ExtractedPrinciple]:
        """Extract structured principles from prose text using LLM.

        Large sections are scanned window-by-window and de-duplicated by
        principle_name, rather than truncating to the first 8k chars. The LLM
        client is initialized lazily — no API key required until called.
        """
        windows = self._windows(text)
        if len(windows) > 1:
            logger.info(
                f"Principle extraction: '{source_title}' is {len(text):,} chars — "
                f"scanning {len(windows)} windows"
            )

        principles: list[ExtractedPrinciple] = []
        seen: set[str] = set()
        for window in windows:
            for p in self._extract_window(window, source_title):
                if p.principle_name not in seen:
                    seen.add(p.principle_name)
                    principles.append(p)
        return principles

    def extract_batch(self, items: list[tuple[str, str, str]]) -> dict[str, list[ExtractedPrinciple]]:
        """`extract()` for many texts through the Message Batches API (COST-1).

        `items` is `[(key, text, source_title), …]`; returns `{key: principles}`
        with the same windowing and per-text de-duplication as `extract()`. A
        window whose request failed is logged and contributes nothing, exactly
        as a failed synchronous call would.
        """
        requests: dict[str, dict] = {}
        windows_by_key: dict[str, list[str]] = {}
        for key, text, source_title in items:
            windows = self._windows(text)
            # custom_id may only hold [A-Za-z0-9_-]; keys are section indexes
            windows_by_key[key] = [f"{key}-w{n}" for n in range(len(windows))]
            for custom_id, window in zip(windows_by_key[key], windows, strict=True):
                requests[custom_id] = self._request_params(window, source_title)
        if not requests:
            return {key: [] for key, _t, _s in items}

        logger.info(
            f"Principle extraction: submitting {len(requests)} window(s) for "
            f"{len(items)} section(s) as one batch"
        )
        responses = run_message_batch(self._get_client(), requests, label="Principle extraction")

        out: dict[str, list[ExtractedPrinciple]] = {}
        for key, _text, source_title in items:
            principles: list[ExtractedPrinciple] = []
            seen: set[str] = set()
            for custom_id in windows_by_key[key]:
                message = responses.get(custom_id)
                if not isinstance(message, BatchRequestFailed) and message is not None:
                    try:
                        parsed = self._parse_response(message, source_title)
                    except Exception as e:
                        logger.warning(f"Principle extraction failed for '{source_title}': {e}")
                        parsed = []
                else:
                    logger.warning(f"Principle extraction failed for '{source_title}': {message}")
                    parsed = []
                for p in parsed:
                    if p.principle_name not in seen:
                        seen.add(p.principle_name)
                        principles.append(p)
            out[key] = principles
        return out

    def _request_params(self, text: str, source_title: str) -> dict:
        """messages.create kwargs for one window (shared by the sync and batch paths)."""
        return dict(
            model=self.settings.llm_model,
            max_tokens=self.settings.llm_max_tokens,
            messages=[{"role": "user", "content": EXTRACTION_PROMPT.format(text=text, source=source_title)}],
            **json_schema_kwargs(PRINCIPLE_SCHEMA, thinking_kwargs(self.settings.llm_model, "disabled")),
        )

    def _extract_window(self, text: str, source_title: str) -> list[ExtractedPrinciple]:
        """Extract principles from a single window of text.

        An empty list for a window of real size is re-asked up to
        PRINCIPLE_EMPTY_RETRIES times: some models return `{"principles": []}`
        at random for text that yields rules on the next call.
        """
        try:
            client = self._get_client()
            retries = PRINCIPLE_EMPTY_RETRIES if len(text) >= PRINCIPLE_EMPTY_RETRY_MIN_CHARS else 0
            for attempt in range(retries + 1):
                message = create_message_growing(
                    client,
                    label=f"Principle extraction '{source_title}'",
                    **self._request_params(text, source_title),
                )
                principles = self._parse_response(message, source_title)
                if principles:
                    return principles
                if attempt < retries:
                    logger.info(f"Principle extraction: empty reply for a {len(text):,}-char window of "
                                f"'{source_title}' — re-asking ({attempt + 1}/{retries})")
            return []
        except Exception as e:
            logger.warning(f"Principle extraction failed for '{source_title}': {e}")
            return []

    @staticmethod
    def _parse_response(message, source_title: str) -> list[ExtractedPrinciple]:
        """Principles from one reply; raises on unparseable JSON.

        Accepts the schema's `{"principles": [...]}` and, for replies produced
        without the schema, a bare array.
        """
        raw = parse_llm_json(message_text(message))
        if isinstance(raw, dict):
            raw = raw.get("principles") or []
        principles = []
        for item in raw:
            try:
                if isinstance(item, dict):
                    item = {**item, "condition": sanitize_condition(item.get("condition"))}
                    # Open models through OpenRouter don't always honour the
                    # schema's enums (Kimi K3 emitted category "hard_constraint"
                    # on Bompa, 2026-09-20); the DB enum would reject the row.
                    for field, allowed, fallback in (("category", PRINCIPLE_CATEGORIES, "periodization"),
                                                     ("rule_type", RULE_TYPES, RULE_TYPES[0])):
                        if item.get(field) not in allowed:
                            logger.warning(f"Principle {item.get('principle_name')!r}: {field}={item.get(field)!r} "
                                           f"is not in the enum — using {fallback!r}")
                            item[field] = fallback
                principles.append(ExtractedPrinciple(**item))
            except (TypeError, KeyError) as e:
                logger.warning(f"Skipping malformed principle: {e}")

        return principles
