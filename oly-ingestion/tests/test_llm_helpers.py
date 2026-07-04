# tests/test_llm_helpers.py
"""
No-key unit tests for the shared LLM JSON parser (I-L8) and the principle
extractor's windowing (I-M8).

Run: python tests/test_llm_helpers.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # repo root for shared.*

from processors.principle_extractor import _PRINCIPLE_WINDOW, PrincipleExtractor

from shared.llm import parse_llm_json

RESULTS = []


def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, f"{type(e).__name__}: {e}"))


# ── parse_llm_json (I-L8) ─────────────────────────────────────────────────────

def test_parse_plain_object():
    assert parse_llm_json('{"content_type": "table", "confidence": 0.9}') == {
        "content_type": "table", "confidence": 0.9
    }


def test_parse_json_fence():
    assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_bare_fence():
    assert parse_llm_json('```\n[1, 2, 3]\n```') == [1, 2, 3]


def test_parse_list():
    assert parse_llm_json('[{"principle_name": "x"}]') == [{"principle_name": "x"}]


def test_parse_garbage_raises():
    try:
        parse_llm_json("not json at all")
        raise AssertionError("expected JSONDecodeError")
    except json.JSONDecodeError:
        pass


# ── PrincipleExtractor._windows (I-M8) ────────────────────────────────────────

def test_windows_short_text_single():
    text = "short section"
    assert PrincipleExtractor._windows(text) == [text]


def test_windows_long_text_covers_everything():
    text = "x" * (_PRINCIPLE_WINDOW * 3)  # 3+ windows
    windows = PrincipleExtractor._windows(text)
    assert len(windows) > 1
    # Every window is within the size cap; first starts at 0, last reaches the end.
    assert all(len(w) <= _PRINCIPLE_WINDOW for w in windows)
    assert windows[0] == text[:_PRINCIPLE_WINDOW]
    assert sum(len(w) for w in windows) >= len(text)  # overlap → total ≥ original


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
