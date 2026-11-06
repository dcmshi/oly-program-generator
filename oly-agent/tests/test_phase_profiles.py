# oly-agent/tests/test_phase_profiles.py
"""
Tests for phase_profiles.py — build_weekly_targets().

No DB or API keys needed.

Run: python tests/test_phase_profiles.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from phase_profiles import PHASE_PROFILES, build_weekly_targets
from session_templates import SESSION_DISTRIBUTIONS, get_session_templates

# ── Helpers ───────────────────────────────────────────────────

def assert_eq(label, actual, expected):
    assert actual == expected, f"{label}: expected {expected!r}, got {actual!r}"


def assert_true(label, condition, msg=""):
    assert condition, f"{label}: {msg or 'assertion failed'}"


# ── Tests ─────────────────────────────────────────────────────

def test_default_duration_week_count():
    """Each phase returns the correct default number of weeks."""
    for phase, profile in PHASE_PROFILES.items():
        n = profile["default_weeks"]
        targets = build_weekly_targets(phase, n, "intermediate")
        assert_eq(f"{phase} week count", len(targets), n)


def test_week_numbers_sequential():
    """Week numbers are 1-indexed and sequential."""
    targets = build_weekly_targets("accumulation", 4, "intermediate")
    nums = [t["week_number"] for t in targets]
    expected = list(range(1, len(targets) + 1))
    assert_eq("week numbers sequential", nums, expected)


def test_deload_week_flagged():
    """The deload week has is_deload=True; all others are False."""
    for phase, profile in PHASE_PROFILES.items():
        deload_week = profile.get("deload_week")
        if deload_week is None:
            continue
        n = profile["default_weeks"]
        targets = build_weekly_targets(phase, n, "intermediate")
        deload_targets = [t for t in targets if t["is_deload"]]
        if len(deload_targets) != 1:
            raise AssertionError(f"{phase}: expected 1 deload week, got {len(deload_targets)}")
        if deload_targets[0]["week_number"] != n:
            raise AssertionError(f"{phase}: deload should be last week")


def test_intensification_no_deload():
    """Intensification has no deload week."""
    targets = build_weekly_targets("intensification", 4, "intermediate")
    deload = [t for t in targets if t["is_deload"]]
    assert_eq("intensification no deload", deload, [])


def test_extended_no_deload_phase_stays_deload_free():
    """A-L7: extending a no-deload profile (intensification, 4→6 weeks) must not
    fabricate a deload flag on the new last week."""
    targets = build_weekly_targets("intensification", 6, "intermediate")
    deload = [t for t in targets if t["is_deload"]]
    assert_eq("extended intensification no fabricated deload", deload, [])


def test_intensity_ceiling_never_exceeds_100():
    """intensity_ceiling is capped at 100% for all phases and levels."""
    for phase in PHASE_PROFILES:
        n = PHASE_PROFILES[phase]["default_weeks"]
        for level in ("beginner", "intermediate", "advanced", "elite"):
            targets = build_weekly_targets(phase, n, level)
            for t in targets:
                if t["intensity_ceiling"] > 100:
                    raise AssertionError(f"{phase}/{level} W{t['week_number']}: ceiling={t['intensity_ceiling']}")


def test_floor_always_below_ceiling():
    """intensity_floor is always strictly less than intensity_ceiling."""
    for phase in PHASE_PROFILES:
        n = PHASE_PROFILES[phase]["default_weeks"]
        targets = build_weekly_targets(phase, n, "intermediate")
        for t in targets:
            if t["intensity_floor"] >= t["intensity_ceiling"]:
                raise AssertionError(
                    f"{phase} W{t['week_number']}: "
                    f"floor={t['intensity_floor']} >= ceiling={t['intensity_ceiling']}"
                )


def test_level_beginner_lower_intensity():
    """Beginners get lower intensity than intermediate."""
    for phase in PHASE_PROFILES:
        n = PHASE_PROFILES[phase]["default_weeks"]
        inter = build_weekly_targets(phase, n, "intermediate")
        begin = build_weekly_targets(phase, n, "beginner")
        for i, e in zip(inter, begin, strict=True):
            if e["intensity_ceiling"] >= i["intensity_ceiling"]:
                raise AssertionError(f"{phase} W{i['week_number']}: beginner ceiling not lower than intermediate")


def test_level_elite_higher_intensity():
    """Elite get higher intensity than intermediate (up to cap)."""
    for phase in PHASE_PROFILES:
        n = PHASE_PROFILES[phase]["default_weeks"]
        inter = build_weekly_targets(phase, n, "intermediate")
        elite = build_weekly_targets(phase, n, "elite")
        for i, e in zip(inter, elite, strict=True):
            # Elite ceiling >= intermediate ceiling (may be equal at cap of 100)
            if e["intensity_ceiling"] < i["intensity_ceiling"]:
                raise AssertionError(f"{phase} W{i['week_number']}: elite ceiling lower than intermediate")


def test_volume_modifier_positive():
    """volume_modifier is always > 0."""
    for phase in PHASE_PROFILES:
        n = PHASE_PROFILES[phase]["default_weeks"]
        for level in ("beginner", "intermediate", "elite"):
            targets = build_weekly_targets(phase, n, level)
            for t in targets:
                if t["volume_modifier"] <= 0:
                    raise AssertionError(f"{phase}/{level} W{t['week_number']}: volume_modifier={t['volume_modifier']}")


def test_extended_duration_adds_weeks():
    """Requesting more weeks than default extends the program to the right length."""
    profile = PHASE_PROFILES["accumulation"]
    default_n = profile["default_weeks"]  # 4
    extended_n = default_n + 2            # 6
    targets = build_weekly_targets("accumulation", extended_n, "intermediate")
    assert_eq("extended week count", len(targets), extended_n)


def test_extended_duration_deload_last():
    """When extended, deload is still the final week."""
    targets = build_weekly_targets("accumulation", 6, "intermediate")
    last = targets[-1]
    assert_true("deload is last", last["is_deload"], f"last week is_deload={last['is_deload']}")


def test_shortened_duration_correct_count():
    """Requesting fewer weeks than default trims correctly."""
    targets = build_weekly_targets("accumulation", 3, "intermediate")
    assert_eq("shortened week count", len(targets), 3)


def test_shortened_duration_deload_last():
    """Even when shortened, the last week is the deload."""
    targets = build_weekly_targets("general_prep", 3, "intermediate")
    last = targets[-1]
    assert_true("deload last after trim", last["is_deload"])


def test_reps_per_set_range_is_pair():
    """reps_per_set_range is always a two-element list [low, high]."""
    for phase in PHASE_PROFILES:
        n = PHASE_PROFILES[phase]["default_weeks"]
        targets = build_weekly_targets(phase, n, "intermediate")
        for t in targets:
            r = t["reps_per_set_range"]
            if len(r) != 2 or r[0] > r[1]:
                raise AssertionError(f"{phase} W{t['week_number']}: bad reps_per_set_range {r}")


def test_realization_high_intensity():
    """Realization phase has peak intensity ≥ 90% for intermediate."""
    targets = build_weekly_targets("realization", 3, "intermediate")
    # Week 2 is the peak
    peak = targets[1]
    assert_true(
        "realization peak ≥ 90%",
        peak["intensity_ceiling"] >= 90,
        f"peak ceiling={peak['intensity_ceiling']}"
    )


# ── T9: get_session_templates — fallback branch (lines 142-143) ───────────────

def test_get_session_templates_exact_3():
    """sessions_per_week=3 returns 3-day templates directly (no fallback)."""
    templates = get_session_templates(3)
    assert_eq("3-day template count", len(templates), 3)


def test_get_session_templates_exact_4():
    """sessions_per_week=4 returns 4-day templates directly (no fallback)."""
    templates = get_session_templates(4)
    assert_eq("4-day template count", len(templates), 4)


def test_get_session_templates_exact_5():
    """sessions_per_week=5 returns 5-day templates directly (no fallback)."""
    templates = get_session_templates(5)
    assert_eq("5-day template count", len(templates), 5)


def test_get_session_templates_fallback_below_minimum():
    """sessions_per_week=1 is below all keys — falls back to 2 (closest, PLAN-2 layouts)."""
    templates = get_session_templates(1)
    assert_eq("fallback-to-2 count", len(templates), 2)


def test_get_session_templates_fallback_above_maximum():
    """sessions_per_week=7 is above all keys — falls back to 6 (closest, PLAN-2 layouts)."""
    templates = get_session_templates(7)
    assert_eq("fallback-to-6 count", len(templates), 6)


def test_get_session_templates_all_have_required_keys():
    """Every template dict has day_number, primary_movement, session_volume_share."""
    failures = []
    for n in SESSION_DISTRIBUTIONS:
        for tmpl in get_session_templates(n):
            for key in ("day_number", "primary_movement", "session_volume_share"):
                if key not in tmpl:
                    failures.append(f"{n}-day template missing key '{key}'")
    if failures:
        raise AssertionError("; ".join(failures))


# ── Runner ────────────────────────────────────────────────────

TESTS = [
    ("Default duration produces correct week count", test_default_duration_week_count),
    ("Week numbers are sequential from 1", test_week_numbers_sequential),
    ("Deload week is flagged is_deload=True (last week)", test_deload_week_flagged),
    ("Intensification has no deload week", test_intensification_no_deload),
    ("Extended no-deload phase stays deload-free (A-L7)", test_extended_no_deload_phase_stays_deload_free),
    ("intensity_ceiling never exceeds 100%", test_intensity_ceiling_never_exceeds_100),
    ("intensity_floor always < intensity_ceiling", test_floor_always_below_ceiling),
    ("Beginner intensity lower than intermediate", test_level_beginner_lower_intensity),
    ("Elite intensity >= intermediate (up to cap)", test_level_elite_higher_intensity),
    ("volume_modifier is always positive", test_volume_modifier_positive),
    ("Extended duration adds correct number of weeks", test_extended_duration_adds_weeks),
    ("Extended duration: deload is still last", test_extended_duration_deload_last),
    ("Shortened duration: correct week count", test_shortened_duration_correct_count),
    ("Shortened duration: deload still last", test_shortened_duration_deload_last),
    ("reps_per_set_range is always [low, high]", test_reps_per_set_range_is_pair),
    ("Realization peak intensity >= 90%", test_realization_high_intensity),
    # T9: get_session_templates
    ("get_session_templates: exact match 3", test_get_session_templates_exact_3),
    ("get_session_templates: exact match 4", test_get_session_templates_exact_4),
    ("get_session_templates: exact match 5", test_get_session_templates_exact_5),
    ("get_session_templates: fallback to 2 when sessions=1", test_get_session_templates_fallback_below_minimum),
    ("get_session_templates: fallback to 6 when sessions=7", test_get_session_templates_fallback_above_maximum),
    ("get_session_templates: all templates have required keys", test_get_session_templates_all_have_required_keys),
]


def main():
    failures = []
    results = []
    for name, fn in TESTS:
        try:
            fn()
            ok, msg = True, ""
            results.append((name, ok, msg))
            if not ok:
                failures.append(name)
        except Exception as e:
            results.append((name, False, str(e)))
            failures.append(name)

    print(f"\n{'='*50}")
    print("PHASE PROFILES — Test Results")
    print(f"{'='*50}")
    for name, ok, msg in results:
        status = "✓" if ok else "✗"
        print(f"  {status} {name}" + (f"\n      {msg}" if not ok else ""))

    total = len(TESTS)
    passed = total - len(failures)
    print(f"\n{passed}/{total} passed")
    if failures:
        print("\nFailed:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()


def test_session_layouts_cover_2_to_6_days_and_shares_sum_to_one():
    """PLAN-2 §2.1: 2- and 6-day layouts exist (no more snapping 6 → 5); every
    layout's volume shares sum to 1."""
    from session_templates import SESSION_DISTRIBUTIONS, get_session_templates
    assert set(SESSION_DISTRIBUTIONS) == {2, 3, 4, 5, 6}
    for n, dist in SESSION_DISTRIBUTIONS.items():
        assert len(dist["sessions"]) == n
        assert abs(sum(s["session_volume_share"] for s in dist["sessions"]) - 1.0) < 1e-6, n
    assert len(get_session_templates(6)) == 6
    assert len(get_session_templates(7)) == 6            # still snaps beyond the range


def test_lift_emphasis_flips_the_light_day():
    """PLAN-2 §2.2: a snatch-biased athlete on 4 days gets 3 snatch-primary
    sessions; balanced stays 2:2; the module-level layouts are not mutated."""
    from session_templates import SESSION_DISTRIBUTIONS, get_session_templates
    balanced = get_session_templates(4, "balanced")
    snatch = get_session_templates(4, "snatch_biased")
    cj = get_session_templates(4, "cj_biased")
    count = lambda ss, m: sum(1 for s in ss if s["primary_movement"] == m)  # noqa: E731
    assert (count(balanced, "snatch"), count(balanced, "clean")) == (2, 2)
    assert (count(snatch, "snatch"), count(snatch, "clean")) == (3, 1)
    assert (count(cj, "snatch"), count(cj, "clean")) == (1, 3)
    assert [s["session_volume_share"] for s in snatch] == [s["session_volume_share"] for s in balanced]
    assert SESSION_DISTRIBUTIONS[4]["sessions"][3]["primary_movement"] == "clean"   # untouched
    assert count(get_session_templates(3, "snatch_biased"), "snatch") == 2           # 3-day: no flip


def test_level_phase_overrides_cap_beginners_and_floor_advanced():
    """PLAN-2 §1.5–1.6: a beginner's realization ceiling is capped (Medvedev:
    no heavy singles/doubles) and reps widen; an advanced lifter's accumulation
    floor never drops below 70 % (Vorobyev); intermediates keep the profile."""
    from phase_profiles import build_weekly_targets
    beg = build_weekly_targets("realization", 3, "beginner")
    assert max(w["intensity_ceiling"] for w in beg) == 85
    assert all(w["reps_per_set_range"] == [1, 3] for w in beg)
    assert all(w["intensity_floor"] <= w["intensity_ceiling"] - 4 for w in beg)
    adv = build_weekly_targets("accumulation", 4, "advanced")
    assert all(w["intensity_floor"] >= 70 for w in adv if not w["is_deload"])
    assert build_weekly_targets("accumulation", 4, "elite")[2]["intensity_ceiling"] == 83   # 80 + 3, no cap
    mid = build_weekly_targets("intensification", 4, "intermediate")
    assert [w["intensity_ceiling"] for w in mid] == [83, 86, 90, 93]                        # unchanged profile
    assert build_weekly_targets("accumulation", 4, "unknown_level") == build_weekly_targets("accumulation", 4, "intermediate")


# ── DOG-2d: long blocks progress in waves instead of plateauing ──────────────

def _band(targets):
    return [(w["intensity_floor"], w["intensity_ceiling"]) for w in targets]


def test_stretched_block_ceilings_never_drop():
    """Longer than the profile, no deload cadence: the working weeks are the
    profile stretched by interpolation — ceilings (and floors) never go down
    across the working weeks, the profile's deload is the last week."""
    for phase in ("accumulation", "general_prep", "realization", "intensification"):
        base = len(PHASE_PROFILES[phase]["weeks"])
        for duration in range(base + 1, base + 9):
            for level in ("beginner", "intermediate", "advanced", "elite"):
                targets = build_weekly_targets(phase, duration, level)
                assert len(targets) == duration
                has_deload = PHASE_PROFILES[phase]["deload_week"] is not None
                working = targets[:-1] if has_deload else targets
                assert not any(w["is_deload"] for w in working), (phase, duration, level)
                assert targets[-1]["is_deload"] == has_deload                    # A-L7: none fabricated
                ceilings = [w["intensity_ceiling"] for w in working]
                floors = [w["intensity_floor"] for w in working]
                assert ceilings == sorted(ceilings), (phase, duration, level, ceilings)
                assert floors == sorted(floors), (phase, duration, level, floors)


def test_stretched_block_spans_the_profile_ramp():
    """The stretched ramp starts at profile week 1 and ends at the last working
    week — 8-week accumulation used to repeat weeks 2–3 three times."""
    targets = build_weekly_targets("accumulation", 8, "intermediate")
    assert _band(targets) == [(70, 76), (70, 77), (70, 77), (70, 78), (71, 79), (71, 79), (72, 80), (65, 73)]
    assert [w["volume_modifier"] for w in targets[:4]] == [0.85, 0.9, 0.95, 1.0]
    assert [w["week_number"] for w in targets if w["is_deload"]] == [8]


def test_general_prep_beginner_8_weeks_deload_every_4_runs_in_waves():
    """DOG-2d dogfood case (athlete 4, program 53): deloads at weeks 4 and 8,
    each wave a ramp, wave 2 resuming at or above wave 1 — not the old
    61-69 / 63-71 / 64-72 / deload / 64-72 / 66-72 / 64-72 saw-tooth."""
    targets = build_weekly_targets("general_prep", 8, "beginner", deload_every_weeks=4)
    assert [w["week_number"] for w in targets if w["is_deload"]] == [4, 8]
    wave1, wave2 = targets[0:3], targets[4:7]
    for wave in (wave1, wave2):
        assert [w["intensity_ceiling"] for w in wave] == sorted(w["intensity_ceiling"] for w in wave)
        assert [w["intensity_floor"] for w in wave] == sorted(w["intensity_floor"] for w in wave)
    assert wave2[0]["intensity_floor"] >= wave1[0]["intensity_floor"]
    assert wave2[0]["intensity_ceiling"] >= wave1[0]["intensity_ceiling"]
    assert all(w["intensity_ceiling"] <= 72 for w in targets)                   # beginner general_prep cap still applies
    assert _band(targets) == [(61, 69), (64, 72), (66, 72), (59, 66), (63, 71), (66, 72), (68, 72), (59, 66)]


def test_waves_step_up_by_the_constant():
    """Wave k sits k × BLOCK_WAVE_STEP_PCT above wave 1 where no cap bites;
    deloads copy the profile's deload week."""
    from shared.constants import BLOCK_WAVE_STEP_PCT
    targets = build_weekly_targets("accumulation", 12, "intermediate", deload_every_weeks=4)
    assert [w["week_number"] for w in targets if w["is_deload"]] == [4, 8, 12]
    waves = [targets[0:3], targets[4:7], targets[8:11]]
    for k, wave in enumerate(waves):
        for w, first in zip(wave, waves[0], strict=True):
            assert w["intensity_ceiling"] == first["intensity_ceiling"] + k * BLOCK_WAVE_STEP_PCT
    assert {_band([targets[i]])[0] for i in (3, 7, 11)} == {(65, 73)}


def test_wave_blocks_end_on_a_deload_and_never_double_one():
    """Every long block with a deload cadence has the right length, ends on a
    deload, and never puts two deloads back to back (a trailing wave with no
    loading week folds into the previous one); a trailing partial wave is
    interpolated like the others."""
    from shared.constants import DELOAD_EVERY_WEEKS_OPTIONS
    for phase in ("accumulation", "general_prep", "realization"):
        base = len(PHASE_PROFILES[phase]["weeks"])
        for duration in range(base + 1, 17):
            for every in DELOAD_EVERY_WEEKS_OPTIONS:
                targets = build_weekly_targets(phase, duration, "intermediate", deload_every_weeks=every)
                flags = [w["is_deload"] for w in targets]
                assert len(targets) == duration and flags[-1], (phase, duration, every)
                assert not any(a and b for a, b in zip(flags, flags[1:], strict=False)), (phase, duration, every, flags)
    partial = build_weekly_targets("accumulation", 7, "intermediate", deload_every_weeks=3)
    assert [w["week_number"] for w in partial if w["is_deload"]] == [3, 7]      # 7 ≡ 1 mod 3: no deload at 6
    assert _band(partial)[3:6] == [(70, 78), (72, 80), (74, 82)]


def test_extended_no_deload_phase_ignores_the_cadence():
    """A-L7 with a cadence: intensification has no deload week to copy, so a
    long block stays deload-free and keeps climbing."""
    targets = build_weekly_targets("intensification", 8, "intermediate", deload_every_weeks=4)
    assert not any(w["is_deload"] for w in targets)
    assert targets[-1]["intensity_ceiling"] == 93


def test_short_and_equal_blocks_are_unchanged():
    """DOG-2d only touches blocks longer than the profile."""
    acc = build_weekly_targets("accumulation", 4, "intermediate")
    assert [(w["intensity_floor"], w["intensity_ceiling"], w["volume_modifier"], w["is_deload"]) for w in acc] == [
        (70, 76, 0.85, False), (70, 78, 1.0, False), (72, 80, 1.0, False), (65, 73, 0.6, True)]
    trimmed = build_weekly_targets("general_prep", 3, "intermediate")
    assert [(w["intensity_floor"], w["intensity_ceiling"], w["is_deload"]) for w in trimmed] == [
        (68, 77, False), (70, 78, False), (63, 70, True)]
    cadence = build_weekly_targets("general_prep", 5, "intermediate", deload_every_weeks=3)
    assert [(w["intensity_floor"], w["intensity_ceiling"], w["is_deload"]) for w in cadence] == [
        (65, 73, False), (67, 75, False), (63, 70, True), (70, 78, False), (63, 70, True)]


def test_deload_style_none_on_long_blocks():
    """deload_style="none" drops the last week's deload flag (its loads stay the
    profile's deload week, as at the profile length); mid-block deloads keep
    theirs."""
    plain = build_weekly_targets("accumulation", 8, "intermediate", deload_style="none")
    assert len(plain) == 8 and not any(w["is_deload"] for w in plain)
    waves = build_weekly_targets("accumulation", 8, "intermediate", deload_every_weeks=4, deload_style="none")
    assert [w["week_number"] for w in waves if w["is_deload"]] == [4]
    assert not waves[-1]["is_deload"]
    assert waves[-1]["intensity_ceiling"] == 73


# ── DOG-2m: every day names its accessory families ───────────────────────────

def test_every_template_day_has_accessory_families_covering_the_week():
    allowed = {"hinge", "row", "press", "core", "carry"}
    for freq, dist in SESSION_DISTRIBUTIONS.items():
        week = set()
        for s in dist["sessions"]:
            fams = s["accessory_families"]
            assert fams and set(fams) <= allowed, (freq, s["day_number"])
            assert len(fams) == len(set(fams)) <= 2, (freq, s["day_number"])
            week |= set(fams)
        expected = {"hinge", "row", "press", "core"} | ({"carry"} if freq >= 3 else set())
        assert week == expected, freq
    # the emphasis flip replaces a day's accessories too
    flipped = {s["day_number"]: s for s in get_session_templates(4, "snatch_biased")}
    assert flipped[4]["accessory_families"] == ["press", "core"]


def test_session_template_section_names_the_accessories():
    from generate import _session_template_section
    from models import SessionTemplate

    tmpl = SessionTemplate(1, "Snatch + Squat", "snatch", ["squat"], 0.3, notes="",
                           accessory_families=["hinge", "core"])
    section = _session_template_section(tmpl, 12)
    assert "Accessories (after the main work, one exercise per family): hinge, core" in section
    assert "Session note:" not in section                                   # DOG-2n: empty line omitted
    old = SessionTemplate(1, "Snatch + Squat", "snatch", [], 0.3)          # a pre-DOG-2m resume snapshot
    section = _session_template_section(old, 12)
    assert "Accessories" not in section and "Supporting work" not in section
