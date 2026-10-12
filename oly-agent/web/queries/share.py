# web/queries/share.py
"""Data for the shareable program page (SHARE-1): one JSON blob the standalone
page renders from — athlete card, each program's sessions, coach brief,
macrocycle, model cost, and the sources each session's prompt cited, resolved
from generation_log.retrieval_set to titles."""

from decimal import Decimal

from web.queries import program as pq

MAX_SHARED_PROGRAMS = 6


async def _sources_by_session(conn, program_id: int) -> dict[str, dict]:
    """{"<week>-<day>": {"C1": {title, author, type}, …}} from each session's
    successful attempt (the labels its rationale cites)."""
    from web.async_db import async_fetch_all
    rows = await async_fetch_all(
        conn,
        """
        SELECT DISTINCT ON (week_number, day_number) week_number, day_number, retrieval_set
        FROM generation_log
        WHERE program_id = $1 AND status = 'success' AND retrieval_set IS NOT NULL
        ORDER BY week_number, day_number, attempt_number DESC
        """,
        program_id,
    )
    source_ids = {item.get("source_id") for r in rows for item in (r["retrieval_set"] or [])
                  if isinstance(item, dict) and item.get("source_id")}
    titles = {}
    if source_ids:
        for s in await async_fetch_all(conn, "SELECT id, title, author, source_type FROM sources WHERE id = ANY($1::int[])",
                                       sorted(source_ids)):
            titles[s["id"]] = {"title": s["title"], "author": s["author"], "type": s["source_type"]}
    out: dict[str, dict] = {}
    for r in rows:
        labels = {item["label"]: titles[item["source_id"]] for item in (r["retrieval_set"] or [])
                  if isinstance(item, dict) and item.get("label") and item.get("source_id") in titles}
        out[f"{r['week_number']}-{r['day_number']}"] = labels
    return out


def _num(v):
    """NUMERIC columns arrive as Decimal; the page's JSON wants plain numbers."""
    return float(v) if isinstance(v, Decimal) else v


async def build_share_data(conn, athlete_id: int, program_ids: list[int]) -> dict | None:
    """The page's data, or None when any id isn't one of this athlete's programs."""
    from web.async_db import async_fetch_all, async_fetch_one
    from web.queries import profile as qprof

    ids = list(dict.fromkeys(program_ids))[:MAX_SHARED_PROGRAMS]
    if not ids:
        return None
    rows = await async_fetch_all(
        conn,
        """
        SELECT id, name, phase::text AS phase, status, duration_weeks, sessions_per_week, start_date,
               rationale, generation_params, macrocycle_id, macrocycle_block_index
        FROM generated_programs WHERE athlete_id = $1 AND id = ANY($2::int[])
        """,
        athlete_id, ids,
    )
    if len(rows) != len(ids):
        return None
    by_id = {r["id"]: dict(r) for r in rows}

    athlete = await qprof.get_athlete(conn, athlete_id) or {}
    maxes = await async_fetch_all(
        conn,
        """
        SELECT e.name, am.weight_kg FROM athlete_maxes am JOIN exercises e ON e.id = am.exercise_id
        WHERE am.athlete_id = $1 AND am.max_type = 'current' ORDER BY e.name
        """,
        athlete_id,
    )
    previous = await async_fetch_one(
        conn,
        """
        SELECT name, duration_weeks, outcome_summary FROM generated_programs
        WHERE athlete_id = $1 AND status = 'completed' ORDER BY updated_at DESC LIMIT 1
        """,
        athlete_id,
    )

    programs = []
    for pid in ids:
        p = by_id[pid]
        params = p.pop("generation_params") or {}
        macro = None
        if p.get("macrocycle_id"):
            m = await async_fetch_one(conn, "SELECT blocks FROM macrocycles WHERE id = $1 AND athlete_id = $2",
                                      p["macrocycle_id"], athlete_id)
            macro = {"blocks": (m or {}).get("blocks") or []}
        cost = await async_fetch_one(
            conn, "SELECT SUM(estimated_cost_usd) AS cost FROM generation_log WHERE program_id = $1", pid)
        sources = await _sources_by_session(conn, pid)
        sessions = []
        for week in await pq.get_program_weeks(conn, pid):
            for s in week["sessions"]:
                sessions.append({
                    "week_number": s["week_number"], "day_number": s["day_number"],
                    "session_label": s["session_label"],
                    "estimated_duration_minutes": s["estimated_duration_minutes"],
                    "sources": sources.get(f"{s['week_number']}-{s['day_number']}", {}),
                    "exercises": [{
                        k: _num(ex.get(k)) for k in (
                            "exercise_order", "exercise_name", "sets", "reps", "intensity_pct",
                            "intensity_reference", "absolute_weight_kg", "rpe_target", "rest_seconds",
                            "notes", "selection_rationale", "is_warmup")
                    } for ex in s["exercises"]],
                })
        programs.append({
            "id": pid, "name": p["name"], "phase": p["phase"], "status": p["status"],
            "duration_weeks": p["duration_weeks"], "sessions_per_week": p["sessions_per_week"],
            "start_date": str(p["start_date"]) if p["start_date"] else None,
            "rationale": p["rationale"], "coach": params.get("coach"), "model": params.get("model"),
            "macrocycle": macro, "macrocycle_block_index": p.get("macrocycle_block_index"),
            "generation_cost_usd": float((cost or {}).get("cost") or 0),
            "sessions": sessions,
        })

    prev = None
    if previous:
        outcome = previous.get("outcome_summary") or {}
        prev = {"name": previous["name"], "duration_weeks": previous["duration_weeks"],
                "adherence_pct": outcome.get("adherence_pct") if isinstance(outcome, dict) else None}
    return {
        "athlete": {
            "name": athlete.get("name"), "level": athlete.get("level"),
            "bodyweight_kg": _num(athlete.get("bodyweight_kg")), "weight_class": athlete.get("weight_class"),
            "training_age_years": _num(athlete.get("training_age_years")),
            "sessions_per_week": athlete.get("sessions_per_week"),
            "technical_faults": list(athlete.get("technical_faults") or []),
        },
        "maxes": {r["name"]: float(r["weight_kg"]) for r in maxes},
        "previous": prev,
        "programs": programs,
    }
