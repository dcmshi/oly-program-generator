# tests/test_macrocycle.py
"""
No-DB tests for macrocycles (PLAN-3e): the backward block layout, the re-flow
after a block completes, the display rows, plan()'s block override, the
orchestrator's block pick, and the web plumbing (form → job, completion →
next block, the timeline).
"""

import asyncio
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from macrocycle import (
    block_rows,
    macrocycle_weeks,
    plan_blocks,
    reflow,
    reflow_after_completion,
)

from shared.constants import (
    BLOCK_WEEKS_MAX_BY_LEVEL,
    BLOCK_WEEKS_MIN,
    MACROCYCLE_GENERAL_PREP_MIN_WEEKS,
    MACROCYCLE_WEEKS_DEFAULT,
    MACROCYCLE_WEEKS_MAX,
    MACROCYCLE_WEEKS_MIN,
)

_ORDER = ["general_prep", "accumulation", "intensification", "realization"]


# ── plan_blocks ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("level", ["beginner", "intermediate", "advanced", "elite"])
@pytest.mark.parametrize("weeks", [6, 7, 8, 9, 12, 13, 16, 20, 24, 52])
def test_layout_is_well_formed(level, weeks):
    blocks = plan_blocks(level, weeks, competition=True)
    assert sum(b["weeks"] for b in blocks) == weeks
    assert blocks[-1]["phase"] == "realization"
    assert all(BLOCK_WEEKS_MIN <= b["weeks"] <= BLOCK_WEEKS_MAX_BY_LEVEL[level] for b in blocks), blocks
    # phases never go backwards through the macrocycle
    idx = [_ORDER.index(b["phase"]) for b in blocks]
    assert idx == sorted(idx), blocks
    assert ("general_prep" == blocks[0]["phase"]) == (weeks >= MACROCYCLE_GENERAL_PREP_MIN_WEEKS)


def test_twelve_weeks_intermediate():
    assert [(b["phase"], b["weeks"]) for b in plan_blocks("intermediate", 12, competition=True)] == [
        ("accumulation", 5), ("intensification", 4), ("realization", 3)]


def test_final_block_note_depends_on_competition():
    assert plan_blocks("intermediate", 12, competition=True)[-1]["note"] == "peak for the meet"
    assert plan_blocks("intermediate", 12, competition=False)[-1]["note"] == "max-test block"


def test_short_plans_have_no_prep_block():
    assert [b["phase"] for b in plan_blocks("intermediate", 7, competition=True)] == ["intensification", "realization"]
    assert [b["phase"] for b in plan_blocks("intermediate", 4, competition=True)] == ["realization"]


def test_macrocycle_weeks():
    assert macrocycle_weeks(None, None) == MACROCYCLE_WEEKS_DEFAULT
    assert macrocycle_weeks(None, 2) == MACROCYCLE_WEEKS_MIN
    assert macrocycle_weeks(None, 99) == MACROCYCLE_WEEKS_MAX
    assert macrocycle_weeks(14, 30) == 14                  # the meet wins over the request
    assert macrocycle_weeks(80, None) == MACROCYCLE_WEEKS_MAX
    with pytest.raises(ValueError, match="too close"):
        macrocycle_weeks(MACROCYCLE_WEEKS_MIN - 1, None)


# ── reflow ───────────────────────────────────────────────────────────────────

_BLOCKS = [{"phase": "accumulation", "weeks": 5, "note": ""},
           {"phase": "intensification", "weeks": 4, "note": ""},
           {"phase": "realization", "weeks": 3, "note": "max-test block"}]
_ADVANCED = {"prev_phase": "accumulation", "next_phase": "intensification", "advanced": True, "reason": "ok"}
_HELD = {"prev_phase": "accumulation", "next_phase": "accumulation", "advanced": False,
         "reason": "Phase repeated — Adherence below threshold"}


def test_reflow_continues_when_advanced():
    blocks, msg = reflow(_BLOCKS, 0, _ADVANCED, "intermediate", None)
    assert blocks == _BLOCKS and msg == ""


def test_reflow_inserts_repeat_without_a_meet():
    blocks, msg = reflow(_BLOCKS, 0, _HELD, "intermediate", None)
    assert [b["phase"] for b in blocks] == ["accumulation", "accumulation", "intensification", "realization"]
    assert blocks[1]["weeks"] == 5 and blocks[1]["note"].startswith("repeat")
    assert "Accumulation repeated" in msg and "Adherence" in msg


def test_reflow_never_repeats_realization():
    verdict = {"prev_phase": "realization", "next_phase": "realization", "advanced": False}
    blocks, msg = reflow(_BLOCKS, 2, verdict, "intermediate", None)
    assert blocks == _BLOCKS and msg == ""


def test_reflow_with_meet_replans_the_tail_from_the_date():
    long_plan = plan_blocks("intermediate", 20, competition=True)
    blocks, msg = reflow(long_plan, 0, _ADVANCED, "intermediate", 11)   # finished a week late
    assert blocks[0] == long_plan[0]
    assert sum(b["weeks"] for b in blocks[1:]) == 11 and blocks[-1]["phase"] == "realization"
    assert msg == ""


def test_reflow_with_meet_hold_replaces_the_next_prep_block():
    long_plan = plan_blocks("intermediate", 20, competition=True)     # gp5 acc4 acc4 int4 real3
    held = {**_HELD, "prev_phase": "general_prep", "next_phase": "general_prep"}
    blocks, msg = reflow(long_plan, 0, held, "intermediate", 15)
    assert blocks[1]["phase"] == "general_prep" and blocks[1]["note"].startswith("repeat")
    assert blocks[-2:] == [b for b in blocks if b["phase"] in ("intensification", "realization")]
    assert "repeated" in msg


def test_reflow_with_meet_keeps_the_peak_over_a_hold():
    held = {**_HELD, "prev_phase": "accumulation", "next_phase": "accumulation"}
    blocks, msg = reflow(_BLOCKS, 0, held, "intermediate", 7)          # only int + real fit
    assert [b["phase"] for b in blocks[1:]] == ["intensification", "realization"]
    assert "keeps its peak" in msg


def test_reflow_with_meet_ends_after_the_peak_even_if_early():
    blocks, msg = reflow(_BLOCKS, 2, {"next_phase": "accumulation"}, "intermediate", 3)
    assert blocks == _BLOCKS and msg == ""


def test_block_rows_derive_status_from_programs():
    rows = block_rows(_BLOCKS, [{"id": 11, "status": "completed", "macrocycle_block_index": 0},
                                {"id": 12, "status": "draft", "macrocycle_block_index": 1}])
    assert [(r["status"], r["program_id"], r["start_week"]) for r in rows] == [
        ("completed", 11, 1), ("draft", 12, 6), ("planned", None, 10)]


# ── reflow_after_completion (DB mocked) ──────────────────────────────────────

def _outcome(verdict):
    o = MagicMock()
    o.phase_verdict = verdict
    return o


def test_reflow_after_completion_updates_blocks_and_status():
    prog = {"athlete_id": 1, "macrocycle_id": 5, "macrocycle_block_index": 0, "level": "intermediate"}
    mc = {"id": 5, "status": "active", "competition_date": None, "blocks": _BLOCKS}
    with patch("macrocycle.fetch_one", side_effect=[prog, mc]), patch("macrocycle.execute") as ex:
        info = reflow_after_completion(MagicMock(), 99, _outcome(_HELD))
    assert info == {"macrocycle_id": 5, "next_index": 1, "message": info["message"], "finished": False}
    assert "repeated" in info["message"]
    params = ex.call_args[0][2]
    assert '"accumulation"' in params[0] and params[1] == "active"


def test_reflow_after_completion_finishes_on_the_last_block():
    prog = {"athlete_id": 1, "macrocycle_id": 5, "macrocycle_block_index": 2, "level": "intermediate"}
    mc = {"id": 5, "status": "active", "competition_date": date(2026, 9, 25), "blocks": _BLOCKS}
    with patch("macrocycle.fetch_one", side_effect=[prog, mc]), patch("macrocycle.execute") as ex:
        info = reflow_after_completion(MagicMock(), 99, _outcome({}), today=date(2026, 9, 22))
    assert info["finished"] and ex.call_args[0][2][1] == "completed"


def test_reflow_after_completion_ignores_programs_outside_a_macrocycle():
    with patch("macrocycle.fetch_one", return_value={"athlete_id": 1, "macrocycle_id": None,
                                                     "macrocycle_block_index": None, "level": None}):
        assert reflow_after_completion(MagicMock(), 99, _outcome({})) is None
    prog = {"athlete_id": 1, "macrocycle_id": 5, "macrocycle_block_index": 0, "level": "intermediate"}
    with patch("macrocycle.fetch_one", side_effect=[prog, {"id": 5, "status": "abandoned"}]):
        assert reflow_after_completion(MagicMock(), 99, _outcome({})) is None


# ── plan(block=…) ────────────────────────────────────────────────────────────

def test_plan_block_override_skips_the_decision_tree():
    from plan import plan
    from tests.test_plan import _ctx, _FakeSettings
    ctx = _ctx(previous_program={"id": 1, "phase": "accumulation", "outcome_summary": {}}, weeks_to_competition=20)
    with patch("plan.fetch_all", return_value=[]):
        p = plan(ctx, None, _FakeSettings(), duration_weeks=3, block={"phase": "general_prep", "weeks": 5})
    assert p.phase == "general_prep" and p.duration_weeks == 5 and len(p.weekly_targets) == 5


# ── orchestrator._macrocycle_block ───────────────────────────────────────────

def _octx(weeks_to_competition=None):
    from tests.test_plan import _ctx
    return _ctx(weeks_to_competition=weeks_to_competition,
                active_goal={"goal": "competition_prep", "competition_date": date(2026, 12, 1)})


def test_new_macrocycle_is_stored_and_block_one_returned():
    import orchestrator
    conn = MagicMock()
    with patch("orchestrator.create_macrocycle", return_value=8) as create:
        mc_id, idx, block = orchestrator._macrocycle_block(conn, 1, _octx(10), None, 30, dry_run=False)
    assert (mc_id, idx) == (8, 0) and block["phase"] == "accumulation"
    blocks, comp_date = create.call_args[0][2], create.call_args[0][3]
    assert sum(b["weeks"] for b in blocks) == 10 and comp_date == date(2026, 12, 1)
    conn.commit.assert_called_once()


def test_new_macrocycle_dry_run_stores_nothing(capsys):
    import orchestrator
    with patch("orchestrator.create_macrocycle") as create:
        mc_id, _, block = orchestrator._macrocycle_block(MagicMock(), 1, _octx(), None, 12, dry_run=True)
    assert mc_id is None and block["phase"] == "accumulation" and not create.called
    assert "MACROCYCLE: 12 weeks" in capsys.readouterr().out


def test_existing_macrocycle_returns_the_next_block():
    import orchestrator
    mc = {"id": 5, "status": "active", "blocks": _BLOCKS}
    with patch("orchestrator.load_macrocycle", return_value=mc), \
         patch("orchestrator.next_block_index", return_value=1):
        assert orchestrator._macrocycle_block(MagicMock(), 1, _octx(), 5, None, False) == (5, 1, _BLOCKS[1])


def test_exhausted_macrocycle_is_marked_completed():
    import orchestrator
    mc = {"id": 5, "status": "active", "blocks": _BLOCKS}
    with patch("orchestrator.load_macrocycle", return_value=mc), \
         patch("orchestrator.next_block_index", return_value=3), \
         patch("orchestrator.execute") as ex:
        assert orchestrator._macrocycle_block(MagicMock(), 1, _octx(), 5, None, False) == (5, 3, None)
    assert "completed" in ex.call_args[0][1]


def test_foreign_or_inactive_macrocycle_raises():
    import orchestrator
    with patch("orchestrator.load_macrocycle", return_value=None), pytest.raises(ValueError):
        orchestrator._macrocycle_block(MagicMock(), 1, _octx(), 5, None, False)


# ── web ──────────────────────────────────────────────────────────────────────

def _client():
    from tests.test_web_routers import _client as client
    return client


def test_generate_run_passes_a_new_macrocycle():
    with patch("web.jobs.submit_generation", return_value="job1") as sub:
        r = _client().post("/generate/run", data={"macrocycle": "on", "macrocycle_weeks": "16"})
    assert r.status_code == 200
    assert sub.call_args.kwargs["macrocycle"] == {"new": True, "weeks": 16}


def test_generate_run_passes_the_next_block():
    with patch("web.jobs.submit_generation", return_value="job1") as sub:
        _client().post("/generate/run", data={"macrocycle_id": "5"})
    assert sub.call_args.kwargs["macrocycle"] == {"id": 5}


@pytest.mark.parametrize("data", [{"macrocycle": "on", "macrocycle_weeks": "3"},
                                  {"macrocycle": "on", "macrocycle_weeks": "x"},
                                  {"macrocycle_id": "abc"}])
def test_generate_run_rejects_bad_macrocycle_input(data):
    with patch("web.jobs.submit_generation") as sub:
        r = _client().post("/generate/run", data=data)
    assert r.status_code == 422 and not sub.called


def _view(can_generate=True):
    rows = block_rows(_BLOCKS, [{"id": 11, "status": "completed", "macrocycle_block_index": 0}])
    return {"id": 5, "status": "active", "competition_date": None, "total_weeks": 12, "rows": rows,
            "next": rows[1], "can_generate": can_generate}


def test_generate_page_shows_the_timeline_and_next_block():
    with patch("web.queries.program.get_all_programs", return_value=[]), \
         patch("web.queries.program.get_macrocycle_view", return_value=_view()), \
         patch("web.jobs.get_inflight_job_id", return_value=None):
        r = _client().get("/generate")
    assert r.status_code == 200
    assert "Macrocycle · 12 weeks" in r.text and 'name="macrocycle_id" value="5"' in r.text
    assert "Generate block 2" in r.text and 'href="/program/11"' in r.text


def test_generate_page_waits_for_the_current_block():
    with patch("web.queries.program.get_all_programs", return_value=[]), \
         patch("web.queries.program.get_macrocycle_view", return_value=_view(can_generate=False)), \
         patch("web.jobs.get_inflight_job_id", return_value=None):
        r = _client().get("/generate")
    assert "Generate block 2" not in r.text and "when the current block is completed" in r.text


def test_program_page_shows_the_timeline_for_a_block():
    from tests.test_web_routers import _program
    with patch("web.queries.program.get_program", return_value=_program(macrocycle_id=5, id=11)), \
         patch("web.queries.program.get_program_weeks", return_value=[]), \
         patch("web.queries.program.get_program_volume_by_week", return_value=[]), \
         patch("web.queries.program.get_macrocycle_view", return_value=_view()) as view:
        r = _client().get("/program/11")
    assert r.status_code == 200 and "Macrocycle · 12 weeks" in r.text and "border-2 border-navy" in r.text
    assert view.call_args[0][1:] == (1, 5)


def test_completing_a_block_reflows_and_queues_the_next():
    from tests.test_web_routers import _outcome, _program
    info = {"macrocycle_id": 5, "next_index": 1, "message": "Accumulation repeated: x.", "finished": False}
    with patch("web.queries.program.get_program", return_value=_program(status="active", macrocycle_id=5)), \
         patch("web.queries.program.complete_program", return_value=_outcome()), \
         patch("web.queries.program.advance_macrocycle", return_value=info), \
         patch("web.jobs.submit_generation", return_value="job2") as sub:
        r = _client().post("/program/1/complete")
    assert r.status_code == 200
    assert sub.call_args.kwargs["macrocycle"] == {"id": 5}
    assert "Accumulation repeated" in r.text and "next macrocycle block is being generated" in r.text


def test_completing_the_last_block_queues_nothing():
    from tests.test_web_routers import _outcome, _program
    info = {"macrocycle_id": 5, "next_index": 3, "message": "", "finished": True}
    with patch("web.queries.program.get_program", return_value=_program(status="active", macrocycle_id=5)), \
         patch("web.queries.program.complete_program", return_value=_outcome()), \
         patch("web.queries.program.advance_macrocycle", return_value=info), \
         patch("web.jobs.submit_generation") as sub:
        r = _client().post("/program/1/complete")
    assert not sub.called and "Macrocycle complete" in r.text


def test_completion_survives_a_reflow_or_queue_failure():
    from tests.test_web_routers import _outcome, _program
    from web import jobs
    with patch("web.queries.program.get_program", return_value=_program(status="active", macrocycle_id=5)), \
         patch("web.queries.program.complete_program", return_value=_outcome()), \
         patch("web.queries.program.advance_macrocycle", side_effect=RuntimeError("db down")):
        r = _client().post("/program/1/complete")
    assert r.status_code == 200 and "could not be updated" in r.text
    info = {"macrocycle_id": 5, "next_index": 1, "message": "", "finished": False}
    with patch("web.queries.program.get_program", return_value=_program(status="active", macrocycle_id=5)), \
         patch("web.queries.program.complete_program", return_value=_outcome()), \
         patch("web.queries.program.advance_macrocycle", return_value=info), \
         patch("web.jobs.submit_generation", side_effect=jobs.GenerationInFlightError("busy")):
        r = _client().post("/program/1/complete")
    assert r.status_code == 200 and "Another generation is running" in r.text


def test_get_macrocycle_view_gates_the_next_block():
    from web.queries import program as qp
    mc = {"id": 5, "status": "active", "competition_date": None, "start_date": date(2026, 9, 1), "blocks": _BLOCKS}

    def run(programs):
        with patch("web.async_db.async_fetch_one", return_value=mc), \
             patch("web.async_db.async_fetch_all", return_value=programs):
            return asyncio.run(qp.get_macrocycle_view(MagicMock(), 1))

    v = run([{"id": 11, "status": "active", "macrocycle_block_index": 0}])
    assert v["next"]["index"] == 1 and not v["can_generate"] and v["total_weeks"] == 12
    v = run([{"id": 11, "status": "completed", "macrocycle_block_index": 0}])
    assert v["can_generate"]
    with patch("web.async_db.async_fetch_one", return_value=None):
        assert asyncio.run(qp.get_macrocycle_view(MagicMock(), 1)) is None
