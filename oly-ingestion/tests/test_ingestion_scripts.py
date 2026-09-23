# tests/test_ingestion_scripts.py
"""
No-DB, no-key tests for the ingestion CLI scripts' main paths: reembed.reembed,
ocr_audit, the local embedder, the Jev chunk-type judge, quarantine_source /
quarantine main, dedupe_principles.main, principle_model_compare.run_model /
main, and kobo_import's pure helpers + verify. Their pure helpers already had
tests; the paths that talk to the DB / APIs are driven here with fakes.
"""

import asyncio
import json
import sys
import types
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class FakeCursor:
    """Answers fetchall() in order; records execute / executemany."""
    def __init__(self, *fetchalls):
        self._fetchalls = list(fetchalls)
        self.executed, self.executemany_calls = [], []
        self.rowcount = 3

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def executemany(self, sql, seq):
        self.executemany_calls.append((" ".join(sql.split()), list(seq)))

    def fetchall(self):
        return self._fetchalls.pop(0) if self._fetchalls else []

    def close(self):
        pass


def fake_conn(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


# ── reembed ──────────────────────────────────────────────────────

def _reembed_env(rows, width=4):
    import reembed
    cur = FakeCursor(rows)
    loader = MagicMock()
    loader.conn = fake_conn(cur)
    loader._embed_batch.side_effect = lambda texts: [[0.1] * width for _ in texts]
    settings = SimpleNamespace(embedding_model="text-embedding-3-large", embedding_dim=4)
    patches = (patch.object(reembed, "Settings", return_value=settings),
               patch("loaders.vector_loader.VectorLoader", return_value=loader))
    return reembed, cur, loader, patches


def test_reembed_writes_every_row_in_batches():
    reembed, cur, loader, (p1, p2) = _reembed_env([(1, "a"), (2, "b"), (3, "c")])
    with p1, p2:
        assert reembed.reembed(None, dry_run=False, batch_size=2) == 3
    updates = [e for e in cur.executed if e[0].startswith("UPDATE knowledge_chunks")]
    assert [u[1][2] for u in updates] == [1, 2, 3] and updates[0][1][1] == "text-embedding-3-large"
    assert loader.conn.commit.call_count == 2 and loader._embed_batch.call_count == 2
    loader.close.assert_called_once()


def test_reembed_dry_run_and_width_mismatch():
    reembed, cur, loader, (p1, p2) = _reembed_env([(1, "a")])
    with p1, p2:
        assert reembed.reembed(7, dry_run=True) == 0
    loader._embed_batch.assert_not_called()
    assert cur.executed[0][1][1] == 7                                   # scoped to the source
    reembed, cur, loader, (p1, p2) = _reembed_env([(1, "a")], width=3)
    with p1, p2, pytest.raises(SystemExit, match="3-d vectors"):
        reembed.reembed(None, dry_run=False)


# ── ocr_audit ────────────────────────────────────────────────────

def _pages(n):
    topics = ["snatch pulls", "clean recovery", "jerk dip", "front squat", "overhead squat", "hang power clean"]
    return [" ".join(f"Page {i}: {topics[i % len(topics)]} are trained with sets of {i + 2} at {60 + i} percent, "
                     f"week {w} of the {topics[(i + w) % len(topics)]} block." for w in range(20)) for i in range(n)]


def test_ocr_audit_reports_and_clears_suspect_pages(tmp_path, capsys):
    import ocr_audit
    p = _pages(4)
    cache = tmp_path / "abc.json"
    cache.write_text(json.dumps({"model": "m", "pages": {"0": p[0], "1": "", "2": p[2], "3": p[3]}}), encoding="utf-8")
    assert ocr_audit.audit(cache, None, clear=False) == 1
    out = capsys.readouterr().out
    assert "4 pages, 1 suspect" in out and "p2" in out and "blank" in out
    assert ocr_audit.audit(cache, None, clear=True) == 1
    assert "1" not in json.loads(cache.read_text(encoding="utf-8"))["pages"]
    assert "cleared 1 page(s)" in capsys.readouterr().out


def test_ocr_audit_main_walks_the_cache_dir(tmp_path, monkeypatch):
    import ocr_audit
    p = _pages(3)
    (tmp_path / "a.json").write_text(json.dumps({"pages": {"0": p[0], "1": p[1], "2": p[2]}}), encoding="utf-8")
    (tmp_path / "a.report.json").write_text("{}", encoding="utf-8")          # reports are skipped
    monkeypatch.setattr(ocr_audit, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["ocr_audit.py"])
    assert ocr_audit.main() == 0
    (tmp_path / "b.json").write_text(json.dumps({"pages": {"0": p[0], "1": ""}}), encoding="utf-8")
    assert ocr_audit.main() == 1                                              # any suspect → exit 1


# ── local embedder ───────────────────────────────────────────────

def test_local_embedder_fits_dimension_and_uses_the_query_prompt(monkeypatch):
    calls = []

    class FakeST:
        def __init__(self, name, device=None, trust_remote_code=False):
            self.name = name

        def get_sentence_embedding_dimension(self):
            return 3

        def encode(self, texts, **kw):
            calls.append(kw)
            return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=FakeST))
    from loaders.local_embedder import LocalEmbedder
    emb = LocalEmbedder("Qwen/Qwen3-Embedding-0.6B", dim=5)
    assert emb.native_dim == 3 and emb.query_prompt == "query"
    docs = emb.embed_documents(["a", "b"])
    assert len(docs) == 2 and len(docs[0]) == 5 and emb.embed_documents([]) == []
    assert len(emb.embed_query("q")) == 5 and calls[-1]["prompt_name"] == "query"
    assert LocalEmbedder("bge-m3", dim=3).query_prompt is None


# ── jev chunk-type judge ─────────────────────────────────────────

def test_jev_label_chunk_types_keeps_answers_and_skips_failures(monkeypatch):
    class Client:
        def __init__(self, model=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def system_one(self, state, questions):
            if "boom" in state["passage"]:
                raise RuntimeError("503")
            return SimpleNamespace(choices={"chunk_type": SimpleNamespace(choice="periodization", confidence=0.83)})

    monkeypatch.setitem(sys.modules, "typesafe_sdk", types.SimpleNamespace(AsyncTypeSafeClient=Client,
                                                                          Choice=lambda **kw: kw))
    from processors.jev_judge import label_chunk_types
    out = label_chunk_types({1: "a deload week", 2: "boom"}, concurrency=2)
    assert out == {1: ("periodization", 0.83)}


# ── quarantine ───────────────────────────────────────────────────

def test_quarantine_source_scores_writes_and_counts(monkeypatch):
    import quarantine_chunks as qc
    cur = FakeCursor([(1, "Index … 12, 45", False), (2, "Snatch pulls build the finish", True), (3, "TOC", False)])
    monkeypatch.setattr(qc.psycopg2, "connect", lambda url: fake_conn(cur))
    monkeypatch.setattr(qc, "score_chunks", lambda passages: {1: 0.95, 2: 0.1, 3: 0.2})
    assert qc.quarantine_source(9, SimpleNamespace(database_url="db"), threshold=0.7) == 1
    assert [p for p in cur.executemany_calls[0][1]] == [(0.95, 1), (0.1, 2), (0.2, 3)]
    sqls = [e[0] for e in cur.executed]
    assert any("quarantined = TRUE" in s for s in sqls) and any("quarantined = FALSE" in s for s in sqls)


def test_quarantine_source_with_no_chunks(monkeypatch):
    import quarantine_chunks as qc
    monkeypatch.setattr(qc.psycopg2, "connect", lambda url: fake_conn(FakeCursor([])))
    assert qc.quarantine_source(9, SimpleNamespace(database_url="db")) == 0


def test_quarantine_main_release_rescore_and_dry_run(monkeypatch, capsys):
    import quarantine_chunks as qc
    rows = [(1, "junk", False, 0.9), (2, "coaching", True, 0.2)]
    monkeypatch.setattr(qc, "Settings", lambda: SimpleNamespace(database_url="db"))
    cur = FakeCursor(rows)
    monkeypatch.setattr(qc.psycopg2, "connect", lambda url: fake_conn(cur))
    assert qc.main(["--release", "--source-id", "4", "--limit", "10"]) == 0
    assert "released 2 chunk(s)" in capsys.readouterr().out
    assert cur.executed[0][1] == [qc.PASSAGE_CHARS, 4, 10]
    cur = FakeCursor(rows)
    monkeypatch.setattr(qc.psycopg2, "connect", lambda url: fake_conn(cur))
    assert qc.main(["--rescore-from-db", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "quarantine 1, release 1" in out
    assert not any(e[0].startswith("UPDATE") for e in cur.executed)


# ── dedupe ───────────────────────────────────────────────────────

def test_dedupe_main_release_and_dry_run(monkeypatch, capsys, tmp_path):
    import dedupe_principles as dp
    monkeypatch.setattr(dp, "Settings", lambda: SimpleNamespace(database_url="db"))
    cur = FakeCursor()
    monkeypatch.setattr(dp.psycopg2, "connect", lambda url: fake_conn(cur))
    assert dp.main(["--release"]) == 0
    assert "released 3 principle(s)" in capsys.readouterr().out

    rows = [(1, "Deload every 4th week", "deload", 10, {}, {}, "r"),
            (2, "Deload each fourth week", "deload", 11, {}, {}, "r"),
            (3, "Pull at 90-110%", "intensity", 10, {}, {}, "r")]
    cur = FakeCursor(rows)
    monkeypatch.setattr(dp.psycopg2, "connect", lambda url: fake_conn(cur))
    loader = MagicMock()
    loader._embed_batch.return_value = [[1.0, 0.0], [0.99, 0.1], [0.0, 1.0]]
    monkeypatch.setattr(dp, "VectorLoader", lambda s: loader)
    monkeypatch.setattr(dp, "judge_pairs", lambda states: {0: 0.92})
    assert dp.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "1 duplicate pairs" in out and "1 cross-source" in out and "#2" in out
    assert not cur.executemany_calls


# ── principle_model_compare ──────────────────────────────────────

def test_run_model_scores_claims_against_the_window_and_totals(monkeypatch):
    import principle_model_compare as pmc
    principle = SimpleNamespace(principle_name="Pull range", recommendation={"intensity_ceiling": 110},
                                condition={})
    empty = SimpleNamespace(principle_name="Rule", recommendation={}, condition={})
    ext = MagicMock()
    ext._request_params.return_value = {}
    ext._parse_response.side_effect = [[principle, empty], RuntimeError("never reached")]
    msg = SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=200))
    monkeypatch.setattr(pmc, "PrincipleExtractor", lambda s: ext)
    calls = iter([msg, RuntimeError("402 payment required")])

    def create(*a, **k):
        v = next(calls)
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(pmc, "create_message_growing", create)
    import config
    monkeypatch.setattr(config, "Settings", lambda llm_model: SimpleNamespace(llm_model=llm_model))
    windows = [{"label": "book:1", "title": "T", "text": "Pulls at 90-100% of the clean."},
               {"label": "web:x", "title": "W", "text": "x"}]
    out = pmc.run_model("moonshotai/kimi-k3", windows)
    first, second = out["windows"]
    assert first["principles"] == 2 and first["claims"] == 1 and first["unsupported"] == 1
    assert first["empty_recommendation"] == 1 and first["unsupported_claims"] == ["Pull range: recommendation.intensity_ceiling=110"]
    assert second["error"] == "402 payment required" and second["principles"] == 0
    assert out["totals"]["failed_windows"] == 1 and out["totals"]["principles"] == 2


def test_principle_model_compare_main_writes_results(monkeypatch, tmp_path, capsys):
    import principle_model_compare as pmc
    windows = [{"label": "book:1", "title": "T", "text": "abc", "source_id": 1}]
    monkeypatch.setattr(pmc, "pick_windows", lambda cur: windows)
    monkeypatch.setattr(pmc, "run_model", lambda m, w: {"model": m, "windows": [], "totals": {
        "principles": 3, "with_numbers": 1, "claims": 2, "unsupported": 1, "empty_recommendation": 0,
        "parser_repairs": 0, "failed_windows": 0, "latency_s": 4.0, "cost_usd": 0.01}})
    import config
    import psycopg2
    monkeypatch.setattr(config, "Settings", lambda: SimpleNamespace(database_url="db"))
    monkeypatch.setattr(psycopg2, "connect", lambda url: MagicMock())
    out = tmp_path / "r.json"
    monkeypatch.setattr(sys, "argv", ["pmc", "--model", "a", "--model", "b", "--out", str(out)])
    pmc.main()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [r["model"] for r in data["results"]] == ["a", "b"] and "text" not in data["windows"][0]
    assert "50.0%" in capsys.readouterr().out


# ── kobo_import ──────────────────────────────────────────────────

def test_kobo_helpers_and_verify(tmp_path, capsys):
    import kobo_import as ki
    assert ki._ids("3,5-7") == [3, 5, 6, 7] and ki._ids("9") == [9]
    assert ki._clean_name('A: B/C?  "D"') == "A BC D"
    ok = tmp_path / "ok.epub"
    with zipfile.ZipFile(ok, "w") as z:
        z.writestr("ch1.xhtml", "<html><body><p>" + "snatch " * 5000 + "</p></body></html>")
    drm = tmp_path / "drm.epub"
    with zipfile.ZipFile(drm, "w") as z:
        z.writestr("META-INF/encryption.xml", "<x/>")
        z.writestr("ch1.xhtml", "<p>x</p>")
    assert ki.cmd_verify(SimpleNamespace(epub=[str(ok)])) == 0
    assert ki.cmd_verify(SimpleNamespace(epub=[str(drm)])) == 1
    out = capsys.readouterr().out
    assert "| ok" in out and "| DRM" in out


def test_asyncio_is_left_clean():
    # the Jev test runs an event loop via asyncio.run; make sure none is left set
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()
