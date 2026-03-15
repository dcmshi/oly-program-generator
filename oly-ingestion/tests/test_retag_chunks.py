# oly-ingestion/tests/test_retag_chunks.py
"""
Tests for retag_chunks.retag().

Mock-based tests cover all logic paths without a live DB.
The live DB integration test requires INTEGRATION_TESTS=1.

Run: python tests/test_retag_chunks.py
     INTEGRATION_TESTS=1 python tests/test_retag_chunks.py
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

RESULTS = []
_INTEGRATION = os.getenv("INTEGRATION_TESTS", "").lower() in ("1", "true")


class _Skip(Exception):
    pass


def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except _Skip as e:
        RESULTS.append(("SKIP", name, str(e)))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, f"{type(e).__name__}: {e}"))


def _integration_only():
    if not _INTEGRATION:
        raise _Skip("set INTEGRATION_TESTS=1 to enable (needs live DB)")


# ── Mock helpers ──────────────────────────────────────────────────────────────

def _make_db(rows: list[tuple]) -> tuple[MagicMock, MagicMock]:
    """Return (mock_conn, mock_cur) pre-loaded with the given chunk rows."""
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = rows
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    return mock_conn, mock_cur


def _run_retag(rows, source_id=None, dry_run=False, keyword_tag_fn=None):
    """Run retag() with mocked psycopg2 and keyword_tag."""
    mock_conn, mock_cur = _make_db(rows)

    if keyword_tag_fn is None:
        # Default: tag text containing "snatch" with ["snatch"]
        def keyword_tag_fn(text):
            return ["snatch"] if "snatch" in text.lower() else []

    from retag_chunks import retag
    with patch("retag_chunks.psycopg2.connect", return_value=mock_conn):
        with patch("retag_chunks.keyword_tag", side_effect=keyword_tag_fn):
            retag(source_id, dry_run)

    return mock_conn, mock_cur


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_unchanged_chunks_not_updated():
    """Chunks whose topics haven't changed should not be written to DB."""
    rows = [
        (1, "snatch technique", ["snatch"]),  # already tagged correctly
        (2, "nutrition advice", []),            # no tags → still no tags
    ]
    mock_conn, mock_cur = _run_retag(rows)

    # No UPDATE should have been executed
    for c in mock_cur.execute.call_args_list:
        assert "UPDATE" not in str(c), f"Unexpected UPDATE: {c}"
    mock_conn.commit.assert_called_once()


def test_dry_run_no_commit_no_update():
    """Dry run prints diff but never commits or issues UPDATE."""
    rows = [
        (1, "snatch technique", []),  # was untagged, should now get ["snatch"]
    ]
    mock_conn, mock_cur = _run_retag(rows, dry_run=True)

    # No UPDATE executed
    for c in mock_cur.execute.call_args_list:
        assert "UPDATE" not in str(c).upper()
    mock_conn.commit.assert_not_called()


def test_changed_chunk_gets_updated():
    """Chunks with new topics are UPDATEd in the DB when not dry_run."""
    rows = [
        (1, "snatch technique guide", []),  # was empty, should become ["snatch"]
    ]
    mock_conn, mock_cur = _run_retag(rows, dry_run=False)

    # Should have issued an UPDATE
    update_calls = [c for c in mock_cur.execute.call_args_list if "UPDATE" in str(c).upper()]
    assert len(update_calls) == 1
    mock_conn.commit.assert_called_once()


def test_multiple_topics_sorted():
    """New topics list is sorted before storing."""
    def tagger(text):
        return ["squat", "snatch", "clean"]  # unsorted

    rows = [(1, "any text", [])]
    mock_conn, mock_cur = _run_retag(rows, dry_run=False, keyword_tag_fn=tagger)

    update_calls = [c for c in mock_cur.execute.call_args_list if "UPDATE" in str(c).upper()]
    assert len(update_calls) == 1
    # args[1] = params tuple = (new_topics, chunk_id); new_topics is at index 0
    stored_topics = update_calls[0].args[1][0]
    assert stored_topics == ["clean", "snatch", "squat"]


def test_source_id_filter_uses_where_clause():
    """Providing source_id= should add WHERE source_id = %s to the query."""
    rows = []
    mock_conn, mock_cur = _run_retag(rows, source_id=499, dry_run=True)

    # The SELECT executed should include source_id
    select_call = mock_cur.execute.call_args_list[0]
    sql = select_call.args[0]
    params = select_call.args[1] if len(select_call.args) > 1 else None
    assert "source_id" in sql.lower()
    assert params == (499,)


def test_no_source_id_fetches_all():
    """Without source_id, the query should have no WHERE clause param."""
    rows = []
    mock_conn, mock_cur = _run_retag(rows, source_id=None, dry_run=True)

    select_call = mock_cur.execute.call_args_list[0]
    # No params passed for the all-chunks query
    if len(select_call.args) > 1:
        # If params are passed they should not be for source_id filtering
        assert select_call.args[1] != (None,)


def test_cursor_and_connection_closed():
    """cur.close() and conn.close() are always called."""
    rows = [(1, "snatch text", [])]
    mock_conn, mock_cur = _run_retag(rows)

    mock_cur.close.assert_called_once()
    mock_conn.close.assert_called_once()


def test_already_tagged_chunks_skipped():
    """Chunks with topics that exactly match what keyword_tag returns are not updated."""
    rows = [
        (1, "snatch technique", ["snatch"]),  # correct — skip
        (2, "squat guide", ["squat"]),         # correct — skip
    ]

    def tagger(text):
        if "snatch" in text:
            return ["snatch"]
        if "squat" in text:
            return ["squat"]
        return []

    mock_conn, mock_cur = _run_retag(rows, keyword_tag_fn=tagger)

    update_calls = [c for c in mock_cur.execute.call_args_list if "UPDATE" in str(c).upper()]
    assert len(update_calls) == 0


def test_topic_removal_triggers_update():
    """If a chunk had a topic that is no longer generated, it should be updated."""
    rows = [
        (1, "neutral text", ["old_tag"]),  # old_tag no longer generated
    ]
    mock_conn, mock_cur = _run_retag(rows, keyword_tag_fn=lambda text: [])

    update_calls = [c for c in mock_cur.execute.call_args_list if "UPDATE" in str(c).upper()]
    assert len(update_calls) == 1  # topic removed → update needed


def test_empty_db_returns_zero_updates():
    rows = []
    mock_conn, mock_cur = _run_retag(rows)

    update_calls = [c for c in mock_cur.execute.call_args_list if "UPDATE" in str(c).upper()]
    assert len(update_calls) == 0


# ── Integration test — live DB ────────────────────────────────────────────────

def test_integration_retag_dry_run_does_not_modify_db():
    """Dry-run against the live DB should not change any topics."""
    _integration_only()

    from retag_chunks import retag
    # Import direct — no mock
    import psycopg2
    from shared.config import Settings
    s = Settings()
    conn = psycopg2.connect(s.database_url)
    cur = conn.cursor()

    # Snapshot current topics for first 5 chunks
    cur.execute("SELECT id, topics FROM knowledge_chunks ORDER BY id LIMIT 5")
    before = {row[0]: row[1] for row in cur.fetchall()}
    cur.close()
    conn.close()

    retag(None, dry_run=True)

    # Topics should be unchanged
    conn2 = psycopg2.connect(s.database_url)
    cur2 = conn2.cursor()
    cur2.execute("SELECT id, topics FROM knowledge_chunks ORDER BY id LIMIT 5")
    after = {row[0]: row[1] for row in cur2.fetchall()}
    cur2.close()
    conn2.close()

    assert before == after, "Dry run should not modify any topics"


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        _test(name, fn)

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    skipped = sum(1 for r in RESULTS if r[0] == "SKIP")
    failed = sum(1 for r in RESULTS if r[0] in ("FAIL", "ERROR"))
    for r in RESULTS:
        detail = f"  → {r[2]}" if len(r) > 2 else ""
        print(f"  {r[0]}  {r[1]}{detail}")
    print(f"\n{passed} passed, {skipped} skipped, {failed} failed")
