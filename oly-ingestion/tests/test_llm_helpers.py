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


def test_openrouter_ids_round_trip_through_the_family_and_pricing_tables():
    """OpenRouter ids ("anthropic/claude-sonnet-5", "anthropic/claude-haiku-4.5")
    must resolve to the same family rules and prices as the Anthropic ids —
    otherwise Sonnet 5 via OpenRouter would silently run adaptive thinking."""
    from shared.llm import canonical_model, openrouter_model_id, pricing_for, sampling_kwargs, thinking_kwargs

    assert openrouter_model_id("claude-sonnet-5") == "anthropic/claude-sonnet-5"
    assert openrouter_model_id("claude-haiku-4-5-20251001") == "anthropic/claude-haiku-4.5"
    assert openrouter_model_id("claude-sonnet-4-6") == "anthropic/claude-sonnet-4.6"
    assert openrouter_model_id("claude-opus-5") == "anthropic/claude-opus-5"
    assert openrouter_model_id("z-ai/glm-5.3-flash") == "z-ai/glm-5.3-flash"      # already vendor-prefixed
    assert canonical_model("anthropic/claude-sonnet-4.6") == "claude-sonnet-4-6"
    assert canonical_model("anthropic/claude-haiku-4.5") == "claude-haiku-4-5"
    assert canonical_model("claude-sonnet-5") == "claude-sonnet-5" and canonical_model(None) == ""
    assert thinking_kwargs("anthropic/claude-sonnet-5", "disabled") == {"thinking": {"type": "disabled"}}
    assert sampling_kwargs("anthropic/claude-sonnet-5", 0.3) == {}
    assert sampling_kwargs("anthropic/claude-sonnet-4.6", 0.3) == {"temperature": 0.3}
    assert pricing_for("anthropic/claude-haiku-4.5") == (1.0, 5.0)
    assert pricing_for("anthropic/claude-sonnet-5") == (2.0, 10.0)


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


def test_create_message_growing_doubles_the_budget_on_truncation():
    """A response that stops on max_tokens is re-sent with double the budget,
    up to the ceiling; anything else is returned as-is."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from shared.llm import create_message_growing

    truncated = SimpleNamespace(stop_reason="max_tokens", content=[])
    ok = SimpleNamespace(stop_reason="end_turn", content=[])
    client = MagicMock()
    client.messages.create.side_effect = [truncated, truncated, ok]
    out = create_message_growing(client, max_tokens=1000, ceiling=3000, model="m", messages=[])
    assert out is ok
    assert [c.kwargs["max_tokens"] for c in client.messages.create.call_args_list] == [1000, 2000, 3000]

    client = MagicMock()
    client.messages.create.side_effect = [truncated, truncated]
    out = create_message_growing(client, max_tokens=2000, ceiling=4000, model="m", messages=[])
    assert out is truncated and client.messages.create.call_count == 2      # stops at the ceiling

    client = MagicMock()
    client.messages.create.return_value = ok
    create_message_growing(client, max_tokens=50, model="m", messages=[], thinking={"type": "disabled"})
    assert client.messages.create.call_args.kwargs["thinking"] == {"type": "disabled"}
    assert client.messages.create.call_count == 1


def _fake_batch_client(*rounds):
    """A client whose `messages.batches` returns one ended batch per call.

    `rounds` are dicts {custom_id: result}; a result is a message-like object
    (returned as `succeeded`) or a string naming a failure type.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    client = MagicMock()
    submitted = []

    def _create(requests):
        submitted.append({r["custom_id"]: r["params"] for r in requests})
        n = len(submitted)
        return SimpleNamespace(id=f"batch_{n}", processing_status="ended", request_counts=None)

    def _results(batch_id):
        payload = rounds[int(batch_id.split("_")[1]) - 1]
        for cid, res in payload.items():
            if isinstance(res, str):
                result = SimpleNamespace(type=res, error=SimpleNamespace(error=SimpleNamespace(message="boom")))
            else:
                result = SimpleNamespace(type="succeeded", message=res)
            yield SimpleNamespace(custom_id=cid, result=result)

    client.messages.batches.create.side_effect = _create
    client.messages.batches.results.side_effect = _results
    client.submitted = submitted
    return client


def test_run_message_batch_collects_results_and_regrows_truncated():
    """Succeeded results map back by custom_id; a reply that stopped on
    max_tokens is re-sent alone with a doubled budget, up to the ceiling."""
    from types import SimpleNamespace

    from shared.llm import run_message_batch

    ok_a = SimpleNamespace(stop_reason="end_turn", content=[])
    cut_b = SimpleNamespace(stop_reason="max_tokens", content=[])
    ok_b = SimpleNamespace(stop_reason="end_turn", content=[])
    client = _fake_batch_client({"a": ok_a, "b": cut_b}, {"b": ok_b})
    out = run_message_batch(
        client,
        {"a": {"max_tokens": 100, "model": "m"}, "b": {"max_tokens": 100, "model": "m"}},
        poll_interval=0, ceiling=400,
    )
    assert out == {"a": ok_a, "b": ok_b}
    assert list(client.submitted[0]) == ["a", "b"]
    assert client.submitted[1] == {"b": {"max_tokens": 200, "model": "m"}}

    # at the ceiling the truncated reply is returned as-is
    client = _fake_batch_client({"b": cut_b})
    out = run_message_batch(client, {"b": {"max_tokens": 400}}, poll_interval=0, ceiling=400)
    assert out == {"b": cut_b} and len(client.submitted) == 1

    # grow=False never re-sends
    client = _fake_batch_client({"b": cut_b})
    out = run_message_batch(client, {"b": {"max_tokens": 100}}, poll_interval=0, ceiling=400, grow=False)
    assert out == {"b": cut_b} and len(client.submitted) == 1


def test_run_message_batch_rejects_bad_custom_ids_before_submitting():
    """The API only accepts [A-Za-z0-9_-]{1,64}; a colon in "3:0" 400'd the first
    --batch principle run after the OCR batch had already been paid for."""
    from unittest.mock import MagicMock

    from shared.llm import run_message_batch

    client = MagicMock()
    for bad in ("3:0", "", "x" * 65, "a b"):
        try:
            run_message_batch(client, {bad: {}, "ok-1": {}}, poll_interval=0)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError as e:
            assert "custom_id" in str(e)
    client.messages.batches.create.assert_not_called()


def test_run_message_batch_reports_failed_and_missing_requests():
    """Errored / expired results and ids the API never returned come back as
    BatchRequestFailed instead of raising, so callers decide per item."""
    from types import SimpleNamespace

    from shared.llm import BatchRequestFailed, run_message_batch

    ok = SimpleNamespace(stop_reason="end_turn", content=[])
    client = _fake_batch_client({"a": ok, "b": "errored"})
    out = run_message_batch(client, {"a": {}, "b": {}, "c": {}}, poll_interval=0)
    assert out["a"] is ok
    assert isinstance(out["b"], BatchRequestFailed) and out["b"].result_type == "errored"
    assert "boom" in str(out["b"])
    assert isinstance(out["c"], BatchRequestFailed) and out["c"].result_type == "missing"


def test_run_message_batch_polls_until_ended_and_times_out():
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from shared.llm import run_message_batch

    client = MagicMock()
    client.messages.batches.create.return_value = SimpleNamespace(id="b1", processing_status="in_progress")
    client.messages.batches.retrieve.side_effect = [
        SimpleNamespace(id="b1", processing_status="in_progress", request_counts=None),
        SimpleNamespace(id="b1", processing_status="ended", request_counts=None),
    ]
    client.messages.batches.results.return_value = iter([])
    with patch("shared.llm.time.sleep") as sleep:
        out = run_message_batch(client, {"x": {}}, poll_interval=7, grow=False)
    assert sleep.call_count == 2 and client.messages.batches.retrieve.call_count == 2
    assert out["x"].result_type == "missing"

    client.messages.batches.retrieve.side_effect = None
    client.messages.batches.retrieve.return_value = SimpleNamespace(
        id="b1", processing_status="in_progress", request_counts=None
    )
    with patch("shared.llm.time.sleep"), patch("shared.llm.time.monotonic", side_effect=[0, 0, 10, 10, 100]):
        try:
            run_message_batch(client, {"x": {}}, poll_interval=1, timeout=50, grow=False)
            raise AssertionError("expected TimeoutError")
        except TimeoutError:
            pass


def test_json_schema_kwargs_merges_with_effort_under_output_config():
    """`output_config` carries both `effort` and `format`; the helper must add
    the format without dropping an effort already present (STRUCT-1)."""
    from shared.llm import json_schema_kwargs, thinking_kwargs

    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    assert json_schema_kwargs(schema) == {
        "output_config": {"format": {"type": "json_schema", "schema": schema}}}
    base = thinking_kwargs("claude-sonnet-5", "adaptive", "low")
    merged = json_schema_kwargs(schema, base)
    assert merged["thinking"] == {"type": "adaptive"}
    assert merged["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": schema}}
    assert base["output_config"] == {"effort": "low"}          # base not mutated


def test_extract_window_sends_the_schema_and_accepts_the_wrapper():
    """The request carries `output_config.format` = PRINCIPLE_SCHEMA and the
    parser reads the schema's {"principles": [...]} as well as a bare array."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from processors.principle_extractor import PRINCIPLE_SCHEMA, PrincipleExtractor

    settings = SimpleNamespace(llm_model="claude-sonnet-5", llm_max_tokens=4096, anthropic_api_key="k")
    ex = PrincipleExtractor(settings)
    principle = {"principle_name": "P", "category": "deload", "rule_type": "guideline",
                 "condition": {"phase": "deload"}, "recommendation": {"volume_modifier": 0.6},
                 "rationale": "r", "priority": 5}
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps({"principles": [principle]}))])
    ex._client = client
    out = ex._extract_window("text", "Book")
    assert [p.principle_name for p in out] == ["P"]
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["output_config"]["format"] == {"type": "json_schema", "schema": PRINCIPLE_SCHEMA}
    assert kwargs["thinking"] == {"type": "disabled"}
    assert '{"principles": [...]}' in kwargs["messages"][0]["content"]
    bare = SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps([principle]))])
    assert len(PrincipleExtractor._parse_response(bare, "Book")) == 1


def test_extract_batch_windows_per_key_and_tolerates_a_failed_window():
    """One request per window across all sections; results are grouped back by
    key and de-duplicated by principle_name; a failed window contributes nothing."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from processors.principle_extractor import PrincipleExtractor

    settings = SimpleNamespace(llm_model="claude-sonnet-5", llm_max_tokens=4096, anthropic_api_key="k")
    ex = PrincipleExtractor(settings)
    ex._client = object()
    long_text = "x" * (_PRINCIPLE_WINDOW + 100)   # two windows

    def _msg(items):
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(items))])

    principle = {"principle_name": "Deload every 4th week", "category": "deload", "rule_type": "guideline",
                 "condition": {"phase": "accumulation"}, "recommendation": {"volume_modifier": 0.6},
                 "rationale": "r", "priority": 5}
    captured = {}

    def fake_batch(client, requests, **kw):
        captured.update(requests)
        from shared.llm import BatchRequestFailed
        return {
            "s1-w0": _msg([principle]),
            "s1-w1": _msg([principle, {**principle, "principle_name": "Other"}]),
            "s2-w0": BatchRequestFailed("s2-w0", "errored"),
        }

    with patch("processors.principle_extractor.run_message_batch", side_effect=fake_batch):
        out = ex.extract_batch([("s1", long_text, "Book"), ("s2", "short", "Book")])

    assert set(captured) == {"s1-w0", "s1-w1", "s2-w0"}
    assert captured["s1-w0"]["model"] == "claude-sonnet-5"
    assert captured["s1-w0"]["thinking"] == {"type": "disabled"}
    assert [p.principle_name for p in out["s1"]] == ["Deload every 4th week", "Other"]
    assert out["s2"] == []


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
