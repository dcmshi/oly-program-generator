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

from phase_profiles import build_weekly_targets, PHASE_PROFILES


# ── Helpers ───────────────────────────────────────────────────

def assert_eq(label, actual, expected):
    ok = actual == expected
    if not ok:
        return False, f"expected {expected!r}, got {actual!r}"
    return True, ""


def assert_true(label, condition, msg=""):
    if not condition:
        return False, msg or f"assertion failed"
    return True, ""


# ── Tests ─────────────────────────────────────────────────────

def test_default_duration_week_count():
    """Each phase returns the correct default number of weeks."""
    results = []
    for phase, profile in PHASE_PROFILES.items():
        n = profile["default_weeks"]
        targets = build_weekly_targets(phase, n, "intermediate")
        results.append(assert_eq(f"{phase} week count", len(targets), n))
    failures = [m for ok, m in results if not ok]
    if failures:
        return False, "; ".join(failures)
    return True, ""


def test_week_numbers_sequential():
    """Week numbers are 1-indexed and sequential."""
    targets = build_weekly_targets("accumulation", 4, "intermediate")
    nums = [t["week_number"] for t in targets]
    expected = list(range(1, len(targets) + 1))
    return assert_eq("week numbers sequential", nums, expected)


def test_deload_week_flagged():
    """The deload week has is_deload=True; all others are False."""
    for phase, profile in PHASE_PROFILES.items():
        deload_week = profile.get("deload_week")
        if deload_week is None:
            continue
        n = profile["default_weeks"]
        targets = build_weekly_targets(phase, n, "intermediate")
        deload_targets = [t for t in targets if t["is_deload"]]
        non_deload_targets = [t for t in targets if not t["is_deload"]]
        if len(deload_targets) != 1:
            return False, f"{phase}: expected 1 deload week, got {len(deload_targets)}"
        if deload_targets[0]["week_number"] != n:
            return False, f"{phase}: deload should be last week"
    return True, ""


def test_intensification_no_deload():
    """Intensification has no deload week."""
    targets = build_weekly_targets("intensification", 4, "intermediate")
    deload = [t for t in targets if t["is_deload"]]
    return assert_eq("intensification no deload", deload, [])


def test_intensity_ceiling_never_exceeds_100():
    """intensity_ceiling is capped at 100% for all phases and levels."""
    for phase in PHASE_PROFILES:
        n = PHASE_PROFILES[phase]["default_weeks"]
        for level in ("beginner", "intermediate", "advanced", "elite"):
            targets = build_weekly_targets(phase, n, level)
            for t in targets:
                if t["intensity_ceiling"] > 100:
                    return False, f"{phase}/{level} W{t['week_number']}: ceiling={t['intensity_ceiling']}"
    return True, ""


def test_floor_always_below_ceiling():
    """intensity_floor is always strictly less than intensity_ceiling."""
    for phase in PHASE_PROFILES:
        n = PHASE_PROFILES[phase]["default_weeks"]
        targets = build_weekly_targets(phase, n, "intermediate")
        for t in targets:
            if t["intensity_floor"] >= t["intensity_ceiling"]:
                return False, (
                    f"{phase} W{t['week_number']}: "
                    f"floor={t['intensity_floor']} >= ceiling={t['intensity_ceiling']}"
                )
    return True, ""


def test_level_beginner_lower_intensity():
    """Beginners get lower intensity than intermediate."""
    for phase in PHASE_PROFILES:
        n = PHASE_PROFILES[phase]["default_weeks"]
        inter = build_weekly_targets(phase, n, "intermediate")
        begin = build_weekly_targets(phase, n, "beginner")
        for i, e in zip(inter, begin):
            if e["intensity_ceiling"] >= i["intensity_ceiling"]:
                return False, f"{phase} W{i['week_number']}: beginner ceiling not lower than intermediate"
    return True, ""


def test_level_elite_higher_intensity():
    """Elite get higher intensity than intermediate (up to cap)."""
    for phase in PHASE_PROFILES:
        n = PHASE_PROFILES[phase]["default_weeks"]
        inter = build_weekly_targets(phase, n, "intermediate")
        elite = build_weekly_targets(phase, n, "elite")
        for i, e in zip(inter, elite):
            # Elite ceiling >= intermediate ceiling (may be equal at cap of 100)
            if e["intensity_ceiling"] < i["intensity_ceiling"]:
                return False, f"{phase} W{i['week_number']}: elite ceiling lower than intermediate"
    return True, ""


def test_volume_modifier_positive():
    """volume_modifier is always > 0."""
    for phase in PHASE_PROFILES:
        n = PHASE_PROFILES[phase]["default_weeks"]
        for level in ("beginner", "intermediate", "elite"):
            targets = build_weekly_targets(phase, n, level)
            for t in targets:
                if t["volume_modifier"] <= 0:
                    return False, f"{phase}/{level} W{t['week_number']}: volume_modifier={t['volume_modifier']}"
    return True, ""


def test_extended_duration_adds_weeks():
    """Requesting more weeks than default extends the program to the right length."""
    profile = PHASE_PROFILES["accumulation"]
    default_n = profile["default_weeks"]  # 4
    extended_n = default_n + 2            # 6
    targets = build_weekly_targets("accumulation", extended_n, "intermediate")
    return assert_eq("extended week count", len(targets), extended_n)


def test_extended_duration_deload_last():
    """When extended, deload is still the final week."""
    targets = build_weekly_targets("accumulation", 6, "intermediate")
    last = targets[-1]
    return assert_true("deload is last", last["is_deload"], f"last week is_deload={last['is_deload']}")


def test_shortened_duration_correct_count():
    """Requesting fewer weeks than default trims correctly."""
    targets = build_weekly_targets("accumulation", 3, "intermediate")
    return assert_eq("shortened week count", len(targets), 3)


def test_shortened_duration_deload_last():
    """Even when shortened, the last week is the deload."""
    targets = build_weekly_targets("general_prep", 3, "intermediate")
    last = targets[-1]
    return assert_true("deload last after trim", last["is_deload"])


def test_reps_per_set_range_is_pair():
    """reps_per_set_range is always a two-element list [low, high]."""
    for phase in PHASE_PROFILES:
        n = PHASE_PROFILES[phase]["default_weeks"]
        targets = build_weekly_targets(phase, n, "intermediate")
        for t in targets:
            r = t["reps_per_set_range"]
            if len(r) != 2 or r[0] > r[1]:
                return False, f"{phase} W{t['week_number']}: bad reps_per_set_range {r}"
    return True, ""


def test_realization_high_intensity():
    """Realization phase has peak intensity ≥ 90% for intermediate."""
    targets = build_weekly_targets("realization", 3, "intermediate")
    # Week 2 is the peak
    peak = targets[1]
    return assert_true(
        "realization peak ≥ 90%",
        peak["intensity_ceiling"] >= 90,
        f"peak ceiling={peak['intensity_ceiling']}"
    )


# ── Runner ────────────────────────────────────────────────────

TESTS = [
    ("Default duration produces correct week count", test_default_duration_week_count),
    ("Week numbers are sequential from 1", test_week_numbers_sequential),
    ("Deload week is flagged is_deload=True (last week)", test_deload_week_flagged),
    ("Intensification has no deload week", test_intensification_no_deload),
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
]


def main():
    failures = []
    results = []
    for name, fn in TESTS:
        try:
            ok, msg = fn()
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
        print(f"\nFailed:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
