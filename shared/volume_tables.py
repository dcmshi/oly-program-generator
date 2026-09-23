# shared/volume_tables.py
"""
Selectable competition-lift volume tables (PLAN-3d, assumption 3.1).

`prilepin` (the default) is Prilepin's chart: a per-session optimal number of
competition-lift reps for the zone the session trains in (shared/prilepin.py).

`medvedev` sizes the week from A.S. Medvedyev's *A System of Multi-Year
Training in Weightlifting* instead, using only numbers the corpus states
(Cissik's summary with its tables transcribed, docs/CORPUS.md #28):

  * average monthly lifts (NL) — beginner 650 in both periods; qualified
    athlete 1,500 preparatory / 850 competition; by class (monthly averages):
    Class I 1,094, CMS 1,169, MS 1,306;
  * competition-lift share — "Snatch: 20–30 %, Clean and jerk: 20–30 %" of the
    volume by exercise category → the midpoints, 0.25 + 0.25 = 0.50;
  * the share of lifts by intensity zone — preparatory 25 / 30 / 40 / 5 %,
    competition 20 / 25 / 42 / 13 % (< 70, 70–75, 80–85, > 90, bands as
    printed), shown to the model as the week's distribution.

Mapping to this app's levels (the one derived step): beginner = Medvedev's
beginner row; elite = the qualified-athlete row; intermediate / advanced = the
Class I / CMS monthly averages for the preparatory period, scaled by the
qualified athlete's competition / preparatory ratio (850 / 1,500) for the
competition period. general_prep / accumulation are the preparatory period,
intensification / realization the competition period.

weekly comp-lift reps = monthly NL × 0.50 / WEEKS_PER_MONTH × volume_modifier
session target        = weekly × session_volume_share
"""

from shared.constants import MIN_SESSION_REPS
from shared.prilepin import compute_session_rep_target

VOLUME_TABLES: tuple[str, ...] = ("prilepin", "medvedev")
DEFAULT_VOLUME_TABLE = "prilepin"

WEEKS_PER_MONTH = 52 / 12
MEDVEDEV_COMP_LIFT_SHARE = 0.25 + 0.25
_QUALIFIED_COMP_TO_PREP = 850 / 1500
# level → (preparatory, competition) monthly NL
MEDVEDEV_MONTHLY_NL: dict[str, tuple[float, float]] = {
    "beginner": (650, 650),
    "intermediate": (1094, 1094 * _QUALIFIED_COMP_TO_PREP),
    "advanced": (1169, 1169 * _QUALIFIED_COMP_TO_PREP),
    "elite": (1500, 850),
}
MEDVEDEV_ZONE_DISTRIBUTION: dict[str, tuple[tuple[str, int], ...]] = {
    "preparatory": (("<70%", 25), ("70–75%", 30), ("80–85%", 40), (">90%", 5)),
    "competition": (("<70%", 20), ("70–75%", 25), ("80–85%", 42), (">90%", 13)),
}
_COMPETITION_PHASES = frozenset({"intensification", "realization"})


def medvedev_period(phase: str) -> str:
    return "competition" if phase in _COMPETITION_PHASES else "preparatory"


def medvedev_weekly_comp_reps(level: str, phase: str, volume_modifier: float = 1.0) -> int:
    """This week's competition-lift reps under the Medvedev table."""
    prep, comp = MEDVEDEV_MONTHLY_NL.get(level, MEDVEDEV_MONTHLY_NL["intermediate"])
    monthly = comp if medvedev_period(phase) == "competition" else prep
    return round(monthly * MEDVEDEV_COMP_LIFT_SHARE / WEEKS_PER_MONTH * volume_modifier)


def session_rep_target(table: str, *, intensity_floor: float, intensity_ceiling: float,
                       session_volume_share: float, volume_modifier: float = 1.0,
                       sessions_per_week: int = 1, level: str = "intermediate",
                       phase: str = "accumulation") -> int:
    """Competition-lift reps for one session under `table` (unknown → Prilepin)."""
    if table == "medvedev":
        weekly = medvedev_weekly_comp_reps(level, phase, volume_modifier)
        return max(MIN_SESSION_REPS, round(weekly * session_volume_share))
    return compute_session_rep_target(intensity_floor, intensity_ceiling, session_volume_share,
                                      volume_modifier, sessions_per_week)


def medvedev_distribution_lines(phase: str) -> list[str]:
    """The zone distribution the prompt shows under the Medvedev table."""
    period = medvedev_period(phase)
    parts = ", ".join(f"{share} % at {zone}" for zone, share in MEDVEDEV_ZONE_DISTRIBUTION[period])
    return [f"  Medvedyev ({period} period): spread this week's competition-lift reps as {parts}",
            "  Reps per set still follow Prilepin's limits (≤ 2 at ≥ 90 %, ≤ 4 at 80–90 %)."]
