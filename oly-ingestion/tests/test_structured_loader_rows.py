# tests/test_structured_loader_rows.py
"""
No-DB tests for every StructuredLoader method (test_structured_loader_unit.py
covers only load_program's dimension guard). A scripted cursor answers
fetchone() in order and records every execute(), so the SQL contract —
savepoints per row, rowcount-based counts, enum coercion, url-first source
identity, run bookkeeping — is checked without Postgres.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from loaders.structured_loader import StructuredLoader, _json_safe


class Cursor:
    def __init__(self, fetches=(), rowcounts=(), fail_on=None):
        self.fetches, self.rowcounts, self.fail_on = list(fetches), list(rowcounts), fail_on
        self.calls: list[tuple[str, tuple | None]] = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        if self.fail_on and self.fail_on(sql, params):
            raise RuntimeError("constraint violated")
        if "INSERT" in sql and self.rowcounts:
            self.rowcount = self.rowcounts.pop(0)

    def fetchone(self):
        return self.fetches.pop(0) if self.fetches else None

    def close(self):
        pass

    def sql(self, fragment):
        return [c for c in self.calls if fragment in c[0]]


def loader_with(cursor) -> StructuredLoader:
    with patch("loaders.structured_loader.psycopg2.connect"):
        ld = StructuredLoader(SimpleNamespace(database_url="postgresql://fake"))
    ld.conn = MagicMock()
    ld.conn.cursor.return_value = cursor
    return ld


# ── sources ──────────────────────────────────────────────────────

def test_upsert_source_url_hit_returns_existing_id():
    cur = Cursor(fetches=[(42,)])
    assert loader_with(cur).upsert_source("T", "A", "website", url="https://x/a/") == 42
    assert len(cur.calls) == 1 and "WHERE url" in cur.calls[0][0]


def test_upsert_source_title_match_backfills_a_missing_url():
    cur = Cursor(fetches=[None, (7, None)])
    ld = loader_with(cur)
    assert ld.upsert_source("T", "A", "website", url="https://x/a/") == 7
    assert cur.sql("UPDATE sources SET url")[0][1] == ("https://x/a/", 7)
    ld.conn.commit.assert_called_once()


def test_upsert_source_same_title_other_url_gets_a_slug_title():
    cur = Cursor(fetches=[None, (7, "https://x/old/"), None, (8,)])
    assert loader_with(cur).upsert_source("Tapering", "A", "website", url="https://x/2016/tapering-2/") == 8
    insert = cur.sql("INSERT INTO sources")[0][1]
    assert insert[0] == "Tapering [tapering-2]" and insert[2] == "website"


def test_upsert_source_slug_collision_falls_back_to_a_url_hash():
    cur = Cursor(fetches=[None, (7, "https://x/old/"), (1,), (9,)])      # slug title already taken
    assert loader_with(cur).upsert_source("Tapering", "A", "website", url="https://x/2017/tapering/") == 9
    title = cur.sql("INSERT INTO sources")[0][1][0]
    assert title.startswith("Tapering [") and len(title.split("[")[1]) == 9     # 8 hex chars + ]


def test_upsert_source_truncates_and_maps_doc_types():
    cur = Cursor(fetches=[None, (3,)])
    loader_with(cur).upsert_source("x" * 400, "y" * 250, "program")
    title, author, db_type, url = cur.sql("INSERT INTO sources")[0][1]
    assert len(title) == 300 and len(author) == 200 and db_type == "manual" and url is None
    cur = Cursor(fetches=[None, None, (4,)])
    loader_with(cur).upsert_source("T", "A", "unknown-type", url="u" * 600)
    assert cur.sql("INSERT INTO sources")[0][1][2:] == ("book", "u" * 500)


# ── principles ───────────────────────────────────────────────────

def _principle(name, priority=5):
    return SimpleNamespace(principle_name=name, category="volume", rule_type="guideline", condition={},
                           recommendation={"volume_modifier": 0.6}, rationale="r", priority=priority)


def test_load_principles_counts_real_inserts_and_isolates_bad_rows():
    cur = Cursor(rowcounts=[1, 0], fail_on=lambda s, p: "INSERT" in s and p and p[0] == "bad")
    ld = loader_with(cur)
    assert ld.load_principles([_principle("a"), _principle("bad"), _principle("dup")], 5) == 1
    assert len(cur.sql("ROLLBACK TO SAVEPOINT p_row")) == 1 and len(cur.sql("RELEASE SAVEPOINT p_row")) == 2
    ld.conn.commit.assert_called_once()


# ── programs ─────────────────────────────────────────────────────

PROGRAM = {"name": "P", "source_id": 2, "duration_weeks": 4, "sessions_per_week": 4, "program_structure": {"weeks": []}}


def test_load_program_coerces_goal_and_level_and_returns_id():
    cur = Cursor(fetches=[(11,)])
    assert loader_with(cur).load_program({**PROGRAM, "goal": "Intensification", "athlete_level": "pro"}) == 11
    params = cur.sql("INSERT INTO program_templates")[0][1]
    assert params[2:4] == ("any", "peaking")
    cur = Cursor(fetches=[(12,)])
    loader_with(cur).load_program({**PROGRAM, "goal": "world domination", "athlete_level": None})
    assert cur.sql("INSERT INTO program_templates")[0][1][2:4] == ("any", "general_strength")


def test_load_program_duplicate_and_failure_return_none():
    assert loader_with(Cursor(fetches=[None])).load_program(PROGRAM) is None           # ON CONFLICT skipped
    ld = loader_with(Cursor(fail_on=lambda s, p: "INSERT" in s))
    assert ld.load_program(PROGRAM) is None
    ld.conn.rollback.assert_called_once()
    assert loader_with(Cursor()).load_program({**PROGRAM, "duration_weeks": "x"}) is None   # un-castable


# ── exercises ────────────────────────────────────────────────────

def test_load_exercise_maps_categories_and_returns_id():
    cur = Cursor(fetches=[(21,)])
    assert loader_with(cur).load_exercise({"name": "Hang Snatch", "category": "Variation"}) == 21
    params = cur.sql("INSERT INTO exercises")[0][1]
    assert params[:3] == ("Hang Snatch", "competition_variant", "snatch") and params[4] == []
    cur = Cursor(fetches=[(22,)])
    loader_with(cur).load_exercise({"name": "X", "category": "cardio", "movement_family": "squat"})
    assert cur.sql("INSERT INTO exercises")[0][1][1:3] == ("competition_variant", "squat")
    assert "COALESCE(NULLIF(exercises.primary_purpose" in cur.calls[0][0]     # curated fields preserved


def test_load_exercise_failure_rolls_back():
    ld = loader_with(Cursor(fail_on=lambda s, p: True))
    assert ld.load_exercise({"name": "X"}) is None
    ld.conn.rollback.assert_called_once()


# ── schemes, prilepin, json ──────────────────────────────────────

def test_load_percentage_schemes_counts_rowcount_and_skips_bad_rows():
    cur = Cursor(rowcounts=[1, 0])
    rows = [{"sets": 5, "reps": 3, "intensity_pct": 80}, {"sets": 5, "reps": 3, "intensity_pct": 80},
            {"reps": 3, "intensity_pct": 80}]                                           # missing sets → KeyError
    assert loader_with(cur).load_percentage_schemes(rows, 3) == 1
    first = cur.sql("INSERT INTO percentage_schemes")[0][1]
    assert first[0] == "Unknown" and first[2:6] == ("accumulation", 1, 1, 1) and first[-1] == "competition_lift"
    assert len(cur.sql("ROLLBACK TO SAVEPOINT s_row")) == 1


def test_load_prilepin_rows_isolates_failures():
    row = {"intensity_range_low": 70, "intensity_range_high": 80, "reps_per_set_low": 3, "reps_per_set_high": 6,
           "optimal_total_reps": 18, "total_reps_range_low": 12, "total_reps_range_high": 24}
    cur = Cursor(fail_on=lambda s, p: "INSERT" in s and p[0] == 99)
    assert loader_with(cur).load_prilepin_rows([row, {**row, "intensity_range_low": 99}]) == 1
    assert cur.sql("INSERT INTO prilepin_chart")[0][1][-2:] == ("competition_lifts", "")


def test_load_json_routes_by_target_table(tmp_path):
    ld = loader_with(Cursor())
    with patch.object(ld, "load_exercise") as ex, patch.object(ld, "load_percentage_schemes") as ps, \
         patch.object(ld, "load_prilepin_rows") as pr:
        for target, n in (("exercises", 2), ("percentage_schemes", 1), ("prilepin_chart", 1), ("nope", 1)):
            f = tmp_path / f"{target}.json"
            f.write_text(json.dumps({"target_table": target, "records": [{"name": "é"}] * n}), encoding="utf-8")
            assert ld.load_json(f, 4) == (0 if target == "nope" else n)
    assert ex.call_count == 2 and ex.call_args.args[0]["source_id"] == 4
    ps.assert_called_once()
    pr.assert_called_once()


# ── ingestion runs ───────────────────────────────────────────────

def test_run_lifecycle_sql():
    cur = Cursor(fetches=[(50,), (60, 12), None])
    ld = loader_with(cur)
    assert ld.create_run(3, "f.pdf", "abc", {"model": "m"}) == 50
    ld.update_run_status(50, "started")
    ld.update_run_progress(50, pages_processed=11, last_processed_page=11)
    ld.complete_run(50, {"chunks_loaded": 7, "principles": 2, "ocr_verdicts": {3: "ok"}})
    ld.fail_run(50, "boom", {"traceback": "tb"})
    assert ld.find_resumable_run("abc") == (60, 12)
    assert ld.find_resumable_run("abc") is None
    ld.log_chunk(50, 9, 1, "Chapter 1", "prose")
    ld.close()
    complete = cur.sql("SET status = 'completed'")[0][1]
    assert complete[0] == 7 and complete[3] == 2 and complete[-1] == 50
    assert json.loads(json.dumps(complete[-2].adapted))["ocr_verdicts"] == {"3": "ok"}    # int keys → str
    assert cur.sql("SET status = 'failed'")[0][1][0] == "boom"
    assert cur.sql("SET pages_processed")[0][1] == (11, 11, 50)
    assert cur.sql("INSERT INTO ingestion_chunk_log")[0][1] == (50, 9, 1, "Chapter 1", "prose")
    ld.conn.close.assert_called_once()


def test_complete_run_falls_back_to_valid_prose_count_and_json_safe():
    cur = Cursor()
    loader_with(cur).complete_run(1, {"prose_chunks_valid": 4})
    assert cur.sql("SET status = 'completed'")[0][1][0] == 4
    assert _json_safe({1: [(2, {3: "x"})]}) == {"1": [[2, {"3": "x"}]]}
