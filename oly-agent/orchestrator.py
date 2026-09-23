# oly-agent/orchestrator.py
"""
Main agent pipeline: runs all 6 steps in order to generate a program.

Usage:
    python orchestrator.py --athlete-id 1
    python orchestrator.py --athlete-id 1 --dry-run   # ASSESS + PLAN only, no generation
"""

import argparse
import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from assess import assess
from explain import explain
from generate import build_session_prompt, generate_session_with_retries
from macrocycle import create_macrocycle, load_macrocycle, next_block_index, plan_blocks
from macrocycle import macrocycle_weeks as macrocycle_weeks_for
from models import AthleteContext, ProgramPlan, RetrievalContext, SessionTemplate
from phase_profiles import PHASE_PROFILES
from plan import plan
from principle_matcher import build_session_state, select_principles
from retrieve import build_session_query, retrieve, retrieve_session_context
from validate import validate_session
from weight_resolver import apply_projected_maxes, attach_source_chunk_ids, resolve_exercise_ids, resolve_weights

from shared.config import Settings
from shared.constants import (
    BLOCK_TEMPLATE_MAX_CHARS,
    GENERATION_WEEK_CONCURRENCY,
    MAX_CONTEXT_CHUNKS,
    MAX_PRINCIPLES_IN_PROMPT,
    MIN_SESSION_DURATION_MINUTES,
)
from shared.db import execute, execute_returning, fetch_all, get_connection
from shared.formulas import estimate_session_minutes, round_kg
from shared.llm import create_llm_client, estimate_cost

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run(
    athlete_id: int,
    settings: Settings,
    dry_run: bool = False,
    deadline: float | None = None,
    max_sessions: int | None = None,
    duration_weeks: int | None = None,
    week_concurrency: int | None = None,
    new_macrocycle: bool = False,
    macrocycle_weeks: int | None = None,
    macrocycle_id: int | None = None,
) -> int | None:
    """Generate a complete training program for the given athlete.

    Args:
        athlete_id: Primary key in the athletes table.
        settings: Unified settings (DB URL, API keys, model config).
        dry_run: If True, runs only ASSESS + PLAN and prints the plan without generating.
        deadline: Optional time.monotonic() timestamp. Checked between sessions —
            when exceeded, generation stops cleanly (draft marked with a
            rationale) instead of letting the job's cancellation leave a zombie
            thread burning LLM spend past the timeout (WEB-M8).
        max_sessions: Optional cap on generated sessions. Used by
            ``eval.model_baseline`` to run the real pipeline on a slice of a
            program: generation stops once this many sessions exist, the
            max-test session is skipped, EXPLAIN still runs (it is part of the
            per-program cost) and the draft's rationale says it is partial.
        week_concurrency: Weeks 2..N generated at once (AUD-4); None =
            GENERATION_WEEK_CONCURRENCY, 1 = sequential. Week 1 always runs
            first and alone.
        new_macrocycle: Plan a macrocycle (PLAN-3e) — the block sequence to the
            goal's competition date, else `macrocycle_weeks` total — store it
            and generate its first block. `duration_weeks` is ignored.
        macrocycle_id: Generate the next unrealised block of this macrocycle.

    Returns:
        program_id of the created program, or None on failure / dry-run.
    """
    conn = get_connection(settings.database_url)

    vector_loader = _make_vector_loader(settings)

    # ── Build exercise lookup (name -> id) ────────────────────
    exercise_rows = fetch_all(conn, "SELECT id, name FROM exercises")
    exercise_lookup = {r["name"].lower(): r["id"] for r in exercise_rows}

    try:
        # ── Step 1: ASSESS ────────────────────────────────────
        logger.info(f"=== Step 1: ASSESS (athlete {athlete_id}) ===")
        _t0 = time.perf_counter()
        athlete_context = assess(athlete_id, conn)
        logger.info("Step 1 complete", extra={"step": "assess", "duration_seconds": round(time.perf_counter() - _t0, 2)})

        # ── Step 2: PLAN ──────────────────────────────────────
        logger.info("=== Step 2: PLAN ===")
        _t0 = time.perf_counter()
        mc_id, mc_index, mc_block = None, None, None
        if new_macrocycle or macrocycle_id is not None:
            mc_id, mc_index, mc_block = _macrocycle_block(conn, athlete_id, athlete_context, macrocycle_id,
                                                          macrocycle_weeks, dry_run)
            if mc_block is None:
                return None
        program_plan = plan(athlete_context, conn, settings, duration_weeks=duration_weeks, block=mc_block)
        logger.info("Step 2 complete", extra={"step": "plan", "duration_seconds": round(time.perf_counter() - _t0, 2)})

        if dry_run:
            _print_plan(athlete_context, program_plan)
            return None

        # ── Projected maxes for realization phase ─────────────
        # In realization, use target maxes (from athlete_goals) for weight
        # calculations instead of current maxes, so percentages reflect
        # competition-day intensity. Other phases always use current maxes.
        effective_maxes = apply_projected_maxes(
            athlete_context.maxes,
            athlete_context.active_goal,
            program_plan.phase,
        )

        # ── Create program record ─────────────────────────────
        llm_client = create_llm_client(settings)
        program_id = _create_program_record(conn, athlete_id, athlete_context, program_plan, settings, max_sessions,
                                            macrocycle=(mc_id, mc_index) if mc_id is not None else None)

        # ── Step 3: RETRIEVE ──────────────────────────────────
        logger.info("=== Step 3: RETRIEVE ===")
        _t0 = time.perf_counter()
        retrieval_context = retrieve(athlete_context, program_plan, conn, vector_loader, settings=settings)
        logger.info("Step 3 complete", extra={"step": "retrieve", "program_id": program_id, "duration_seconds": round(time.perf_counter() - _t0, 2)})

        # ── Step 4+5: GENERATE + VALIDATE (session by session) ─
        logger.info("=== Step 4+5: GENERATE + VALIDATE ===")
        _t_gen_start = time.perf_counter()
        # Athlete-specific limit takes precedence over the global setting.
        # `is not None`, not `or`, so an explicit 0 ("no spend") is honored (A-L8).
        cost_limit = athlete_context.athlete.get("cost_limit_usd")
        if cost_limit is None:
            cost_limit = settings.cost_limit_per_program
        g = _GenerationRun(
            settings=settings, program_id=program_id, athlete_context=athlete_context,
            program_plan=program_plan, retrieval_context=retrieval_context,
            effective_maxes=effective_maxes, exercise_lookup=exercise_lookup,
            llm_client=llm_client, vector_loader=vector_loader, deadline=deadline,
            cost_limit=cost_limit, spend=_SpendGuard(cost_limit),
        )

        days_per_week = len(program_plan.session_templates)
        total_planned = len(program_plan.weekly_targets) * days_per_week
        # max_sessions keeps the first N sessions in (week, day) order
        capped = max_sessions is not None and max_sessions < total_planned
        concurrency = max(1, GENERATION_WEEK_CONCURRENCY if week_concurrency is None else week_concurrency)
        outcomes = _run_weeks(g, conn, _schedule_weeks(program_plan, max_sessions), concurrency)

        all_sessions_data: list[dict] = [s for o in outcomes for s in o.sessions]
        failed_sessions: list[str] = [f for o in outcomes for f in o.failed]
        cumulative_cost = g.spend.spent

        reason = _abort_reason(outcomes, len(all_sessions_data), total_planned, cost_limit, cumulative_cost)
        if reason is not None:
            _mark_program_draft(conn, program_id, reason=reason)
            return program_id

        if capped:
            next_week = program_plan.weekly_targets[max_sessions // days_per_week].week_number
            next_day = program_plan.session_templates[max_sessions % days_per_week].day_number
            logger.info(f"  Session cap reached ({max_sessions}); stopping before W{next_week}D{next_day}")
        else:
            _add_max_test_session(conn, program_id, athlete_context, program_plan, exercise_lookup, all_sessions_data)

        logger.info("Step 4+5 complete", extra={
            "step": "generate_validate",
            "program_id": program_id,
            "duration_seconds": round(time.perf_counter() - _t_gen_start, 2),
            "total_cost_usd": round(cumulative_cost, 4),
        })

        # Sessions that exhausted their retries were stored empty. The program
        # stays in 'draft' (its creation status); surface the gap loudly here
        # and in the rationale below so it isn't activated with holes in it.
        if failed_sessions:
            logger.error(
                f"Program {program_id} incomplete: {len(failed_sessions)} session(s) "
                f"failed generation ({', '.join(failed_sessions)}). "
                f"Re-run generation or fill the sessions in manually."
            )

        # ── Step 6: EXPLAIN ───────────────────────────────────
        logger.info("=== Step 6: EXPLAIN ===")
        _t0 = time.perf_counter()
        rationale, cumulative_cost = _explain_program(g, all_sessions_data, cumulative_cost)
        rationale = _prefix_rationale_warnings(
            rationale, max_sessions if capped else None, len(all_sessions_data), total_planned, failed_sessions,
        )
        execute(
            conn,
            "UPDATE generated_programs SET rationale = %s, updated_at = NOW() WHERE id = %s",
            (rationale, program_id),
        )
        conn.commit()

        logger.info("Step 6 complete", extra={"step": "explain", "program_id": program_id, "duration_seconds": round(time.perf_counter() - _t0, 2)})
        logger.info(
            f"Program {program_id} complete. "
            f"Total cost: ${cumulative_cost:.4f}",
            extra={"program_id": program_id, "total_cost_usd": round(cumulative_cost, 4)},
        )
        return program_id

    except Exception as e:
        import traceback
        logger.error(f"Generation failed: {e}\n{traceback.format_exc()}")
        conn.rollback()
        return None
    finally:
        conn.close()
        if vector_loader:
            try:
                vector_loader.close()
            except Exception as e:
                logger.debug(f"vector_loader.close() failed (non-fatal): {e}")


def _create_program_record(conn, athlete_id: int, athlete_context: AthleteContext, program_plan: ProgramPlan,
                           settings: Settings, max_sessions: int | None,
                           macrocycle: tuple[int, int] | None = None) -> int:
    """INSERT the draft generated_programs row and commit; returns its id."""
    # recorded-only, not the estimation-merged dict: else a later real max
    # replacing an estimate reads as "strength progress" (audit5-L3)
    maxes_snapshot = athlete_context.recorded_maxes or athlete_context.maxes
    athlete_snapshot = _build_athlete_snapshot(athlete_context.athlete)

    program_id = execute_returning(
        conn,
        """
        INSERT INTO generated_programs
            (athlete_id, name, status, phase, duration_weeks,
             sessions_per_week, start_date,
             athlete_snapshot, maxes_snapshot, generation_params,
             macrocycle_id, macrocycle_block_index)
        VALUES (%s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            athlete_id,
            f"{program_plan.phase.replace('_', ' ').title()} Block"
            + (f" {macrocycle[1] + 1} of macrocycle" if macrocycle else "") + f" — {date.today()}",
            program_plan.phase,
            program_plan.duration_weeks,
            program_plan.sessions_per_week,
            date.today(),
            json.dumps(athlete_snapshot, default=str),
            json.dumps(maxes_snapshot),
            json.dumps({
                "model": settings.generation_model,
                "temperature": settings.generation_temperature,
                "top_k": settings.vector_search_top_k,
                "thinking": settings.generation_thinking or None,
                "effort": settings.generation_effort or None,
                **({"max_sessions": max_sessions} if max_sessions is not None else {}),
            }),
            macrocycle[0] if macrocycle else None,
            macrocycle[1] if macrocycle else None,
        ),
    )
    conn.commit()
    logger.info(f"Created program record: id={program_id}", extra={"program_id": program_id})
    return program_id


def _abort_reason(outcomes: list, n_sessions: int, total_planned: int,
                  cost_limit: float, spent: float) -> str | None:
    """The draft rationale for a deadline / cost abort, or None when no week aborted.

    Weeks record their own abort; the earliest (week, day) names the stop point.
    """
    aborts = [o.abort for o in outcomes if o.abort is not None]
    if not aborts:
        return None
    kind, week_number, day_number = min(aborts, key=lambda a: (a[1], a[2]))
    if kind == "deadline":
        return (
            f"# Generation Aborted — Time Limit\n"
            f"Stopped before W{week_number}D{day_number}: the job's time "
            f"limit was reached. {n_sessions} of {total_planned} "
            f"sessions were generated. Re-run generation to continue."
        )
    # Store a self-explanatory rationale so a cost-truncated draft
    # is never mistaken for a finished program (A-L1).
    return (
        f"# Generation Aborted — Cost Limit\n"
        f"Stopped before W{week_number}D{day_number}: cost limit "
        f"${cost_limit:.2f} reached (spent ${spent:.4f}). "
        f"{n_sessions} of {total_planned} sessions were "
        f"generated. Re-run generation or raise the cost limit before "
        f"activating this program."
    )


def _add_max_test_session(conn, program_id: int, athlete_context: AthleteContext, program_plan: ProgramPlan,
                          exercise_lookup: dict, all_sessions_data: list[dict]) -> None:
    """Max test session (realization / intensification, or per the athlete's
    max_test preference — PLAN-2 §3.8): saved and appended to all_sessions_data."""
    peak_week = compute_peak_week(program_plan.weekly_targets)
    from plan import training_preferences
    max_test_pref = training_preferences(athlete_context.athlete)["max_test"]
    wants_max_test = {"always": True, "never": False}.get(
        max_test_pref, bool(PHASE_PROFILES.get(program_plan.phase, {}).get("includes_max_test")))
    if not wants_max_test or peak_week is None:
        return
    max_test_day = compute_max_test_day(program_plan.session_templates, program_plan.sessions_per_week)
    logger.info(f"  Building max test session W{peak_week}D{max_test_day}")
    max_test_exercises = _build_max_test_session(athlete_context, exercise_lookup)
    max_test_template = SessionTemplate(
        day_number=max_test_day,
        label="Max Testing — Snatch & Clean & Jerk",
        primary_movement="snatch",
        secondary_movements=["clean"],
        session_volume_share=0.0,
        notes="Work up to a max single on snatch and clean & jerk. Log new maxes.",
    )
    max_test_session_id = _save_session(
        conn, program_id, peak_week, max_test_day,
        max_test_template, max_test_exercises,
    )
    all_sessions_data.append({
        "week": peak_week,
        "day": max_test_day,
        "label": max_test_template.label,
        "session_id": max_test_session_id,
        "exercises": max_test_exercises,
    })


def _explain_program(g: "_GenerationRun", all_sessions_data: list[dict], cumulative_cost: float) -> tuple[str, float]:
    """Step 6: (rationale, cumulative cost including the explain call)."""
    if cumulative_cost > g.cost_limit:
        # Generation landed at/over the limit — don't spend more on the
        # rationale call, and don't hide that from the reader (AGT-L7).
        logger.warning("Skipping EXPLAIN — cost limit already reached")
        return (
            "# Rationale Skipped — Cost Limit\n"
            "The program generated, but the cost limit was reached before "
            "the rationale step. Re-run generation with a higher limit to "
            "get a full program rationale."
        ), cumulative_cost
    rationale, explain_in_tokens, explain_out_tokens = explain(
        athlete_context=g.athlete_context,
        plan=g.program_plan,
        program_sessions=all_sessions_data,
        llm_client=g.llm_client,
        settings=g.settings,
    )
    # The explain call is paid too — count it (AGT-L7)
    return rationale, cumulative_cost + estimate_cost(
        explain_in_tokens, explain_out_tokens, g.settings.explanation_model)


def _prefix_rationale_warnings(rationale: str, session_cap: int | None, n_sessions: int,
                               total_planned: int, failed_sessions: list[str]) -> str:
    """Prepend the partial-program (session cap) and failed-session warnings."""
    if session_cap is not None:
        rationale = (
            f"# Partial Program — Session Cap\n"
            f"Generation was capped at {session_cap} session(s) for a model baseline run; "
            f"{n_sessions} of {total_planned} planned sessions exist. "
            f"Do not activate this program.\n\n" + rationale
        )
    if failed_sessions:
        rationale = (
            f"# Generation Warning\n"
            f"{len(failed_sessions)} session(s) could not be generated and were stored "
            f"empty: {', '.join(failed_sessions)}. Re-run generation before activating "
            f"this program.\n\n" + rationale
        )
    return rationale


def _make_vector_loader(settings: Settings):
    """The corpus retriever, imported from oly-ingestion at runtime. Returns None
    (and generation runs with NO knowledge context — every session prompt loses
    its [Cn] chunks) when the import or construction fails: missing `openai` in
    this venv, no OPENAI_API_KEY, or no DB. The agent's pyproject therefore
    lists `openai` + `tiktoken` as core dependencies; unit tests patch this to
    None so they never touch OpenAI or Postgres (DOG-1 finding)."""
    try:
        ingestion_path = Path(__file__).parent.parent / "oly-ingestion"
        if str(ingestion_path) not in sys.path:
            sys.path.insert(0, str(ingestion_path))
        from loaders.vector_loader import VectorLoader
        loader = VectorLoader(settings)
        logger.info("VectorLoader initialized")
        return loader
    except Exception as e:
        logger.warning(f"VectorLoader not available (vector search disabled): {e}")
        return None


# ── Concurrent weeks (AUD-4) ───────────────────────────────────

class _SpendGuard:
    """Program-level LLM spend shared by the week workers, plus the stop flag.

    try_start() admits a session while ``spent + in_flight × avg`` stays within
    the limit, where avg is the mean cost of the sessions finished so far (week
    1 always finishes first, so it is known before any concurrency). With
    nothing in flight that is exactly the sequential rule — abort once spent >
    limit — so concurrency adds no overshoot beyond the one session the
    sequential guard already allows.
    """

    def __init__(self, limit: float):
        self.limit = limit
        self.spent = 0.0
        self._completed = 0
        self._in_flight = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def try_start(self) -> bool:
        with self._lock:
            avg = self.spent / self._completed if self._completed else 0.0
            if self.spent + self._in_flight * avg > self.limit:
                return False
            self._in_flight += 1
            return True

    def finish(self, cost: float) -> None:
        with self._lock:
            self._in_flight -= 1
            self._completed += 1
            self.spent += cost

    def abandon(self) -> None:
        with self._lock:
            self._in_flight -= 1

    def stop(self) -> None:
        self._stop.set()

    def stopped(self) -> bool:
        return self._stop.is_set()


@dataclass
class _WeekOutcome:
    week_number: int
    sessions: list[dict] = field(default_factory=list)   # all_sessions_data rows, day order
    templates: list = field(default_factory=list)        # SessionTemplate per row (deferred save)
    failed: list[str] = field(default_factory=list)
    abort: tuple[str, int, int] | None = None            # ("deadline" | "cost", week, day)


def summarize_block_template(exercises: list[dict], max_chars: int = BLOCK_TEMPLATE_MAX_CHARS) -> str | None:
    """Compact form of one session for later weeks' prompts (AUD-4):
    ``Snatch 2x3 @ 55%, 5x2 @ 75%; Back Squat 5x5 @ 72%``, consecutive rows
    of one exercise merged, cut at a whole exercise to ≤ max_chars. None for an
    empty (failed) session."""
    groups: list[tuple[str, list[str]]] = []
    for ex in sorted(exercises, key=lambda e: e.get("exercise_order") or 0):
        name = ex.get("exercise_name")
        if not name:
            continue
        dose = f"{ex.get('sets')}x{ex.get('reps')}"
        pct = ex.get("intensity_pct")
        if pct is not None:
            try:
                dose += f" @ {float(pct):g}%"
            except (TypeError, ValueError):
                pass
        if groups and groups[-1][0] == name:
            groups[-1][1].append(dose)
        else:
            groups.append((name, [dose]))
    if not groups:
        return None
    text = "; ".join(f"{name} {', '.join(doses)}" for name, doses in groups)
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 2]
    if "; " in cut:
        cut = cut[: cut.rfind("; ")]
    return cut + " …"


def _block_templates_from(week: _WeekOutcome) -> dict[int, str]:
    """{day_number: summary} of a generated week — week 1's, for weeks 2..N."""
    out: dict[int, str] = {}
    for s in week.sessions:
        summary = summarize_block_template(s["exercises"])
        if summary:
            out[s["day"]] = summary
    return out


# ── Generation state + per-session steps (AUD-6 split of run) ──

@dataclass
class _GenerationRun:
    """Everything a session needs that is fixed for the whole program.

    Shared read-only by the week workers, except `spend` (locked) and
    `session_context_cache`, which only the main thread touches (retrieval
    never runs on a worker — AUD-4)."""
    settings: Settings
    program_id: int
    athlete_context: AthleteContext
    program_plan: ProgramPlan
    retrieval_context: RetrievalContext
    effective_maxes: dict[str, float]
    exercise_lookup: dict
    llm_client: object
    vector_loader: object
    deadline: float | None
    cost_limit: float
    spend: "_SpendGuard"
    session_context_cache: dict[str, list[dict]] = field(default_factory=dict)  # query → chunks (RAG-H4)
    available_exercise_names: list[str] = field(init=False)
    fault_exercise_names: list[str] | None = field(init=False)

    def __post_init__(self):
        self.available_exercise_names = [e["name"] for e in self.retrieval_context.available_exercises]
        # Flat list of fault-correction exercise names for validate_session's fault-coverage check
        self.fault_exercise_names = (
            [ex["name"] for exs in self.retrieval_context.fault_exercises.values() for ex in exs]
            if self.retrieval_context.fault_exercises else None
        )


@dataclass
class _WeekState:
    """Within-week running state; reset every week, so weeks are independent."""
    cumulative_reps: dict[str, int] = field(default_factory=dict)   # Prilepin zone → comp-lift reps
    already_prescribed: list[dict] = field(default_factory=list)


def _schedule_weeks(program_plan: ProgramPlan, max_sessions: int | None) -> list[tuple]:
    """[(week_target, day_limit)] for every week with at least one session to
    generate; max_sessions keeps the first N sessions in (week, day) order."""
    days_per_week = len(program_plan.session_templates)

    def _day_limit(week_index: int) -> int:
        if max_sessions is None:
            return days_per_week
        return max(0, min(days_per_week, max_sessions - week_index * days_per_week))

    return [(wt, _day_limit(i)) for i, wt in enumerate(program_plan.weekly_targets) if _day_limit(i) > 0]


def _run_weeks(g: _GenerationRun, conn, scheduled: list[tuple], concurrency: int) -> list["_WeekOutcome"]:
    """Scheduling (AUD-4).

    Week 1 runs first, alone; weeks 2..N then run up to
    GENERATION_WEEK_CONCURRENCY at a time (days in order within a week).
    Trade-off: a week can't see the week before it, so continuity comes
    from week 1 instead — each later session's prompt carries week 1's
    same-day session as the block's template. Anchoring on week 1 (not
    N−1) is what lets weeks 2..N run in parallel; it also stops the
    week-to-week drift a chained N−1 anchor would allow (Jerk → Push
    Jerk → Jerk from Behind Neck, program 29). Week 1 going first also
    warms the prompt cache before the concurrent calls. The anchor is
    passed at every concurrency, so prompts (and the rows they produce)
    do not depend on the setting; 1 reproduces the sequential run.
    """
    outcomes: list[_WeekOutcome] = []
    block_templates: dict[int, str] = {}
    if scheduled:
        first_week, first_limit = scheduled[0]
        outcomes.append(_generate_week(g, first_week, conn, first_limit, block_templates))
        block_templates = _block_templates_from(outcomes[0])
    rest = scheduled[1:]
    if rest and not g.spend.stopped():
        if concurrency == 1 or len(rest) == 1:
            for wt, limit in rest:
                outcomes.append(_generate_week(g, wt, conn, limit, block_templates))
                if g.spend.stopped():
                    break
        else:
            outcomes += _run_weeks_concurrently(g, conn, rest, block_templates, concurrency)
    return outcomes


def _run_weeks_concurrently(g: _GenerationRun, conn, rest: list[tuple], block_templates: dict[int, str],
                            concurrency: int) -> list["_WeekOutcome"]:
    """Weeks 2..N on a thread pool; sessions are saved here, on the main
    thread's connection, in (week, day) order. Re-raises the first worker error
    after the pool has joined."""
    # Retrieval stays on this thread, in the sequential order, so the
    # query cache fills exactly as it would sequentially.
    prefetched = {
        wt.week_number: {
            t.day_number: _session_chunks(g, wt, t)
            for t in g.program_plan.session_templates[:limit]
        }
        for wt, limit in rest
    }
    outcomes: list[_WeekOutcome] = []
    error: BaseException | None = None
    with ThreadPoolExecutor(
        max_workers=min(concurrency, len(rest)), thread_name_prefix="gen-week",
    ) as pool:
        futures = [pool.submit(_week_worker, g, wt, limit, block_templates, prefetched[wt.week_number])
                   for wt, limit in rest]
        try:
            for fut in futures:  # week order: rows are inserted by (week, day)
                try:
                    out = fut.result()
                except Exception as e:
                    error = error or e
                    continue
                if error is None:
                    for session, template in zip(out.sessions, out.templates, strict=True):
                        session["session_id"] = _save_session(
                            conn, g.program_id, session["week"], session["day"],
                            template, session["exercises"],
                        )
                    outcomes.append(out)
        except BaseException:
            g.spend.stop()  # a failed save: stop the workers before the pool joins them
            raise
    if error is not None:
        raise error
    return outcomes


def _week_worker(g: _GenerationRun, week_target, day_limit: int, block_templates: dict[int, str],
                 prefetched: dict[int, list[dict]]) -> "_WeekOutcome":
    # Own psycopg2 connection per worker — never share one
    # across threads. It carries only generation_log writes.
    worker_conn = get_connection(g.settings.database_url)
    try:
        out = _generate_week(
            g, week_target, worker_conn, day_limit, block_templates,
            prefetched=prefetched, save=False,
        )
        worker_conn.commit()
        return out
    except BaseException:
        g.spend.stop()
        worker_conn.rollback()
        raise
    finally:
        worker_conn.close()


def _generate_week(g: _GenerationRun, week_target, conn, day_limit: int, block_templates: dict[int, str],
                   prefetched: dict[int, list[dict]] | None = None, save: bool = True) -> "_WeekOutcome":
    """Generate one week's sessions in day order on `conn`.

    Weeks are independent: the Prilepin budget (week_cumulative_reps)
    and the already-prescribed list reset every week, and the only
    program-level state — spend, the stop flag — lives in `g.spend`.
    `prefetched` ({day: chunks}) replaces retrieval on worker threads;
    `save=False` buffers sessions for the main thread to insert in
    (week, day) order.
    """
    week = _WeekState()
    outcome = _WeekOutcome(week_target.week_number)
    for session_template in g.program_plan.session_templates[:day_limit]:
        if g.spend.stopped():
            break  # another week hit the deadline / cost limit / an error
        logger.info(
            f"  Generating W{week_target.week_number}D{session_template.day_number}: {session_template.label}"
        )
        prefetched_chunks = prefetched[session_template.day_number] if prefetched is not None else None
        if not _generate_session(g, conn, week_target, session_template, week, outcome,
                                 block_templates.get(session_template.day_number), prefetched_chunks, save):
            break
    return outcome


def _session_chunks(g: _GenerationRun, week_target, session_template) -> list[dict]:
    """Per-session knowledge context (RAG-H4): this template's own query
    (cached per template + phase + intensity band), fault chunks
    round-robined, per-source cap — not the same four snippets for all
    sixteen sessions. Called on the main thread only: the VectorLoader holds a
    psycopg2 connection and the query cache is a plain dict (AUD-4)."""
    return retrieve_session_context(
        g.vector_loader, g.athlete_context, g.program_plan, session_template, week_target,
        g.retrieval_context, top_k=g.settings.vector_search_top_k, cache=g.session_context_cache,
    )


def _session_principles(g: _GenerationRun, week_target, session_template) -> list[dict]:
    """Per-session principle selection (RAG-H3): the plan's list is a
    phase/level superset; apply every condition field for THIS week and THIS
    day's primary movement before prompting/validating. AUD-1: ranked by
    overlap with this session's retrieval query and capped per category;
    limited to what the prompt shows so the validator enforces exactly the
    rules the model saw."""
    session_state = build_session_state(
        g.athlete_context, g.program_plan, week_target.week_number, session_template
    )
    return select_principles(
        g.retrieval_context.active_principles, session_state,
        limit=MAX_PRINCIPLES_IN_PROMPT,
        query=build_session_query(g.athlete_context, g.program_plan, session_template, week_target),
    )


def _retrieval_set(session_chunks: list[dict]) -> list[dict]:
    """What this call was shown, labelled as in the prompt — logged with
    every attempt so retrieval can be audited per call (RAG-M5)."""
    return [
        {
            "label": f"C{i}", "id": c.get("id"), "chunk_type": c.get("chunk_type"),
            "source_id": c.get("source_id"), "similarity": c.get("similarity"),
            "score": c.get("score"), "session_query": c.get("session_query"),
        }
        for i, c in enumerate(session_chunks[:MAX_CONTEXT_CHUNKS], 1)
    ]


def _admit_session(g: _GenerationRun, outcome: "_WeekOutcome", week_number: int, day_number: int) -> bool:
    """The deadline and cost guards; on False the week's abort is recorded and
    every week is told to stop. On True one session is in flight in `g.spend`."""
    # Deadline guard — the ARQ job timeout can only cancel the
    # awaiting coroutine, never this worker thread, so the thread
    # must stop itself before the deadline (WEB-M8). The draft is
    # marked once, by the main thread, after every week returns.
    if g.deadline is not None and time.monotonic() > g.deadline:
        logger.error(
            f"Job deadline reached. Aborting before W{week_number}D{day_number}."
        )
        outcome.abort = ("deadline", week_number, day_number)
        g.spend.stop()
        return False
    # Cost guard (limit hoisted above the loop; also gates EXPLAIN).
    # try_start also counts sessions other weeks have in flight.
    if not g.spend.try_start():
        logger.error(
            f"Cost limit exceeded: ${g.spend.spent:.4f} > "
            f"${g.cost_limit:.2f}. Aborting before "
            f"W{week_number}D{day_number}."
        )
        outcome.abort = ("cost", week_number, day_number)
        g.spend.stop()
        return False
    return True


def _resolve_session_exercises(g: _GenerationRun, exercises: list[dict], week_number: int, day_number: int,
                               session_chunks: list[dict]) -> list[dict]:
    """Exercise ids, absolute weights and [Cn]-citation chunk ids."""
    exercises = resolve_exercise_ids(exercises, g.exercise_lookup)
    unresolved = [ex.get("exercise_name") for ex in exercises if ex.get("exercise_id") is None]
    if unresolved:
        logger.warning(
            f"W{week_number}D{day_number}: {len(unresolved)} exercise(s) have no "
            f"exercise_id (will store with NULL): {unresolved}"
        )
    exercises = resolve_weights(exercises, g.effective_maxes)
    # Trace against what THIS session was shown: [Cn] citations in the
    # rationale map onto the labelled list; heuristic fallback otherwise
    return attach_source_chunk_ids(exercises, {
        "context_chunks": session_chunks[:MAX_CONTEXT_CHUNKS],
        "programming_rationale": session_chunks,
        "fault_correction_chunks": [
            c for c in session_chunks if c.get("chunk_type") == "fault_correction"
        ],
    })


def _generate_session(g: _GenerationRun, conn, week_target, session_template, week: _WeekState,
                      outcome: "_WeekOutcome", block_template: str | None,
                      prefetched_chunks: list[dict] | None, save: bool) -> bool:
    """Prompt → guards → generate/validate → resolve → save one session.

    Returns False when the deadline or cost guard stopped the week (the abort
    is on `outcome`); a failed generation is stored empty and returns True.
    """
    week_number = week_target.week_number
    day_number = session_template.day_number

    # Session rep target from the week's volume table (Prilepin / Medvedev — PLAN-3d)
    from shared.volume_tables import session_rep_target as _session_rep_target
    session_rep_target = _session_rep_target(
        getattr(week_target, "volume_table", "prilepin"),
        intensity_floor=week_target.intensity_floor,
        intensity_ceiling=week_target.intensity_ceiling,
        session_volume_share=session_template.session_volume_share,
        volume_modifier=week_target.volume_modifier,
        sessions_per_week=len(g.program_plan.session_templates),
        level=g.athlete_context.level,
        phase=g.program_plan.phase,
    )
    cumulative_comp_reps = sum(week.cumulative_reps.values())
    session_principles = _session_principles(g, week_target, session_template)
    session_chunks = (
        prefetched_chunks if prefetched_chunks is not None
        else _session_chunks(g, week_target, session_template)
    )

    prompt = build_session_prompt(
        athlete_context=g.athlete_context,
        week_target=week_target,
        session_template=session_template,
        retrieval_context=g.retrieval_context,
        week_number=week_number,
        duration_weeks=g.program_plan.duration_weeks,
        already_prescribed=week.already_prescribed,
        session_rep_target=session_rep_target,
        cumulative_comp_reps=cumulative_comp_reps,
        effective_maxes=g.effective_maxes,
        phase=g.program_plan.phase,
        sessions_per_week=g.program_plan.sessions_per_week,
        active_principles=session_principles,
        context_chunks=session_chunks,
        block_template=block_template,
    )

    if not _admit_session(g, outcome, week_number, day_number):
        return False

    try:
        result = generate_session_with_retries(
            prompt=prompt,
            llm_client=g.llm_client,
            settings=g.settings,
            available_exercise_names=g.available_exercise_names,
            week_target=asdict(week_target),
            athlete=g.athlete_context.athlete,
            active_principles=session_principles,
            week_cumulative_reps=week.cumulative_reps,
            program_id=g.program_id,
            week_number=week_number,
            day_number=day_number,
            conn=conn,  # this week's connection: generation_log rows per attempt
            fault_exercise_names=g.fault_exercise_names,
            retrieval_set=_retrieval_set(session_chunks),
            week_already_prescribed=week.already_prescribed,
            available_complexes=getattr(g.retrieval_context, "available_complexes", None),
        )
    except BaseException:
        g.spend.abandon()
        raise

    g.spend.finish(estimate_cost(
        result.input_tokens, result.output_tokens, g.settings.generation_model,
        cache_read_tokens=getattr(result, "cache_read_tokens", 0) or 0,
        cache_creation_tokens=getattr(result, "cache_creation_tokens", 0) or 0,
    ))

    if result.exercises is None:
        outcome.failed.append(f"W{week_number}D{day_number}")
        logger.warning(
            f"  W{week_number}D{day_number} generation failed — "
            f"storing empty session"
        )
        exercises = []
    else:
        exercises = result.exercises
    exercises = _resolve_session_exercises(g, exercises, week_number, day_number, session_chunks)

    # Accumulate volume for next session's context
    validation = validate_session(
        session_exercises=exercises,
        week_target=asdict(week_target),
        active_principles=session_principles,
        athlete=g.athlete_context.athlete,
        week_cumulative_reps=week.cumulative_reps,
        week_already_prescribed=week.already_prescribed,
    )
    for zone, reps in validation.session_comp_reps.items():
        week.cumulative_reps[zone] = week.cumulative_reps.get(zone, 0) + reps

    # Persist session to DB — inline weeks save as they go; worker
    # weeks buffer and the main thread inserts them in week order
    session_id = _save_session(
        conn, g.program_id, week_number, day_number,
        session_template, exercises,
    ) if save else None

    # Track for within-week context
    for ex in exercises:
        week.already_prescribed.append({**ex, "day_number": day_number})

    outcome.sessions.append({
        "week": week_number,
        "day": day_number,
        "label": session_template.label,
        "session_id": session_id,
        "exercises": exercises,
    })
    outcome.templates.append(session_template)
    return True


# ── Snapshot / peak-week helpers ───────────────────────────────

# Never persist credential/identity material into generated_programs.
# athlete_snapshot only needs the profile fields the prompt + feedback read;
# copying password_hash/username/is_admin into a second table retained old
# hashes past password changes and exposed them to any program-page/export
# view (audit5-M1).
_SNAPSHOT_EXCLUDE = frozenset({
    "created_at", "updated_at", "password_hash", "username", "is_admin",
})


def _build_athlete_snapshot(athlete: dict) -> dict:
    return {k: v for k, v in athlete.items() if k not in _SNAPSHOT_EXCLUDE}


def compute_peak_week(weekly_targets):
    """The last non-deload week's number, or None when every week is a deload.

    A 1-week realization is entirely taper/deload — bolting a work-up-to-100%
    max-test session onto it, days before the meet, is exactly wrong
    (audit5-L5). None signals the caller to skip the max test.
    """
    working = [wt.week_number for wt in weekly_targets if not wt.is_deload]
    return max(working) if working else None


# ── Max test session builder ───────────────────────────────────

def compute_max_test_day(session_templates, sessions_per_week: int) -> int:
    """Day number for the max-test session: one past the LAST templated day.

    sessions_per_week + 1 is wrong when the template distribution falls back to
    a different frequency (e.g. spw=2 → 3-day fallback stores days 1–3), where
    it collides with UNIQUE(program_id, week_number, day_number) and aborts a
    fully-generated program at save time (AGT-H1).
    """
    days = [t.day_number for t in session_templates]
    return (max(days) if days else sessions_per_week) + 1



def _build_max_test_session(
    athlete_context: AthleteContext,
    exercise_lookup: dict,
) -> list[dict]:
    """Build a deterministic warmup-to-max session for snatch and clean & jerk.

    No LLM call needed — the structure is fixed by convention:
    progressive singles ending in a max attempt (is_max_attempt=True) for each lift.
    """
    # (intensity_pct, reps, rest_seconds, rpe_target, is_max_attempt)
    PROGRESSION = [
        (50,  3, 90,  5.0, False),
        (60,  2, 90,  6.0, False),
        (70,  2, 120, 7.0, False),
        (77,  1, 180, 7.5, False),
        (83,  1, 180, 8.0, False),
        (88,  1, 240, 8.5, False),
        (93,  1, 240, 9.0, False),
        (97,  1, 300, 9.5, False),
        (100, 1, 300, 10.0, True),
    ]

    # Intentionally uses CURRENT maxes, not effective/projected targets (A-L6):
    # a max test works up to 100% of the athlete's actual 1RM to attempt a new PR.
    # Building the ramp off a projected goal they haven't hit yet would prescribe
    # warm-ups above their real max.
    snatch_max = athlete_context.maxes.get("snatch", 0)
    cj_max     = athlete_context.maxes.get("clean_and_jerk", 0)
    snatch_id  = exercise_lookup.get("snatch")
    cj_id      = exercise_lookup.get("clean & jerk")

    exercises = []
    order = 1

    for pct, reps, rest, rpe, is_max in PROGRESSION:
        exercises.append({
            "exercise_order": order,
            "exercise_name": "Snatch",
            "exercise_id": snatch_id,
            "sets": 1,
            "reps": reps,
            "intensity_pct": float(pct),
            "intensity_reference": "snatch",
            "absolute_weight_kg": round_kg(snatch_max * pct / 100) if snatch_max else None,
            "rpe_target": rpe,
            "rest_seconds": rest,
            "is_max_attempt": is_max,
            "selection_rationale": (
                "Snatch max attempt — log the weight you hit as your new max."
                if is_max else
                f"Snatch build-up at {pct}% toward max attempt."
            ),
            "source_principle_ids": [],
            "source_chunk_ids": [],
        })
        order += 1

    for pct, reps, rest, rpe, is_max in PROGRESSION:
        exercises.append({
            "exercise_order": order,
            "exercise_name": "Clean & Jerk",
            "exercise_id": cj_id,
            "sets": 1,
            "reps": reps,
            "intensity_pct": float(pct),
            "intensity_reference": "clean_and_jerk",
            "absolute_weight_kg": round_kg(cj_max * pct / 100) if cj_max else None,
            "rpe_target": rpe,
            "rest_seconds": rest,
            "is_max_attempt": is_max,
            "selection_rationale": (
                "Clean & Jerk max attempt — log the weight you hit as your new max."
                if is_max else
                f"Clean & Jerk build-up at {pct}% toward max attempt."
            ),
            "source_principle_ids": [],
            "source_chunk_ids": [],
        })
        order += 1

    return exercises


# ── DB helpers ─────────────────────────────────────────────────

def _save_session(
    conn, program_id, week_number, day_number, session_template, exercises
) -> int:
    """Persist a program session + its exercises to the DB."""
    session_id = execute_returning(
        conn,
        """
        INSERT INTO program_sessions
            (program_id, week_number, day_number, session_label,
             estimated_duration_minutes, focus_area)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            program_id, week_number, day_number,
            session_template.label,
            _estimate_duration(exercises),
            session_template.primary_movement,
        ),
    )

    for ex in exercises:
        execute(
            conn,
            """
            INSERT INTO session_exercises
                (session_id, exercise_order, exercise_id, exercise_name,
                 sets, reps, intensity_pct, intensity_reference, absolute_weight_kg,
                 rpe_target, rest_seconds, is_max_attempt,
                 selection_rationale, source_principle_ids, source_chunk_ids,
                 complex_id, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                ex.get("exercise_order"),
                ex.get("exercise_id"),
                ex.get("exercise_name"),
                ex.get("sets"),
                ex.get("reps"),
                ex.get("intensity_pct"),
                ex.get("intensity_reference"),
                ex.get("absolute_weight_kg"),
                ex.get("rpe_target"),
                ex.get("rest_seconds"),
                ex.get("is_max_attempt", False),
                ex.get("selection_rationale"),
                ex.get("source_principle_ids") or [],
                ex.get("source_chunk_ids") or [],
                ex.get("complex_id"),
                ex.get("notes"),
            ),
        )

    conn.commit()
    return session_id


def _estimate_duration(exercises: list[dict]) -> int:
    """Rough session duration estimate in minutes (floored at the minimum)."""
    return max(MIN_SESSION_DURATION_MINUTES, round(estimate_session_minutes(exercises)))


def _mark_program_draft(conn, program_id: int, reason: str | None = None):
    """Keep a program in 'draft'. When `reason` is given (cost-limit abort), also
    store it as the rationale so the truncated draft explains itself (A-L1)."""
    if reason is not None:
        execute(
            conn,
            "UPDATE generated_programs SET status = 'draft', rationale = %s, "
            "updated_at = NOW() WHERE id = %s",
            (reason, program_id),
        )
    else:
        execute(
            conn,
            "UPDATE generated_programs SET status = 'draft', updated_at = NOW() WHERE id = %s",
            (program_id,),
        )
    conn.commit()


def _macrocycle_block(conn, athlete_id: int, ctx: AthleteContext, macrocycle_id: int | None,
                      macrocycle_weeks: int | None, dry_run: bool) -> tuple[int | None, int, dict | None]:
    """(macrocycle_id, block_index, block) to generate (PLAN-3e).

    New macrocycle: plan the blocks and store them (not on a dry run, which
    prints them). Existing one: the first block without a program; when every
    block has one the macrocycle is marked completed and the block is None.
    """
    if macrocycle_id is None:
        total = macrocycle_weeks_for(ctx.weeks_to_competition, macrocycle_weeks)
        competition = ctx.weeks_to_competition is not None
        blocks = plan_blocks(ctx.level, total, competition=competition)
        if dry_run:
            print(f"\nMACROCYCLE: {total} weeks{' to the meet' if competition else ''}")
            for i, b in enumerate(blocks, 1):
                print(f"  {i}. {b['phase']:<16} {b['weeks']} wks  {b['note']}")
            return None, 0, blocks[0]
        comp_date = (ctx.active_goal or {}).get("competition_date") if competition else None
        mc_id = create_macrocycle(conn, athlete_id, blocks, comp_date)
        conn.commit()
        logger.info(f"Macrocycle {mc_id} planned: {[(b['phase'], b['weeks']) for b in blocks]}")
        return mc_id, 0, blocks[0]
    mc = load_macrocycle(conn, macrocycle_id, athlete_id)
    if not mc or mc["status"] != "active":
        raise ValueError(f"Macrocycle {macrocycle_id} is not an active macrocycle of athlete {athlete_id}")
    idx = next_block_index(conn, macrocycle_id)
    if idx >= len(mc["blocks"]):
        execute(conn, "UPDATE macrocycles SET status = 'completed', updated_at = now() WHERE id = %s",
                (macrocycle_id,))
        conn.commit()
        logger.info(f"Macrocycle {macrocycle_id} has no blocks left — marked completed")
        return macrocycle_id, idx, None
    return macrocycle_id, idx, mc["blocks"][idx]


def _print_plan(ctx: AthleteContext, p: ProgramPlan):
    """Print plan summary for dry-run mode."""
    print(f"\n{'='*60}")
    print(f"PLAN: {p.phase.upper()} — {p.duration_weeks} weeks")
    print(f"Athlete: {ctx.athlete['name']} ({ctx.level})")
    print(f"Sessions/week: {p.sessions_per_week}")
    print(f"Cold start: {'yes' if ctx.previous_program is None else 'no'}")
    print("\nWeekly targets:")
    for wt in p.weekly_targets:
        deload = " [DELOAD]" if wt.is_deload else ""
        print(
            f"  Week {wt.week_number}{deload}: "
            f"{wt.intensity_floor}-{wt.intensity_ceiling}% | "
            f"vol={wt.volume_modifier:.0%} | "
            f"target {wt.total_competition_lift_reps} comp reps"
        )
    print("\nSession templates:")
    for st in p.session_templates:
        print(f"  D{st.day_number}: {st.label} ({st.session_volume_share:.0%} volume)")
    print(f"\nPrinciples loaded: {len(p.active_principles)}")
    print(f"{'='*60}\n")


# ── CLI ────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a training program for an athlete")
    parser.add_argument("--athlete-id", type=int, required=True,
                        help="Athlete ID from the athletes table")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run ASSESS + PLAN only; don't call LLM or write sessions")
    parser.add_argument("--weeks", type=int, default=None,
                        help="Block length; clamped to the athlete's level bounds, ignored when a competition date fixes it (PLAN-1)")
    parser.add_argument("--macrocycle", type=int, nargs="?", const=0, default=None, metavar="WEEKS",
                        help="Plan a macrocycle to the goal's competition date (else WEEKS, default "
                             "MACROCYCLE_WEEKS_DEFAULT) and generate its first block (PLAN-3e)")
    parser.add_argument("--macrocycle-id", type=int, default=None,
                        help="Generate the next block of this macrocycle (PLAN-3e)")
    args = parser.parse_args()

    settings = Settings()
    program_id = run(args.athlete_id, settings, dry_run=args.dry_run, duration_weeks=args.weeks,
                     new_macrocycle=args.macrocycle is not None,
                     macrocycle_weeks=args.macrocycle or None,
                     macrocycle_id=args.macrocycle_id)

    if program_id:
        print(f"\nProgram generated: id={program_id}")
        print(f"View in DB: SELECT * FROM generated_programs WHERE id = {program_id};")
    elif not args.dry_run:
        print("\nProgram generation failed. Check logs above.")
        sys.exit(1)
