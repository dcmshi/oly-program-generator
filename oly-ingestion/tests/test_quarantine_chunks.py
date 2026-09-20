# tests/test_quarantine_chunks.py
"""No-key tests for quarantine_chunks.py (JEV-1a): the plan against stored
state, the write path, and the retrieval filter that consumes the flag."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from quarantine_chunks import REASON, plan


def test_plan_matches_table_to_threshold():
    probs = {1: 0.95, 2: 0.7, 3: 0.69, 4: 0.1, 5: 0.9}
    current = {1: False, 2: False, 3: True, 4: True, 5: True}
    to_q, to_r = plan(probs, current, 0.7)
    assert sorted(to_q) == [1, 2]          # newly at/above threshold (inclusive)
    assert sorted(to_r) == [3, 4]          # were quarantined, now below → released
    assert plan({}, {}, 0.7) == ([], [])


def test_main_writes_probabilities_and_flags(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import quarantine_chunks as mod

    rows = [(1, "index page", False, None), (2, "real content", False, None), (3, "old junk", True, 0.95)]
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.fetchone.return_value = (2, 3)
    conn = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *_a, **_k: conn)
    monkeypatch.setattr(mod, "Settings", lambda: SimpleNamespace(database_url="db"))
    monkeypatch.setattr(mod, "score_chunks", lambda passages, **kw: {1: 0.93, 2: 0.05, 3: 0.2})

    assert mod.main(["--threshold", "0.7"]) == 0
    executed = [c.args[0] for c in cur.execute.call_args_list]
    assert any("SET quarantined = TRUE" in s for s in executed)
    assert any("SET quarantined = FALSE" in s for s in executed)     # #3 released
    q_call = next(c for c in cur.execute.call_args_list if "SET quarantined = TRUE" in c.args[0])
    assert q_call.args[1] == (REASON, [1])
    assert cur.executemany.call_count == 1                            # probabilities stored
    conn.commit.assert_called()


def test_similarity_search_excludes_quarantined_rows():
    """Both retrieval legs carry NOT quarantined (migration 0015)."""
    import inspect

    from loaders.vector_loader import VectorLoader

    src = inspect.getsource(VectorLoader.similarity_search)
    assert '"NOT quarantined"' in src
