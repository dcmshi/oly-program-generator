# shared/prilepin.py
"""
Prilepin's chart lookup functions.
Used by the PLAN step (setting rep targets) and VALIDATE step (checking compliance).
"""

# Prilepin's zones — mirrors the prilepin_chart table for in-memory use.
# Loaded from DB at startup; hardcoded here as a reliable fallback.
PRILEPIN_ZONES = [
    {"zone": "55-65",  "low": 55,  "high": 65,  "reps_per_set": (3, 6), "optimal": 24, "range": (18, 30)},
    # 65-70% is a transition zone not explicitly in Prilepin's original table;
    # treat it as a lighter accumulation band with slightly lower volume than 55-65.
    {"zone": "65-70",  "low": 65,  "high": 70,  "reps_per_set": (3, 6), "optimal": 20, "range": (15, 26)},
    {"zone": "70-80",  "low": 70,  "high": 80,  "reps_per_set": (3, 6), "optimal": 18, "range": (12, 24)},
    {"zone": "80-90",  "low": 80,  "high": 90,  "reps_per_set": (2, 4), "optimal": 15, "range": (10, 20)},
    {"zone": "90-100", "low": 90,  "high": 100, "reps_per_set": (1, 2), "optimal": 7,  "range": (4, 10)},
]


def get_prilepin_zone(intensity_pct: float) -> str | None:
    """Map an intensity percentage to its Prilepin zone string.

    Returns the zone key (e.g., "80-90") or None if below 55%.
    Intensities above 100% (pulls) map to the 90-100 zone.

    Examples:
        get_prilepin_zone(67)  -> "65-70"
        get_prilepin_zone(73)  -> "70-80"
        get_prilepin_zone(85)  -> "80-90"
        get_prilepin_zone(95)  -> "90-100"
        get_prilepin_zone(105) -> "90-100"  (supramaximal pulls)
        get_prilepin_zone(50)  -> None       (below chart)
    """
    if intensity_pct > 100:
        return "90-100"
    for zone in PRILEPIN_ZONES:
        if zone["low"] <= intensity_pct <= zone["high"]:
            return zone["zone"]
    return None  # below 55%


def get_prilepin_data(zone_key: str) -> dict | None:
    """Get full Prilepin data for a zone.

    Returns dict with: reps_per_set_low, reps_per_set_high,
    optimal_total_reps, total_reps_range_low, total_reps_range_high
    """
    for zone in PRILEPIN_ZONES:
        if zone["zone"] == zone_key:
            return {
                "reps_per_set_low": zone["reps_per_set"][0],
                "reps_per_set_high": zone["reps_per_set"][1],
                "optimal_total_reps": zone["optimal"],
                "total_reps_range_low": zone["range"][0],
                "total_reps_range_high": zone["range"][1],
            }
    return None


def compute_session_rep_target(
    intensity_floor: float,
    intensity_ceiling: float,
    session_volume_share: float,
    volume_modifier: float = 1.0,
) -> int:
    """Compute a target rep count for competition lifts in a single session.

    Uses the midpoint of the intensity range to find the Prilepin zone,
    then scales the optimal total by session share and volume modifier.

    Example:
        intensity 70-80%, session_volume_share=0.30, volume_modifier=1.0
        -> zone "70-80", optimal=18, target = 18 x 0.30 x 1.0 = 5 reps
    """
    midpoint = (intensity_floor + intensity_ceiling) / 2
    zone_key = get_prilepin_zone(midpoint)
    if not zone_key:
        # Below 55%: use the 55-65 zone as proxy so deload rep targets stay proportional.
        fallback_optimal = 24  # 55-65% optimal
        return max(3, round(fallback_optimal * session_volume_share * volume_modifier))

    zone_data = get_prilepin_data(zone_key)
    if not zone_data:
        return 6

    raw_target = zone_data["optimal_total_reps"] * session_volume_share * volume_modifier
    return max(3, round(raw_target))  # minimum 3 reps to be meaningful
