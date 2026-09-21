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


def test_normalize_comparisons_to_matcher_form():
    """The schema's {"op", "values"} comparisons become the {"lte": 2} /
    {"between": [lo, hi]} dicts principle_matcher.compare evaluates; the
    pre-schema form passes through; malformed ones are dropped, not raised."""
    from processors.principle_extractor import normalize_comparisons

    assert normalize_comparisons({
        "weeks_out_from_competition": {"op": "lte", "values": [2]},
        "week_of_block": {"op": "between", "values": [3, 5]},
        "training_age_years": {"gte": 2},                       # old form
        "phase": ["accumulation"],
        "recent_make_rate": {"op": "lt", "values": []},          # malformed
        "rpe_average_last_week": {"op": "between", "values": [9]},   # malformed
    }) == {
        "weeks_out_from_competition": {"lte": 2},
        "week_of_block": {"between": [3, 5]},
        "training_age_years": {"gte": 2},
        "phase": ["accumulation"],
    }
    # sanitize_condition applies it after dropping unknown keys
    assert sanitize_condition({"week_of_block": {"op": "gte", "values": [3]}, "bogus": 1}) == {"week_of_block": {"gte": 3}}
    # and the matcher evaluates the result
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "oly-agent"))
    from principle_matcher import condition_matches
    cond = sanitize_condition({"week_of_block": {"op": "between", "values": [3, 5]}})
    assert condition_matches(cond, {"week_of_block": 4}) and not condition_matches(cond, {"week_of_block": 6})


def test_condition_keys_match_the_agent_matcher():
    """Drift guard: what the extractor keeps must be exactly what plan/orchestrator can evaluate."""
    from principle_matcher import KNOWN_CONDITION_KEYS

    assert set(CONDITION_KEYS) == set(KNOWN_CONDITION_KEYS)


def test_parse_response_coerces_out_of_enum_category_and_rule_type():
    """Open models via OpenRouter don't always honour schema enums (Kimi emitted
    category='hard_constraint' on Bompa); the row must not reach the DB enum."""
    from types import SimpleNamespace

    from processors.principle_extractor import PrincipleExtractor

    item = {"principle_name": "Eccentric spotter requirement", "category": "hard_constraint",
            "rule_type": "must", "condition": {}, "recommendation": {"spotter": True}, "rationale": "safety",
            "priority": 3}
    msg = SimpleNamespace(content=[SimpleNamespace(type="text", text='{"principles": [' + __import__("json").dumps(item) + ']}')])
    out = PrincipleExtractor._parse_response(msg, "Bompa")
    assert len(out) == 1
    assert out[0].category == "periodization" and out[0].rule_type == "hard_constraint"
