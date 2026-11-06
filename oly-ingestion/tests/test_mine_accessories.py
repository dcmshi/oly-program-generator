# tests/test_mine_accessories.py
"""
No-key, no-DB tests for mine_accessories (DOG-2g): name normalisation, the
named-in-text guard, reply parsing, aggregation by distinct source, the
insert filter, and the schema rules the structured-outputs API enforces.

Run: PYTHONUTF8=1 uv run pytest tests/test_mine_accessories.py -q
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from mine_accessories import (
    MINE_SCHEMA,
    aggregate,
    build_prompt,
    category_for,
    display_name,
    insertable,
    named_in_text,
    normalize_name,
    parse_reply,
)


def test_normalize_name_folds_spellings_onto_one_key():
    assert normalize_name("Good Mornings") == normalize_name("good morning") == "good morning"
    assert normalize_name("RDL") == normalize_name("Romanian Deadlifts") == "romanian deadlift"
    assert normalize_name("Hyperextensions") == normalize_name("Back Extension") == "back extension"
    assert normalize_name("Farmer's Walk") == normalize_name("farmers carry") == "farmer carry"
    assert normalize_name("Pull-ups") == normalize_name("pullup") == "pull up"
    assert normalize_name("Barbell Presses") == "barbell press"
    assert normalize_name("Overhead Carries") == "overhead carry"
    assert normalize_name("Thoracic Extension (foam roller)") == "thoracic extension"
    assert normalize_name("Triceps Extension") == normalize_name("tricep extensions")
    assert normalize_name("") == "" and normalize_name(None) == ""


def test_named_in_text_rejects_names_the_passage_lacks():
    words = set(normalize_name("Finish with RDLs and hyperextensions, then some pull-ups.").split())
    assert named_in_text("Romanian Deadlift", words)
    assert named_in_text("Back Extension", words)
    assert named_in_text("Pull-Up", words)
    assert not named_in_text("Good Morning", words)
    assert not named_in_text("Weighted Pull-Up", words)          # "weighted" is not in the passage
    assert not named_in_text("", words)


def test_parse_reply_cleans_numbers_and_drops_bad_entries():
    passages = [(1, "Do good mornings 3x8 and planks for the trunk."), (2, "Snatch work only.")]
    raw = {"passages": [
        {"index": 1, "exercises": [
            {"name": "Good Morning", "movement_family": "hinge", "sets_low": 3, "reps_low": 8,
             "purpose": "  posterior   chain "},
            {"name": "Plank", "movement_family": "core", "sets_low": 5, "sets_high": 2},   # low > high
            {"name": "Nordic Curl", "movement_family": "hinge"},                         # not in the text
            {"name": "Good Morning", "movement_family": "snatch"},                       # not a mine family
            {"name": "Good Morning", "movement_family": "hinge", "reps_low": 400},       # implausible reps
        ]},
        {"index": 2, "exercises": []},
        {"index": 9, "exercises": [{"name": "Plank", "movement_family": "core"}]},     # unknown index
    ]}
    out = parse_reply(raw, passages)
    assert set(out) == {1, 2} and out[2] == []
    gm, plank, gm2 = out[1]
    assert gm == {"name": "Good Morning", "movement_family": "hinge", "purpose": "posterior chain",
                  "sets_low": 3, "sets_high": 3, "reps_low": 8, "reps_high": 8}
    assert plank["sets_low"] is None and plank["sets_high"] is None and plank["purpose"] is None
    assert gm2["reps_low"] is None and gm2["reps_high"] is None
    assert parse_reply(None, passages) == {1: [], 2: []}


def test_parse_reply_drops_generic_names_and_placeholder_purposes():
    """Smoke run 2026-11-05: "Squat" / "Row" / "Curl" came back as names and
    "Not stated" as a purpose."""
    passages = [(1, "Squat, row and curl, then planks and good mornings.")]
    raw = {"passages": [{"index": 1, "exercises": [
        {"name": "Squat", "movement_family": "squat"},
        {"name": "Rows", "movement_family": "row"},
        {"name": "Curl", "movement_family": "pull"},
        {"name": "Plank", "movement_family": "core", "purpose": "Not stated"},
        {"name": "Good Morning", "movement_family": "hinge", "purpose": "N/A"},
    ]}]}
    out = parse_reply(raw, passages)[1]
    assert [m["name"] for m in out] == ["Plank", "Good Morning"]
    assert all(m["purpose"] is None for m in out)


def test_aggregate_counts_distinct_sources_and_dedupes_against_the_catalogue():
    def m(name, fam="hinge", **kw):
        return {"name": name, "movement_family": fam, "purpose": kw.get("purpose"),
                "sets_low": kw.get("s"), "sets_high": kw.get("s"), "reps_low": kw.get("r"), "reps_high": kw.get("r")}
    mentions = {
        1: [m("Good Morning", s=3, r=8), m("good mornings")],     # same chunk twice → one chunk
        2: [m("Good Morning", s=4, r=6, purpose="hamstrings")],
        3: [m("Good Morning", s=3, r=10, purpose="hamstrings")],
        4: [m("Pendlay Row", "row")],
        5: [m("Hollow Body Hold", "core")],
    }
    sources = {1: [10], 2: [10, 11], 3: [12], 4: [10], 5: [13]}
    cands = {c["key"]: c for c in aggregate(mentions, sources, ["Pendlay Row", "Snatch"])}
    gm = cands["good morning"]
    assert gm["name"] == "Good Morning" and gm["movement_family"] == "hinge"
    assert gm["chunks"] == 3 and gm["sources"] == 3                     # sources 10, 11, 12
    assert gm["sets"] == [3, 3] and gm["reps"] == [8, 8] and gm["purpose"] == "hamstrings"
    assert gm["primary_source_id"] == 10 and not gm["in_catalogue"]
    assert cands["pendlay row"]["in_catalogue"] and cands["pendlay row"]["catalogue_name"] == "Pendlay Row"
    assert cands["hollow body hold"]["movement_family"] == "core"       # the model's family when the name is silent
    assert list(cands)[0] == "good morning"                             # most sources first


def test_insertable_needs_sources_absence_and_no_lift_name():
    base = {"sources": 6, "in_catalogue": False, "counts_as_lift": False}
    assert insertable([base])
    assert not insertable([{**base, "sources": 5}])
    assert insertable([{**base, "sources": 5}], min_sources=5)
    assert not insertable([{**base, "in_catalogue": True}])
    assert not insertable([{**base, "counts_as_lift": True}])


def test_lift_named_candidates_are_flagged():
    mentions = {1: [{"name": "Hang Snatch", "movement_family": "pull"}],
                2: [{"name": "Snatch Grip Shrug", "movement_family": "pull"}]}
    cands = {c["key"]: c for c in aggregate(mentions, {1: [1], 2: [1]}, [])}
    assert cands["hang snatch"]["counts_as_lift"]
    assert not cands["snatch grip shrug"]["counts_as_lift"]             # "shrug" marks it as non-lift
    assert cands["snatch grip shrug"]["movement_family"] == "pull"


def test_display_name_and_category():
    assert display_name(Counter({"good morning": 3, "Good Morning": 1})) == "Good Morning"
    assert display_name(Counter({"farmer's carry": 1})) == "Farmer's Carry"
    assert display_name(Counter({"RDL": 2})) == "RDL"
    assert display_name(Counter({"push-up on the box": 1})) == "Push-up on the Box"
    assert category_for("hinge") == "accessory" and category_for("press") == "strength"


def test_prompt_numbers_passages_and_caps_their_length():
    prompt = build_prompt([(1, "a" * 10_000), (2, "short")])
    assert "[1] " + "a" * 4000 + "\n\n[2] short" in prompt and "a" * 4001 not in prompt


def test_schema_follows_the_structured_output_rules():
    """STRUCT-1: additionalProperties false on every object, no numeric bounds,
    ≤ 24 optional properties summed over the schema."""
    optional = 0

    def walk(node):
        nonlocal optional
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                props = node.get("properties", {})
                optional += len(set(props) - set(node.get("required", [])))
            for bad in ("minimum", "maximum", "minLength", "pattern"):
                assert bad not in node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(MINE_SCHEMA)
    assert optional <= 24


def test_full_run_review_findings():
    """2026-11-05 full run: the lift guard flagged Lunge / Plank / Chin-up,
    synonyms missed six catalogue twins, and machine work was offered."""
    from mine_accessories import names_a_lift, needs_machine, reaggregate

    assert not names_a_lift("Lunge") and not names_a_lift("Plank") and not names_a_lift("Chin-up")
    assert names_a_lift("Hang Snatch") and names_a_lift("Power Clean")
    assert not names_a_lift("Snatch Grip Shrug") and not names_a_lift("Clean Pull")
    for twin, catalogue in (("Stiff-legged Deadlift", "Stiff-Leg Deadlift"),
                            ("Press Behind the Neck", "Behind the Neck Press"),
                            ("Overhead Press", "Strict Press"), ("Standing Press", "Strict Press"),
                            ("Bent Row", "Barbell Row"), ("Bent-over Rows", "Barbell Row"),
                            ("Incline Bench Press", "Incline Press"), ("Dead Lift", "Deadlift"),
                            ("Parallel Bar Dips", "Dips")):
        assert normalize_name(twin) == normalize_name(catalogue), twin
    for machine in ("Leg Press", "Leg Extension", "Leg Curl", "Lat Pulldown", "Cable Row", "Hack Squat"):
        assert needs_machine(machine), machine
    assert not needs_machine("Nordic Curl") and not needs_machine("Pull-Up")

    # one passage's numbers are not a typical prescription
    one = {1: [{"name": "Split Squat", "movement_family": "squat", "sets_low": 1, "sets_high": 1}]}
    assert aggregate(one, {1: [1]}, [])[0]["sets"] == [None, None]

    # a report re-aggregates from its stored mentions
    report = {"mentions": {"1": one[1]}, "chunk_sources": {"1": [7]}}
    (cand,) = reaggregate(report, ["Split Squat"])
    assert cand["in_catalogue"] and cand["primary_source_id"] == 7
