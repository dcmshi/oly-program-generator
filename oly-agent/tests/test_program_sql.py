# oly-agent/tests/test_program_sql.py
"""CLEAN-3: the program delete / activate SQL is written once (shared/program_sql)
and run by both drivers — the web app (asyncpg) and the CLIs (psycopg2)."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.program_sql import (
    ACTIVATE_DRAFT,
    DELETE_STATEMENTS,
    SUPERSEDE_OTHER_ACTIVE,
    psycopg,
    psycopg_params,
)


def test_placeholders_convert_to_named_psycopg_params():
    assert psycopg("WHERE a = $1 AND ($2::int IS NULL OR b = $2::int)") == \
        "WHERE a = %(p1)s AND (%(p2)s::int IS NULL OR b = %(p2)s::int)"
    assert psycopg_params([3], None) == {"p1": [3], "p2": None}
    assert all("%" not in sql for sql in (*DELETE_STATEMENTS, SUPERSEDE_OTHER_ACTIVE, ACTIVATE_DRAFT))


def test_delete_unlinks_the_training_logs_before_the_cascade():
    first, second, last = DELETE_STATEMENTS
    assert "training_log_exercises" in first and "training_logs" in second
    assert last.strip().startswith("DELETE FROM generated_programs")


def _captured_async(fn, *args):
    seen = []

    async def fake(conn, sql, *params):
        seen.append((sql, params))

    with patch("web.async_db.async_execute", fake):
        asyncio.run(fn(MagicMock(), *args))
    return seen


def test_web_delete_and_activate_run_the_shared_text():
    from web.queries import program as pq

    deleted = _captured_async(pq.delete_program, 7, 3)
    assert [sql for sql, _ in deleted] == list(DELETE_STATEMENTS)
    assert all(params == ([7], 3) for _, params in deleted)
    activated = _captured_async(pq.activate_program, 7, 3)
    assert activated == [(SUPERSEDE_OTHER_ACTIVE, (3, 7)), (ACTIVATE_DRAFT, (3, 7))]


def test_cli_delete_and_activate_run_the_same_text_converted():
    import import_program_md
    from eval import model_baseline

    conn = MagicMock()
    with patch("eval.model_baseline.get_connection", return_value=conn), \
         patch("eval.model_baseline.execute", return_value=1) as ex:
        model_baseline.delete_programs("db", [7, 8])
    assert [c.args[1] for c in ex.call_args_list] == [psycopg(s) for s in DELETE_STATEMENTS]
    assert ex.call_args.args[2] == {"p1": [7, 8], "p2": None}

    with patch("import_program_md.fetch_all", return_value=[]), \
         patch("import_program_md.execute") as ex:
        import_program_md.activate(MagicMock(), 7, 3)
    assert [c.args[1] for c in ex.call_args_list] == [psycopg(SUPERSEDE_OTHER_ACTIVE), psycopg(ACTIVATE_DRAFT)]
    assert ex.call_args.args[2] == {"p1": 3, "p2": 7}
