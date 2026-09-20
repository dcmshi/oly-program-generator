# oly-agent/tests/test_model_baseline.py
"""
No-key / no-DB tests for eval/model_baseline.py: config parsing, the per-call
recorder, the generation_log summary, the report table, and the per-config
runner wired onto a mocked orchestrator.

Run: PYTHONUTF8=1 uv run pytest tests/test_model_baseline.py -q
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.model_baseline import (
    BaselineConfig,
    CallRecord,
    CallRecorder,
    flatten_result,
    parse_config,
    render_table,
    run_config,
    summarize_calls,
    summarize_log_rows,
    write_report,
)

from shared.config import Settings

# ── parse_config ─────────────────────────────────────────────────────────────

def test_parse_config_forms_and_labels():
    assert parse_config("claude-sonnet-4-6") == BaselineConfig("claude-sonnet-4-6", "", "")
    assert parse_config("claude-sonnet-5:disabled:low") == BaselineConfig("claude-sonnet-5", "disabled", "low")
    assert parse_config("claude-sonnet-5::high").label == "claude-sonnet-5:default:high"
    assert parse_config("claude-opus-5:adaptive").label == "claude-opus-5:adaptive"
    assert parse_config("claude-sonnet-4-6").label == "claude-sonnet-4-6"


@pytest.mark.parametrize("spec", [
    "", ":adaptive", "claude-sonnet-5:sometimes", "claude-sonnet-5:adaptive:turbo",
    "claude-sonnet-5:disabled:xhigh",          # thinking_kwargs rejects disabled + xhigh
    "a:b:c:d",
])
def test_parse_config_rejects_bad_specs(spec):
    with pytest.raises(ValueError):
        parse_config(spec)


def test_config_settings_point_both_roles_at_the_model():
    base = Settings(database_url="postgresql://x", cost_limit_per_program=2.5)
    s = parse_config("claude-sonnet-5:disabled:low").settings(base)
    assert (s.generation_model, s.explanation_model) == ("claude-sonnet-5", "claude-sonnet-5")
    assert (s.generation_thinking, s.generation_effort) == ("disabled", "low")
    assert (s.explanation_thinking, s.explanation_effort) == ("disabled", "low")
    assert s.database_url == "postgresql://x" and s.cost_limit_per_program == 2.5
    assert parse_config("claude-sonnet-4-6").settings(base, cost_limit_per_program=9.0).cost_limit_per_program == 9.0
    # a bare model spec inherits the production thinking default (disabled), so
    # `--config claude-sonnet-5` measures what production would run; use
    # `claude-sonnet-5:adaptive` to measure the model's own default
    assert parse_config("claude-sonnet-5").settings(base).generation_thinking == "disabled"
    assert parse_config("claude-sonnet-5:adaptive").settings(base).generation_thinking == "adaptive"


# ── recorder ─────────────────────────────────────────────────────────────────

def _response(input_tokens=1000, output_tokens=200, cache_read=0, cache_creation=0, stop="end_turn"):
    return SimpleNamespace(
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                              cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_creation),
        stop_reason=stop, content=[],
    )


def test_recorder_captures_usage_cost_latency_and_errors():
    rec = CallRecorder()
    real = MagicMock(return_value=_response(1_000_000, 0, cache_read=1_000_000))
    wrapped = rec.wrap("generate", real)
    client = object()
    out = wrapped(client, model="claude-sonnet-5", max_tokens=10, messages=[])
    assert out is real.return_value
    real.assert_called_once_with(client, model="claude-sonnet-5", max_tokens=10, messages=[])

    boom = MagicMock(side_effect=RuntimeError("overloaded"))
    with pytest.raises(RuntimeError):
        rec.wrap("explain", boom)(client, model="claude-sonnet-5")

    ok, err = rec.calls
    assert ok.role == "generate" and ok.stop_reason == "end_turn" and ok.elapsed_s >= 0
    assert (ok.input_tokens, ok.cache_read_tokens) == (1_000_000, 1_000_000)
    assert abs(ok.cost_usd - (2.0 + 0.2)) < 1e-9        # $2 input + 0.1× cache read on Sonnet 5
    assert err.role == "explain" and err.error == "RuntimeError: overloaded" and err.cost_usd == 0.0

    summary = summarize_calls(rec.calls)
    assert summary["calls"] == 2 and summary["errors"] == 1
    assert summary["input_tokens"] == 1_000_000 and summary["cache_read_tokens"] == 1_000_000
    assert summary["stop_reasons"] == {"end_turn": 1}
    assert summary["by_role"]["generate"]["calls"] == 1 and summary["by_role"]["explain"]["calls"] == 0
    assert summary["by_role"]["explain"]["mean_s"] is None


def test_summarize_calls_empty():
    s = summarize_calls([])
    assert s["calls"] == 0 and s["cost_usd"] == 0.0 and s["mean_call_s"] is None


# ── generation_log summary ───────────────────────────────────────────────────

def _row(week, day, attempt, status, errors=None, in_tok=100, out_tok=50):
    return {"week_number": week, "day_number": day, "attempt_number": attempt, "status": status,
            "validation_errors": errors, "input_tokens": in_tok, "output_tokens": out_tok}


def test_summarize_log_rows_counts_sessions_attempts_and_failures():
    rows = [
        _row(1, 1, 1, "success"),
        _row(1, 2, 2, "success"), _row(1, 2, 1, "validation_error", ["too many reps", "no squat"]),
        _row(1, 3, 1, "parse_error"), _row(1, 3, 2, "parse_error"), _row(1, 3, 3, "failed", in_tok=0, out_tok=0),
        _row(2, 1, 1, "failed", in_tok=0, out_tok=0), _row(2, 1, 2, "success"),
    ]
    s = summarize_log_rows(rows)
    assert s["sessions"] == 4 and s["attempts"] == 8 and s["attempts_per_session"] == 2.0
    assert s["sessions_ok"] == 3 and s["first_try_ok"] == 1      # W1D1 only; W1D2 needed attempt 2
    assert (s["parse_errors"], s["validation_errors"], s["failed"]) == (2, 1, 2)
    assert s["validation_error_items"] == 2
    assert s["log_input_tokens"] == 600 and s["log_output_tokens"] == 300


def test_summarize_log_rows_empty():
    s = summarize_log_rows([])
    assert s["sessions"] == 0 and s["attempts_per_session"] is None and s["first_try_ok"] == 0


# ── report ───────────────────────────────────────────────────────────────────

def _result(config, sessions=2, cost=0.5, error=None):
    r = {
        "config": config, "program_id": 7, "wall_s": 12.3,
        "calls": {"input_tokens": 1000, "output_tokens": 300, "cache_read_tokens": 9000,
                  "cost_usd": cost, "mean_call_s": 4.1},
        "log": {"sessions": sessions, "attempts": sessions + 1, "first_try_ok": sessions - 1,
                "parse_errors": 0, "validation_errors": 1, "failed": 0},
    }
    if error:
        r["error"] = error
    return r


def test_flatten_and_render_table():
    row = flatten_result(_result("claude-sonnet-5", sessions=2, cost=0.5))
    assert row["cost_per_session"] == 0.25 and row["attempts"] == 3 and row["cache_read_tokens"] == 9000

    table = render_table([_result("claude-sonnet-4-6"), _result("claude-sonnet-5:disabled:low"),
                          {"config": "claude-opus-5", "error": "orchestrator.run returned None (see log)"}])
    lines = table.splitlines()
    assert lines[0].startswith("| config | sess | attempts | 1st-try OK |")
    assert lines[1].startswith("|---|---|")
    assert lines[2].startswith("| claude-sonnet-4-6 | 2 | 3 | 1 | 0 | 1 | 0 | 1000 | 300 | 9000 | 0.5000 | 12.3 | 4.1000 | 0.2500 |")
    assert "| claude-opus-5 | 0 | 0 |" in lines[4] and lines[4].rstrip().endswith("| — |")
    assert "claude-opus-5: orchestrator.run returned None" in table


def test_write_report_has_rows_and_meta(tmp_path):
    path = write_report([_result("claude-sonnet-5")], {"athlete_id": 1, "sessions": 2}, out_dir=tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert path.name.startswith("model_baseline_") and path.suffix == ".json"
    assert data["meta"]["athlete_id"] == 1
    assert data["rows"][0]["config"] == "claude-sonnet-5" and data["results"][0]["program_id"] == 7


# ── run_config wiring ────────────────────────────────────────────────────────

def test_run_config_patches_both_llm_entry_points_and_reads_the_log():
    """The recorder must wrap the names generate.py / explain.py actually call,
    the orchestrator gets max_sessions, the program is renamed, and the row
    summary comes from generation_log."""
    import explain as explain_mod
    import generate as generate_mod
    import orchestrator

    cfg = parse_config("claude-sonnet-5:disabled:low")
    base = Settings(database_url="postgresql://x", cost_limit_per_program=1.0, llm_provider="anthropic")   # pin: dev .env may be openrouter
    seen = {}

    def fake_run(athlete_id, settings, dry_run=False, deadline=None, max_sessions=None):
        seen["settings"] = settings
        seen["max_sessions"] = max_sessions
        # simulate the pipeline's two LLM calls through the (patched) module names
        generate_mod.create_message_with_retries(object(), model=settings.generation_model, messages=[])
        explain_mod.create_message_with_retries(object(), model=settings.explanation_model, messages=[])
        return 99

    log_rows = [_row(1, 1, 1, "success"), _row(1, 2, 1, "validation_error", ["x"]), _row(1, 2, 2, "success")]
    conn = MagicMock()
    with patch.object(orchestrator, "run", side_effect=fake_run), \
         patch("shared.llm.create_message_with_retries") as _unused, \
         patch.object(generate_mod, "create_message_with_retries", MagicMock(return_value=_response())), \
         patch.object(explain_mod, "create_message_with_retries", MagicMock(return_value=_response(stop="max_tokens"))), \
         patch("eval.model_baseline.get_connection", return_value=conn), \
         patch("eval.model_baseline.fetch_all", return_value=log_rows) as fetch_all, \
         patch("eval.model_baseline.execute") as execute:
        result = run_config(cfg, athlete_id=1, sessions=2, base_settings=base, cost_limit=4.0)

    assert seen["max_sessions"] == 2
    assert seen["settings"].generation_model == "claude-sonnet-5" and seen["settings"].cost_limit_per_program == 4.0
    assert result["program_id"] == 99 and result["config"] == "claude-sonnet-5:disabled:low"
    assert result["calls"]["calls"] == 2 and result["calls"]["stop_reasons"] == {"end_turn": 1, "max_tokens": 1}
    assert result["calls"]["by_role"]["generate"]["calls"] == 1 and result["calls"]["by_role"]["explain"]["calls"] == 1
    assert result["log"]["sessions"] == 2 and result["log"]["first_try_ok"] == 1
    assert fetch_all.call_args.args[2] == (99,)
    rename_sql, rename_params = execute.call_args.args[1], execute.call_args.args[2]
    assert "SET name = name ||" in rename_sql and rename_params == (" [baseline: claude-sonnet-5:disabled:low]", 99)
    conn.commit.assert_called_once()
    conn.close.assert_called_once()
    assert isinstance(result["call_records"][0], dict)


def test_run_config_reports_a_failed_run_without_touching_the_db():
    import orchestrator
    cfg = parse_config("claude-sonnet-4-6")
    with patch.object(orchestrator, "run", return_value=None), \
         patch("eval.model_baseline.get_connection") as get_connection:
        result = run_config(cfg, 1, 2, Settings(database_url="postgresql://x"))
    assert result["program_id"] is None and "returned None" in result["error"]
    get_connection.assert_not_called()
    assert flatten_result(result)["cost_per_session"] is None


def test_call_record_defaults():
    c = CallRecord(role="generate", model="m", elapsed_s=0.5)
    assert c.cost_usd == 0.0 and c.error is None and c.stop_reason is None
