# processors/principle_extractor.py
"""
Uses an LLM to extract structured programming principles from prose text.

This is the most valuable part of the pipeline — converting sentences like
"During the final two weeks before competition, volume should be reduced
by 40-60% while maintaining intensity above 90%" into queryable rules.
"""

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


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

    def extract(self, text: str, source_title: str, source_id: int) -> list[ExtractedPrinciple]:
        """Extract structured principles from prose text using LLM.

        The LLM client is initialized lazily — no API key required until
        principle extraction is actually called.
        """
        prompt = EXTRACTION_PROMPT.format(text=text[:8000], source=source_title)

        try:
            client = self._get_client()
            message = client.messages.create(
                model=self.settings.llm_model,
                max_tokens=self.settings.llm_max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = message.content[0].text.strip()

            # Strip markdown code fences if present
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]

            raw = json.loads(raw_text)
        except Exception as e:
            logger.warning(f"Principle extraction failed for '{source_title}': {e}")
            return []

        principles = []
        for item in raw:
            try:
                principles.append(ExtractedPrinciple(**item))
            except (TypeError, KeyError) as e:
                logger.warning(f"Skipping malformed principle: {e}")

        return principles
