# oly-agent/tests/test_eval_harness.py
"""
No-key tests for the retrieval eval harness (RAG-M6): metrics, the
production-shaped query set, golden-file validation, baseline comparison,
grading-response parsing, and — under INTEGRATION_TESTS=1 with a golden set — the
live regression gate.

Run: PYTHONUTF8=1 uv run pytest tests/test_eval_harness.py -q
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.build_golden import build_grading_prompt, parse_grades
from eval.metrics import max_source_share, mrr, ndcg_at_k, recall_at_k, score_query, summarize
from eval.queries import all_queries, legacy_queries, production_queries
from eval.run_eval import GOLDEN_PATH, compare_to_baseline, load_golden

# ── metrics ──────────────────────────────────────────────────────────────────

def test_recall_mrr_ndcg_basics():
    assert recall_at_k([1, 2, 3], {2, 9}, 3) == 0.5
    assert recall_at_k([1, 2, 3], set(), 3) is None
    assert mrr([5, 2, 1], {1}) == 1 / 3 and mrr([5], {1}) == 0.0
    assert ndcg_at_k([1, 2], {1: 2, 2: 1}, 2) == 1.0            # ideal order
    worse = ndcg_at_k([2, 1], {1: 2, 2: 1}, 2)
    assert 0 < worse < 1.0
    assert ndcg_at_k([1], {1: 0}, 5) is None                   # nothing relevant → undefined


def test_max_source_share_and_summary():
    res = [{"id": 1, "source_id": 7}, {"id": 2, "source_id": 7}, {"id": 3, "source_id": 8}]
    assert abs(max_source_share(res) - 2 / 3) < 1e-9
    q = score_query(res, {1: 2, 3: 1, 4: 2}, k=3)
    assert q["recall_at_k"] == 2 / 3 and q["hit_at_k"] == 1.0 and q["mrr"] == 1.0 and q["n_relevant"] == 3
    s = summarize([q, {**q, "recall_at_k": None}])
    assert s["n_queries"] == 2 and s["recall_at_k_n"] == 1 and s["recall_at_k"] == round(2 / 3, 4)


# ── query set ─────────────────────────────────────────────────────────────────

def test_production_queries_cover_every_ui_vocabulary_value():
    from phase_profiles import PHASE_PROFILES
    from web.options import FAULT_OPTIONS, STRENGTH_LIMITER_OPTIONS

    qs = production_queries()
    ids = {q["id"] for q in qs}
    for _label, fault in FAULT_OPTIONS:
        assert f"fault:{fault}" in ids
    for _label, lim in STRENGTH_LIMITER_OPTIONS:
        assert f"limiter:{lim}" in ids
    for phase in PHASE_PROFILES:
        assert any(i.startswith(f"session:{phase}:") for i in ids), phase
    assert all(q["preferred_chunk_types"] for q in qs), "every production query carries its preference"


def test_query_ids_unique_and_legacy_included():
    qs = all_queries()
    assert len({q["id"] for q in qs}) == len(qs)
    assert len(legacy_queries()) == 22
    assert sum(q["kind"] == "legacy" for q in qs) == 22


def test_session_queries_match_production_builder_output():
    """Parity: the harness must send exactly what retrieve.py sends."""
    from retrieve import build_fault_query

    qs = {q["id"]: q for q in production_queries()}
    assert qs["fault:early_arm_bend"]["query"] == build_fault_query("early_arm_bend", "intermediate")
    sess = qs["session:accumulation:d1:snatch"]["query"]
    assert "snatch session" in sess and "accumulation phase" in sess and "intermediate athlete" in sess


# ── golden file + baseline gate ───────────────────────────────────────────────

def test_load_golden_validates_and_coerces(tmp_path):
    good = tmp_path / "g.json"
    good.write_text(json.dumps({"meta": {}, "queries": [{"id": "a", "query": "q", "grades": {"12": "2", "13": 0}}]}))
    g = load_golden(good)
    assert g["queries"][0]["grades"] == {12: 2, 13: 0}
    bad = tmp_path / "b.json"
    bad.write_text(json.dumps({"queries": [{"id": "a", "query": "q"}]}))
    try:
        load_golden(bad)
        raise AssertionError("missing grades must be rejected")
    except ValueError:
        pass


def test_compare_to_baseline_flags_only_real_regressions():
    base = {"recall_at_k": 0.70, "mrr": 0.60, "ndcg_at_k": 0.65}
    assert compare_to_baseline({"recall_at_k": 0.69, "mrr": 0.60, "ndcg_at_k": 0.70}, base) == []   # within tolerance / better
    flagged = compare_to_baseline({"recall_at_k": 0.60, "mrr": 0.60, "ndcg_at_k": 0.65}, base)
    assert flagged and flagged[0].startswith("recall_at_k")
    assert compare_to_baseline({"recall_at_k": 0.1}, None) == []


def test_parse_grades_and_prompt():
    prompt = build_grading_prompt("correcting early arm bend", [{"id": 1, "raw_content": "x" * 2000}, {"id": 2, "raw_content": "y"}])
    assert "[id 1]" in prompt and "[id 2]" in prompt and "x" * 1201 not in prompt
    raw = '```json\n[{"id": 1, "grade": 2}, {"id": 2, "grade": 3}, {"id": 9, "grade": 1}, "junk"]\n```'
    assert parse_grades(raw, {1, 2}) == {1: 2}
    # "Extra data" replies (prose or a second array after the JSON) are salvaged, not raised
    messy = 'Here are the grades:\n[{"id": 1, "grade": 2},\n {"id": 2, "grade": 0}]\nNote: id 9 was off-topic.\n[{"id": 9, "grade": 0}]'
    assert parse_grades(messy, {1, 2, 9}) == {1: 2, 2: 0, 9: 0}
    assert parse_grades("no json here", {1}) == {}
    # the STRUCT-1 schema reply
    assert parse_grades('{"grades": [{"id": 1, "grade": 2}, {"id": 2, "grade": 0}]}', {1, 2}) == {1: 2, 2: 0}


def test_grade_candidates_skips_a_bad_batch_and_keeps_the_rest():
    from unittest.mock import MagicMock

    from eval.build_golden import GRADE_BATCH, grade_candidates

    good = MagicMock()
    good.content = [MagicMock(text='[{"id": 1, "grade": 2}]')]
    bad = MagicMock()
    bad.content = [MagicMock(text="I cannot grade these.")]
    client = MagicMock()
    # batch 1: two unusable replies → skipped; batch 2: graded first time
    client.messages.create.side_effect = [bad, bad, good]
    pool = [{"id": 100 + k, "raw_content": "c"} for k in range(GRADE_BATCH)] + [{"id": 1, "raw_content": "c"}]
    out = grade_candidates(client, "claude-haiku-4-5-20251001", "q", pool)
    assert out == {1: 2} and client.messages.create.call_count == 3
    assert "ONLY the JSON array" in client.messages.create.call_args_list[1].kwargs["messages"][0]["content"]


# ── live gate (needs corpus DB + OPENAI_API_KEY + golden.json) ───────────────

def test_live_eval_does_not_regress_baseline():
    if os.getenv("INTEGRATION_TESTS", "").lower() not in ("1", "true"):
        import pytest
        pytest.skip("set INTEGRATION_TESTS=1 (needs corpus DB, OPENAI_API_KEY, eval/golden.json)")
    if not GOLDEN_PATH.exists():
        import pytest
        pytest.skip("eval/golden.json not built yet — run: uv run python -m eval.build_golden")
    from eval.run_eval import main
    assert main([]) == 0
