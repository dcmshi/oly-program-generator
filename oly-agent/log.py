# oly-agent/log.py
"""
Training log CLI — record and review training sessions.

Usage:
    python log.py show   --athlete-id 1              # Current week's prescribed sessions
    python log.py session --athlete-id 1             # Log a completed session (interactive)
    python log.py exercise --log-id 5               # Add exercises to an existing log
    python log.py status --athlete-id 1             # RPE / make-rate warnings for active program
    python log.py history --athlete-id 1 [--weeks 2] # Recent log history
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import Settings
from shared.db import get_connection, fetch_one, fetch_all, execute, execute_returning


# ── Helpers ─────────────────────────────────────────────────────

def _prompt(label: str, default=None, cast=str) -> str | None:
    """Prompt user for input with optional default and type casting."""
    suffix = f" [{default}]" if default is not None else ""
    raw = input(f"  {label}{suffix}: ").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except (ValueError, TypeError):
        print(f"    Invalid input, using default: {default}")
        return default


def _prompt_required(label: str, cast=str):
    """Prompt user until a non-empty value is entered."""
    while True:
        raw = input(f"  {label}: ").strip()
        if raw:
            try:
                return cast(raw)
            except (ValueError, TypeError):
                print("    Invalid input, try again.")
        else:
            print("    This field is required.")


def _fmt_date(d) -> str:
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    return str(d)


# ── Command: show ────────────────────────────────────────────────

def cmd_show(athlete_id: int, conn) -> None:
    """Display the current week's prescribed sessions for the active program."""
    program = fetch_one(
        conn,
        """
        SELECT id, name, phase, start_date, duration_weeks, sessions_per_week
        FROM generated_programs
        WHERE athlete_id = %s AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (athlete_id,),
    )
    if not program:
        # Fall back to most recent draft
        program = fetch_one(
            conn,
            """
            SELECT id, name, phase, start_date, duration_weeks, sessions_per_week
            FROM generated_programs
            WHERE athlete_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (athlete_id,),
        )
    if not program:
        print("No program found for this athlete.")
        return

    program_id = program["id"]
    start_date = program["start_date"]
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)

    today = date.today()
    days_in = (today - start_date).days
    current_week = max(1, min(program["duration_weeks"], (days_in // 7) + 1))

    print(f"\n{'='*60}")
    print(f"Program: {program['name']}  (id={program_id})")
    print(f"Phase: {program['phase'].upper()}  |  Week {current_week} of {program['duration_weeks']}")
    print(f"Start date: {_fmt_date(start_date)}  |  Today: {today}")
    print(f"{'='*60}")

    sessions = fetch_all(
        conn,
        """
        SELECT ps.id, ps.week_number, ps.day_number, ps.session_label,
               ps.estimated_duration_minutes, ps.focus_area
        FROM program_sessions ps
        WHERE ps.program_id = %s AND ps.week_number = %s
        ORDER BY ps.day_number
        """,
        (program_id, current_week),
    )

    if not sessions:
        print(f"No sessions found for week {current_week}.")
        return

    for s in sessions:
        # Check if logged already
        log = fetch_one(
            conn,
            "SELECT id, overall_rpe FROM training_logs WHERE session_id = %s",
            (s["id"],),
        )
        logged_tag = f"  ✓ logged (log_id={log['id']}, RPE={log['overall_rpe']})" if log else ""
        print(f"\n  Day {s['day_number']}: {s['session_label']}{logged_tag}")
        print(f"    session_id={s['id']}  |  ~{s['estimated_duration_minutes']} min  |  focus: {s['focus_area']}")

        exercises = fetch_all(
            conn,
            """
            SELECT exercise_order, exercise_name, sets, reps,
                   intensity_pct, absolute_weight_kg, rest_seconds, rpe_target
            FROM session_exercises
            WHERE session_id = %s
            ORDER BY exercise_order
            """,
            (s["id"],),
        )
        for ex in exercises:
            weight_str = (
                f"  @{ex['absolute_weight_kg']}kg"
                if ex["absolute_weight_kg"] else
                (f"  @{ex['intensity_pct']}%" if ex["intensity_pct"] else "")
            )
            rpe_str = f"  RPE {ex['rpe_target']}" if ex["rpe_target"] else ""
            rest_str = f"  rest {ex['rest_seconds']}s" if ex["rest_seconds"] else ""
            print(
                f"    {ex['exercise_order']}. {ex['exercise_name']}"
                f"  {ex['sets']}×{ex['reps']}{weight_str}{rpe_str}{rest_str}"
            )

    print()


# ── Command: session ─────────────────────────────────────────────

def cmd_session(athlete_id: int, conn, session_id: int | None = None) -> int | None:
    """Interactively create a training_logs row for a completed session."""
    print(f"\n{'─'*50}")
    print("Log a completed session")
    print(f"{'─'*50}")

    # If session_id not provided, show options for current week
    if session_id is None:
        program = fetch_one(
            conn,
            """
            SELECT id, name, start_date, duration_weeks
            FROM generated_programs
            WHERE athlete_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (athlete_id,),
        )
        if program:
            start_date = program["start_date"]
            if isinstance(start_date, str):
                start_date = date.fromisoformat(start_date)
            days_in = (date.today() - start_date).days
            current_week = max(1, min(program["duration_weeks"], (days_in // 7) + 1))

            sessions = fetch_all(
                conn,
                """
                SELECT ps.id, ps.day_number, ps.session_label
                FROM program_sessions ps
                LEFT JOIN training_logs tl ON tl.session_id = ps.id
                WHERE ps.program_id = %s AND ps.week_number = %s AND tl.id IS NULL
                ORDER BY ps.day_number
                """,
                (program["id"], current_week),
            )
            if sessions:
                print(f"\nUnlogged sessions for Week {current_week} of '{program['name']}':")
                for s in sessions:
                    print(f"  [{s['id']}] Day {s['day_number']}: {s['session_label']}")
                raw = input("\n  Enter session_id to link (or Enter to skip): ").strip()
                if raw.isdigit():
                    session_id = int(raw)

    # Gather log details
    log_date_str = _prompt("Log date", default=str(date.today()))
    try:
        log_date = date.fromisoformat(log_date_str)
    except ValueError:
        log_date = date.today()

    overall_rpe = _prompt("Overall session RPE (1-10)", cast=float)
    duration = _prompt("Session duration (minutes)", cast=int)
    bodyweight = _prompt("Bodyweight kg (optional)", cast=float)
    sleep_quality = _prompt("Sleep quality 1-5 (optional)", cast=int)
    stress_level = _prompt("Stress level 1-5 (optional)", cast=int)
    notes = _prompt("Notes (optional)") or None

    log_id = execute_returning(
        conn,
        """
        INSERT INTO training_logs
            (athlete_id, session_id, log_date, overall_rpe,
             session_duration_minutes, bodyweight_kg, sleep_quality,
             stress_level, athlete_notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            athlete_id, session_id, log_date, overall_rpe,
            duration, bodyweight, sleep_quality, stress_level, notes,
        ),
    )
    conn.commit()
    print(f"\n  Session logged. log_id={log_id}")

    add_exs = input("\n  Add exercise details now? [y/N]: ").strip().lower()
    if add_exs == "y":
        cmd_exercise(log_id, conn, session_id=session_id)

    return log_id


# ── Command: exercise ────────────────────────────────────────────

def cmd_exercise(log_id: int, conn, session_id: int | None = None) -> None:
    """Add exercise entries to an existing training log."""
    print(f"\n{'─'*50}")
    print(f"Adding exercises to log_id={log_id}")
    print(f"{'─'*50}")

    # Show prescribed exercises if session linked
    prescribed: dict[int, dict] = {}
    if session_id:
        rows = fetch_all(
            conn,
            """
            SELECT id, exercise_order, exercise_name, sets, reps,
                   absolute_weight_kg, intensity_pct, rpe_target
            FROM session_exercises
            WHERE session_id = %s
            ORDER BY exercise_order
            """,
            (session_id,),
        )
        if rows:
            print("\n  Prescribed exercises:")
            for r in rows:
                weight_str = f"  @{r['absolute_weight_kg']}kg" if r["absolute_weight_kg"] else ""
                print(f"    [{r['id']}] {r['exercise_order']}. {r['exercise_name']}  {r['sets']}×{r['reps']}{weight_str}")
            prescribed = {r["id"]: r for r in rows}

    print("\n  Enter each exercise. Leave exercise name blank to finish.\n")
    order = 1
    while True:
        print(f"  --- Exercise {order} ---")
        if prescribed:
            raw_link = input(f"  Link to session_exercise_id (or Enter to skip): ").strip()
            linked_ex = prescribed.get(int(raw_link)) if raw_link.isdigit() else None
        else:
            linked_ex = None
            raw_link = None

        if linked_ex:
            exercise_name = linked_ex["exercise_name"]
            session_exercise_id = linked_ex["id"]
            prescribed_weight = linked_ex["absolute_weight_kg"]
            print(f"    Linked: {exercise_name}  (prescribed {linked_ex['sets']}×{linked_ex['reps']}  @{prescribed_weight}kg)")
        else:
            exercise_name = input("  Exercise name (blank to finish): ").strip()
            if not exercise_name:
                break
            session_exercise_id = None
            prescribed_weight = None

        sets_completed = _prompt("  Sets completed", cast=int, default=None)

        reps_input = input("  Reps per set (e.g. 3,3,3 or 3): ").strip()
        if reps_input:
            try:
                reps_per_set = [int(r.strip()) for r in reps_input.split(",")]
            except ValueError:
                reps_per_set = []
        else:
            reps_per_set = []

        weight_kg = _prompt("  Weight used (kg)", cast=float, default=None)
        rpe = _prompt("  RPE", cast=float, default=None)
        make_rate = _prompt("  Make rate % (e.g. 100 = all makes)", cast=float, default=None)
        if make_rate is not None:
            make_rate = make_rate / 100.0  # store as fraction
        tech_notes = input("  Technical notes (optional): ").strip() or None

        # Compute deviations if prescribed data available
        weight_deviation = None
        rpe_deviation = None
        if prescribed_weight and weight_kg is not None:
            weight_deviation = round(weight_kg - prescribed_weight, 2)
        if linked_ex and linked_ex.get("rpe_target") and rpe is not None:
            rpe_deviation = round(rpe - float(linked_ex["rpe_target"]), 1)

        execute(
            conn,
            """
            INSERT INTO training_log_exercises
                (log_id, session_exercise_id, exercise_name, sets_completed,
                 reps_per_set, weight_kg, rpe, make_rate, technical_notes,
                 prescribed_weight_kg, weight_deviation_kg, rpe_deviation)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                log_id, session_exercise_id, exercise_name, sets_completed,
                reps_per_set if reps_per_set else None,
                weight_kg, rpe, make_rate, tech_notes,
                prescribed_weight, weight_deviation, rpe_deviation,
            ),
        )
        conn.commit()
        print(f"    Saved.")
        order += 1

    print(f"\n  Done. {order - 1} exercise(s) logged for log_id={log_id}.")


# ── Command: status ──────────────────────────────────────────────

def cmd_status(athlete_id: int, conn) -> None:
    """Surface warnings from the active program: RPE overshoot, low make rates."""
    program = fetch_one(
        conn,
        """
        SELECT id, name, phase, start_date, duration_weeks
        FROM generated_programs
        WHERE athlete_id = %s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (athlete_id,),
    )
    if not program:
        print("No program found.")
        return

    program_id = program["id"]
    print(f"\n{'='*60}")
    print(f"Status: {program['name']}  (id={program_id})")
    print(f"Phase: {program['phase'].upper()}  |  Start: {_fmt_date(program['start_date'])}")
    print(f"{'='*60}")

    # Recent logs (last 14 days)
    logs = fetch_all(
        conn,
        """
        SELECT tl.id, tl.log_date, tl.overall_rpe, tl.session_duration_minutes,
               tl.sleep_quality, tl.stress_level, tl.athlete_notes,
               ps.session_label, ps.week_number, ps.day_number
        FROM training_logs tl
        LEFT JOIN program_sessions ps ON ps.id = tl.session_id
        WHERE tl.athlete_id = %s
          AND tl.log_date >= %s
        ORDER BY tl.log_date DESC
        """,
        (athlete_id, date.today() - timedelta(days=14)),
    )

    if not logs:
        print("\nNo logs in the past 14 days.")
    else:
        print(f"\nLast {len(logs)} session(s) logged:\n")
        warnings = []
        for log in logs:
            label = log["session_label"] or "Unlinked session"
            rpe_str = f"RPE {log['overall_rpe']}" if log["overall_rpe"] else "RPE —"
            dur_str = f"{log['session_duration_minutes']} min" if log["session_duration_minutes"] else "— min"
            week_str = f"W{log['week_number']}D{log['day_number']}" if log["week_number"] else ""
            print(f"  {_fmt_date(log['log_date'])}  {week_str:8s}  {label}")
            print(f"    {rpe_str}  |  {dur_str}  |  sleep={log['sleep_quality'] or '—'}  stress={log['stress_level'] or '—'}")
            if log["athlete_notes"]:
                print(f"    Notes: {log['athlete_notes']}")

            # Flag high RPE
            if log["overall_rpe"] and float(log["overall_rpe"]) >= 9.0:
                warnings.append(f"High session RPE ({log['overall_rpe']}) on {_fmt_date(log['log_date'])}")
            if log["sleep_quality"] and int(log["sleep_quality"]) <= 2:
                warnings.append(f"Poor sleep (quality={log['sleep_quality']}) on {_fmt_date(log['log_date'])}")
            if log["stress_level"] and int(log["stress_level"]) >= 4:
                warnings.append(f"High stress (level={log['stress_level']}) on {_fmt_date(log['log_date'])}")

        # Exercise-level warnings
        ex_stats = fetch_all(
            conn,
            """
            SELECT tle.exercise_name,
                   AVG(tle.rpe) as avg_rpe,
                   AVG(tle.rpe_deviation) as avg_rpe_dev,
                   AVG(tle.make_rate) as avg_make_rate,
                   COUNT(*) as sessions
            FROM training_log_exercises tle
            JOIN training_logs tl ON tl.id = tle.log_id
            WHERE tl.athlete_id = %s
              AND tl.log_date >= %s
              AND tle.rpe IS NOT NULL
            GROUP BY tle.exercise_name
            HAVING COUNT(*) >= 2
            ORDER BY avg_rpe_dev DESC NULLS LAST
            """,
            (athlete_id, date.today() - timedelta(days=14)),
        )

        if ex_stats:
            print("\n  Exercise summary (last 14 days):\n")
            for ex in ex_stats:
                avg_rpe = f"{float(ex['avg_rpe']):.1f}" if ex["avg_rpe"] else "—"
                avg_dev = f"{float(ex['avg_rpe_dev']):+.1f}" if ex["avg_rpe_dev"] else "—"
                make = f"{float(ex['avg_make_rate'])*100:.0f}%" if ex["avg_make_rate"] else "—"
                print(f"    {ex['exercise_name']:<35}  avg RPE {avg_rpe}  RPE dev {avg_dev}  make {make}")

                if ex["avg_rpe_dev"] and float(ex["avg_rpe_dev"]) > 1.5:
                    warnings.append(
                        f"{ex['exercise_name']}: avg RPE deviation +{float(ex['avg_rpe_dev']):.1f} "
                        f"(consistently harder than prescribed)"
                    )
                if ex["avg_make_rate"] and float(ex["avg_make_rate"]) < 0.70:
                    warnings.append(
                        f"{ex['exercise_name']}: make rate {float(ex['avg_make_rate'])*100:.0f}% "
                        f"— consider reducing intensity"
                    )

        if warnings:
            print(f"\n  ⚠  Warnings:\n")
            for w in warnings:
                print(f"    • {w}")
        else:
            print("\n  All metrics within normal range.")

    # Adherence summary
    start_date = program["start_date"]
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    days_in = (date.today() - start_date).days
    current_week = max(1, (days_in // 7) + 1)

    prescribed_count = fetch_one(
        conn,
        """
        SELECT COUNT(*) as cnt FROM program_sessions
        WHERE program_id = %s AND week_number <= %s
        """,
        (program_id, current_week),
    )
    logged_count = fetch_one(
        conn,
        """
        SELECT COUNT(*) as cnt FROM training_logs tl
        JOIN program_sessions ps ON ps.id = tl.session_id
        WHERE ps.program_id = %s AND ps.week_number <= %s
        """,
        (program_id, current_week),
    )
    p = prescribed_count["cnt"] if prescribed_count else 0
    l = logged_count["cnt"] if logged_count else 0
    if p > 0:
        pct = round(l / p * 100)
        print(f"\n  Adherence through week {current_week}: {l}/{p} sessions logged ({pct}%)")
    print()


# ── Command: history ─────────────────────────────────────────────

def cmd_history(athlete_id: int, conn, weeks: int = 2) -> None:
    """Show recent training log history."""
    cutoff = date.today() - timedelta(weeks=weeks)
    logs = fetch_all(
        conn,
        """
        SELECT tl.id, tl.log_date, tl.overall_rpe, tl.session_duration_minutes,
               tl.athlete_notes, ps.session_label, ps.week_number, ps.day_number
        FROM training_logs tl
        LEFT JOIN program_sessions ps ON ps.id = tl.session_id
        WHERE tl.athlete_id = %s AND tl.log_date >= %s
        ORDER BY tl.log_date DESC, tl.id DESC
        """,
        (athlete_id, cutoff),
    )

    if not logs:
        print(f"\nNo logs in the past {weeks} week(s).")
        return

    print(f"\n{'='*60}")
    print(f"Training history — last {weeks} week(s)")
    print(f"{'='*60}")

    for log in logs:
        label = log["session_label"] or "Unlinked"
        week_str = f"W{log['week_number']}D{log['day_number']}" if log["week_number"] else "     "
        rpe_str = f"RPE {log['overall_rpe']}" if log["overall_rpe"] else "RPE —"
        dur_str = f"{log['session_duration_minutes']}min" if log["session_duration_minutes"] else ""
        print(f"\n  {_fmt_date(log['log_date'])}  log_id={log['id']}  {week_str:8s}  {label}")
        print(f"    {rpe_str}  {dur_str}")
        if log["athlete_notes"]:
            print(f"    {log['athlete_notes']}")

        exercises = fetch_all(
            conn,
            """
            SELECT exercise_name, sets_completed, reps_per_set,
                   weight_kg, rpe, make_rate, technical_notes
            FROM training_log_exercises
            WHERE log_id = %s
            ORDER BY id
            """,
            (log["id"],),
        )
        for ex in exercises:
            reps_str = ",".join(str(r) for r in (ex["reps_per_set"] or []))
            weight_str = f"@{ex['weight_kg']}kg" if ex["weight_kg"] else ""
            rpe_str = f"RPE {ex['rpe']}" if ex["rpe"] else ""
            make_str = f"make {float(ex['make_rate'])*100:.0f}%" if ex["make_rate"] else ""
            sets_str = f"{ex['sets_completed']}×" if ex["sets_completed"] else ""
            print(
                f"    • {ex['exercise_name']:<35}  "
                f"{sets_str}[{reps_str}]  {weight_str}  {rpe_str}  {make_str}"
            )
            if ex["technical_notes"]:
                print(f"        ↳ {ex['technical_notes']}")
    print()


# ── CLI ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Training log CLI for Olympic Weightlifting Program Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  show      Current week's prescribed sessions
  session   Log a completed session (interactive)
  exercise  Add exercises to an existing log
  status    RPE and make-rate warnings
  history   Recent log history
        """
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # show
    p_show = sub.add_parser("show", help="Current week's prescribed sessions")
    p_show.add_argument("--athlete-id", type=int, required=True)

    # session
    p_session = sub.add_parser("session", help="Log a completed session")
    p_session.add_argument("--athlete-id", type=int, required=True)
    p_session.add_argument("--session-id", type=int, default=None,
                           help="Link to a specific program_sessions.id")

    # exercise
    p_exercise = sub.add_parser("exercise", help="Add exercises to an existing log")
    p_exercise.add_argument("--log-id", type=int, required=True)
    p_exercise.add_argument("--session-id", type=int, default=None,
                            help="Link to program_sessions.id for prescribed comparison")

    # status
    p_status = sub.add_parser("status", help="RPE / make-rate warnings")
    p_status.add_argument("--athlete-id", type=int, required=True)

    # history
    p_history = sub.add_parser("history", help="Recent log history")
    p_history.add_argument("--athlete-id", type=int, required=True)
    p_history.add_argument("--weeks", type=int, default=2)

    args = parser.parse_args()
    settings = Settings()
    conn = get_connection(settings.database_url)

    try:
        if args.command == "show":
            cmd_show(args.athlete_id, conn)
        elif args.command == "session":
            cmd_session(args.athlete_id, conn, session_id=getattr(args, "session_id", None))
        elif args.command == "exercise":
            cmd_exercise(args.log_id, conn, session_id=getattr(args, "session_id", None))
        elif args.command == "status":
            cmd_status(args.athlete_id, conn)
        elif args.command == "history":
            cmd_history(args.athlete_id, conn, weeks=args.weeks)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
