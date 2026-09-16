# tests/test_principle_schema.py
"""
No-key tests for the principle condition schema (RAG-L9): unknown condition keys
are dropped at extraction time, and the extractor's key set matches what the
agent's principle_matcher can evaluate.

Run: PYTHONUTF8=1 uv run pytest tests/test_principle_schema.py -q
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "oly-agent"))

from processors.principle_extractor import CONDITION_KEYS, PrincipleExtractor, sanitize_condition


def test_sanitize_condition_keeps_schema_keys_only():
    cond = {"phase": ["accumulation"], "movement_family": "snatch", "athlete_characteristics": "tall",
            "delay_minutes": 5, "weeks_out_from_competition": {"lte": 2}}
    assert sanitize_condition(cond) == {"phase": ["accumulation"], "movement_family": "snatch",
                                        "weeks_out_from_competition": {"lte": 2}}
    assert sanitize_condition(None) == {} and sanitize_condition("phase=x") == {}


def test_extract_window_strips_unknown_keys_before_building_principles():
    item = {
        "principle_name": "Taper rule", "category": "peaking", "rule_type": "guideline",
        "condition": {"weeks_out_from_competition": {"lte": 2}, "training_focus": "meet"},
        "recommendation": {"volume_modifier": 0.6}, "rationale": "r", "priority": 8,
    }
    message = MagicMock()
    message.content = [MagicMock(text=json.dumps([item]))]
    extractor = PrincipleExtractor(MagicMock(llm_model="claude-sonnet-5", llm_max_tokens=100, anthropic_api_key="k"))
    extractor._client = MagicMock()
    with patch("processors.principle_extractor.create_message_growing", return_value=message) as call:
        out = extractor._extract_window("text", "Book")
    assert len(out) == 1
    assert out[0].condition == {"weeks_out_from_competition": {"lte": 2}}
    # structured JSON out — never let a Sonnet 5 llm_model run adaptive thinking
    assert call.call_args.kwargs["thinking"] == {"type": "disabled"}
    assert call.call_args.kwargs["max_tokens"] == 100


def test_condition_keys_match_the_agent_matcher():
    """Drift guard: what the extractor keeps must be exactly what plan/orchestrator can evaluate."""
    from principle_matcher import KNOWN_CONDITION_KEYS

    assert set(CONDITION_KEYS) == set(KNOWN_CONDITION_KEYS)
