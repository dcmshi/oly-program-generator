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
from eval.metrics import (
    max_source_share,
    mrr,
    ndcg_at_k,
    recall_at_k,
    recall_at_k_normalized,
    score_query,
    summarize,
)
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


def test_recall_at_k_normalized_is_not_capped_by_the_relevant_count():
    """AUD-3: 16 relevant chunks and k=5 cap plain recall at 5/16; the normalised
    form divides by min(k, relevant), so a perfect top 5 scores 1.0."""
    relevant = set(range(100, 116))                                 # 16 relevant
    top5 = [100, 101, 102, 103, 104]
    assert recall_at_k(top5, relevant, 5) == 5 / 16
    assert recall_at_k_normalized(top5, relevant, 5) == 1.0
    assert recall_at_k_normalized([100, 1, 2, 101, 3], relevant, 5) == 2 / 5
    # fewer relevant than k: the denominator is the relevant count
    assert recall_at_k_normalized([1, 7, 2, 3, 4], {7, 8}, 5) == 0.5
    assert recall_at_k_normalized([7, 8], {7, 8}, 5) == 1.0
    assert recall_at_k_normalized([1, 2], set(), 5) is None
    assert recall_at_k_normalized([1, 2], {1}, 0) is None


def test_score_query_normalised_recall_grade2_and_grade1():
    res = [{"id": i, "source_id": 1} for i in (1, 2, 3, 4, 5)]
    grades = {1: 2, 2: 1, 3: 0, 4: 1, 9: 2, 10: 2, 11: 1}              # grade 2: 1, 9, 10; grade>=1: 6 ids
    q = score_query(res, grades, k=5)
    assert q["nrecall_at_k"] == 1 / 3                                 # 1 of min(5, 3) grade-2
    assert q["nrecall_g1_at_k"] == 3 / 5                              # 1, 2, 4 of min(5, 6)
    assert q["n_relevant_g2"] == 3 and q["n_relevant"] == 6
    only_partial = score_query(res, {2: 1}, k=5)
    assert only_partial["nrecall_at_k"] is None and only_partial["nrecall_g1_at_k"] == 1.0
    s = summarize([q, only_partial])
    assert s["nrecall_at_k_n"] == 1 and s["nrecall_at_k"] == round(1 / 3, 4)
    assert s["nrecall_g1_at_k_n"] == 2 and s["nrecall_g1_at_k"] == round((3 / 5 + 1.0) / 2, 4)


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
    base = {"recall_at_k": 0.70, "mrr": 0.60, "ndcg_at_k": 0.65, "nrecall_at_k": 0.50}
    assert compare_to_baseline({"ndcg_at_k": 0.64, "nrecall_at_k": 0.52}, base) == []   # within tolerance / better
    flagged = compare_to_baseline({"ndcg_at_k": 0.65, "nrecall_at_k": 0.45}, base)
    assert len(flagged) == 1 and flagged[0].startswith("nrecall_at_k")
    assert compare_to_baseline({"ndcg_at_k": 0.60, "nrecall_at_k": 0.50}, base)[0].startswith("ndcg_at_k")
    assert compare_to_baseline({"recall_at_k": 0.1}, None) == []


def test_gate_ignores_saturated_metrics_and_old_baselines():
    """AUD-3: recall@5 and MRR are informational — a drop there never fails the
    run — and a baseline frozen before nrecall existed is not failed on it."""
    from eval.run_eval import GATED_METRICS, INFORMATIONAL_METRICS

    assert set(GATED_METRICS) == {"ndcg_at_k", "nrecall_at_k"}
    assert {"recall_at_k", "mrr"} <= set(INFORMATIONAL_METRICS)
    base = {"recall_at_k": 0.30, "mrr": 0.98, "ndcg_at_k": 0.70, "nrecall_at_k": 0.60}
    assert compare_to_baseline({"recall_at_k": 0.10, "mrr": 0.50, "ndcg_at_k": 0.70, "nrecall_at_k": 0.60}, base) == []
    old = {"recall_at_k": 0.206, "mrr": 0.930, "ndcg_at_k": 0.716}          # pre-AUD-3 baseline.json
    assert compare_to_baseline({"ndcg_at_k": 0.72, "nrecall_at_k": 0.1}, old) == []
    assert compare_to_baseline({"ndcg_at_k": 0.60, "nrecall_at_k": 0.1}, old)[0].startswith("ndcg_at_k")


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


def test_build_golden_extend_grades_only_new_candidates(tmp_path):
    """--extend keeps the prior grades and grades only the pool members they
    lack — the union pool for comparing two retrievers on the same labels."""
    from unittest.mock import MagicMock, patch

    from eval import build_golden

    q = {"id": "q1", "kind": "legacy", "query": "snatch pulls", "preferred_chunk_types": []}
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"meta": {}, "queries": [{**q, "grades": {"1": 2}, "snippets": {"1": "old"}}]}))
    out = tmp_path / "union.json"
    loader = MagicMock()
    loader.similarity_search.return_value = [{"id": 1, "raw_content": "a"}, {"id": 2, "raw_content": "b"}]
    graded_ids = []

    def fake_grade(client, model, query, candidates):
        graded_ids.extend(c["id"] for c in candidates)
        return {c["id"]: 1 for c in candidates}

    with patch("eval.queries.all_queries", return_value=[q]), \
         patch("loaders.vector_loader.VectorLoader", return_value=loader), \
         patch.object(build_golden, "create_llm_client", return_value=MagicMock()), \
         patch.object(build_golden, "grade_candidates", side_effect=fake_grade):
        assert build_golden.main(["--extend", str(base), "--out", str(out), "--model", "m"]) == 0
    assert graded_ids == [2]                                        # 1 was already graded
    row = json.loads(out.read_text())["queries"][0]
    assert row["grades"] == {"1": 2, "2": 1} and row["snippets"]["1"] == "a"
    assert json.loads(out.read_text())["meta"]["extends"] == str(base)


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


def test_program_diff_renders_sessions_and_summary():
    """program_diff aligns sessions across programs, marks warm-ups, and the
    summary's overlap fields are relative to the first program."""
    from collections import defaultdict

    from eval.program_diff import render, summarise

    def row(name, order, sets, reps, pct, rpe=7.5):
        return {"exercise_name": name, "exercise_order": order, "sets": sets, "reps": reps, "intensity_pct": pct, "rpe_target": rpe}

    a = {"name": "A", "focus": {(1, 1): "Snatch + Squat"}, "sessions": defaultdict(list, {
        (1, 1): [row("Snatch", 1, 2, 3, 55.0, 6.0), row("Snatch", 2, 6, 3, 75.0), row("Back Squat", 3, 4, 5, 75.0)],
        (1, 2): [row("Clean & Jerk", 1, 5, 2, 75.0)],
    })}
    b = {"name": "B", "focus": {}, "sessions": defaultdict(list, {
        (1, 1): [row("Snatch", 1, 6, 3, 72.0), row("Overhead Squat", 2, 3, 3, 66.0)],
        (1, 2): [row("Clean & Jerk", 1, 5, 2, 78.0), row("Snatch Pull", 2, 4, 3, 90.0)],
    })}
    programs = {12: a, 20: b}
    text = render(programs)
    assert "### Week 1 Day 1 — Snatch + Squat" in text and "### Week 1 Day 2" in text
    assert "| 1 | w Snatch 2×3 @55% | Snatch 6×3 @72% |" in text        # warm-up marked, columns aligned
    assert "| 3 | Back Squat 4×5 @75% | — |" in text                       # shorter session padded
    assert "**20** adds: Overhead Squat, Snatch Pull" in text and "**20** drops: Back Squat" in text
    assert render(programs, week=2).count("### Week") == 0

    s = summarise(programs)
    assert s[0]["program"] == 12 and s[0]["warmup_rows"] == 1 and s[0]["distinct_exercises"] == 3
    assert s[1]["shared_with_first"] == 2 and s[1]["unique_vs_first"] == ["Overhead Squat", "Snatch Pull"]
    assert s[1]["session_jaccard_vs_first"] == round((1 / 3 + 1 / 2) / 2, 2)
    assert s[0]["mean_working_pct"] == 75.0 and s[1]["mean_working_pct"] == 76.5


# ── composed-context diversity (AUD-3) ────────────────────────────────────────

def _rs(*entries):
    return [{"label": f"C{i}", "id": cid, "chunk_type": "fault_correction" if cid < 10 else "concept",
             "source_id": src, "similarity": 0.6, "score": 0.03, "session_query": "q"}
            for i, (cid, src) in enumerate(entries, 1)]


def test_context_diversity_report_counts_slots_sources_and_repeats():
    from eval.context_diversity import diversity_report

    # 4 sessions x 3 slots; chunks 1 and 2 (source 507) lead every session
    sessions = [
        _rs((1, 507), (2, 507), (11, 507)),
        _rs((1, 507), (2, 507), (12, 600)),
        _rs((1, 507), (2, 507), (11, 507)),
        _rs((1, 507), (2, 507), (13, 700)),
    ]
    rep = diversity_report(sessions)
    assert rep["sessions"] == 4 and rep["slots"] == 12
    assert rep["distinct_chunks"] == 5 and rep["distinct_ratio"] == round(5 / 12, 3)
    assert rep["max_source_id"] == 507 and rep["max_source_slots"] == 10
    assert rep["max_source_share"] == round(10 / 12, 3) and rep["sources"] == 3
    repeated = {c["id"]: c for c in rep["repeated_chunks"]}
    assert set(repeated) == {1, 2, 11}                               # 11 is in exactly 50 %
    assert repeated[1]["sessions"] == 4 and repeated[1]["labels"] == {"C1": 4}
    assert repeated[11]["session_share"] == 0.5 and repeated[11]["source_id"] == 507
    assert [c["id"] for c in diversity_report(sessions, repeat_fraction=0.75)["repeated_chunks"]] == [1, 2]


def test_context_diversity_counts_a_chunk_once_per_session_and_handles_empty():
    from eval.context_diversity import diversity_report, format_report

    dup = diversity_report([_rs((1, 5), (1, 5)), _rs((2, 5))])
    assert dup["slots"] == 3 and dup["distinct_chunks"] == 2
    assert {c["id"]: c["sessions"] for c in dup["repeated_chunks"]} == {1: 1, 2: 1}
    empty = diversity_report([[], None])
    assert empty["slots"] == 0 and empty["distinct_ratio"] is None and empty["repeated_chunks"] == []
    assert "no logged retrieval sets" in format_report(99, empty)
    assert "3 slots, 2 distinct chunks" in format_report(1, dup)
