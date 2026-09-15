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
    assert "JSON array only" in prompt


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
