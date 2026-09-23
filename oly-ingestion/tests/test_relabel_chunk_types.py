# tests/test_relabel_chunk_types.py
"""
No-key tests for relabel_chunk_types.py (RAG-H2 step 3): prompt batching, response
parsing/validation, update planning, and the enum mirror.

Run: PYTHONUTF8=1 uv run pytest tests/test_relabel_chunk_types.py -q
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from relabel_chunk_types import (
    CHUNK_TYPES,
    PASSAGE_CHARS,
    build_prompt,
    parse_labels,
    plan_updates,
)


def test_chunk_types_mirror_the_db_enum():
    """The script's whitelist must match CREATE TYPE chunk_type in migration 0000,
    or a valid label would be dropped (or an invalid one written and rejected)."""
    migration = Path(__file__).parent.parent.parent / "oly-agent" / "migrations" / "versions" / "0000_ingestion_schema.py"
    src = migration.read_text(encoding="utf-8")
    block = re.search(r"CREATE TYPE chunk_type AS ENUM \((.*?)\)", src, re.S).group(1)
    enum_values = set(re.findall(r"'([a-z_]+)'", block))
    assert set(CHUNK_TYPES) == enum_values, set(CHUNK_TYPES) ^ enum_values


def test_build_prompt_indexes_and_truncates_passages():
    long_text = "x" * (PASSAGE_CHARS + 500)
    prompt = build_prompt([(1, "short passage"), (2, long_text)])
    assert "[1] short passage" in prompt
    assert "[2] " + "x" * PASSAGE_CHARS in prompt
    assert "x" * (PASSAGE_CHARS + 1) not in prompt
    assert '{{"labels": [' in prompt or '{"labels": [' in prompt


def test_parse_labels_validates_type_index_and_confidence():
    raw = """```json
    [
      {"index": 1, "chunk_type": "periodization", "confidence": 0.9},
      {"index": 2, "chunk_type": "not_a_type", "confidence": 0.9},
      {"index": 3, "chunk_type": "fault_correction", "confidence": 1.7},
      {"index": 9, "chunk_type": "concept", "confidence": 0.8},
      {"index": "x", "chunk_type": "concept", "confidence": 0.8},
      "garbage"
    ]
    ```"""
    labels = parse_labels(raw, expected_indexes={1, 2, 3})
    assert labels == {1: ("periodization", 0.9), 3: ("fault_correction", 1.0)}


def test_parse_labels_accepts_schema_wrapper():
    raw = '{"labels": [{"index": 1, "chunk_type": "periodization", "confidence": 0.9}]}'
    assert parse_labels(raw, {1, 2}) == {1: ("periodization", 0.9)}


def test_parse_labels_accepts_single_object():
    assert parse_labels('{"index": 1, "chunk_type": "concept", "confidence": 0.5}', {1}) == {1: ("concept", 0.5)}


def test_plan_updates_skips_unchanged_and_low_confidence():
    rows = [(101, "concept"), (102, "recovery_adaptation"), (103, "concept"), (104, "concept")]
    labels = {
        1: ("periodization", 0.95),       # changed, confident → update
        2: ("recovery_adaptation", 0.99),  # unchanged → skip
        3: ("fault_correction", 0.4),      # changed, below threshold → skip
        # 4: no label → skip
    }
    assert plan_updates(rows, labels, min_confidence=0.6) == [(101, "concept", "periodization", 0.95)]


def test_plan_updates_threshold_is_inclusive():
    rows = [(1, "concept")]
    assert plan_updates(rows, {1: ("biomechanics", 0.6)}, min_confidence=0.6) == [(1, "concept", "biomechanics", 0.6)]


def test_relabel_batch_mode_submits_one_message_batch(monkeypatch):
    """use_batch=True builds every group's request up front, sends them through
    run_message_batch and applies the labels per group; a failed group is skipped."""
    import json
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import relabel_chunk_types as mod

    from shared.llm import BatchRequestFailed

    rows = [(1, "concept", "deload text"), (2, "concept", "bar path text"), (3, "concept", "x")]
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *_a, **_k: conn)
    monkeypatch.setattr(mod, "Settings", lambda: SimpleNamespace(
        anthropic_api_key="k", database_url="db", light_model="claude-haiku-4-5", llm_model="m"))
    # `import anthropic` inside relabel() builds a real client from the fake key — no network needed

    captured = {}

    def fake_batch(client, requests, **kw):
        captured.update(requests)
        return {
            "0": SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps([
                {"index": 1, "chunk_type": "periodization", "confidence": 0.9},
                {"index": 2, "chunk_type": "biomechanics", "confidence": 0.9},
            ]))]),
            "2": BatchRequestFailed("2", "errored"),
        }
    monkeypatch.setattr(mod, "run_message_batch", fake_batch)
    monkeypatch.setattr(mod, "create_message_with_retries", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sync path used")))

    transitions = mod.relabel(None, dry_run=False, batch_size=2, use_batch=True)

    assert list(captured) == ["0", "2"]
    assert captured["0"]["model"] == "claude-haiku-4-5"
    assert captured["0"]["max_tokens"] == mod.RELABEL_MAX_TOKENS
    assert transitions == {("concept", "periodization"): 1, ("concept", "biomechanics"): 1}
    assert cur.execute.call_count == 1 + 2          # the SELECT + two UPDATEs


def test_relabel_jev_judge_labels_each_passage(monkeypatch):
    """--judge jev routes every passage through processors.jev_judge.label_chunk_types
    (no LLM client), applies the same confidence threshold, and never touches the
    Message Batches path."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import relabel_chunk_types as mod

    rows = [(1, "concept", "deload text"), (2, "concept", "bar path text"), (3, "concept", "x" * 3000)]
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *_a, **_k: conn)
    monkeypatch.setattr(mod, "Settings", lambda: SimpleNamespace(
        anthropic_api_key="", database_url="db", light_model="claude-haiku-4-5", llm_model="m",
        llm_provider="openrouter", openrouter_api_key="k"))
    monkeypatch.setattr(mod, "create_llm_client", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("LLM client built")))
    monkeypatch.setattr(mod, "run_message_batch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("batch used")))

    seen = []

    def fake_jev(passages, **kw):
        seen.append(passages)
        return {1: ("periodization", 0.91), 2: ("biomechanics", 0.55), 3: ("concept", 0.8)}
    import processors.jev_judge as jev
    monkeypatch.setattr(jev, "label_chunk_types", fake_jev)

    transitions = mod.relabel(None, dry_run=False, batch_size=10, use_batch=True, judge="jev")

    assert seen == [{1: "deload text", 2: "bar path text", 3: "x" * mod.PASSAGE_CHARS}]   # passages capped
    assert transitions == {("concept", "periodization"): 1}       # 0.55 is below min_confidence
    assert cur.execute.call_count == 1 + 1
