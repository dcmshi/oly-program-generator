# oly-agent/tests/test_exercise_aliases.py
"""CLEAN-1: the import vocabulary lives in shared/exercise_aliases.py and every
name it maps to is a catalogue row. The catalogue check needs the database
(INTEGRATION_TESTS=1) — DOG-2g mines accessories at runtime, so the migrations
alone don't list the catalogue."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.exercise_aliases import (
    EXACT_ALIASES,
    MD_ALIASES,
    all_alias_targets,
    catalogue_name_for,
    hang_variant_for,
    intensity_reference_for,
)


def test_the_importers_use_the_shared_vocabulary():
    import import_program_csv as ipc
    import import_program_md as ipm

    assert ipc.catalogue_name_for is catalogue_name_for and ipc.EXACT_ALIASES is EXACT_ALIASES
    assert ipm.catalogue_name_for is catalogue_name_for and ipm._MD_ALIASES is MD_ALIASES


def test_rules_in_order_exact_hang_substring():
    assert catalogue_name_for("Sn Balance") == "Snatch Balance"                       # exact
    assert catalogue_name_for("Hang Clean (below knee)") == "Hang Clean (below knee)"  # hang before substring
    assert hang_variant_for("hang power snatch below knee") is None                   # no below-knee power row
    assert catalogue_name_for("Tempo Snatch Pull") == "Snatch Pull"                   # substring, first hit
    assert catalogue_name_for("Something Else") is None
    assert intensity_reference_for("Stiff Leg Deadlift (% of CL)") == "clean"


def test_alias_targets_cover_every_rule_and_skip_deliberate_misses():
    targets = all_alias_targets()
    assert set(EXACT_ALIASES.values()) <= targets
    assert None not in targets and "Hang Power Clean (above knee)" in targets
    assert any(t is None for _, t in MD_ALIASES)                                      # "deficit clean extension"


def test_vocabularies_moved_out_of_constants():
    import shared.constants as c
    from shared.exercise_mapping import CLEAN_MAX_REFS, COMP_LIFT_AVOID_NAMES
    from shared.schema_enums import NOVICE_LEVELS

    assert "clean & jerk" in COMP_LIFT_AVOID_NAMES and "novice" in NOVICE_LEVELS
    assert CLEAN_MAX_REFS == {"clean", "clean_and_jerk"}
    assert not hasattr(c, "COMP_LIFT_AVOID_NAMES") and not hasattr(c, "NOVICE_LEVELS")


@pytest.mark.skipif(os.environ.get("INTEGRATION_TESTS") != "1", reason="needs the database (INTEGRATION_TESTS=1)")
def test_every_alias_target_is_a_catalogue_row():
    """Renaming a catalogue row used to unmap imports silently (NULL exercise_id)."""
    from shared.config import Settings
    from shared.db import fetch_all, get_connection

    conn = get_connection(Settings().database_url)
    try:
        names = {r["name"].lower() for r in fetch_all(conn, "SELECT name FROM exercises")}
    finally:
        conn.close()
    missing = sorted(t for t in all_alias_targets() if t.lower() not in names)
    assert not missing, f"alias targets with no exercises row: {missing}"
