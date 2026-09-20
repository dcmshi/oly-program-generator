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
from shared.llm import (
    BatchRequestFailed,
    create_message_growing,
    message_text,
    parse_llm_json,
    run_message_batch,
    thinking_kwargs,
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
    - "weeks_out_from_competition": comparison object, e.g. {{"lte": 2}}
    - "athlete_level": array, e.g. ["intermediate", "advanced"]
    - "training_age_years": comparison object, e.g. {{"gte": 2}}
    - "week_of_block": comparison object, e.g. {{"gte": 3}}
    - "movement_family": string, e.g. "snatch"
    - "recent_make_rate": comparison object, e.g. {{"lt": 0.7}}
    - "rpe_average_last_week": comparison object, e.g. {{"gte": 9.0}}
    Comparison operators: lte, gte, lt, gt, eq, between
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

Respond with a JSON array of principles. If no clear principles are found, return [].

TEXT TO ANALYZE:
{text}

SOURCE: {source}

Respond ONLY with valid JSON array, no other text."""


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


def sanitize_condition(condition) -> dict:
    """Keep only schema condition keys; None / non-dict → {} (unconditional)."""
    if not isinstance(condition, dict):
        return {}
    dropped = sorted(set(condition) - CONDITION_KEYS)
    if dropped:
        logger.warning(f"Dropping unknown principle condition key(s): {dropped}")
    return {k: v for k, v in condition.items() if k in CONDITION_KEYS}


class PrincipleExtractor:
    def __init__(self, settings):
        self.settings = settings
        self._client = None  # lazy-initialized when first needed

    def _get_client(self):
        """Lazy-init Anthropic client (requires ANTHROPIC_API_KEY in env)."""
        if self._client is None:
            import anthropic
            if not self.settings.anthropic_api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY is required for principle extraction. "
                    "Set it in .env or pass via environment variable."
                )
            self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
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
            windows_by_key[key] = [f"{key}:{n}" for n in range(len(windows))]
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
            **thinking_kwargs(self.settings.llm_model, "disabled"),   # JSON out, no thinking
        )

    def _extract_window(self, text: str, source_title: str) -> list[ExtractedPrinciple]:
        """Extract principles from a single window of text."""
        try:
            client = self._get_client()
            message = create_message_growing(
                client,
                label=f"Principle extraction '{source_title}'",
                **self._request_params(text, source_title),
            )
            return self._parse_response(message, source_title)
        except Exception as e:
            logger.warning(f"Principle extraction failed for '{source_title}': {e}")
            return []

    @staticmethod
    def _parse_response(message, source_title: str) -> list[ExtractedPrinciple]:
        """Principles from one reply; raises on unparseable JSON."""
        raw = parse_llm_json(message_text(message))
        principles = []
        for item in raw:
            try:
                if isinstance(item, dict):
                    item = {**item, "condition": sanitize_condition(item.get("condition"))}
                principles.append(ExtractedPrinciple(**item))
            except (TypeError, KeyError) as e:
                logger.warning(f"Skipping malformed principle: {e}")

        return principles
