# oly-agent/tests/test_web_queries.py
"""
Tests for web query/job helpers touched by the web LOW batch:
  - W-L4: get_job_status reads ownership from the job's embedded args
  - W-L6: _representative_reps_per_set (prescribed volume basis)
  - W-L7: _parse_log_date clamping
  - W-INFO: prefillExercise uses data-* attributes, not JS-string interpolation

Run: python tests/test_web_queries.py
"""

import asyncio
import re
import sys
from datetime import UTC, date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import web.jobs as jobs
from web.queries.log_session import _parse_log_date
from web.queries.program import _representative_reps_per_set
from web.routers.dashboard import _current_week

from shared.constants import MAX_LOG_BACKFILL_DAYS
from shared.timeutil import today_in_tz

RESULTS = []


def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, f"{type(e).__name__}: {e}"))


# ── W-L6: _representative_reps_per_set ───────────────────────────────────────

def test_reps_single():
    assert _representative_reps_per_set("3") == 3.0


def test_reps_comma_list_averages():
    # "3,2,1" → per-set average 2.0 (so sets×avg matches the actual-side math)
    assert _representative_reps_per_set("3,2,1") == 2.0


def test_reps_range_midpoint():
    assert _representative_reps_per_set("8-10") == 9.0


def test_reps_unparseable_none():
    assert _representative_reps_per_set("") is None
    assert _representative_reps_per_set("abc") is None


# ── W-L7: _parse_log_date clamping ───────────────────────────────────────────

def test_log_date_valid_recent_kept():
    d = date.today() - timedelta(days=3)
    assert _parse_log_date({"log_date": d.isoformat()}) == d


def test_log_date_future_clamped_to_today():
    future = (date.today() + timedelta(days=30)).isoformat()
    assert _parse_log_date({"log_date": future}) == date.today()


def test_log_date_far_past_clamped_to_today():
    ancient = (date.today() - timedelta(days=MAX_LOG_BACKFILL_DAYS + 10)).isoformat()
    assert _parse_log_date({"log_date": ancient}) == date.today()


def test_log_date_garbage_falls_back_to_today():
    assert _parse_log_date({"log_date": "3000-99-99"}) == date.today()
    assert _parse_log_date({}) == date.today()


# ── W-L4: get_job_status ownership from job args ─────────────────────────────

def _fake_job(args, status=None, kwargs=None):
    from arq.jobs import JobStatus
    job = MagicMock()
    info = MagicMock(args=args, kwargs=kwargs or {}) if args is not None else None
    job.info = AsyncMock(return_value=info)
    job.status = AsyncMock(return_value=status or JobStatus.in_progress)
    return job


def test_job_status_owner_match_returns_running():
    job = _fake_job(args=[7])
    with patch.object(jobs, "_arq_pool", MagicMock()), patch("arq.jobs.Job", return_value=job):
        result = asyncio.run(jobs.get_job_status("jid", 7))
    assert result["status"] == "running", result


def test_job_status_owner_mismatch_not_found():
    job = _fake_job(args=[7])
    with patch.object(jobs, "_arq_pool", MagicMock()), patch("arq.jobs.Job", return_value=job):
        result = asyncio.run(jobs.get_job_status("jid", 999))
    assert result["status"] == "failed" and result["error"] == "Job not found", result


def test_job_status_missing_info_not_found():
    job = _fake_job(args=None)  # info() returns None (job/result expired)
    with patch.object(jobs, "_arq_pool", MagicMock()), patch("arq.jobs.Job", return_value=job):
        result = asyncio.run(jobs.get_job_status("jid", 7))
    assert result["status"] == "failed", result


# ── W-L5: timezone-aware week/today ──────────────────────────────────────────

def test_today_in_tz_utc_matches_utc_now():
    from datetime import datetime
    assert today_in_tz("UTC") == datetime.now(UTC).date()


def test_today_in_tz_bad_or_missing_zone_falls_back_to_utc():
    from datetime import datetime
    utc_today = datetime.now(UTC).date()
    assert today_in_tz("Not/AZone") == utc_today
    assert today_in_tz(None) == utc_today
    assert today_in_tz("") == utc_today


def test_today_in_tz_valid_zone_returns_a_date():
    from datetime import date as _date
    assert isinstance(today_in_tz("America/New_York"), _date)


def test_current_week_uses_passed_today():
    from datetime import date as _date
    start = _date(2026, 1, 1)
    assert _current_week(start, 12, _date(2026, 1, 1)) == 1     # day 0 → week 1
    assert _current_week(start, 12, _date(2026, 1, 16)) == 3    # 15 days in → week 3
    assert _current_week(start, 4, _date(2027, 1, 1)) == 4      # clamped to duration
    # the athlete's local 'today' drives the bucket, not the server's
    assert _current_week(start, 12, _date(2026, 1, 8)) == 2


# ── WEB-H1: get_exercise_log_entry must be scoped by log_id ──────────────────

def test_get_exercise_log_entry_scoped_by_log_id():
    """IDOR regression: the read-back after an exercise-log edit must only
    return rows belonging to the (ownership-checked) log, not any tle_id."""
    from web.queries import log_session as lsq
    captured = {}

    async def fake_fetch_one(conn, sql, *params):
        captured["sql"] = sql
        captured["params"] = params
        return None

    with patch("web.async_db.async_fetch_one", fake_fetch_one):
        asyncio.run(lsq.get_exercise_log_entry(MagicMock(), 5, 9))
    assert "log_id" in captured["sql"], "WEB-H1: query must scope by log_id"
    assert 9 in captured["params"], captured["params"]


# ── WEB-H3: date form fields parsed to datetime.date before asyncpg ──────────

def _capture_async(ret=1):
    captured = {}

    async def fake(conn, sql, *params):
        captured["sql"], captured["params"] = sql, params
        return ret

    return captured, fake


def test_create_athlete_dob_string_becomes_date():
    from web.queries import setup as setup_q
    captured, fake = _capture_async()
    data = {"name": "A", "level": "beginner", "username": "u",
            "date_of_birth": "1990-05-10"}
    with patch("web.async_db.async_execute_returning", fake):
        asyncio.run(setup_q.create_athlete(MagicMock(), data, "hash"))
    dob = captured["params"][6]
    assert isinstance(dob, date), f"date_of_birth must be datetime.date, got {type(dob).__name__}"
    assert dob == date(1990, 5, 10)


def test_create_athlete_garbage_dob_becomes_none():
    from web.queries import setup as setup_q
    captured, fake = _capture_async()
    data = {"name": "A", "level": "beginner", "username": "u",
            "date_of_birth": "10/05/1990"}
    with patch("web.async_db.async_execute_returning", fake):
        asyncio.run(setup_q.create_athlete(MagicMock(), data, "hash"))
    assert captured["params"][6] is None


def test_update_profile_dob_string_becomes_date():
    from web.queries import profile as profile_q
    captured, fake = _capture_async()
    data = {"name": "A", "level": "beginner", "date_of_birth": "1991-02-03"}
    with patch("web.async_db.async_execute", fake):
        asyncio.run(profile_q.update_profile(MagicMock(), 1, data))
    dob = captured["params"][4]
    assert isinstance(dob, date), f"date_of_birth must be datetime.date, got {type(dob).__name__}"


def test_upsert_goal_insert_competition_date_becomes_date():
    from web.queries import profile as profile_q
    captured, fake = _capture_async()

    async def no_existing(conn, sql, *params):
        return None

    with patch("web.async_db.async_execute", fake), \
         patch("web.async_db.async_fetch_one", no_existing):
        asyncio.run(profile_q.upsert_goal(
            MagicMock(), 1,
            {"goal": "competition_prep", "competition_date": "2026-09-12"},
        ))
    cd = captured["params"][2]
    assert isinstance(cd, date), f"competition_date must be datetime.date, got {type(cd).__name__}"


def test_upsert_goal_update_competition_date_becomes_date():
    from web.queries import profile as profile_q
    captured, fake = _capture_async()

    async def existing_goal(conn, sql, *params):
        return {"id": 3}

    with patch("web.async_db.async_execute", fake), \
         patch("web.async_db.async_fetch_one", existing_goal):
        asyncio.run(profile_q.upsert_goal(
            MagicMock(), 1,
            {"goal": "competition_prep", "competition_date": "2026-09-12"},
        ))
    cd = captured["params"][1]
    assert isinstance(cd, date), f"competition_date must be datetime.date, got {type(cd).__name__}"


# ── WEB-M2: _parse_log_date clamps against the athlete's local today ─────────

def test_log_date_clamps_against_passed_today_not_server():
    """An athlete-local 'today' east of the server must not be treated as a
    future date and silently re-dated to the server's yesterday."""
    athlete_today = date(2030, 6, 15)  # far from server today on purpose
    assert _parse_log_date({"log_date": "2030-06-15"}, today=athlete_today) == athlete_today
    # future relative to the athlete's today clamps to the athlete's today
    assert _parse_log_date({"log_date": "2030-06-16"}, today=athlete_today) == athlete_today


def test_create_session_log_threads_today_through():
    from web.queries import log_session as lsq
    captured, fake = _capture_async(ret=10)
    athlete_today = date(2030, 6, 15)
    with patch("web.async_db.async_execute_returning", fake):
        asyncio.run(lsq.create_session_log(
            MagicMock(), 1, 2, {"log_date": "2030-06-15"}, today=athlete_today))
    assert captured["params"][2] == athlete_today, captured["params"]


# ── WEB-M5: blank sets/weight must not hit NOT NULL columns ──────────────────

def test_create_exercise_log_defaults_blank_sets_and_weight():
    from web.queries import log_session as lsq
    captured, fake = _capture_async(ret=5)
    form = {"exercise_name": "Plank", "reps_per_set": "3,3", "sets_completed": "", "weight_kg": ""}
    with patch("web.async_db.async_execute_returning", fake):
        asyncio.run(lsq.create_exercise_log(MagicMock(), 10, form))
    params = captured["params"]
    assert params[3] is not None, "sets_completed must default, not NULL (WEB-M5)"
    assert params[5] is not None, "weight_kg must default, not NULL (WEB-M5)"
    assert params[3] == 2, "sets default should follow the reps entries"


def test_update_exercise_log_defaults_blank_sets_and_weight():
    from web.queries import log_session as lsq
    captured = {}

    async def fake_execute(conn, sql, *params):
        captured["sql"], captured["params"] = sql, params

    async def fake_fetch_one(conn, sql, *params):
        return {"prescribed_weight_kg": None, "session_exercise_id": None}

    form = {"exercise_name": "Plank", "reps_per_set": "", "sets_completed": "", "weight_kg": ""}
    with patch("web.async_db.async_execute", fake_execute), \
         patch("web.async_db.async_fetch_one", fake_fetch_one):
        asyncio.run(lsq.update_exercise_log(MagicMock(), 5, form, 10))
    params = captured["params"]
    assert params[0] is not None, "sets_completed must default, not NULL (WEB-M5)"
    assert params[2] is not None, "weight_kg must default, not NULL (WEB-M5)"


# ── WEB-M6: exports/history must not drop logs unlinked by program deletion ──

def test_full_training_log_uses_left_joins():
    import inspect

    from web.queries import export as export_q
    src = inspect.getsource(export_q.get_full_training_log)
    assert "LEFT JOIN program_sessions" in src, \
        "unlinked logs (session_id NULL after program delete) must survive the export"


def test_exercise_history_uses_left_joins():
    import inspect

    from web.queries import history as history_q
    src = inspect.getsource(history_q.get_exercise_history)
    assert "LEFT JOIN program_sessions" in src, \
        "unlinked logs must appear in per-exercise history"


# ── WEB-M8: worker passes a deadline so the job timeout is enforceable ───────

def test_worker_passes_deadline_to_orchestrator():
    import web.worker as worker
    captured = {}

    def fake_run(athlete_id, settings, dry_run=False, deadline=None, duration_weeks=None, **macro):
        captured["deadline"] = deadline
        captured["duration_weeks"] = duration_weeks
        captured["macro"] = macro
        return 42

    with patch("orchestrator.run", side_effect=fake_run):
        result = asyncio.run(worker.run_generation({}, 1, duration_weeks=6))
    assert result["program_id"] == 42
    assert captured["duration_weeks"] == 6                      # PLAN-1: the form's block length reaches the planner
    assert captured.get("deadline") is not None, \
        "worker must pass a monotonic deadline (WEB-M8 — thread outlives job_timeout)"
    assert captured["macro"] == {"new_macrocycle": False, "macrocycle_weeks": None, "macrocycle_id": None}


def test_worker_passes_macrocycle_to_orchestrator():
    """PLAN-3e: {"new", "weeks"} plans one; {"id"} generates its next block."""
    import web.worker as worker
    seen = []
    with patch("orchestrator.run", side_effect=lambda *a, **kw: seen.append(kw) or 7):
        asyncio.run(worker.run_generation({}, 1, macrocycle={"new": True, "weeks": 16}))
        asyncio.run(worker.run_generation({}, 1, macrocycle={"id": 3}))
    assert (seen[0]["new_macrocycle"], seen[0]["macrocycle_weeks"], seen[0]["macrocycle_id"]) == (True, 16, None)
    assert (seen[1]["new_macrocycle"], seen[1]["macrocycle_id"]) == (False, 3)


# ── WEB-L2: one in-flight generation per athlete ─────────────────────────────

def test_submit_generation_rejects_concurrent():
    pool = MagicMock()
    pool.set = AsyncMock(return_value=False)  # NX guard already held
    pool.enqueue_job = AsyncMock()
    with patch.object(jobs, "_arq_pool", pool):
        try:
            asyncio.run(jobs.submit_generation(7))
            raise AssertionError("expected GenerationInFlightError (WEB-L2)")
        except jobs.GenerationInFlightError:
            pass
    assert not pool.enqueue_job.called, "a second job must not be enqueued"


def test_submit_generation_guard_then_enqueue():
    pool = MagicMock()
    pool.set = AsyncMock(return_value=True)
    job = MagicMock()
    job.job_id = "j1"
    pool.enqueue_job = AsyncMock(return_value=job)
    with patch.object(jobs, "_arq_pool", pool):
        jid = asyncio.run(jobs.submit_generation(7))
    assert jid == "j1"
    # first set is the NX reservation; a second set stamps the job_id (web-L3)
    reserve = pool.set.await_args_list[0]
    assert reserve.kwargs.get("nx") is True, "guard must be SET NX"
    assert reserve.kwargs.get("ex"), "guard must expire (stuck-job safety)"
    stamp = pool.set.await_args_list[1]
    assert stamp.args[1] == "j1", "second set must stamp the job_id onto the guard"


def test_stale_poll_does_not_release_other_jobs_guard():
    """audit5 web-L3: the guard value stores the owning job_id; a terminal poll
    of an OLD job must not free the guard held by a NEW in-flight job."""
    from arq.jobs import JobStatus
    old_job = _fake_job(args=[7], status=JobStatus.complete)
    old_job.result = AsyncMock(return_value={"program_id": 1, "duration_seconds": 1.0})
    pool = MagicMock()
    pool.get = AsyncMock(return_value=b"new_job_id")  # guard held by a different job
    pool.delete = AsyncMock()
    with patch.object(jobs, "_arq_pool", pool), patch("arq.jobs.Job", return_value=old_job):
        asyncio.run(jobs.get_job_status("old_job_id", 7))
    assert not pool.delete.called, "must not release a guard held by a different job (audit5 web-L3)"


def test_submit_generation_releases_guard_on_cancellation():
    """audit3-L3: `except Exception` misses asyncio.CancelledError — a request
    cancelled mid-enqueue leaked the guard for the full 660s TTL."""
    import asyncio as _asyncio
    pool = MagicMock()
    pool.set = AsyncMock(return_value=True)
    pool.delete = AsyncMock()
    pool.enqueue_job = AsyncMock(side_effect=_asyncio.CancelledError())
    with patch.object(jobs, "_arq_pool", pool):
        try:
            asyncio.run(jobs.submit_generation(7))
            raise AssertionError("expected CancelledError to propagate")
        except _asyncio.CancelledError:
            pass
    pool.delete.assert_awaited_with("gen_inflight:7")


def test_job_status_terminal_clears_inflight():
    from arq.jobs import JobStatus
    job = _fake_job(args=[7], status=JobStatus.complete)
    job.result = AsyncMock(return_value={"program_id": 42, "duration_seconds": 1.0})
    pool = MagicMock()
    pool.get = AsyncMock(return_value=b"jid")  # guard held by THIS job → release
    pool.delete = AsyncMock()
    with patch.object(jobs, "_arq_pool", pool), patch("arq.jobs.Job", return_value=job):
        result = asyncio.run(jobs.get_job_status("jid", 7))
    assert result["status"] == "done"
    pool.delete.assert_awaited_with("gen_inflight:7")


# ── WEB-L12: exercise_name must respect VARCHAR(200) NOT NULL ────────────────

def test_parse_text_bounds_and_defaults():
    from web.formparse import parse_text
    assert parse_text("  Snatch  ", 200) == "Snatch"
    assert parse_text("x" * 500, 200) == "x" * 200, "must truncate to the column width"
    assert parse_text("", 200, default="Unnamed") == "Unnamed"
    assert parse_text("   ", 200, default="Unnamed") == "Unnamed"
    assert parse_text(None, 200, default="Unnamed") == "Unnamed"


def test_create_exercise_log_bounds_exercise_name():
    """queries/log_session.py passed the raw string into VARCHAR(200) NOT NULL:
    >200 chars is an asyncpg 22001 → 500, blank inserts a junk row (WEB-L12)."""
    import asyncio as _asyncio

    from web.queries import log_session as qls

    captured = {}

    async def _fake_returning(conn, query, *args):
        captured["args"] = args
        return 1

    async def _fake_fetch_one(conn, query, *args):
        return None

    with patch("web.async_db.async_execute_returning", _fake_returning), \
         patch("web.async_db.async_fetch_one", _fake_fetch_one):
        _asyncio.run(qls.create_exercise_log(MagicMock(), 1, {"exercise_name": "S" * 500}))
        name = captured["args"][2]
        assert len(name) <= 200, f"exercise_name must be truncated, got {len(name)} chars"

        _asyncio.run(qls.create_exercise_log(MagicMock(), 1, {"exercise_name": "   "}))
        assert captured["args"][2].strip(), "blank name must not insert an unnamed junk row"


# ── WEB-L11: a failed real run must not report as a completed dry run ────────

def _completed_job(dry_run: bool, program_id):
    from arq.jobs import JobStatus
    job = _fake_job(args=[7], status=JobStatus.complete, kwargs={"dry_run": dry_run})
    job.result = AsyncMock(return_value={"program_id": program_id, "duration_seconds": 9.0})
    pool = MagicMock()
    pool.get = AsyncMock(return_value=b"jid")
    pool.delete = AsyncMock()
    with patch.object(jobs, "_arq_pool", pool), patch("arq.jobs.Job", return_value=job):
        return asyncio.run(jobs.get_job_status("jid", 7))


def test_job_status_failed_real_run_is_not_done():
    """orchestrator.run returns None on failure but the ARQ job still completes,
    so the UI painted the green '✓ Program generated / Dry run complete' banner
    over a failed paid run (WEB-L11)."""
    result = _completed_job(dry_run=False, program_id=None)
    assert result["status"] == "failed", result
    assert result["program_id"] is None
    assert result["error"], "a failed run must carry a message for the UI"


def test_job_status_dry_run_still_done():
    result = _completed_job(dry_run=True, program_id=None)
    assert result["status"] == "done", result


def test_job_status_successful_run_still_done():
    result = _completed_job(dry_run=False, program_id=42)
    assert result["status"] == "done" and result["program_id"] == 42, result


# ── WEB-L3: duplicate training_logs race ─────────────────────────────────────

def test_session_log_insert_upserts_on_session_conflict():
    import inspect

    from web.queries import log_session as lsq
    src = inspect.getsource(lsq.create_session_log)
    assert "ON CONFLICT" in src and "session_id" in src, \
        "double-submit must upsert, not raise on the unique index (WEB-L3)"
    src2 = inspect.getsource(lsq.get_existing_log)
    assert "ORDER BY id" in src2, "get_existing_log must be deterministic (WEB-L3)"


# ── WEB-L4: nan/inf/huge floats must not reach NUMERIC columns ───────────────

def test_parse_float_rejects_nan_inf_huge():
    from web.formparse import parse_float
    assert parse_float("nan") is None
    assert parse_float("inf") is None
    assert parse_float("-inf") is None
    assert parse_float("1e9") is None, "NUMERIC overflow guard"
    assert parse_float("82.5") == 82.5
    assert parse_float("") is None
    assert parse_float(None) is None


def test_parse_int_bounded():
    """audit2-L3: unbounded ints overflow int4 / violate CHECKs into 500s."""
    from web.formparse import parse_int
    assert parse_int("99999999999999999999") is None, "int4 overflow guard"
    assert parse_int("4") == 4
    assert parse_int("4", lo=1, hi=14) == 4
    assert parse_int("99", lo=1, hi=14) is None, "CHECK-range guard"
    assert parse_int("-5", lo=1, hi=14) is None
    assert parse_int("abc") is None


def test_session_log_bounded_int_fields():
    """sleep_quality/stress_level have CHECK 1..5 — out-of-range must store
    NULL, not 500 on the constraint (audit2-L3)."""
    from web.queries import log_session as lsq
    captured, fake = _capture_async(ret=10)
    form = {"log_date": "", "sleep_quality": "7", "stress_level": "0", "duration": "60"}
    with patch("web.async_db.async_execute_returning", fake):
        asyncio.run(lsq.create_session_log(MagicMock(), 1, 2, form))
    params = captured["params"]
    assert params[6] is None, f"sleep_quality 7 violates CHECK 1..5: {params[6]}"
    assert params[7] is None, f"stress_level 0 violates CHECK 1..5: {params[7]}"
    assert params[4] == 60


# ── audit2-L5: in-flight guard released if the enqueue itself fails ──────────

def test_submit_generation_releases_guard_on_enqueue_failure():
    pool = MagicMock()
    pool.set = AsyncMock(return_value=True)
    pool.delete = AsyncMock()
    pool.enqueue_job = AsyncMock(side_effect=RuntimeError("redis hiccup"))
    with patch.object(jobs, "_arq_pool", pool):
        try:
            asyncio.run(jobs.submit_generation(7))
            raise AssertionError("expected the enqueue error to propagate")
        except RuntimeError:
            pass
    pool.delete.assert_awaited_with("gen_inflight:7")


def test_update_profile_sessions_per_week_bounded():
    """audit3-M2: athletes.sessions_per_week has CHECK BETWEEN 1 AND 14 —
    an out-of-range submit must fall back to the default, not 500."""
    from web.queries import profile as profile_q
    captured, fake = _capture_async()
    data = {"name": "A", "level": "beginner", "sessions_per_week": "20"}
    with patch("web.async_db.async_execute", fake):
        asyncio.run(profile_q.update_profile(MagicMock(), 1, data))
    assert captured["params"][9] == 4, f"20 violates CHECK 1..14, must default: {captured['params'][9]}"


def test_create_athlete_sessions_per_week_bounded():
    from web.queries import setup as setup_q
    captured, fake = _capture_async()
    data = {"name": "A", "level": "beginner", "username": "u", "sessions_per_week": "99"}
    with patch("web.async_db.async_execute_returning", fake):
        asyncio.run(setup_q.create_athlete(MagicMock(), data, "hash"))
    assert captured["params"][9] == 4, f"99 violates CHECK 1..14, must default: {captured['params'][9]}"


def test_reps_per_set_entries_bounded():
    """audit3-L2: a huge rep entry parses fine in Python and overflows the
    INT[] column into a 500 — bound the entries."""
    from web.queries import log_session as lsq
    captured, fake = _capture_async(ret=5)
    form = {"exercise_name": "Snatch", "sets_completed": "2",
            "reps_per_set": "3,99999999999999999999", "weight_kg": "70"}
    with patch("web.async_db.async_execute_returning", fake), \
         patch("web.async_db.async_fetch_one", AsyncMock(return_value=None)):
        asyncio.run(lsq.create_exercise_log(MagicMock(), 10, form))
    assert captured["params"][4] is None, \
        f"overflowing rep entries must invalidate the list: {captured['params'][4]}"


def test_update_profile_nan_bodyweight_stored_as_null():
    from web.queries import profile as profile_q
    captured, fake = _capture_async()
    data = {"name": "A", "level": "beginner", "bodyweight_kg": "nan"}
    with patch("web.async_db.async_execute", fake):
        asyncio.run(profile_q.update_profile(MagicMock(), 1, data))
    assert captured["params"][5] is None, "NaN must be dropped, not stored (WEB-L4)"


# ── WEB-L9: client-controlled session_exercise_id must be scoped ─────────────

def _l9_form():
    return {"exercise_name": "Snatch", "session_exercise_id": "999",
            "reps_per_set": "3", "sets_completed": "3", "weight_kg": "70"}


def test_create_exercise_log_drops_foreign_session_exercise_id():
    from web.queries import log_session as lsq
    captured, fake = _capture_async(ret=5)

    async def no_match(conn, sql, *params):
        return None  # se_id does not belong to this log's session

    with patch("web.async_db.async_execute_returning", fake), \
         patch("web.async_db.async_fetch_one", no_match):
        asyncio.run(lsq.create_exercise_log(MagicMock(), 10, _l9_form()))
    assert captured["params"][1] is None, "cross-tenant se_id must not be stored (WEB-L9)"


def test_create_exercise_log_keeps_valid_session_exercise_id():
    from web.queries import log_session as lsq
    captured, fake = _capture_async(ret=5)

    async def match(conn, sql, *params):
        return {"ok": 1}

    with patch("web.async_db.async_execute_returning", fake), \
         patch("web.async_db.async_fetch_one", match):
        asyncio.run(lsq.create_exercise_log(MagicMock(), 10, _l9_form()))
    assert captured["params"][1] == 999, captured["params"]


# ── audit5 web-M1: dashboard warnings not gated on RPE presence ──────────────

def test_dashboard_warnings_query_accepts_either_metric():
    import inspect

    from web.queries import dashboard as dq
    src = inspect.getsource(dq.get_warnings)
    assert "rpe IS NOT NULL OR" in src and "make_rate IS NOT NULL" in src, \
        "make-rate-only rows must feed the dashboard warning (audit5 web-M1)"
    assert "COUNT(tle.make_rate)" in src, "per-metric sample gating missing"


# ── audit5 web-L1: status-machine guards on lifecycle transitions ────────────

def test_activate_program_scoped_to_draft():
    from web.queries import program as pq
    captured = []

    async def fake(conn, sql, *params):
        captured.append(sql)

    with patch("web.async_db.async_execute", fake):
        asyncio.run(pq.activate_program(MagicMock(), 1, 1))
    activate_sql = next(s for s in captured if "status = 'active'" in s and "WHERE id" in s)
    assert "status IN ('draft'" in activate_sql or "status = 'draft'" in activate_sql, \
        f"activate must be scoped to draft status (audit5 web-L1): {activate_sql}"


# ── audit5 web-H1: get_athlete_maxes vs the real estimate contract ───────────

def test_get_athlete_maxes_unpacks_real_estimates():
    """audit5-H1: A-R8 changed estimate_missing_maxes to return {ref: float};
    this web caller still unpacked 2-tuples → TypeError → dashboard 500 for any
    athlete with a snatch/C&J max, and max upserts rolled back. The suite was
    green because every router test mocks get_athlete_maxes."""
    from decimal import Decimal

    from web.queries import program as pq

    async def fake_fetch_all(conn, sql, *params):
        return [{"exercise_name": "Snatch", "weight_kg": Decimal("100.0"),
                 "date_achieved": None}]

    with patch("web.async_db.async_fetch_all", fake_fetch_all):
        result = asyncio.run(pq.get_athlete_maxes(MagicMock(), 1))
    estimated = [r for r in result if r["is_estimated"]]
    assert estimated, "snatch-derived estimates must be present"
    assert all(isinstance(r["weight_kg"], (int, float)) for r in estimated), result


# ── audit5 web-H2: arq must receive a real RedisSettings, not the classmethod ─

def test_worker_settings_consumable_by_arq():
    """audit5-H2: arq reads WorkerSettings.__dict__ verbatim — a @classmethod
    redis_settings reaches Worker() as a classmethod object and the worker
    crashes on startup ('classmethod' has no attribute 'host'). The generation
    feature was dead end-to-end."""
    import web.worker as worker
    from arq.connections import RedisSettings
    from arq.worker import get_kwargs

    kwargs = get_kwargs(worker.WorkerSettings)
    assert isinstance(kwargs["redis_settings"], RedisSettings), \
        f"arq got {type(kwargs['redis_settings']).__name__} instead of RedisSettings (audit5-H2)"


# ── W-INFO: prefillExercise uses data-* attributes ───────────────────────────

def test_prefill_uses_data_attributes_not_js_string():
    tpl = (Path(__file__).parent.parent / "web" / "templates" / "partials"
           / "exercise_log_section.html").read_text(encoding="utf-8")
    assert "onclick=\"prefillExercise(this)\"" in tpl
    assert "data-name=" in tpl
    # the old JS-string interpolation form must be gone
    assert "prefillExercise('" not in tpl


# ── FE-L5: the Warmup badge must key off structure, not prose ────────────────

def test_warmup_set_detected_from_intensity():
    from shared.constants import WARMUP_VOLUME_EXCLUSION_PCT
    from shared.exercise_mapping import is_warmup_set
    cutoff = WARMUP_VOLUME_EXCLUSION_PCT
    assert is_warmup_set("snatch", 55) is True
    assert is_warmup_set("snatch", cutoff) is True, "the band is inclusive"
    assert is_warmup_set("clean_and_jerk", 50) is True
    assert is_warmup_set("snatch", cutoff + 1) is False, "working sets are not warmups"
    assert is_warmup_set("snatch", 85) is False


def test_warmup_set_only_applies_to_competition_lifts():
    """The prompt mandates the ramp before comp lifts; a light accessory set is
    not a warmup."""
    from shared.exercise_mapping import is_warmup_set
    assert is_warmup_set("back_squat", 55) is False
    assert is_warmup_set("push_press", 50) is False


def test_warmup_set_handles_missing_and_junk_intensity():
    from shared.exercise_mapping import is_warmup_set
    assert is_warmup_set("snatch", None) is False
    assert is_warmup_set(None, 55) is False
    assert is_warmup_set("snatch", "not a number") is False
    # asyncpg hands NUMERIC back as Decimal
    from decimal import Decimal
    assert is_warmup_set("snatch", Decimal("57.5")) is True


def test_get_program_weeks_tags_warmup_rows():
    """The template just reads ex.is_warmup, so the query has to set it."""
    from web.queries.program import get_program_weeks
    sessions = [{"id": 1, "week_number": 1, "day_number": 1, "session_label": "Snatch Day",
                 "estimated_duration_minutes": 60, "focus_area": "snatch", "log_id": None}]
    exercises = [
        {"session_id": 1, "exercise_order": 1, "exercise_name": "Snatch", "sets": 2, "reps": 3,
         "intensity_pct": 55.0, "intensity_reference": "snatch", "absolute_weight_kg": 55.0,
         "rest_seconds": 90, "rpe_target": None, "selection_rationale": "ramp"},
        {"session_id": 1, "exercise_order": 2, "exercise_name": "Snatch", "sets": 4, "reps": 2,
         "intensity_pct": 85.0, "intensity_reference": "snatch", "absolute_weight_kg": 85.0,
         "rest_seconds": 180, "rpe_target": 8.0, "selection_rationale": "not a warmup priority"},
    ]
    with patch("web.async_db.async_fetch_all",
               new=AsyncMock(side_effect=[sessions, exercises])):
        weeks = asyncio.run(get_program_weeks(MagicMock(), 1))

    rows = weeks[0]["sessions"][0]["exercises"]
    assert [r["is_warmup"] for r in rows] == [True, False], \
        "the 55% ramp set is a warmup; the 85% working set is not, despite its rationale text"


def test_badge_no_longer_substring_matches_the_rationale():
    """'warmup' in selection_rationale fired on prose like "not a warmup
    priority" and disappeared whenever the generator reworded."""
    tpl = (Path(__file__).parent.parent / "web" / "templates" / "program.html").read_text(encoding="utf-8")
    assert "selection_rationale | lower" not in tpl
    assert "{% if ex.is_warmup %}" in tpl


# ── FE-L1: remapped gray text must clear WCAG AA ─────────────────────────────

def _relative_luminance(hex_color):
    h = hex_color.lstrip("#")
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg, bg):
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


_WEB = Path(__file__).parent.parent / "web"
_CONFIG = _WEB / "tailwind" / "tailwind.config.js"
_COMPILED = _WEB / "static" / "tailwind.css"


def _palette():
    """Parse the colour tokens out of tailwind.config.js.

    Reads the config rather than the compiled CSS so a failure points at the
    value a human edits. None of the colour groups nest, so a flat scan is
    enough. Returns {"paper": "#FDFBF8", "paper-100": ..., "blue-100": ...}.
    """
    text = _CONFIG.read_text(encoding="utf-8")
    marker = "colors: {"
    # Slice past the wrapper, or the group scan below matches `colors: {` itself.
    colors = text[text.index(marker) + len(marker):]
    out = {}
    # Top-level singles, e.g. canvas: '#F4F0EA'
    for name, value in re.findall(r"^\s{8}(\w+): '(#[0-9A-Fa-f]{6})'", colors, re.M):
        out[name] = value
    # Grouped scales, e.g. paper: { DEFAULT: '#FDFBF8', 100: '#EDE8E0', ... }
    for group, body in re.findall(r"(\w+): \{(.*?)\}", colors, re.S):
        for shade, value in re.findall(r"(\w+): '(#[0-9A-Fa-f]{6})'", body):
            out[group if shade == "DEFAULT" else f"{group}-{shade}"] = value
    return out


def test_contrast_helper_matches_known_ratios():
    # Sanity-check the formula against the extremes before trusting it below.
    assert round(_contrast("#000000", "#FFFFFF"), 1) == 21.0
    assert round(_contrast("#777777", "#FFFFFF"), 2) == 4.48


def test_palette_parses():
    p = _palette()
    for token in ("canvas", "paper", "paper-100", "ink", "ink-faint", "ink-muted",
                  "ink-sec", "ink-ghost", "navy", "navy-100", "navy-200", "blue-100"):
        assert token in p, f"{token} missing from the parsed palette: {sorted(p)}"


def test_ink_clears_aa_on_the_text_bearing_surfaces():
    """ink-faint/muted/sec carry dates, "Day N" labels, helper text and table
    metadata — small text, so 4.5:1. The old remap put them at #A09A94 (2.7:1)
    and #7A7570 (4.4:1) on the warm card.

    Only canvas/paper/paper-100 are checked: paper-200 and paper-300 are fills
    (progress-bar tracks, pill badges) that carry ink-700 or darker at most. That
    assumption isn't taken on trust — test_themed_pairs_clear_aa below derives the
    real class co-occurrences from the templates, so putting ink-faint on a
    paper-300 fill would fail there.
    """
    p = _palette()
    failures = []
    for token in ("ink", "ink-800", "ink-700", "ink-sec", "ink-muted", "ink-faint"):
        for surface in ("canvas", "paper", "paper-100"):
            ratio = _contrast(p[token], p[surface])
            if ratio < 4.5:
                failures.append(f"{token} on {surface} = {ratio:.2f}:1")
    assert not failures, "small text below 4.5:1: " + "; ".join(failures)


def test_nav_text_clears_aa_on_the_navy_nav():
    """The regression that motivated the palette split: the nav used
    text-gray-400, the same token as card metadata, and the !important remap
    force-darkened it to #6C6761 — 2.58:1 on navy. One token cannot serve both
    a cream card and a navy bar, so the nav has its own shades."""
    p = _palette()
    failures = []
    for token in ("navy-100", "navy-200"):
        ratio = _contrast(p[token], p["navy"])
        if ratio < 4.5:
            failures.append(f"{token} on navy = {ratio:.2f}:1")
    assert not failures, "nav text below 4.5:1: " + "; ".join(failures)
    assert _contrast("#FFFFFF", p["navy"]) >= 4.5, "hover:text-white must clear AA too"


def test_nav_uses_the_navy_text_tokens_not_the_ink_ones():
    base = (_WEB / "templates" / "base.html").read_text(encoding="utf-8")
    nav = base[base.index("<nav"):base.index("</nav>")]
    assert "text-navy-100" in nav and "text-navy-200" in nav
    assert "text-ink" not in nav, \
        "ink shades are tuned for cream surfaces and are unreadable on the navy nav"


def test_icon_only_controls_clear_the_non_text_threshold():
    """ink-ghost is for the hover-reveal ✕ buttons. As a graphical control it
    needs 3:1, which Tailwind's gray-300 (#D1D5DB) never met."""
    p = _palette()
    for surface in ("paper", "paper-100"):
        ratio = _contrast(p["ink-ghost"], p[surface])
        assert ratio >= 3.0, f"ink-ghost on {surface} = {ratio:.2f}:1, needs 3:1"
    assert _contrast("#D1D5DB", p["paper"]) < 3.0, \
        "sanity: the old value really was below the threshold"


def test_ink_keeps_its_visual_hierarchy():
    p = _palette()
    order = [_relative_luminance(p[t]) for t in ("ink-ghost", "ink-faint", "ink-muted", "ink-sec", "ink-800", "ink")]
    assert order == sorted(order, reverse=True), \
        f"ink should darken monotonically from ghost to DEFAULT, got {order}"


def test_themed_pairs_clear_aa():
    """Every bg-X/text-Y pair in themed tokens that the templates actually use —
    accents and ink-on-paper alike. Three accent pairs were already under 4.5:1
    with Tailwind's own tints (amber-700 on amber-100, blue-500 on blue-50,
    green-700 on green-100) and were bumped a shade."""
    p = _palette()
    pairs = set()
    for tpl in (_WEB / "templates").rglob("*.html"):
        for m in re.finditer(r'class="([^"]+)"', tpl.read_text(encoding="utf-8")):
            cls = m.group(1).split()
            bgs = [c[3:] for c in cls if re.fullmatch(r"bg-[a-z]+-\d{2,3}", c)]
            txt = [c[5:] for c in cls if re.fullmatch(r"text-[a-z]+-\d{2,3}", c)]
            pairs.update((b, t) for b in bgs for t in txt)

    checked, failures = 0, []
    for bg, fg in sorted(pairs):
        if bg not in p or fg not in p:
            continue          # not a themed token (e.g. a Tailwind default)
        checked += 1
        ratio = _contrast(p[fg], p[bg])
        if ratio < 4.5:
            failures.append(f"text-{fg} on bg-{bg} = {ratio:.2f}:1")
    assert checked >= 8, f"expected to check the accent badges, only saw {checked} pairs"
    assert not failures, "accent pairs below 4.5:1: " + "; ".join(failures)


# ── FE-L2: one compiled stylesheet, no overrides, no inline handlers ──────────

def test_no_important_overrides_anywhere():
    """The theme used to be ~40 !important rules retinting Tailwind's gray scale.
    With the palette defined in the config, Tailwind generates the right colours
    and nothing needs overriding."""
    css = _COMPILED.read_text(encoding="utf-8")
    assert "!important" not in css, "the compiled CSS should need no overrides"


def test_templates_carry_no_palette_of_their_own():
    """base/login/setup/error each had an inline <style> block with a *different
    subset* of the palette, so a fix in one silently skipped the others."""
    tpl_dir = _WEB / "templates"
    for name in ("base.html", "login.html", "setup.html", "error.html"):
        text = (tpl_dir / name).read_text(encoding="utf-8")
        assert "/static/tailwind.css" in text, f"{name} does not load the compiled CSS"
        assert "--text-sec" not in text and "--navy" not in text, \
            f"{name} still defines its own palette"
    # program.html keeps a page-specific @media print block; nothing else should
    # have an inline <style> at all.
    with_styles = sorted(p.name for p in tpl_dir.rglob("*.html") if "<style>" in p.read_text(encoding="utf-8"))
    assert with_styles == ["program.html"], f"unexpected inline styles in {with_styles}"


def test_no_default_gray_utilities_remain():
    """Tailwind's default gray is cool and is no longer remapped, so a stray
    text-gray-500 would now render visibly off-theme instead of being caught by
    the old override list."""
    offenders = {}
    for f in list((_WEB / "templates").rglob("*.html")) + [_WEB / "app.py"]:
        found = re.findall(r"(?:bg|text|border|divide|ring)-gray-\d{2,3}", f.read_text(encoding="utf-8"))
        if found:
            offenders[f.name] = sorted(set(found))
    assert not offenders, f"use the role-based tokens instead of Tailwind's gray: {offenders}"


def test_no_inline_event_handlers_outside_htmx_plumbing():
    """error.html styled its button with onmouseover/onmouseout — the only thing
    in the app a Content-Security-Policy would have broken."""
    text = (_WEB / "templates" / "error.html").read_text(encoding="utf-8")
    assert "onmouseover" not in text and "onmouseout" not in text
    assert "btn-navy" in text, "the hover state belongs in the stylesheet"
    assert ".btn-navy:hover" in _COMPILED.read_text(encoding="utf-8")


# ── FE-M10: mobile nav needs ARIA state and a way to close ───────────────────

def test_nav_toggle_exposes_and_updates_its_state():
    base = (Path(__file__).parent.parent / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'aria-expanded="false" aria-controls="nav-menu"' in base, \
        "the hamburger had only aria-label — nothing announced open/closed"
    assert "toggle.setAttribute('aria-expanded'" in base, "the state must follow the menu"


def test_nav_menu_closes_on_outside_click_and_escape():
    base = (Path(__file__).parent.parent / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "evt.key === 'Escape'" in base, "Escape must close the menu"
    assert "!menu.contains(evt.target)" in base, "tapping outside must close the menu"


# ── FE-M9: every label must be associated with its control ───────────────────

_FORM_TEMPLATES = [
    "profile.html", "setup.html", "log_session.html", "login.html",
    "partials/exercise_log_section.html", "partials/exercise_log_entry.html",
    "partials/maxes_table.html", "partials/weight_class_select.html",
]

_LABEL = re.compile(r"<label\b(?![^>]*\bfor=)[^>]*>((?:(?!</?label)[\s\S])*?)</label>")


def test_every_label_is_associated_with_a_control():
    """Labels sat as bare siblings of their inputs with no for/id pair, so
    clicking one didn't focus the field and screen readers announced the control
    as unlabelled. A label that *wraps* its input is already associated."""
    tpl_dir = Path(__file__).parent.parent / "web" / "templates"
    orphans = []
    for name in _FORM_TEMPLATES:
        text = (tpl_dir / name).read_text(encoding="utf-8")
        for m in _LABEL.finditer(text):
            body = m.group(1)
            if "<input" in body or "<select" in body or "<textarea" in body:
                continue  # wrapping label
            orphans.append((name, re.sub(r"\s+", " ", body).strip()[:40]))
    assert not orphans, f"labels with no for= and no wrapped control: {orphans}"


def test_repeated_row_ids_are_scoped_per_row():
    """exercise_log_entry.html renders once per logged exercise and shares field
    names with the add-exercise form, so name-based ids would collide."""
    tpl = (Path(__file__).parent.parent / "web" / "templates" / "partials"
           / "exercise_log_entry.html").read_text(encoding="utf-8")
    for field in ("sets", "reps", "weight", "rpe", "make-rate", "notes"):
        assert f'id="tle-{{{{ tle.id }}}}-{field}"' in tpl, f"{field} id is not row-scoped"


# ── FE-M8: the exercise row's edit form must be keyboard-reachable ────────────

def test_exercise_row_has_a_real_toggle_button():
    """The row was a <div onclick> with no role, tabindex or key handler, so a
    keyboard user could not open the edit form at all."""
    tpl = (Path(__file__).parent.parent / "web" / "templates" / "partials"
           / "exercise_log_entry.html").read_text(encoding="utf-8")
    assert 'aria-controls="tle-edit-{{ tle.id }}"' in tpl, \
        "the toggle must point at the form it opens"
    assert 'aria-expanded="false"' in tpl
    assert 'id="tle-toggle-{{ tle.id }}"' in tpl, "toggleEdit needs it to sync aria-expanded"


def test_toggle_edit_syncs_aria_expanded():
    tpl = (Path(__file__).parent.parent / "web" / "templates" / "partials"
           / "exercise_log_section.html").read_text(encoding="utf-8")
    assert "function toggleEdit" in tpl
    assert "aria-expanded" in tpl, "the toggle's state must follow the form's visibility"


# ── FE-M6: the delete button must be reachable without hover ─────────────────

def test_program_delete_button_visible_without_hover():
    tpl = (Path(__file__).parent.parent / "web" / "templates" / "program_list.html").read_text(encoding="utf-8")
    assert "opacity-60 focus:opacity-100 sm:opacity-0" in tpl, \
        "the ✕ must be visible below sm — touch devices have no hover"
    assert "sm:group-hover:opacity-100" in tpl, "keep the hover reveal on pointer widths"
    assert "sm:focus:opacity-100" in tpl, \
        "sm:opacity-0 outranks an unprefixed focus: variant, so focus needs the prefix too"


# ── FE-M4: the exit link must not claim to save ──────────────────────────────

def test_done_link_is_not_labelled_as_a_save():
    """It is an <a href> to the program page — everything was already persisted
    over HTMX — but the label read "Save Session ✓", which implies unsaved work
    and trains users to fear leaving the page."""
    tpl = (Path(__file__).parent.parent / "web" / "templates" / "partials"
           / "exercise_log_section.html").read_text(encoding="utf-8")
    assert "Save Session" not in tpl
    assert "Done — back to program" in tpl


# ── FE-M3: every HTMX action shows an in-flight state ────────────────────────

def test_theme_styles_the_htmx_request_state():
    # The minifier strips the quotes from attribute selectors and spaces from
    # declarations, so match on the normalised form.
    css = (Path(__file__).parent.parent / "web" / "static" / "tailwind.css").read_text(encoding="utf-8")
    assert "button.htmx-request" in css, "buttons need an in-flight style"
    assert "form.htmx-request button:not([type=button])" in css, \
        "form submits need one too — htmx marks the form, not the button, and a " \
        "button with no type attribute is still a submit button"
    assert "pointer-events:none" in css.replace(" ", ""), \
        "the in-flight state must block double-submits"
    assert "@keyframes oly-spin" in css


def test_every_htmx_trigger_gets_a_busy_state():
    """The in-flight CSS is selector-based rather than per-template, so this
    checks the selectors actually reach every element that fires a request."""
    tpl_dir = Path(__file__).parent.parent / "web" / "templates"
    uncovered = []
    for p in sorted(tpl_dir.rglob("*.html")):
        text = p.read_text(encoding="utf-8")
        for m in re.finditer(r"<(\w+)\b([^>]*\bhx-(?:post|get|delete|put|patch)=[^>]*)>", text, re.S):
            tag, attrs = m.group(1), m.group(2)
            if tag == "button":
                continue                                   # button.htmx-request
            if tag == "form":
                continue                                   # form.htmx-request button:not(...)
            # Anything else only gets a busy state if it polls (deliberately
            # excluded so #gen-status doesn't flicker every 3s).
            if "hx-trigger" in attrs and "every" in attrs:
                continue
            uncovered.append(f"{p.name}: <{tag}>")
    assert not uncovered, f"htmx triggers with no in-flight feedback: {uncovered}"


def test_form_submits_declare_their_type():
    """A <button> with no type is a submit button. The CSS handles that now, but
    being explicit keeps the intent readable next to type="button" siblings."""
    tpl_dir = Path(__file__).parent.parent / "web" / "templates"
    untyped = []
    for p in sorted(tpl_dir.rglob("*.html")):
        text = p.read_text(encoding="utf-8")
        for fm in re.finditer(r"<form\b[^>]*\bhx-\w+=[^>]*>(.*?)</form>", text, re.S):
            for bm in re.finditer(r"<button\b([^>]*)>", fm.group(1)):
                if "type=" not in bm.group(1):
                    untyped.append(p.name)
    assert not untyped, f"buttons in hx- forms with no explicit type: {untyped}"


def test_icon_only_buttons_opt_out_of_the_spinner():
    """A spinner pseudo-element inside a 24px round ✕ button just breaks its
    layout, so those dim instead."""
    tpl_dir = Path(__file__).parent.parent / "web" / "templates"
    for name in ("program_list.html", "partials/exercise_log_entry.html",
                 "partials/maxes_table.html"):
        text = (tpl_dir / name).read_text(encoding="utf-8")
        assert "icon-btn" in text, f"{name} has an icon-only HTMX button with no opt-out"


# ── FE-M2: no dead frontend assets ───────────────────────────────────────────

def test_no_unreferenced_partials():
    """exercise_logged_row.html outlived exercise_log_entry.html by months with
    nothing including it."""
    tpl_dir = Path(__file__).parent.parent / "web" / "templates"
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in tpl_dir.rglob("*.html"))
    py = "\n".join(p.read_text(encoding="utf-8")
                   for p in (Path(__file__).parent.parent / "web").rglob("*.py"))
    orphans = [p.name for p in (tpl_dir / "partials").glob("*.html")
               if f"partials/{p.name}" not in corpus and f"partials/{p.name}" not in py]
    assert not orphans, f"partials nothing references: {orphans}"


def test_no_alpine_leftovers():
    """Alpine.js is never loaded, so the [x-cloak] rule styled nothing."""
    base = (Path(__file__).parent.parent / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "x-cloak" not in base
    assert "alpine" not in base.lower()


# ── FE-H5: the adherence bar must not overflow its track ─────────────────────

def _adherence(prescribed, logged):
    from web.queries.dashboard import get_adherence
    with patch("web.async_db.async_fetch_one",
               new=AsyncMock(side_effect=[{"cnt": prescribed}, {"cnt": logged}])):
        return asyncio.run(get_adherence(MagicMock(), 1, 4))


def test_adherence_pct_normal():
    assert _adherence(16, 12)["pct"] == 75


def test_adherence_pct_clamped_at_100():
    """Logging more sessions than prescribed rendered style="width: 112%",
    and the track has no overflow-hidden, so the bar spilled out of it."""
    r = _adherence(16, 18)
    assert r["pct"] == 100, f"expected clamp to 100, got {r['pct']}"
    assert r["logged"] == 18, "the raw count must still be reported"


def test_adherence_pct_zero_prescribed():
    assert _adherence(0, 0)["pct"] == 0


def test_adherence_track_clips_overflow():
    tpl = (Path(__file__).parent.parent / "web" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert "rounded-full h-2 overflow-hidden" in tpl, \
        "the adherence track needs overflow-hidden as a second line of defence"


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
