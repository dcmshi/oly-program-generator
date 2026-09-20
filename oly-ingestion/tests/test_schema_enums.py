# tests/test_schema_enums.py
"""
STRUCT-1 no-key tests: the shared enum tuples mirror the Postgres enums, every
structured-output schema obeys the API's JSON-schema rules (a violation is a
400 at request time, not a test failure anywhere else), and the schemas only
name values the DB accepts.

Run: PYTHONUTF8=1 uv run pytest tests/test_schema_enums.py -q
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # repo root for shared.*

from shared import schema_enums

REPO = Path(__file__).parent.parent.parent
_INGESTION_MIGRATION = REPO / "oly-agent" / "migrations" / "versions" / "0000_ingestion_schema.py"
_AGENT_BASELINE = REPO / "oly-agent" / "migrations" / "versions" / "0001_baseline.py"


def _sql_enum(source: Path, name: str) -> set[str]:
    block = re.search(rf"CREATE TYPE {name} AS ENUM \((.*?)\)", source.read_text(encoding="utf-8"), re.S).group(1)
    return set(re.findall(r"'([a-z_]+)'", block))


def test_enum_tuples_mirror_the_migrations():
    for attr, sql_name, source in (
        ("TRAINING_PHASES", "training_phase", _INGESTION_MIGRATION),
        ("MOVEMENT_FAMILIES", "movement_family", _INGESTION_MIGRATION),
        ("PRINCIPLE_CATEGORIES", "principle_category", _INGESTION_MIGRATION),
        ("RULE_TYPES", "rule_type", _INGESTION_MIGRATION),
        ("CHUNK_TYPES", "chunk_type", _INGESTION_MIGRATION),
        ("ATHLETE_LEVELS", "athlete_level", _AGENT_BASELINE),
    ):
        values = getattr(schema_enums, attr)
        assert len(values) == len(set(values)), attr
        assert set(values) == _sql_enum(source, sql_name), (attr, set(values) ^ _sql_enum(source, sql_name))


# ── Structured-output schema rules ────────────────────────────────────────────
# From the structured-outputs reference: every object must set
# additionalProperties: false; numeric / string-length / pattern constraints and
# minItems > 1 are rejected; enums hold scalars only.

_FORBIDDEN_KEYWORDS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
                       "minLength", "maxLength", "pattern", "maxItems", "uniqueItems"}


def _walk(node, path="$"):
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            assert node.get("additionalProperties") is False, f"{path}: object without additionalProperties: false"
            assert "properties" in node, f"{path}: object without properties"
            for req in node.get("required", []):
                assert req in node["properties"], f"{path}: required {req!r} not in properties"
        bad = _FORBIDDEN_KEYWORDS & set(node)
        assert not bad, f"{path}: unsupported keyword(s) {sorted(bad)}"
        if "minItems" in node:
            assert node["minItems"] in (0, 1), f"{path}: minItems must be 0 or 1"
        if "enum" in node:
            assert all(isinstance(v, str | int | float | bool) or v is None for v in node["enum"]), f"{path}: enum of non-scalars"
            assert node["enum"], f"{path}: empty enum"
        for k, v in node.items():
            _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, f"{path}[{i}]")


def _all_schemas() -> dict[str, dict]:
    from pipeline import PROGRAM_CONTINUATION_SCHEMA, PROGRAM_TEMPLATE_SCHEMA
    from processors.classifier import ContentClassifier
    from processors.principle_extractor import PRINCIPLE_SCHEMA
    from relabel_chunk_types import RELABEL_SCHEMA
    return {
        "PRINCIPLE_SCHEMA": PRINCIPLE_SCHEMA,
        "PROGRAM_TEMPLATE_SCHEMA": PROGRAM_TEMPLATE_SCHEMA,
        "PROGRAM_CONTINUATION_SCHEMA": PROGRAM_CONTINUATION_SCHEMA,
        "CLASSIFY_SCHEMA": ContentClassifier._CLASSIFY_SCHEMA,
        "RELABEL_SCHEMA": RELABEL_SCHEMA,
    }


# Undocumented but enforced (400 on 2026-09-20: "Schemas contains too many
# optional parameters (50) … (limit: 24)"): optional properties summed over
# every object in the schema.
MAX_OPTIONAL_PROPERTIES = 24


def _optional_count(node) -> int:
    n = 0
    if isinstance(node, dict):
        if "properties" in node:
            n += len(set(node["properties"]) - set(node.get("required", [])))
        n += sum(_optional_count(v) for v in node.values())
    elif isinstance(node, list):
        n += sum(_optional_count(v) for v in node)
    return n


def test_every_ingestion_schema_obeys_the_structured_output_rules():
    for name, schema in _all_schemas().items():
        assert schema["type"] == "object", f"{name}: top level must be an object"
        _walk(schema, name)
        assert _optional_count(schema) <= MAX_OPTIONAL_PROPERTIES, f"{name}: {_optional_count(schema)} optional properties"


def test_principle_schema_values_are_db_values():
    """The extractor can only emit what the matcher evaluates and the DB stores:
    the `"novice"` / `"peaking"` padding and the rejected `"intensification"`
    category of 2026-09-16/20 are unrepresentable."""
    from processors.principle_extractor import CONDITION_KEYS, PRINCIPLE_SCHEMA
    item = PRINCIPLE_SCHEMA["properties"]["principles"]["items"]["properties"]
    cond = item["condition"]["properties"]
    assert set(cond) == CONDITION_KEYS
    assert set(item["category"]["enum"]) == set(schema_enums.PRINCIPLE_CATEGORIES)
    assert set(item["rule_type"]["enum"]) == set(schema_enums.RULE_TYPES)
    assert set(cond["athlete_level"]["items"]["enum"]) == set(schema_enums.ATHLETE_LEVELS)
    assert set(cond["movement_family"]["enum"]) == set(schema_enums.MOVEMENT_FAMILIES)
    phase_scalar, phase_array = cond["phase"]["anyOf"]
    assert set(phase_scalar["enum"]) == set(phase_array["items"]["enum"]) == set(schema_enums.TRAINING_PHASES)
    assert item["priority"]["enum"] == list(range(1, 11))
    # recommendation values are typed and never nullable — no more `intensity_floor: null`
    for key, spec in item["recommendation"]["properties"].items():
        assert "null" not in str(spec), key


def test_relabel_and_classifier_enums_match():
    from processors.classifier import ContentClassifier
    from relabel_chunk_types import RELABEL_SCHEMA
    labels = RELABEL_SCHEMA["properties"]["labels"]["items"]["properties"]
    assert set(labels["chunk_type"]["enum"]) == set(schema_enums.CHUNK_TYPES)
    assert set(ContentClassifier._CLASSIFY_SCHEMA["properties"]["content_type"]["enum"]) == {
        "prose", "principle", "mixed", "table", "program_template", "exercise_description"}
