# web/routers/export.py
"""Training log CSV export."""

import csv
import io
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from web.auth import get_current_athlete_id
from web.deps import get_db
from web.queries.export import get_full_training_log, get_program_for_export

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/export")

_CSV_HEADERS = [
    "date", "program", "week", "day", "session",
    "session_rpe", "duration_min", "bodyweight_kg", "sleep_quality", "stress_level",
    "session_notes",
    "exercise", "sets", "reps", "weight_kg", "prescribed_kg", "weight_deviation_kg",
    "exercise_rpe", "rpe_deviation", "make_rate_pct", "technical_notes",
]


@router.get("/log.csv")
async def export_training_log(
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    rows = await get_full_training_log(conn, athlete_id)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(_CSV_HEADERS)

    for row in rows:
        reps = (
            ",".join(str(r) for r in row["reps_per_set"])
            if row["reps_per_set"]
            else ""
        )
        make_rate = (
            round(float(row["make_rate"]) * 100)
            if row["make_rate"] is not None
            else ""
        )
        writer.writerow([
            row["log_date"],
            row["program_name"],
            row["week_number"],
            row["day_number"],
            row["session_label"],
            row["session_rpe"] if row["session_rpe"] is not None else "",
            row["duration_min"] if row["duration_min"] is not None else "",
            row["bodyweight_kg"] if row["bodyweight_kg"] is not None else "",
            row["sleep_quality"] if row["sleep_quality"] is not None else "",
            row["stress_level"] if row["stress_level"] is not None else "",
            row["session_notes"] or "",
            row["exercise_name"] or "",
            row["sets_completed"] if row["sets_completed"] is not None else "",
            reps,
            row["weight_kg"] if row["weight_kg"] is not None else "",
            row["prescribed_weight_kg"] if row["prescribed_weight_kg"] is not None else "",
            row["weight_deviation_kg"] if row["weight_deviation_kg"] is not None else "",
            row["exercise_rpe"] if row["exercise_rpe"] is not None else "",
            row["rpe_deviation"] if row["rpe_deviation"] is not None else "",
            make_rate,
            row["technical_notes"] or "",
        ])

    logger.info(f"Training log CSV export: athlete={athlete_id}, {len(rows)} exercise rows")

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=training_log.csv"},
    )


_PROGRAM_CSV_HEADERS = [
    "week", "day", "session", "focus_area", "session_duration_min",
    "order", "exercise", "sets", "reps",
    "intensity_pct", "intensity_reference", "weight_kg",
    "rpe_target", "rest_seconds",
    "backoff_sets", "backoff_intensity_pct",
    "is_max_attempt", "notes",
]


@router.get("/program/{program_id}.csv")
async def export_program(
    program_id: int,
    request: Request,
    conn=Depends(get_db),
    athlete_id: int = Depends(get_current_athlete_id),
):
    from fastapi import HTTPException

    program, rows = await get_program_for_export(conn, program_id, athlete_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Program not found")

    output = io.StringIO()
    writer = csv.writer(output)

    # Program metadata block
    writer.writerow(["program", program["name"]])
    writer.writerow(["phase", program["phase"]])
    writer.writerow(["status", program["status"]])
    writer.writerow(["start_date", program["start_date"]])
    writer.writerow(["duration_weeks", program["duration_weeks"]])
    writer.writerow(["sessions_per_week", program["sessions_per_week"]])
    writer.writerow([])  # blank separator

    writer.writerow(_PROGRAM_CSV_HEADERS)
    for row in rows:
        writer.writerow([
            row["week_number"],
            row["day_number"],
            row["session_label"],
            row["focus_area"] or "",
            row["session_duration_min"] if row["session_duration_min"] is not None else "",
            row["exercise_order"],
            row["exercise_name"],
            row["sets"],
            row["reps"],
            row["intensity_pct"] if row["intensity_pct"] is not None else "",
            row["intensity_reference"] or "",
            row["absolute_weight_kg"] if row["absolute_weight_kg"] is not None else "",
            row["rpe_target"] if row["rpe_target"] is not None else "",
            row["rest_seconds"] if row["rest_seconds"] is not None else "",
            row["backoff_sets"] if row["backoff_sets"] else "",
            row["backoff_intensity_pct"] if row["backoff_intensity_pct"] is not None else "",
            "yes" if row["is_max_attempt"] else "",
            row["notes"] or "",
        ])

    safe_name = "".join(c if c.isalnum() or c in "-_ " else "" for c in program["name"]).strip()
    filename = f"{safe_name or 'program'}.csv"

    logger.info(f"Program CSV export: athlete={athlete_id}, program_id={program_id}, {len(rows)} exercises")

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
