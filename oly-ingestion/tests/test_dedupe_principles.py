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
