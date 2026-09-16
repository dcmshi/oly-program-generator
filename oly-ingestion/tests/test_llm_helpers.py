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

# ── light_model_for (model roles) ─────────────────────────────────────────────

def test_light_model_for_precedence():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from shared.llm import light_model_for

    full = SimpleNamespace(llm_model="sonnet", light_model="haiku")
    assert light_model_for(full) == "haiku"
    assert light_model_for(full, "explicit") == "explicit"
    assert light_model_for(SimpleNamespace(llm_model="sonnet", light_model="")) == "sonnet"
    assert light_model_for(SimpleNamespace(llm_model="sonnet")) == "sonnet"
    mocked = MagicMock(llm_model="sonnet")           # a partial mock exposes a non-str light_model
    assert light_model_for(mocked) == "sonnet"


# ── Claude 5 request/response safety (shared/llm.py) ─────────────────────────

def test_message_text_skips_thinking_blocks_and_raises_on_refusal():
    from types import SimpleNamespace

    from shared.llm import LLMRefusal, message_text

    thinking = SimpleNamespace(type="thinking", thinking="…")
    text_a = SimpleNamespace(type="text", text='{"a": ')
    text_b = SimpleNamespace(type="text", text="1}")
    assert message_text(SimpleNamespace(content=[thinking, text_a, text_b], stop_reason="end_turn")) == '{"a": 1}'
    # MagicMock-style blocks (type is not a str) still count as text
    from unittest.mock import MagicMock
    assert message_text(MagicMock(content=[MagicMock(text="plain")])) == "plain"
    try:
        message_text(SimpleNamespace(content=[], stop_reason="refusal"))
        raise AssertionError("expected LLMRefusal")
    except LLMRefusal:
        pass
    assert message_text(SimpleNamespace(content=[], stop_reason="end_turn")) == ""


def test_sampling_kwargs_dropped_for_models_that_reject_temperature():
    from shared.llm import accepts_sampling_params, sampling_kwargs

    assert sampling_kwargs("claude-sonnet-4-6", 0.3) == {"temperature": 0.3}
    assert sampling_kwargs("claude-haiku-4-5-20251001", 0.3) == {"temperature": 0.3}
    assert sampling_kwargs("claude-opus-4-6", 0.3) == {"temperature": 0.3}
    for model in ("claude-sonnet-5", "claude-opus-5", "claude-opus-4-7", "claude-opus-4-8", "claude-fable-5-1"):
        assert not accepts_sampling_params(model), model
        assert sampling_kwargs(model, 0.3) == {}, model
    assert sampling_kwargs("claude-sonnet-4-6", None) == {}


def test_thinking_kwargs_per_model_family():
    from shared.llm import thinking_kwargs

    assert thinking_kwargs("claude-sonnet-4-6") == {}
    assert thinking_kwargs("claude-sonnet-5") == {}                       # model default (adaptive)
    assert thinking_kwargs("claude-haiku-4-5-20251001", "adaptive", "low") == {}   # no adaptive/effort on 4.5
    assert thinking_kwargs("claude-sonnet-4-6", "adaptive", "medium") == {
        "thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"}}
    assert thinking_kwargs("claude-sonnet-4-6", "disabled") == {}         # already off when omitted
    assert thinking_kwargs("claude-sonnet-5", "disabled", "low") == {
        "thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}
    assert thinking_kwargs("claude-opus-5", "DISABLED") == {"thinking": {"type": "disabled"}}
    assert thinking_kwargs("claude-fable-5-1", "disabled") == {}          # cannot be turned off
    for bad in (("claude-sonnet-5", "budget", ""), ("claude-sonnet-5", "", "ultra"),
                ("claude-opus-5", "disabled", "max")):
        try:
            thinking_kwargs(*bad)
            raise AssertionError(f"expected ValueError for {bad}")
        except ValueError:
            pass


def test_estimate_cost_is_per_model_and_counts_cache_tokens():
    from shared.llm import DEFAULT_PRICING_PER_MTOK, estimate_cost, pricing_for

    assert pricing_for("claude-haiku-4-5-20251001") == (1.0, 5.0)     # dated snapshot → prefix
    assert pricing_for("claude-fable-5-1") == (10.0, 50.0)             # longest prefix wins over claude-fable-5
    assert pricing_for("claude-sonnet-5") == (2.0, 10.0)
    assert pricing_for("some-unknown-model") == DEFAULT_PRICING_PER_MTOK
    assert pricing_for(None) == DEFAULT_PRICING_PER_MTOK
    # 1M in / 1M out at Sonnet 4.6 = $18 (the old constant behaviour)
    assert abs(estimate_cost(1_000_000, 1_000_000) - 18.0) < 1e-9
    assert abs(estimate_cost(1_000_000, 1_000_000, "claude-sonnet-5") - 12.0) < 1e-9
    # cache read 0.1x, cache write 1.25x of the input rate
    assert abs(estimate_cost(0, 0, "claude-sonnet-4-6", cache_read_tokens=1_000_000) - 0.3) < 1e-9
    assert abs(estimate_cost(0, 0, "claude-sonnet-4-6", cache_creation_tokens=1_000_000) - 3.75) < 1e-9


def test_usage_tokens_tolerates_missing_and_mocked_fields():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from shared.llm import usage_tokens

    full = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=100, cache_creation_input_tokens=7)
    assert usage_tokens(full) == {"input": 10, "output": 5, "cache_read": 100, "cache_creation": 7}
    assert usage_tokens(SimpleNamespace(input_tokens=10, output_tokens=5)) == {
        "input": 10, "output": 5, "cache_read": 0, "cache_creation": 0}
    mocked = MagicMock()   # attributes not set below stay MagicMocks, not ints
    mocked.input_tokens = 3
    mocked.output_tokens = 4
    assert usage_tokens(mocked) == {"input": 3, "output": 4, "cache_read": 0, "cache_creation": 0}


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
