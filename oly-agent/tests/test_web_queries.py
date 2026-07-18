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

def _fake_job(args, status=None):
    from arq.jobs import JobStatus
    job = MagicMock()
    job.info = AsyncMock(return_value=MagicMock(args=args) if args is not None else None)
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

    def fake_run(athlete_id, settings, dry_run=False, deadline=None):
        captured["deadline"] = deadline
        return 42

    with patch("orchestrator.run", side_effect=fake_run):
        result = asyncio.run(worker.run_generation({}, 1))
    assert result["program_id"] == 42
    assert captured.get("deadline") is not None, \
        "worker must pass a monotonic deadline (WEB-M8 — thread outlives job_timeout)"


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
    assert pool.set.await_args.kwargs.get("nx") is True, "guard must be SET NX"
    assert pool.set.await_args.kwargs.get("ex"), "guard must expire (stuck-job safety)"


def test_job_status_terminal_clears_inflight():
    from arq.jobs import JobStatus
    job = _fake_job(args=[7], status=JobStatus.complete)
    job.result = AsyncMock(return_value={"program_id": 42, "duration_seconds": 1.0})
    pool = MagicMock()
    pool.delete = AsyncMock()
    with patch.object(jobs, "_arq_pool", pool), patch("arq.jobs.Job", return_value=job):
        result = asyncio.run(jobs.get_job_status("jid", 7))
    assert result["status"] == "done"
    pool.delete.assert_awaited_with("gen_inflight:7")


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


# ── W-INFO: prefillExercise uses data-* attributes ───────────────────────────

def test_prefill_uses_data_attributes_not_js_string():
    tpl = (Path(__file__).parent.parent / "web" / "templates" / "partials"
           / "exercise_log_section.html").read_text(encoding="utf-8")
    assert "onclick=\"prefillExercise(this)\"" in tpl
    assert "data-name=" in tpl
    # the old JS-string interpolation form must be gone
    assert "prefillExercise('" not in tpl


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
