#!/usr/bin/env python3
"""
Model baseline: run the real generation pipeline for one athlete under several
model configs and compare reliability, cost and latency side by side.

    cd oly-agent
    PYTHONUTF8=1 uv run python -m eval.model_baseline --athlete-id 1 --sessions 8 \
        --config claude-sonnet-4-6 \
        --config claude-sonnet-5 \
        --config claude-sonnet-5:disabled:low \
        [--cost-limit 3.0] [--delete]

Each ``--config`` is ``model[:thinking[:effort]]`` (thinking: adaptive | disabled;
effort: low | medium | high | xhigh | max; blank = model default) and becomes a
``Settings`` whose ``generation_*`` and ``explanation_*`` roles both point at that
model. For every config the tool calls ``orchestrator.run(..., max_sessions=N)`` —
the full ASSESS → PLAN → RETRIEVE → GENERATE → VALIDATE → EXPLAIN pipeline, capped
at N sessions — with the two LLM entry points wrapped in a recorder that captures
what ``generation_log`` does not: cache tokens, latency and stop reasons. The
attempt/status picture comes from ``generation_log`` afterwards.

Needs the corpus DB and both API keys; every config costs real money (one
partial program each). Programs are left as drafts named
``… [baseline: <config>]`` so they can be reviewed in the UI; ``--delete``
removes them once the report is written. The report is printed as a markdown
table and written to ``eval/model_baseline_<UTC timestamp>.json`` (no prompts
or responses in it — commit it next to the retrieval baseline).
"""

import argparse
import json
import logging
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
_AGENT = _HERE.parent
_REPO = _AGENT.parent
for p in (str(_REPO), str(_AGENT), str(_REPO / "oly-ingestion")):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared.config import Settings
from shared.db import execute, fetch_all, get_connection
from shared.llm import EFFORT_LEVELS, THINKING_MODES, estimate_cost, thinking_kwargs, usage_tokens

logger = logging.getLogger(__name__)

GENERATION_LOG_STATUSES = ("success", "parse_error", "validation_error", "failed")


# ── Config specs ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BaselineConfig:
    model: str
    thinking: str = ""      # "" | "adaptive" | "disabled"
    effort: str = ""        # "" | low | medium | high | xhigh | max

    @property
    def label(self) -> str:
        parts = [self.model]
        if self.thinking or self.effort:
            parts.append(self.thinking or "default")
        if self.effort:
            parts.append(self.effort)
        return ":".join(parts)

    def settings(self, base: Settings | None = None, **overrides) -> Settings:
        """A Settings whose generation and explanation roles both use this config."""
        kwargs = dict(
            generation_model=self.model,
            explanation_model=self.model,
            generation_thinking=self.thinking,
            generation_effort=self.effort,
            explanation_thinking=self.thinking,
            explanation_effort=self.effort,
        )
        if base is not None:
            for name in ("database_url", "anthropic_api_key", "openai_api_key",
                         "cost_limit_per_program"):
                kwargs[name] = getattr(base, name)
        kwargs.update(overrides)
        return Settings(**kwargs)


def parse_config(spec: str) -> BaselineConfig:
    """``model[:thinking[:effort]]`` → BaselineConfig; invalid values raise ValueError
    (the same rules `shared.llm.thinking_kwargs` applies at request time)."""
    parts = [p.strip() for p in spec.split(":")]
    if not 1 <= len(parts) <= 3 or not parts[0]:
        raise ValueError(f"config must be model[:thinking[:effort]], got {spec!r}")
    model, thinking, effort = (parts + ["", ""])[:3]
    if thinking not in THINKING_MODES:
        raise ValueError(f"{spec!r}: thinking must be one of {THINKING_MODES[1:]} (or blank)")
    if effort not in EFFORT_LEVELS:
        raise ValueError(f"{spec!r}: effort must be one of {EFFORT_LEVELS[1:]} (or blank)")
    thinking_kwargs(model, thinking, effort)   # rejects e.g. disabled + xhigh
    return BaselineConfig(model=model, thinking=thinking, effort=effort)


# ── Per-call recorder ─────────────────────────────────────────────────────────

@dataclass
class CallRecord:
    role: str               # "generate" | "explain"
    model: str | None
    elapsed_s: float
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    stop_reason: str | None = None
    cost_usd: float = 0.0
    error: str | None = None


class CallRecorder:
    """Wraps `create_message_with_retries` so each real API call is timed and
    its usage (incl. cache tokens) and stop reason captured. The wrapped
    function is called exactly as the pipeline calls it; exceptions propagate."""

    def __init__(self):
        self.calls: list[CallRecord] = []

    def wrap(self, role: str, real):
        def _recorded(client, **kwargs):
            model = kwargs.get("model")
            t0 = time.perf_counter()
            try:
                response = real(client, **kwargs)
            except Exception as e:
                self.calls.append(CallRecord(
                    role=role, model=model, elapsed_s=time.perf_counter() - t0,
                    error=f"{type(e).__name__}: {e}",
                ))
                raise
            elapsed = time.perf_counter() - t0
            usage = usage_tokens(getattr(response, "usage", None))
            self.calls.append(CallRecord(
                role=role, model=model, elapsed_s=elapsed,
                input_tokens=usage["input"], output_tokens=usage["output"],
                cache_read_tokens=usage["cache_read"], cache_creation_tokens=usage["cache_creation"],
                stop_reason=getattr(response, "stop_reason", None),
                cost_usd=estimate_cost(
                    usage["input"], usage["output"], model,
                    cache_read_tokens=usage["cache_read"],
                    cache_creation_tokens=usage["cache_creation"],
                ),
            ))
            return response
        return _recorded


def summarize_calls(calls: list[CallRecord]) -> dict:
    ok = [c for c in calls if c.error is None]
    by_role = {}
    for role in ("generate", "explain"):
        rc = [c for c in ok if c.role == role]
        by_role[role] = {
            "calls": len(rc),
            "mean_s": round(statistics.fmean(c.elapsed_s for c in rc), 2) if rc else None,
            "max_s": round(max(c.elapsed_s for c in rc), 2) if rc else None,
            "cost_usd": round(sum(c.cost_usd for c in rc), 4),
        }
    stop_reasons: dict[str, int] = {}
    for c in ok:
        key = c.stop_reason or "unknown"
        stop_reasons[key] = stop_reasons.get(key, 0) + 1
    return {
        "calls": len(calls),
        "errors": len(calls) - len(ok),
        "input_tokens": sum(c.input_tokens for c in ok),
        "output_tokens": sum(c.output_tokens for c in ok),
        "cache_read_tokens": sum(c.cache_read_tokens for c in ok),
        "cache_creation_tokens": sum(c.cache_creation_tokens for c in ok),
        "cost_usd": round(sum(c.cost_usd for c in ok), 4),
        "mean_call_s": round(statistics.fmean(c.elapsed_s for c in ok), 2) if ok else None,
        "stop_reasons": stop_reasons,
        "by_role": by_role,
    }


# ── generation_log summary ────────────────────────────────────────────────────

def summarize_log_rows(rows: list[dict]) -> dict:
    """Reliability picture from `generation_log` rows for one program
    (week_number, day_number, attempt_number, status, validation_errors)."""
    sessions: dict[tuple[int, int], list[dict]] = {}
    for r in rows:
        sessions.setdefault((r["week_number"], r["day_number"]), []).append(r)
    status_counts = {s: 0 for s in GENERATION_LOG_STATUSES}
    validation_items = 0
    for r in rows:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
        if r["status"] == "validation_error":
            validation_items += len(r.get("validation_errors") or [])
    first_try_ok = 0
    sessions_ok = 0
    for attempts in sessions.values():
        attempts.sort(key=lambda r: r["attempt_number"])
        if attempts and attempts[0]["status"] == "success":
            first_try_ok += 1
        if any(a["status"] == "success" for a in attempts):
            sessions_ok += 1
    n = len(sessions)
    return {
        "sessions": n,
        "sessions_ok": sessions_ok,
        "first_try_ok": first_try_ok,
        "attempts": len(rows),
        "attempts_per_session": round(len(rows) / n, 2) if n else None,
        "parse_errors": status_counts["parse_error"],
        "validation_errors": status_counts["validation_error"],
        "validation_error_items": validation_items,
        "failed": status_counts["failed"],
        "log_input_tokens": sum(int(r.get("input_tokens") or 0) for r in rows),
        "log_output_tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
    }


# ── Report ────────────────────────────────────────────────────────────────────

TABLE_COLUMNS = (
    ("config", "config"), ("sessions", "sess"), ("attempts", "attempts"),
    ("first_try_ok", "1st-try OK"), ("parse_errors", "parse err"),
    ("validation_errors", "valid err"), ("failed", "failed"),
    ("input_tokens", "in tok"), ("output_tokens", "out tok"), ("cache_read_tokens", "cache rd"),
    ("cost_usd", "cost $"), ("wall_s", "wall s"), ("mean_call_s", "call s"),
    ("cost_per_session", "$/session"),
)


def flatten_result(result: dict) -> dict:
    """One flat row per config for the table (and the JSON's `rows`)."""
    log = result.get("log") or {}
    calls = result.get("calls") or {}
    sessions = log.get("sessions") or 0
    cost = calls.get("cost_usd", 0.0)
    return {
        "config": result["config"],
        "program_id": result.get("program_id"),
        "error": result.get("error"),
        "sessions": sessions,
        "attempts": log.get("attempts", 0),
        "first_try_ok": log.get("first_try_ok", 0),
        "parse_errors": log.get("parse_errors", 0),
        "validation_errors": log.get("validation_errors", 0),
        "failed": log.get("failed", 0),
        "input_tokens": calls.get("input_tokens", 0),
        "output_tokens": calls.get("output_tokens", 0),
        "cache_read_tokens": calls.get("cache_read_tokens", 0),
        "cost_usd": cost,
        "wall_s": result.get("wall_s"),
        "mean_call_s": calls.get("mean_call_s"),
        "cost_per_session": round(cost / sessions, 4) if sessions else None,
    }


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}" if value < 10 else f"{value:.1f}"
    return str(value)


def render_table(results: list[dict]) -> str:
    rows = [flatten_result(r) for r in results]
    head = "| " + " | ".join(h for _, h in TABLE_COLUMNS) + " |"
    sep = "|" + "|".join("---" for _ in TABLE_COLUMNS) + "|"
    lines = [head, sep]
    for row in rows:
        lines.append("| " + " | ".join(_fmt(row[key]) for key, _ in TABLE_COLUMNS) + " |")
    errors = [r for r in rows if r["error"]]
    for r in errors:
        lines.append(f"\n{r['config']}: {r['error']}")
    return "\n".join(lines)


# ── Runner ────────────────────────────────────────────────────────────────────

_LOG_SQL = """
    SELECT week_number, day_number, attempt_number, status, validation_errors,
           input_tokens, output_tokens
    FROM generation_log WHERE program_id = %s
    ORDER BY week_number, day_number, attempt_number
"""


def run_config(cfg: BaselineConfig, athlete_id: int, sessions: int,
               base_settings: Settings, cost_limit: float | None = None) -> dict:
    import explain as explain_mod
    import generate as generate_mod
    import orchestrator

    overrides = {"cost_limit_per_program": cost_limit} if cost_limit is not None else {}
    settings = cfg.settings(base_settings, **overrides)
    recorder = CallRecorder()
    logger.info(f"=== baseline config {cfg.label} ===")
    t0 = time.perf_counter()
    with patch.object(generate_mod, "create_message_with_retries",
                      recorder.wrap("generate", generate_mod.create_message_with_retries)), \
         patch.object(explain_mod, "create_message_with_retries",
                      recorder.wrap("explain", explain_mod.create_message_with_retries)):
        program_id = orchestrator.run(athlete_id, settings, max_sessions=sessions)
    wall = round(time.perf_counter() - t0, 1)

    result = {
        "config": cfg.label, "model": cfg.model, "thinking": cfg.thinking, "effort": cfg.effort,
        "program_id": program_id, "wall_s": wall,
        "calls": summarize_calls(recorder.calls),
        "call_records": [asdict(c) for c in recorder.calls],
    }
    if program_id is None:
        result["error"] = "orchestrator.run returned None (see log)"
        return result

    conn = get_connection(settings.database_url)
    try:
        rows = fetch_all(conn, _LOG_SQL, (program_id,))
        result["log"] = summarize_log_rows(rows)
        execute(conn, "UPDATE generated_programs SET name = name || %s, updated_at = NOW() WHERE id = %s",
                (f" [baseline: {cfg.label}]", program_id))
        conn.commit()
    finally:
        conn.close()
    return result


def delete_programs(database_url: str, program_ids: list[int]) -> None:
    """Remove baseline drafts. Mirrors web.queries.program.delete_program: NULL
    the training-log back-references first (no ON DELETE CASCADE), then delete
    the program row, which cascades to sessions / exercises / generation_log."""
    if not program_ids:
        return
    conn = get_connection(database_url)
    try:
        execute(conn, """
            UPDATE training_log_exercises SET session_exercise_id = NULL
            WHERE session_exercise_id IN (
                SELECT se.id FROM session_exercises se
                JOIN program_sessions ps ON ps.id = se.session_id
                WHERE ps.program_id = ANY(%s))
        """, (program_ids,))
        execute(conn, """
            UPDATE training_logs SET session_id = NULL
            WHERE session_id IN (SELECT id FROM program_sessions WHERE program_id = ANY(%s))
        """, (program_ids,))
        n = execute(conn, "DELETE FROM generated_programs WHERE id = ANY(%s)", (program_ids,))
        conn.commit()
        logger.info(f"Deleted {n} baseline program(s): {program_ids}")
    finally:
        conn.close()


def write_report(results: list[dict], meta: dict, out_dir: Path = _HERE) -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"model_baseline_{ts}.json"
    payload = {"meta": meta, "rows": [flatten_result(r) for r in results], "results": results}
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--athlete-id", type=int, required=True)
    ap.add_argument("--sessions", type=int, default=8, help="sessions to generate per config (max_sessions)")
    ap.add_argument("--config", action="append", required=True, metavar="MODEL[:THINKING[:EFFORT]]",
                    help="repeatable; e.g. claude-sonnet-4-6, claude-sonnet-5, claude-sonnet-5:disabled:low")
    ap.add_argument("--cost-limit", type=float, default=None,
                    help="per-program cost limit for the run (default: Settings.cost_limit_per_program)")
    ap.add_argument("--delete", action="store_true", help="delete the baseline drafts after the report")
    ap.add_argument("--out-dir", type=Path, default=_HERE)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        configs = [parse_config(c) for c in args.config]
    except ValueError as e:
        ap.error(str(e))
    if args.sessions < 1:
        ap.error("--sessions must be ≥ 1")

    base = Settings()
    results = []
    for cfg in configs:
        try:
            results.append(run_config(cfg, args.athlete_id, args.sessions, base, args.cost_limit))
        except Exception as e:      # keep going — one bad config must not lose the others
            logger.exception(f"config {cfg.label} crashed")
            results.append({"config": cfg.label, "model": cfg.model, "thinking": cfg.thinking,
                            "effort": cfg.effort, "error": f"{type(e).__name__}: {e}"})

    meta = {
        "athlete_id": args.athlete_id, "sessions": args.sessions,
        "cost_limit": args.cost_limit if args.cost_limit is not None else base.cost_limit_per_program,
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "configs": [c.label for c in configs],
        "deleted": bool(args.delete),
    }
    path = write_report(results, meta, args.out_dir)
    print()
    print(render_table(results))
    print(f"\nreport: {path}")

    if args.delete:
        delete_programs(base.database_url, [r["program_id"] for r in results if r.get("program_id")])
    return 0 if all(not r.get("error") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
