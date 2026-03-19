# tests/test_structured_loader_unit.py
"""
Unit tests for StructuredLoader.load_program() validation guard.

No live DB needed — psycopg2.connect is mocked.

Run: PYTHONUTF8=1 uv run python tests/test_structured_loader_unit.py
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from loaders.structured_loader import StructuredLoader


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_loader() -> StructuredLoader:
    """Create a StructuredLoader with a mocked DB connection."""
    with patch("loaders.structured_loader.psycopg2.connect"):
        settings = MagicMock()
        settings.database_url = "postgresql://fake"
        loader = StructuredLoader(settings)
    loader.conn = MagicMock()
    return loader


VALID_PROGRAM = {
    "name": "Test 4-Week Program",
    "source_id": 1,
    "athlete_level": "intermediate",
    "goal": "general_strength",
    "duration_weeks": 4,
    "sessions_per_week": 4,
    "program_structure": {"weeks": []},
}


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_load_program_duration_weeks_zero_returns_none():
    """duration_weeks=0 → returns None without touching the DB cursor."""
    loader = _make_loader()
    result = loader.load_program(dict(VALID_PROGRAM, duration_weeks=0))
    assert result is None
    loader.conn.cursor.assert_not_called()


def test_load_program_duration_weeks_negative_returns_none():
    """duration_weeks=-1 → returns None (< 1 guard)."""
    loader = _make_loader()
    result = loader.load_program(dict(VALID_PROGRAM, duration_weeks=-1))
    assert result is None
    loader.conn.cursor.assert_not_called()


def test_load_program_sessions_per_week_zero_returns_none():
    """sessions_per_week=0 → returns None without touching the DB cursor."""
    loader = _make_loader()
    result = loader.load_program(dict(VALID_PROGRAM, sessions_per_week=0))
    assert result is None
    loader.conn.cursor.assert_not_called()


def test_load_program_sessions_per_week_15_returns_none():
    """sessions_per_week=15 (above max 14) → returns None."""
    loader = _make_loader()
    result = loader.load_program(dict(VALID_PROGRAM, sessions_per_week=15))
    assert result is None
    loader.conn.cursor.assert_not_called()


def test_load_program_sessions_per_week_1_valid():
    """sessions_per_week=1 is within [1, 14] → proceeds to cursor.execute."""
    loader = _make_loader()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = [99]
    loader.conn.cursor.return_value = mock_cursor

    result = loader.load_program(dict(VALID_PROGRAM, sessions_per_week=1))
    assert result == 99
    mock_cursor.execute.assert_called_once()


def test_load_program_sessions_per_week_14_valid():
    """sessions_per_week=14 is the max allowed → proceeds to cursor.execute."""
    loader = _make_loader()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = [100]
    loader.conn.cursor.return_value = mock_cursor

    result = loader.load_program(dict(VALID_PROGRAM, sessions_per_week=14))
    assert result == 100
    mock_cursor.execute.assert_called_once()


def test_load_program_valid_attempts_insert():
    """Valid program (duration_weeks>=1, sessions_per_week 1-14) calls cursor.execute."""
    loader = _make_loader()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = [42]
    loader.conn.cursor.return_value = mock_cursor

    result = loader.load_program(VALID_PROGRAM)
    assert result == 42
    mock_cursor.execute.assert_called_once()


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        ("duration_weeks=0 → None, no cursor", test_load_program_duration_weeks_zero_returns_none),
        ("duration_weeks=-1 → None", test_load_program_duration_weeks_negative_returns_none),
        ("sessions_per_week=0 → None, no cursor", test_load_program_sessions_per_week_zero_returns_none),
        ("sessions_per_week=15 → None", test_load_program_sessions_per_week_15_returns_none),
        ("sessions_per_week=1 → proceeds", test_load_program_sessions_per_week_1_valid),
        ("sessions_per_week=14 → proceeds", test_load_program_sessions_per_week_14_valid),
        ("valid program → calls cursor.execute", test_load_program_valid_attempts_insert),
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
