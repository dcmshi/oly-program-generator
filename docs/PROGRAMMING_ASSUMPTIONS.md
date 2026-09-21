# Hard-coded programming assumptions

An audit (2026-09-21) of every place the agent decides *what a program looks like*
from a constant rather than from the athlete, the corpus or a setting. The retrieval
side (which chunks and principles the model sees) is data-driven; the *skeleton* the
model fills in is not. Each row says where the assumption lives, what it fixes, and
the lever that would make it flexible. TODO items: PLAN-1 (block length) and PLAN-2
(the rest).

Legend for "Lever": **A** = athlete/goal field or generate-form option, **C** = derive
from the corpus (principles / templates), **S** = setting or constant already
exists, just not wired.

## 1. Block structure

| # | Assumption | Where | Fixed value | Lever |
|---|---|---|---|---|
| 1.1 | Block length is the phase default | `plan._select_phase_and_duration`, `phase_profiles.PHASE_PROFILES[*].default_weeks` | accumulation 4 · intensification 4 · realization 3 · general_prep 5; comp date → 4/4/≤3; cold start ≤ 4 | **A** `duration_weeks` on form/CLI, level-aware defaults (PLAN-1) |
| 1.2 | One phase per program | `plan.py` returns one phase; multi-block only via repeated generations | — | **A** "plan to a date" mode chaining blocks (`weeks_to_competition` already computed) |
| 1.3 | Phase order is fixed | `phase_progression.PHASE_SEQUENCE` | general_prep → accumulation → intensification → realization → (back to) accumulation | **C/A** allow skipping (advanced athlete straight to intensification), or a "repeat with more volume" branch |
| 1.4 | Deload is always the last week of accumulation / general_prep / realization, never inside intensification | `PHASE_PROFILES[*].deload_week` | 4 / 5 / 3 / none | **A** deload cadence per athlete (every 3rd vs 4th week; masters more often) |
| 1.5 | Week-by-week intensity/volume curve | `PHASE_PROFILES[*].weeks` | e.g. accumulation 68–76 → 70–78 → 72–80 → 65–73 %, volume ×0.85/1.0/1.0/0.6 | **C** these are the numbers Roman / Bompa / Medvedev tabulate by level — the corpus now has them; a `level × phase` lookup extracted from principles would replace the single table |
| 1.6 | Level only shifts intensity ±4 % and volume ±10 % | `phase_profiles._LEVEL_ADJUSTMENTS` | beginner −4/×1.10 · intermediate 0/×1.0 · advanced +2/×0.95 · elite +3/×0.90 | **C** same as 1.5; Soviet tables differ by *class*, not by a flat offset |
| 1.7 | Advancement thresholds | `shared/constants.py` `ADVANCE_*`, `EXCELLENT_*` | adherence ≥ 70 %, make rate ≥ 75 %, RPE deviation ≤ 1.5; "excellent" ≥ 90 % / 0.85 | **S** per-athlete overrides (a masters lifter at 70 % make rate is fine) |
| 1.8 | Outcome nudges are fixed steps | `plan._apply_outcome_adjustments` | volume −10 % (adherence), ceiling −3 % (make rate), volume −5 % (RPE), ceiling +2 % (excellent) | **S** proportional to the miss instead of steps |

## 2. Weekly layout

| # | Assumption | Where | Fixed value | Lever |
|---|---|---|---|---|
| 2.1 | Only 3, 4 or 5 sessions/week exist | `session_templates.SESSION_DISTRIBUTIONS`; other counts snap to the closest | 6-day athletes get the 5-day plan; 2-day athletes get 3 | **A/C** add 2 and 6, and let `program_templates` (39 parsed programs, Roman/Takano/Zhekov) supply layouts |
| 2.2 | Each day has one primary lift and a fixed label/secondary list | same | e.g. 4-day: Sn+Sq, C&J+Pull, Sn variations, C&J+Sq | **A** `lift_emphasis` is read by the prompt but not by the template picker — a "snatch-weak" athlete should get a 2:1 snatch split |
| 2.3 | Session volume shares are fixed per layout | `session_volume_share` per day (e.g. 0.30/0.30/0.20/0.20) | — | **S** derive from `sessions_per_week` and emphasis |
| 2.4 | Strength/accessory work is whatever the model chooses | prompt only; no squat/pull progression curve | squats follow the comp-lift band via `intensity_reference` | **C** separate strength curve (Bompa MxS phases, Roman's squat tables) |

## 3. Session content

| # | Assumption | Where | Fixed value | Lever |
|---|---|---|---|---|
| 3.1 | Volume is Prilepin per session × share × modifier | `shared/prilepin.compute_session_rep_target`, `PRILEPIN_ZONES` (incl. the invented 65–70 band) | optimal 24/20/18/15/7 reps by zone; hard cap ×1.5 | **C/A** Prilepin is one of several tables in the corpus now (Roman's zones, Medvedev's monthly totals); make the table selectable, and scale by bodyweight class / level |
| 3.2 | Reps per set are the Prilepin range, validator-enforced as an error | `validate.py` check 3 | e.g. 90–100 % → 1–2 reps | **S** allow complexes / cluster sets (2+1) as an exception the validator understands |
| 3.3 | Intensity ceiling is a hard error, floor a warning | `validate.py` check 2 | — | fine; but cold start caps ceiling at 80 % (75 % beginner) regardless of history — **A** skip the cap when maxes are recent and logged |
| 3.4 | Warm-ups are always 2–3 sets at 50–60 % | `generate.py` rules | — | **A** athletes who warm up on their own; **S** constant |
| 3.5 | Deload week: ≤ 3 sets × 3 reps on comp lifts | `generate.py` rules | — | **S** deload style (volume vs intensity deload) per athlete |
| 3.6 | Session duration | `DEFAULT_SESSION_DURATION_MINUTES = 90`, `SECONDS_PER_SET = 30`, `DEFAULT_REST_SECONDS = 90` | — | **A** available minutes per session is an athlete field in spirit but only the default is used |
| 3.7 | Exercise complexity cap | `plan.py` | cold start beginner 2, others 3, else 5 | **S** fine, but expose |
| 3.8 | Max test only in intensification / realization | `PHASE_PROFILES[*].includes_max_test` | — | **A** "test at end of block" toggle |
| 3.9 | Competition lifts are snatch / clean / jerk / C&J and their variants | `shared/exercise_mapping.COMP_LIFT_REFS` | — | fine for this sport; note for masters/para variants |

## 4. What is *not* hard-coded (for contrast)

Exercise selection, rationale, substitutions, fault-driven accessory choice, per-set
loading inside the band, and the citations all come from retrieval + the model; the
principle block (≤ `MAX_PRINCIPLES_IN_PROMPT` = 8) is selected per session from the
corpus. So the corpus already *could* drive most of §1–§3; the skeleton just doesn't
ask it to.

## Suggested order

1. PLAN-1 (block length) — smallest change, most user-visible.
2. 2.1 + 2.2 — layouts from `program_templates` and `lift_emphasis`; the 39 parsed
   templates (Roman ×3, Takano ×18, Zhekov ×2, Kono ×1, …) are unused by the planner.
3. 1.5 + 1.6 + 3.1 — replace the single phase table with a `level × phase` lookup mined
   from principles (Roman/Bompa/Vorobyev/Medvedev now give real numbers per class).
4. 1.4, 1.7, 3.4–3.6 — athlete preferences.
