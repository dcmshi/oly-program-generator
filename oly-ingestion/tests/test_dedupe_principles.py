# tests/test_dedupe_principles.py
"""No-key tests for dedupe_principles.py (PRIN-DEDUPE): the cosine prefilter,
the union-find canonical mapping, and the plan.py consumer's filter."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dedupe_principles import candidate_pairs, canonical_map, pair_state, principle_text


def test_candidate_pairs_same_category_above_cosine():
    import numpy as np

    rows = [{"category": "deload"}, {"category": "deload"}, {"category": "volume"}, {"category": "deload"}]
    v = np.array([[1, 0], [0.99, 0.14], [0.99, 0.14], [0, 1]], dtype=np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    pairs = candidate_pairs(rows, v, 0.9)
    assert [(i, j) for i, j, _c in pairs] == [(0, 1)]        # (0,2) crosses categories, (0,3) is orthogonal
    assert pairs[0][2] > 0.98


def test_canonical_map_collapses_chains_to_lowest_id():
    mapping = canonical_map([10, 11, 12, 13, 20], [(11, 12), (12, 13), (20, 10)])
    assert mapping == {12: 11, 13: 11, 20: 10}       # two groups; canonical = lowest id in each
    assert canonical_map([1, 2], []) == {}


def test_pair_state_and_text_are_bounded():
    row = {"principle_name": "Deload every fourth week", "condition": {"phase": "deload"},
           "recommendation": {"volume_modifier": 0.6}, "rationale": "x" * 1000}
    st = pair_state(row, row)
    assert set(st) == {"principle_A", "principle_B"} and len(st["principle_A"]["rationale"]) == 400
    assert principle_text(row).startswith("Deload every fourth week — ") and len(principle_text(row)) < 450


def test_plan_skips_marked_duplicates():
    import inspect
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "oly-agent"))
    import plan
    assert "duplicate_of IS NULL" in inspect.getsource(plan._load_principles)


# ── Jev score cache ──────────────────────────────────────────────────────────

def _states():
    a = {"principle_name": "Deload every fourth week", "condition": {}, "recommendation": {"volume_modifier": 0.6},
         "rationale": "r1"}
    b = {**a, "principle_name": "Fourth-week deload"}
    c = {**a, "principle_name": "Taper 10 days out"}
    return [pair_state(a, b), pair_state(a, c)]


def test_score_cache_reuses_scores_and_rejudges_only_new_or_edited_pairs(tmp_path):
    from dedupe_principles import ScoreCache, judge_with_cache

    calls = []

    def fake_judge(states, on_result=None):
        calls.append(len(states))
        out = {}
        for i, _s in enumerate(states):
            out[i] = 0.9 - 0.1 * i
            on_result(i, out[i])
        return out

    path = tmp_path / "scores.jsonl"
    cache = ScoreCache(path)
    first = judge_with_cache(_states(), cache, judge=fake_judge)
    cache.close()
    assert first == {0: 0.9, 1: 0.8} and calls == [2]

    # a fresh process: both pairs from the file, no judge call
    cache = ScoreCache(path)
    assert judge_with_cache(_states(), cache, judge=fake_judge) == first and calls == [2]

    # an edited rule (new rationale) changes that pair's key — only it is judged again
    edited = _states()
    edited[1]["principle_B"]["rationale"] = "rewritten by a re-ingest"
    probs = judge_with_cache(edited, cache, judge=fake_judge)
    cache.close()
    assert calls == [2, 1] and probs == {0: 0.9, 1: 0.9}


def test_score_cache_skips_a_torn_line_and_keeps_scores_of_a_failed_run(tmp_path):
    from dedupe_principles import ScoreCache, judge_with_cache, pair_key

    path = tmp_path / "scores.jsonl"
    key = pair_key(_states()[0])
    path.write_text(f'{{"key": "{key}", "p": 0.75}}\n{{"key": "torn', encoding="utf-8")
    cache = ScoreCache(path)
    assert key in cache and cache[key] == 0.75 and len(cache.scores) == 1

    def dies_after_one(states, on_result=None):
        on_result(0, 0.4)
        raise RuntimeError("network")

    try:
        judge_with_cache(list(reversed(_states())), cache, judge=dies_after_one)
    except RuntimeError:
        pass
    cache.close()
    assert len(ScoreCache(path).scores) == 2          # the score that landed before the failure is kept
