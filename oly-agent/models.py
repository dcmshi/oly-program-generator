# oly-agent/models.py
"""
Data classes used throughout the agent pipeline.

Flow: AthleteContext -> ProgramPlan -> RetrievalContext
      -> GenerationResult -> ValidationResult -> ProgramOutcome
"""

from dataclasses import dataclass, field


@dataclass
class AthleteContext:
    """Full picture of where the athlete is right now. Output of ASSESS step."""
    athlete: dict                       # full athletes row
    level: str                          # beginner / intermediate / advanced / elite
    maxes: dict[str, float]             # {"snatch": 100.0, "clean_and_jerk": 125.0, ...}
                                        # keyed by intensity_reference, not exercise name
    active_goal: dict | None            # athlete_goals row, or None
    previous_program: dict | None       # last completed program summary
    recent_logs: list[dict]             # last 2 weeks of training_log_exercises rows
    technical_faults: list[str]         # from athletes.technical_faults
    injuries: list[str]                 # from athletes.injuries
    sessions_per_week: int
    weeks_to_competition: int | None    # calculated from goal.competition_date


@dataclass
class WeekTarget:
    """Volume and intensity targets for a single week of the program."""
    week_number: int
    volume_modifier: float              # 1.0 = baseline, 0.6 = deload
    intensity_floor: float              # min % for competition lifts
    intensity_ceiling: float            # max % for competition lifts
    total_competition_lift_reps: int    # Prilepin's target for the week
    reps_per_set_range: list[int]       # [min, max] reps per set for this week
    is_deload: bool


@dataclass
class SessionTemplate:
    """Shape of a single training session (what kind of work goes here)."""
    day_number: int
    label: str                          # e.g. "Snatch + Squat"
    primary_movement: str               # movement_family for the main lift
    secondary_movements: list[str]      # supporting work families
    session_volume_share: float         # fraction of weekly volume (e.g. 0.30)
    notes: str = ""


@dataclass
class ProgramPlan:
    """Shape of the program before exercises are generated. Output of PLAN step."""
    phase: str                          # training_phase enum value
    duration_weeks: int
    sessions_per_week: int
    deload_week: int | None             # which week is the deload?

    weekly_targets: list[WeekTarget]
    session_templates: list[SessionTemplate]

    active_principles: list[dict]       # programming_principles rows
    supporting_chunks: list[dict]       # knowledge_chunks that informed the plan

    # Cold-start constraints (populated by plan.py when no prior history)
    intensity_ceiling_override: float | None = None
    max_complexity: int = 5             # max exercise complexity level (1-5)


@dataclass
class RetrievalContext:
    """All knowledge gathered before generation. Output of RETRIEVE step."""
    fault_exercises: dict[str, list[dict]]      # fault -> matching exercises from DB
    template_references: list[dict]             # similar program_templates rows
    programming_rationale: list[dict]           # knowledge_chunks (periodization/rationale)
    fault_correction_chunks: list[dict]         # knowledge_chunks (fault_correction)
    available_substitutions: dict[str, list]    # exercise_name -> substitute exercises
    active_principles: list[dict]               # programming_principles constraints
    prilepin_targets: dict[str, dict]           # zone_key -> Prilepin data
    available_exercises: list[dict]             # exercises rows the LLM can select from


@dataclass
class GenerationResult:
    """Result of a single session generation attempt. Output of GENERATE step."""
    exercises: list[dict] | None        # parsed exercises, or None on failure
    raw_response: str
    input_tokens: int
    output_tokens: int
    status: str                         # success / parse_error / validation_error / failed
    error_message: str | None
    attempt_number: int


@dataclass
class ValidationResult:
    """Output of VALIDATE step for a single session."""
    is_valid: bool
    errors: list[str]                   # hard failures — must fix before saving
    warnings: list[str]                 # soft issues — worth surfacing to athlete
    session_comp_reps: dict             # zone -> reps prescribed in this session
                                        # caller accumulates for weekly tracking


@dataclass
class ProgramOutcome:
    """Computed when a program transitions to 'completed' status."""
    program_id: int
    athlete_id: int

    maxes_delta: dict[str, float]       # {"snatch": +3.0, "clean_and_jerk": +2.0}

    sessions_prescribed: int
    sessions_completed: int
    adherence_pct: float

    avg_rpe_deviation: float            # positive = harder than intended
    avg_make_rate: float                # across competition lifts (0.0-1.0)
    make_rate_by_lift: dict[str, float] = field(default_factory=dict)  # per intensity_reference
    phase_verdict: dict = field(default_factory=dict)  # phase progression explanation

    # Volume signals for next program
    avg_weekly_reps: float = 0.0
    rpe_trend: str = "stable"           # "ascending", "stable", "descending"
    make_rate_trend: str = "stable"

    athlete_feedback: str | None = None  # free text from training_logs
