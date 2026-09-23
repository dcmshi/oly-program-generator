# tests/test_eval_cli.py
"""
No-DB, no-key tests for the eval CLIs' entry points: eval/run_eval.main (the
retrieval regression gate — exit codes, baseline write, dense-only ablation)
and eval/context_diversity (load_sessions, format_report, main). The metric
and gate logic itself is in test_eval_harness.py.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from eval import context_diversity, run_eval

GOLDEN = {"meta": {"model": "judge"}, "queries": [
    {"id": "q1", "kind": "session", "query": "snatch pulls", "preferred_chunk_types": ["methodology"],
     "grades": {"1": 2, "2": 1}},
    {"id": "q2", "kind": "fault", "query": "early arm bend", "grades": {"3": 2}},
]}


def _loader(results_by_query):
    loader = MagicMock()
    loader.similarity_search.side_effect = lambda **kw: results_by_query[kw["query"]]
    return loader


def _run(tmp_path, argv, results, baseline=None):
    golden = tmp_path / "golden.json"
    golden.write_text(json.dumps(GOLDEN), encoding="utf-8")
    base = tmp_path / "baseline.json"
    if baseline is not None:
        base.write_text(json.dumps(baseline), encoding="utf-8")
    loader = _loader(results)
    with patch("loaders.vector_loader.VectorLoader", return_value=loader), patch("shared.config.Settings"):
        code = run_eval.main(["--golden", str(golden), "--baseline", str(base), *argv])
    return code, loader, base


PERFECT = {"snatch pulls": [{"id": 1, "source_id": 9}, {"id": 2, "source_id": 9}],
           "early arm bend": [{"id": 3, "source_id": 8}]}
POOR = {"snatch pulls": [{"id": 50, "source_id": 9}, {"id": 51, "source_id": 9}],
        "early arm bend": [{"id": 60, "source_id": 8}]}


def test_run_eval_without_a_golden_set_exits_2(tmp_path, capsys):
    assert run_eval.main(["--golden", str(tmp_path / "missing.json")]) == 2
    assert "No golden set" in capsys.readouterr().out


def test_run_eval_passes_without_baseline_and_writes_one(tmp_path, capsys):
    code, loader, base = _run(tmp_path, [], PERFECT)
    assert code == 0 and "no baseline.json" in capsys.readouterr().out
    loader.close.assert_called_once()
    code, _, base = _run(tmp_path, ["--update-baseline"], PERFECT)
    written = json.loads(base.read_text(encoding="utf-8"))
    from shared.constants import HYBRID_SEARCH_ENABLED
    assert code == 0 and written["gate"]["hybrid"] is HYBRID_SEARCH_ENABLED and written["golden_meta"] == {"model": "judge"}
    assert set(written["by_kind"]) == {"session", "fault"} and written["ndcg_at_k"] == 1.0


def test_run_eval_fails_on_a_gated_regression(tmp_path, capsys):
    baseline = {"ndcg_at_k": 1.0, "nrecall_at_k": 1.0, "recall_at_k": 1.0, "mrr": 1.0}
    code, _, _ = _run(tmp_path, [], POOR, baseline=baseline)
    assert code == 1 and "REGRESSION" in capsys.readouterr().out
    code, _, _ = _run(tmp_path, [], PERFECT, baseline=baseline)
    assert code == 0


def test_run_eval_dense_only_turns_the_lexical_leg_off(tmp_path):
    _, loader, _ = _run(tmp_path, ["--dense-only", "--top-k", "3"], PERFECT)
    kwargs = loader.similarity_search.call_args_list[0].kwargs
    assert kwargs["hybrid"] is False and kwargs["top_k"] == 3
    assert kwargs["preferred_chunk_types"] == ["methodology"]


def test_run_eval_notes_a_baseline_without_gated_metrics(tmp_path, capsys):
    code, _, _ = _run(tmp_path, [], PERFECT, baseline={"recall_at_k": 0.2, "mrr": 0.9})
    out = capsys.readouterr().out
    assert code == 0 and "not gated; re-freeze with --update-baseline" in out


# ── context_diversity ─────────────────────────────────────────────

def _slot(cid, source, label="C1"):
    return {"label": label, "id": cid, "chunk_type": "concept", "source_id": source}


def test_load_sessions_decodes_json_and_empty_sets_read_only():
    conn = MagicMock()
    rows = [{"retrieval_set": json.dumps([_slot(1, 9)])}, {"retrieval_set": [_slot(2, 8)]}, {"retrieval_set": None}]
    with patch("shared.db.fetch_all", return_value=rows):
        sessions = context_diversity.load_sessions(conn, 29)
    assert sessions == [[_slot(1, 9)], [_slot(2, 8)], []]
    conn.cursor.return_value.__enter__.return_value.execute.assert_called_once_with("SET TRANSACTION READ ONLY")


def test_format_report_lists_repeated_chunks_and_handles_no_data():
    sessions = [[_slot(1, 9, "C1"), _slot(2, 9, "C2")], [_slot(1, 9, "C1"), _slot(3, 8, "C2")]]
    rep = context_diversity.diversity_report(sessions)
    text = context_diversity.format_report(29, rep)
    assert text.startswith("program 29: 2 sessions, 4 slots, 3 distinct chunks")
    assert "chunk      1" in text and "in 2/2 sessions" in text
    empty = context_diversity.format_report(12, context_diversity.diversity_report([[], []]))
    assert empty == "program 12: no logged retrieval sets (2 sessions)"


def test_context_diversity_main_text_and_json(capsys):
    conn = MagicMock()
    sessions = [[_slot(1, 9)], [_slot(1, 9)]]
    with patch("shared.db.get_connection", return_value=conn), patch("shared.config.Settings"), \
         patch.object(context_diversity, "load_sessions", return_value=sessions):
        assert context_diversity.main(["29", "12"]) == 0
        text = capsys.readouterr().out
        assert context_diversity.main(["29", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
    assert "program 29:" in text and "program 12:" in text
    assert data["29"]["distinct_chunks"] == 1 and data["29"]["slots"] == 2
    conn.close.assert_called()


def test_run_eval_hybrid_flag_forces_the_lexical_leg_on(tmp_path):
    _, loader, _ = _run(tmp_path, ["--hybrid"], PERFECT)
    assert loader.similarity_search.call_args_list[0].kwargs["hybrid"] is True
