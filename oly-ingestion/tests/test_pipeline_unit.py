# tests/test_pipeline_unit.py
"""
Unit tests for IngestionPipeline._parse_program_template.

Mocks the Anthropic client so no API key or DB is needed.

Run: PYTHONUTF8=1 uv run python tests/test_pipeline_unit.py
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import IngestionPipeline


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_section(content: str, metadata: dict | None = None):
    section = MagicMock()
    section.content = content
    section.metadata = metadata or {}
    return section


def _make_source(title: str = "Test Book"):
    source = MagicMock()
    source.title = title
    return source


def _make_llm_response(data: dict) -> MagicMock:
    """Build a mock Anthropic message response returning JSON text."""
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps(data))]
    return resp


def _make_pipeline(mock_client) -> MagicMock:
    """Create a minimal mock pipeline self with a controlled Anthropic client.

    No spec= — IngestionPipeline sets principle_extractor in __init__ so spec
    would block attribute access before our assignment.
    """
    pipeline = MagicMock()
    pipeline.principle_extractor._get_client.return_value = mock_client
    pipeline.settings.llm_model = "claude-sonnet-4-6"
    return pipeline


def _call(pipeline, section, source=None, source_id=1):
    return IngestionPipeline._parse_program_template(
        pipeline, section, source or _make_source(), source_id
    )


RESULTS = []


def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, str(e)))


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_short_section_single_llm_call():
    """Section < 5000 chars makes one LLM call and returns its metadata."""
    llm_data = {
        "athlete_level": "intermediate",
        "goal": "general_strength",
        "duration_weeks": 4,
        "sessions_per_week": 4,
        "weeks": [
            {"week_number": 1, "sessions": [{}] * 4},
            {"week_number": 2, "sessions": [{}] * 4},
            {"week_number": 3, "sessions": [{}] * 4},
            {"week_number": 4, "sessions": [{}] * 4},
        ],
    }
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_llm_response(llm_data)

    section = _make_section("short content " * 10)  # well under 5000 chars
    pipeline = _make_pipeline(mock_client)
    result = _call(pipeline, section)

    assert mock_client.messages.create.call_count == 1
    assert result["duration_weeks"] == 4
    assert result["sessions_per_week"] == 4
    assert result["athlete_level"] == "intermediate"


def test_long_section_triggers_continuation():
    """Section > 5000 chars makes a second (continuation) LLM call."""
    first_response = {
        "athlete_level": "intermediate",
        "goal": "general_strength",
        "duration_weeks": 8,
        "sessions_per_week": 4,
        "weeks": [
            {"week_number": 1, "sessions": []},
            {"week_number": 2, "sessions": []},
            {"week_number": 3, "sessions": []},
            {"week_number": 4, "sessions": []},
        ],
    }
    continuation_response = {
        "weeks": [
            {"week_number": 5, "sessions": []},
            {"week_number": 6, "sessions": []},
            {"week_number": 7, "sessions": []},
            {"week_number": 8, "sessions": []},
        ]
    }
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _make_llm_response(first_response),
        _make_llm_response(continuation_response),
    ]

    # Content > 5000 chars to trigger continuation
    section = _make_section("x " * 3000)
    pipeline = _make_pipeline(mock_client)
    result = _call(pipeline, section)

    assert mock_client.messages.create.call_count == 2
    # All 8 weeks should be merged into program_structure
    weeks = result["program_structure"].get("weeks", [])
    assert len(weeks) == 8
    week_numbers = {w["week_number"] for w in weeks}
    assert week_numbers == set(range(1, 9))


def test_continuation_deduplicates_seen_weeks():
    """Duplicate week numbers from continuation are filtered by seen_weeks set."""
    first_response = {
        "duration_weeks": 4,
        "sessions_per_week": 4,
        "weeks": [
            {"week_number": 1, "sessions": []},
            {"week_number": 2, "sessions": []},
        ],
    }
    # Continuation returns week 2 again (duplicate) plus new week 3
    continuation_response = {
        "weeks": [
            {"week_number": 2, "sessions": []},  # duplicate — should be skipped
            {"week_number": 3, "sessions": []},
        ]
    }
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _make_llm_response(first_response),
        _make_llm_response(continuation_response),
        # Third call returns no new weeks → loop terminates
        _make_llm_response({"weeks": []}),
    ]

    section = _make_section("x " * 3000)
    pipeline = _make_pipeline(mock_client)
    result = _call(pipeline, section)

    weeks = result["program_structure"].get("weeks", [])
    week_numbers = [w["week_number"] for w in weeks]
    assert week_numbers.count(2) == 1, "Week 2 should not be duplicated"
    assert 3 in week_numbers


def test_duration_weeks_inferred_from_weeks_count():
    """When LLM returns duration_weeks=0, it is inferred from len(weeks)."""
    llm_data = {
        "duration_weeks": 0,  # LLM failed to fill this in
        "sessions_per_week": 4,
        "weeks": [
            {"week_number": 1, "sessions": []},
            {"week_number": 2, "sessions": []},
            {"week_number": 3, "sessions": []},
        ],
    }
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_llm_response(llm_data)

    section = _make_section("short content")
    pipeline = _make_pipeline(mock_client)
    result = _call(pipeline, section)

    assert result["duration_weeks"] == 3, (
        f"Expected 3 (inferred from weeks), got {result['duration_weeks']}"
    )


def test_sessions_per_week_inferred_from_first_week():
    """When LLM returns sessions_per_week=0, infer from first week's session count."""
    llm_data = {
        "duration_weeks": 4,
        "sessions_per_week": 0,  # LLM failed to fill this in
        "weeks": [
            {"week_number": 1, "sessions": [{}, {}, {}, {}]},  # 4 sessions
        ],
    }
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_llm_response(llm_data)

    section = _make_section("short content")
    pipeline = _make_pipeline(mock_client)
    result = _call(pipeline, section)

    assert result["sessions_per_week"] == 4, (
        f"Expected 4 (inferred from first week), got {result['sessions_per_week']}"
    )


def test_llm_failure_returns_empty_structure():
    """If the LLM call raises, _parse_program_template returns a safe empty dict."""
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("API timeout")

    section = _make_section("content", metadata={"program_name": "My Program"})
    pipeline = _make_pipeline(mock_client)
    result = _call(pipeline, section, source_id=1)

    # Should not raise — returns a dict with safe defaults
    assert isinstance(result, dict)
    assert result["name"] == "My Program"
    assert result["duration_weeks"] == 0  # default when no weeks parsed


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        ("Short section: single LLM call", test_short_section_single_llm_call),
        ("Long section: continuation triggered", test_long_section_triggers_continuation),
        ("Continuation: deduplicates seen weeks", test_continuation_deduplicates_seen_weeks),
        ("duration_weeks=0: inferred from len(weeks)", test_duration_weeks_inferred_from_weeks_count),
        ("sessions_per_week=0: inferred from first week", test_sessions_per_week_inferred_from_first_week),
        ("LLM failure: returns safe empty structure", test_llm_failure_returns_empty_structure),
    ]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {name}: {e}")
            import traceback; traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
