# web/queries/dashboard.py
"""DB queries for the dashboard."""

from datetime import date, timedelta


def _f(v):
    return float(v) if v is not None else None


async def get_active_program(conn, athlete_id: int) -> dict | None:
    from web.async_db import async_fetch_one
    return await async_fetch_one(
        conn,
        """
        SELECT id, name, phase, status, start_date, duration_weeks, sessions_per_week
        FROM generated_programs
        WHERE athlete_id = $1 AND status IN ('active', 'draft')
        ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, created_at DESC
        LIMIT 1
        """,
        athlete_id,
    )


async def get_current_week_sessions(conn, program_id: int, week_number: int) -> list[dict]:
    from web.async_db import async_fetch_all
    return await async_fetch_all(
        conn,
        """
        SELECT ps.id, ps.day_number, ps.session_label, ps.estimated_duration_minutes,
               ps.focus_area, tl.id AS log_id, tl.overall_rpe
        FROM program_sessions ps
        LEFT JOIN training_logs tl ON tl.session_id = ps.id
        WHERE ps.program_id = $1 AND ps.week_number = $2
        ORDER BY ps.day_number
        """,
        program_id, week_number,
    )


async def get_adherence(conn, program_id: int, week_number: int) -> dict:
    from web.async_db import async_fetch_one
    prescribed = await async_fetch_one(
        conn,
        "SELECT COUNT(*) AS cnt FROM program_sessions WHERE program_id = $1 AND week_number <= $2",
        program_id, week_number,
    )
    logged = await async_fetch_one(
        conn,
        """
        SELECT COUNT(*) AS cnt FROM training_logs tl
        JOIN program_sessions ps ON ps.id = tl.session_id
        WHERE ps.program_id = $1 AND ps.week_number <= $2
        """,
        program_id, week_number,
    )
    p = (prescribed or {}).get("cnt", 0)
    l = (logged or {}).get("cnt", 0)
    return {"prescribed": p, "logged": l, "pct": round(l / p * 100) if p else 0}


async def get_lift_ratios(conn, athlete_id: int) -> list[dict]:
    """Compute key lift ratios vs. expected ranges. Returns [] if insufficient maxes."""
    from web.async_db import async_fetch_one

    row = await async_fetch_one(
        conn,
        """
        SELECT
            MAX(CASE WHEN e.name = 'Snatch'       THEN am.weight_kg END) AS snatch,
            MAX(CASE WHEN e.name = 'Clean & Jerk' THEN am.weight_kg END) AS cj,
            MAX(CASE WHEN e.name = 'Back Squat'   THEN am.weight_kg END) AS back_squat,
            MAX(CASE WHEN e.name = 'Front Squat'  THEN am.weight_kg END) AS front_squat,
            MAX(CASE WHEN e.name = 'Clean'        THEN am.weight_kg END) AS clean
        FROM athlete_maxes am
        JOIN exercises e ON e.id = am.exercise_id
        WHERE am.athlete_id = $1 AND am.max_type = 'current'
          AND e.name IN ('Snatch', 'Clean & Jerk', 'Back Squat', 'Front Squat', 'Clean')
        """,
        athlete_id,
    )
    if not row:
        return []

    snatch      = _f(row["snatch"])
    cj          = _f(row["cj"])
    back_squat  = _f(row["back_squat"])
    front_squat = _f(row["front_squat"])
    clean       = _f(row["clean"]) or cj  # fall back to C&J if clean not recorded separately

    # bar_min/max define the visual scale; target_low/high are the expected range
    _CHECKS = [
        ("Snatch / C&J",        snatch,      cj,         0.80, 0.83, 0.60, 1.00,
         "Snatch is lagging — technique or snatch-specific strength may be limiting",
         "C&J may be holding back total — check jerk technique or clean strength"),
        ("Back Squat / Snatch", back_squat,  snatch,     1.35, 1.55, 1.00, 2.50,
         "Squat strength may be limiting the snatch",
         "Strength is not converting to the snatch — technique is likely the limiter"),
        ("Front Squat / Clean", front_squat, clean,      1.10, 1.20, 0.80, 1.60,
         "Front squat strength may be limiting the clean",
         "Clean is technique-limited relative to front squat strength"),
        ("Back Squat / C&J",   back_squat,  cj,         1.20, 1.30, 0.90, 1.80,
         "Squat strength may be limiting the clean & jerk",
         "Strength is not converting to the C&J — check jerk or clean technique"),
    ]

    def _pct(v, lo, hi):
        return max(0, min(100, round((v - lo) / (hi - lo) * 100)))

    results = []
    for label, num, den, t_lo, t_hi, b_lo, b_hi, low_msg, high_msg in _CHECKS:
        if num is None or den is None or den == 0:
            continue
        value = round(num / den, 2)
        if value < t_lo:
            status, message = "low", low_msg
        elif value > t_hi:
            status, message = "high", high_msg
        else:
            status, message = "ok", "Within the expected range"
        results.append({
            "label":            label,
            "value":            value,
            "target_low":       t_lo,
            "target_high":      t_hi,
            "status":           status,
            "message":          message,
            "numerator_kg":     num,
            "denominator_kg":   den,
            "value_pct":        _pct(value, b_lo, b_hi),
            "target_low_pct":   _pct(t_lo,  b_lo, b_hi),
            "target_width_pct": _pct(t_hi,  b_lo, b_hi) - _pct(t_lo, b_lo, b_hi),
        })
    return results


async def get_goal_progress(conn, athlete_id: int) -> dict | None:
    """Return goal + current maxes merged into a single progress dict, or None."""
    from web.async_db import async_fetch_one

    goal = await async_fetch_one(
        conn,
        """
        SELECT goal, competition_date, competition_name,
               target_snatch_kg, target_cj_kg
        FROM athlete_goals
        WHERE athlete_id = $1 AND is_active = TRUE
        ORDER BY priority DESC, id DESC
        LIMIT 1
        """,
        athlete_id,
    )
    if not goal:
        return None

    maxes = await async_fetch_one(
        conn,
        """
        SELECT
            MAX(CASE WHEN e.name = 'Snatch'       THEN am.weight_kg END) AS snatch,
            MAX(CASE WHEN e.name = 'Clean & Jerk' THEN am.weight_kg END) AS cj
        FROM athlete_maxes am
        JOIN exercises e ON e.id = am.exercise_id
        WHERE am.athlete_id = $1 AND am.max_type = 'current'
          AND e.name IN ('Snatch', 'Clean & Jerk')
        """,
        athlete_id,
    )

    snatch_cur = _f(maxes["snatch"]) if maxes else None
    cj_cur     = _f(maxes["cj"])     if maxes else None
    snatch_tgt = _f(goal["target_snatch_kg"])
    cj_tgt     = _f(goal["target_cj_kg"])

    def _bar(cur, tgt):
        if cur is None or tgt is None or tgt == 0:
            return None
        return {"current": cur, "target": tgt,
                "gap": round(tgt - cur, 1),
                "pct": min(100, round(cur / tgt * 100))}

    days_to_comp = None
    if goal["competition_date"]:
        delta = (goal["competition_date"] - date.today()).days
        days_to_comp = delta

    goal_labels = {
        "general_strength": "General Strength",
        "competition_prep": "Competition Prep",
        "technique_focus":  "Technique Focus",
    }

    return {
        "goal":             goal["goal"],
        "goal_label":       goal_labels.get(goal["goal"], goal["goal"].replace("_", " ").title()),
        "competition_date": goal["competition_date"],
        "competition_name": goal["competition_name"],
        "days_to_comp":     days_to_comp,
        "snatch":           _bar(snatch_cur, snatch_tgt),
        "cj":               _bar(cj_cur, cj_tgt),
        "has_targets":      snatch_tgt is not None or cj_tgt is not None,
    }


async def get_warnings(conn, athlete_id: int) -> list[str]:
    from web.async_db import async_fetch_all
    warnings = []
    cutoff = date.today() - timedelta(days=14)

    recent_logs = await async_fetch_all(
        conn,
        """
        SELECT log_date, overall_rpe, sleep_quality, stress_level
        FROM training_logs
        WHERE athlete_id = $1 AND log_date >= $2
        ORDER BY log_date DESC
        """,
        athlete_id, cutoff,
    )
    for log in recent_logs:
        d = log["log_date"].strftime("%b %d") if hasattr(log["log_date"], "strftime") else str(log["log_date"])
        if log["overall_rpe"] and float(log["overall_rpe"]) >= 9.0:
            warnings.append(f"High session RPE ({log['overall_rpe']}) on {d}")
        if log["sleep_quality"] and int(log["sleep_quality"]) <= 2:
            warnings.append(f"Poor sleep (quality {log['sleep_quality']}/5) on {d}")
        if log["stress_level"] and int(log["stress_level"]) >= 4:
            warnings.append(f"High stress (level {log['stress_level']}/5) on {d}")

    ex_stats = await async_fetch_all(
        conn,
        """
        SELECT tle.exercise_name,
               AVG(tle.rpe_deviation) AS avg_rpe_dev,
               AVG(tle.make_rate) AS avg_make_rate,
               COUNT(*) AS sessions
        FROM training_log_exercises tle
        JOIN training_logs tl ON tl.id = tle.log_id
        WHERE tl.athlete_id = $1 AND tl.log_date >= $2 AND tle.rpe IS NOT NULL
        GROUP BY tle.exercise_name
        HAVING COUNT(*) >= 2
        """,
        athlete_id, cutoff,
    )
    for ex in ex_stats:
        if ex["avg_rpe_dev"] and float(ex["avg_rpe_dev"]) > 1.5:
            warnings.append(f"{ex['exercise_name']}: avg RPE +{float(ex['avg_rpe_dev']):.1f} above target")
        if ex["avg_make_rate"] and float(ex["avg_make_rate"]) < 0.70:
            warnings.append(f"{ex['exercise_name']}: make rate {float(ex['avg_make_rate'])*100:.0f}% — consider reducing intensity")

    return warnings
