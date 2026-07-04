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
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import web.jobs as jobs
from web.queries.log_session import _parse_log_date
from web.queries.program import _representative_reps_per_set

from shared.constants import MAX_LOG_BACKFILL_DAYS

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
