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
from dataclasses import asdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import Settings
from shared.db import get_connection, fetch_all, execute, execute_returning
from shared.llm import create_llm_client, estimate_cost
from assess import assess
from plan import plan
from retrieve import retrieve
from generate import generate_session_with_retries, build_session_prompt
from validate import validate_session
from explain import explain
from weight_resolver import resolve_exercise_ids, resolve_weights, attach_source_chunk_ids
from models import AthleteContext, ProgramPlan, RetrievalContext

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run(athlete_id: int, settings: Settings, dry_run: bool = False) -> int | None:
    """Generate a complete training program for the given athlete.

    Args:
        athlete_id: Primary key in the athletes table.
        settings: Unified settings (DB URL, API keys, model config).
        dry_run: If True, runs only ASSESS + PLAN and prints the plan without generating.

    Returns:
        program_id of the created program, or None on failure / dry-run.
    """
    conn = get_connection(settings.database_url)

    # ── Set up VectorLoader (optional) ────────────────────────
    vector_loader = None
    try:
        ingestion_path = Path(__file__).parent.parent / "oly-ingestion"
        if str(ingestion_path) not in sys.path:
            sys.path.insert(0, str(ingestion_path))
        from loaders.vector_loader import VectorLoader
        vector_loader = VectorLoader(settings)
        logger.info("VectorLoader initialized")
    except Exception as e:
        logger.warning(f"VectorLoader not available (vector search disabled): {e}")

    # ── Build exercise lookup (name -> id) ────────────────────
    exercise_rows = fetch_all(conn, "SELECT id, name FROM exercises")
    exercise_lookup = {r["name"].lower(): r["id"] for r in exercise_rows}

    try:
        # ── Step 1: ASSESS ────────────────────────────────────
        logger.info(f"=== Step 1: ASSESS (athlete {athlete_id}) ===")
        athlete_context = assess(athlete_id, conn)

        # ── Step 2: PLAN ──────────────────────────────────────
        logger.info("=== Step 2: PLAN ===")
        program_plan = plan(athlete_context, conn, settings)

        if dry_run:
            _print_plan(athlete_context, program_plan)
            return None

        # ── Create program record ─────────────────────────────
        llm_client = create_llm_client(settings)

        maxes_snapshot = athlete_context.maxes
        athlete_snapshot = {
            k: v for k, v in athlete_context.athlete.items()
            if k not in ("created_at", "updated_at")
        }

        program_id = execute_returning(
            conn,
            """
            INSERT INTO generated_programs
                (athlete_id, name, status, phase, duration_weeks,
                 sessions_per_week, start_date,
                 athlete_snapshot, maxes_snapshot, generation_params)
            VALUES (%s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                athlete_id,
                f"{program_plan.phase.title()} Block — {date.today()}",
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
                }),
            ),
        )
        conn.commit()
        logger.info(f"Created program record: id={program_id}")

        # ── Step 3: RETRIEVE ──────────────────────────────────
        logger.info("=== Step 3: RETRIEVE ===")
        retrieval_context = retrieve(athlete_context, program_plan, conn, vector_loader)
        available_exercise_names = [e["name"] for e in retrieval_context.available_exercises]

        # ── Step 4+5: GENERATE + VALIDATE (session by session) ─
        logger.info("=== Step 4+5: GENERATE + VALIDATE ===")
        cumulative_cost = 0.0
        all_sessions_data: list[dict] = []

        for week_target in program_plan.weekly_targets:
            week_number = week_target.week_number
            week_cumulative_reps: dict[str, int] = {}
            week_already_prescribed: list[dict] = []

            for session_template in program_plan.session_templates:
                day_number = session_template.day_number
                logger.info(
                    f"  Generating W{week_number}D{day_number}: {session_template.label}"
                )

                # Compute session rep target from Prilepin
                from shared.prilepin import compute_session_rep_target
                session_rep_target = compute_session_rep_target(
                    intensity_floor=week_target.intensity_floor,
                    intensity_ceiling=week_target.intensity_ceiling,
                    session_volume_share=session_template.session_volume_share,
                    volume_modifier=week_target.volume_modifier,
                )
                cumulative_comp_reps = sum(week_cumulative_reps.values())

                prompt = build_session_prompt(
                    athlete_context=athlete_context,
                    week_target=week_target,
                    session_template=session_template,
                    retrieval_context=retrieval_context,
                    week_number=week_number,
                    duration_weeks=program_plan.duration_weeks,
                    already_prescribed=week_already_prescribed,
                    session_rep_target=session_rep_target,
                    cumulative_comp_reps=cumulative_comp_reps,
                )

                result = generate_session_with_retries(
                    prompt=prompt,
                    llm_client=llm_client,
                    settings=settings,
                    available_exercise_names=available_exercise_names,
                    week_target=asdict(week_target),
                    athlete=athlete_context.athlete,
                    active_principles=retrieval_context.active_principles,
                    week_cumulative_reps=week_cumulative_reps,
                    program_id=program_id,
                    week_number=week_number,
                    day_number=day_number,
                    conn=conn,
                )

                # Cost tracking
                cumulative_cost += estimate_cost(result.input_tokens, result.output_tokens)
                if cumulative_cost > settings.cost_limit_per_program:
                    logger.error(
                        f"Cost limit exceeded: ${cumulative_cost:.4f} > "
                        f"${settings.cost_limit_per_program:.2f}. Aborting."
                    )
                    _mark_program_draft(conn, program_id)
                    return program_id

                if result.exercises is None:
                    logger.warning(
                        f"  W{week_number}D{day_number} generation failed — "
                        f"storing empty session"
                    )
                    exercises = []
                else:
                    exercises = result.exercises

                # Resolve exercise IDs and weights
                exercises = resolve_exercise_ids(exercises, exercise_lookup)
                exercises = resolve_weights(exercises, athlete_context.maxes)
                exercises = attach_source_chunk_ids(exercises, {
                    "programming_rationale": retrieval_context.programming_rationale,
                    "fault_correction_chunks": retrieval_context.fault_correction_chunks,
                })

                # Accumulate volume for next session's context
                validation = validate_session(
                    session_exercises=exercises,
                    week_target=asdict(week_target),
                    active_principles=retrieval_context.active_principles,
                    athlete=athlete_context.athlete,
                    week_cumulative_reps=week_cumulative_reps,
                )
                for zone, reps in validation.session_comp_reps.items():
                    week_cumulative_reps[zone] = week_cumulative_reps.get(zone, 0) + reps

                # Persist session to DB
                session_id = _save_session(
                    conn, program_id, week_number, day_number,
                    session_template, exercises,
                )

                # Track for within-week context
                for ex in exercises:
                    week_already_prescribed.append({**ex, "day_number": day_number})

                all_sessions_data.append({
                    "week": week_number,
                    "day": day_number,
                    "label": session_template.label,
                    "session_id": session_id,
                    "exercises": exercises,
                })

        # ── Step 6: EXPLAIN ───────────────────────────────────
        logger.info("=== Step 6: EXPLAIN ===")
        rationale = explain(
            athlete_context=athlete_context,
            plan=program_plan,
            program_sessions=all_sessions_data,
            llm_client=llm_client,
            settings=settings,
        )

        execute(
            conn,
            "UPDATE generated_programs SET rationale = %s, updated_at = NOW() WHERE id = %s",
            (rationale, program_id),
        )
        conn.commit()

        logger.info(
            f"Program {program_id} complete. "
            f"Total cost: ${cumulative_cost:.4f}"
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
                 selection_rationale, source_principle_ids, source_chunk_ids)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            ),
        )

    conn.commit()
    return session_id


def _estimate_duration(exercises: list[dict]) -> int:
    """Rough session duration estimate in minutes."""
    minutes = sum(
        (ex.get("sets") or 0) * (30 + (ex.get("rest_seconds") or 90))
        for ex in exercises
    ) / 60
    return max(30, round(minutes))


def _mark_program_draft(conn, program_id: int):
    execute(
        conn,
        "UPDATE generated_programs SET status = 'draft', updated_at = NOW() WHERE id = %s",
        (program_id,),
    )
    conn.commit()


def _print_plan(ctx: AthleteContext, p: ProgramPlan):
    """Print plan summary for dry-run mode."""
    print(f"\n{'='*60}")
    print(f"PLAN: {p.phase.upper()} — {p.duration_weeks} weeks")
    print(f"Athlete: {ctx.athlete['name']} ({ctx.level})")
    print(f"Sessions/week: {p.sessions_per_week}")
    print(f"Cold start: {'yes' if ctx.previous_program is None else 'no'}")
    print(f"\nWeekly targets:")
    for wt in p.weekly_targets:
        deload = " [DELOAD]" if wt.is_deload else ""
        print(
            f"  Week {wt.week_number}{deload}: "
            f"{wt.intensity_floor}-{wt.intensity_ceiling}% | "
            f"vol={wt.volume_modifier:.0%} | "
            f"target {wt.total_competition_lift_reps} comp reps"
        )
    print(f"\nSession templates:")
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
    args = parser.parse_args()

    settings = Settings()
    program_id = run(args.athlete_id, settings, dry_run=args.dry_run)

    if program_id:
        print(f"\nProgram generated: id={program_id}")
        print(f"View in DB: SELECT * FROM generated_programs WHERE id = {program_id};")
    elif not args.dry_run:
        print("\nProgram generation failed. Check logs above.")
        sys.exit(1)
