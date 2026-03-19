# tests/test_schemas.py
"""
Unit tests for JSONB Pydantic schemas (schemas.py).

No DB or API keys needed.
Run: uv run pytest tests/test_schemas.py
"""

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))

from schemas import OutcomeSummary, PhaseCheck, PhaseVerdict


# ── PhaseCheck ───────────────────────────────────────────────────────────────

def test_phase_check_valid():
    c = PhaseCheck(metric="Adherence", value=85.0, display="85%", threshold="≥ 70%", passed=True)
    assert c.passed is True
    assert c.value == 85.0


def test_phase_check_rejects_missing_field():
    with pytest.raises(ValidationError):
        PhaseCheck(metric="Adherence", display="85%", threshold="≥ 70%", passed=True)
        # missing value


# ── PhaseVerdict ─────────────────────────────────────────────────────────────

_VERDICT_DICT = {
    "prev_phase": "accumulation",
    "next_phase": "intensification",
    "prev_label": "Accumulation",
    "next_label": "Intensification",
    "advanced": True,
    "reason": "All thresholds met",
    "checks": [
        {"metric": "Adherence", "value": 85.0, "display": "85%", "threshold": "≥ 70%", "passed": True},
        {"metric": "Make rate", "value": 0.82, "display": "82%", "threshold": "≥ 75%", "passed": True},
        {"metric": "RPE deviation", "value": 0.4, "display": "+0.40", "threshold": "≤ +1.5", "passed": True},
    ],
    "adjustments": ["Intensity ceiling +2% (excellent performance)"],
}


def test_phase_verdict_from_dict():
    v = PhaseVerdict.model_validate(_VERDICT_DICT)
    assert v.advanced is True
    assert v.next_phase == "intensification"
    assert len(v.checks) == 3
    assert v.checks[0].passed is True
    assert v.adjustments == ["Intensity ceiling +2% (excellent performance)"]


def test_phase_verdict_null_prev_phase():
    d = {**_VERDICT_DICT, "prev_phase": None}
    v = PhaseVerdict.model_validate(d)
    assert v.prev_phase is None


def test_phase_verdict_empty_adjustments_default():
    d = {k: v for k, v in _VERDICT_DICT.items() if k != "adjustments"}
    v = PhaseVerdict.model_validate(d)
    assert v.adjustments == []


# ── OutcomeSummary ────────────────────────────────────────────────────────────

_FULL_OUTCOME = {
    "adherence_pct": 88.5,
    "avg_rpe_deviation": 0.3,
    "avg_make_rate": 0.82,
    "make_rate_by_lift": {"snatch": 0.80, "clean_and_jerk": 0.84},
    "avg_weekly_reps": 42.0,
    "rpe_trend": "stable",
    "make_rate_trend": "ascending",
    "maxes_delta": {"Snatch": 2.5, "Clean & Jerk": 2.0},
    "athlete_feedback": "Felt strong on snatches.",
    "phase_verdict": _VERDICT_DICT,
}


def test_outcome_summary_full():
    o = OutcomeSummary.model_validate(_FULL_OUTCOME)
    assert o.adherence_pct == 88.5
    assert o.avg_make_rate == 0.82
    assert o.make_rate_by_lift == {"snatch": 0.80, "clean_and_jerk": 0.84}
    assert o.rpe_trend == "stable"
    assert o.make_rate_trend == "ascending"
    assert o.phase_verdict is not None
    assert o.phase_verdict.advanced is True


def test_outcome_summary_empty_dict_uses_defaults():
    """An empty dict (e.g. old DB record with no outcome) must not raise."""
    o = OutcomeSummary.model_validate({})
    assert o.adherence_pct == 100.0   # permissive default
    assert o.avg_make_rate == 1.0
    assert o.avg_rpe_deviation == 0.0
    assert o.rpe_trend == "stable"
    assert o.phase_verdict is None
    assert o.make_rate_by_lift == {}


def test_outcome_summary_null_phase_verdict():
    d = {**_FULL_OUTCOME, "phase_verdict": None}
    o = OutcomeSummary.model_validate(d)
    assert o.phase_verdict is None


def test_outcome_summary_invalid_trend_raises():
    d = {**_FULL_OUTCOME, "rpe_trend": "sideways"}
    with pytest.raises(ValidationError):
        OutcomeSummary.model_validate(d)


def test_outcome_summary_null_athlete_feedback():
    d = {**_FULL_OUTCOME, "athlete_feedback": None}
    o = OutcomeSummary.model_validate(d)
    assert o.athlete_feedback is None


# ── Round-trip serialisation ──────────────────────────────────────────────────

def test_round_trip_json():
    """model_dump_json() → json.loads() → model_validate() preserves all values."""
    original = OutcomeSummary.model_validate(_FULL_OUTCOME)
    serialised = original.model_dump_json()

    # Must be valid JSON
    raw = json.loads(serialised)
    assert isinstance(raw, dict)

    restored = OutcomeSummary.model_validate(raw)
    assert restored.adherence_pct == original.adherence_pct
    assert restored.make_rate_by_lift == original.make_rate_by_lift
    assert restored.phase_verdict is not None
    assert restored.phase_verdict.next_phase == original.phase_verdict.next_phase
    assert len(restored.phase_verdict.checks) == len(original.phase_verdict.checks)


def test_model_dump_json_is_string():
    o = OutcomeSummary.model_validate(_FULL_OUTCOME)
    dumped = o.model_dump_json()
    assert isinstance(dumped, str)
    # Should be parseable and contain expected fields
    d = json.loads(dumped)
    assert "adherence_pct" in d
    assert "phase_verdict" in d


# ── Partial data (backward-compat with older DB records) ─────────────────────

def test_partial_outcome_no_phase_verdict():
    """Records written before phase_verdict was added must validate cleanly."""
    partial = {
        "adherence_pct": 75.0,
        "avg_rpe_deviation": 0.8,
        "avg_make_rate": 0.78,
    }
    o = OutcomeSummary.model_validate(partial)
    assert o.adherence_pct == 75.0
    assert o.phase_verdict is None
    assert o.make_rate_by_lift == {}


def test_partial_outcome_no_make_rate_by_lift():
    partial = {"adherence_pct": 80.0, "avg_make_rate": 0.80}
    o = OutcomeSummary.model_validate(partial)
    assert o.make_rate_by_lift == {}
