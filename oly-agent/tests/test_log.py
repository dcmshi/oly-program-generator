# oly-agent/tests/test_log.py
"""
Tests for pure helpers in log.py (no DB or interactive input needed).

Run: python tests/test_log.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from log import _validate_session_link

RESULTS = []


def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, f"{type(e).__name__}: {e}"))


_SESSIONS = [{"id": 41, "day_number": 1}, {"id": 42, "day_number": 2}]


# ── _validate_session_link (A-L5) ────────────────────────────────────────────

def test_valid_listed_id_returns_it():
    assert _validate_session_link("41", _SESSIONS) == 41


def test_unlisted_id_returns_none():
    # A typo'd or foreign session id must not be linked — it would FK-violate
    # training_logs.session_id and abort the transaction mid-entry.
    assert _validate_session_link("9999", _SESSIONS) is None


def test_blank_input_returns_none():
    assert _validate_session_link("", _SESSIONS) is None


def test_non_digit_input_returns_none():
    assert _validate_session_link("abc", _SESSIONS) is None


def test_empty_session_list_returns_none():
    assert _validate_session_link("41", []) is None


# ── NOT NULL defaults for blank interactive prompts (AGT-L4) ─────────────────

def test_exercise_defaults_for_blank_prompts():
    from log import _apply_notnull_defaults
    sets, weight = _apply_notnull_defaults(None, None, [3, 3])
    assert sets == 2 and weight == 0.0
    sets, weight = _apply_notnull_defaults(None, None, [])
    assert sets == 1 and weight == 0.0
    sets, weight = _apply_notnull_defaults(5, 80.0, [3])
    assert sets == 5 and weight == 80.0


# ── Make-rate warnings not gated on RPE presence (AGT-L5) ────────────────────

def test_status_query_not_gated_on_rpe():
    import inspect

    import log as log_mod
    src = inspect.getsource(log_mod.cmd_status)
    assert "rpe IS NOT NULL OR" in src, \
        "make-rate-only rows must feed the <70% warning (AGT-L5)"


if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        _test(name, fn)

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    failed = sum(1 for r in RESULTS if r[0] in ("FAIL", "ERROR"))
    for r in RESULTS:
        detail = f"  → {r[2]}" if len(r) > 2 else ""
        print(f"  {r[0]}  {r[1]}{detail}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
