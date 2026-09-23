# oly-agent/complexes.py
"""
Exercise complexes (PLAN-3a): several lifts done back to back as one set —
e.g. Snatch ×1 + Overhead Squat ×2, prescribed "4 × (1+2) @ 75 % of the snatch".

A complex is a row in `exercise_complexes` (components in order with their
reps, total reps per set, the max it is loaded from, a typical intensity band).
The model prescribes it like an exercise — `exercise_name` = the complex name,
`sets` = complexes — and `annotate_complexes` attaches the definition after
parsing: `reps` becomes the complex's total, `complex_id` and a scheme note
("complex 1+2") are set for storage and display.

The validator judges a complex by its components, not by its name: the name
"Snatch + Overhead Squat" contains "squat" and "Clean Pull + Clean" contains
"pull", which `is_competition_lift` reads as non-competition, so a heavy
complex would otherwise escape the week's intensity ceiling. The reps that
count are the competition-lift components' (the Snatch in Snatch + OHS),
checked per set against Prilepin and summed into the session / weekly volume.
"""

from shared.exercise_mapping import is_competition_lift


def complex_scheme(components: list[dict]) -> str:
    """"1+2" for Snatch ×1 + Overhead Squat ×2."""
    return "+".join(str(int(c.get("reps") or 1)) for c in components)


def complex_line(cx: dict) -> str:
    """One prompt line: `name = A ×1 + B ×2 [ref, cN] lo–hi % | reps = total`."""
    parts = " + ".join(f"{c['exercise_name']} ×{int(c.get('reps') or 1)}" for c in cx["exercises_ordered"])
    band = ""
    if cx.get("typical_intensity_low") is not None and cx.get("typical_intensity_high") is not None:
        band = f" {float(cx['typical_intensity_low']):.0f}–{float(cx['typical_intensity_high']):.0f}%"
    return (f"  {cx['name']} = {parts} [{cx.get('intensity_reference') or '?'}, "
            f"c{cx.get('complexity_level', '?')}]{band} | reps = {cx['total_reps_per_set']}")


def annotate_complexes(exercises: list[dict], complexes: list[dict] | None) -> list[dict]:
    """Attach the complex definition to every prescribed exercise whose name is
    a complex: `complex`, `complex_id`, reps = the complex's total, the scheme
    in `notes`, and the complex's max as `intensity_reference` when the model
    left it blank. Exercises that aren't complexes are left alone."""
    by_name = {c["name"].lower(): c for c in (complexes or [])}
    for ex in exercises:
        cx = by_name.get(str(ex.get("exercise_name") or "").lower())
        if cx is None:
            continue
        ex["complex"] = cx
        ex["complex_id"] = cx.get("id")
        ex["exercise_id"] = None                        # a complex is not an exercises row
        ex["reps"] = int(cx["total_reps_per_set"])
        ex["notes"] = f"complex {complex_scheme(cx['exercises_ordered'])}"
        if not ex.get("intensity_reference") and cx.get("intensity_reference"):
            ex["intensity_reference"] = cx["intensity_reference"]
    return exercises


def comp_component_reps(ex: dict) -> list[int]:
    """Reps of the competition-lift components of a complex, in order (empty for
    a plain exercise or a complex with none, e.g. a pull + squat complex)."""
    cx = ex.get("complex")
    if not cx:
        return []
    ref = ex.get("intensity_reference") or cx.get("intensity_reference")
    return [int(c.get("reps") or 1) for c in cx["exercises_ordered"]
            if is_competition_lift(c.get("exercise_name"), ref)]


def counts_as_competition_lift(ex: dict) -> bool:
    """A plain competition lift, or a complex with a competition-lift component."""
    if ex.get("complex"):
        return bool(comp_component_reps(ex))
    return is_competition_lift(ex.get("exercise_name"), ex.get("intensity_reference"))


def reps_for_prilepin(ex: dict) -> int:
    """Reps per set that Prilepin's per-set limit applies to: the largest
    competition-lift component of a complex, else the exercise's reps."""
    comp = comp_component_reps(ex)
    if ex.get("complex"):
        return max(comp) if comp else 0
    return int(ex.get("reps") or 0)


def comp_reps_per_set(ex: dict) -> int:
    """Competition-lift reps one set contributes to session / weekly volume."""
    if ex.get("complex"):
        return sum(comp_component_reps(ex))
    return int(ex.get("reps") or 0)


def prescription_label(ex) -> str:
    """"4×3" for an exercise, "4×(1+2)" for a complex (its scheme lives in the
    stored note "complex 1+2") — the program page and the log form."""
    get = ex.get if hasattr(ex, "get") else (lambda k, d=None: getattr(ex, k, d))
    sets, reps, notes = get("sets"), get("reps"), get("notes") or ""
    if get("complex_id") and str(notes).startswith("complex "):
        return f"{sets}×({str(notes)[len('complex '):]})"
    return f"{sets}×{reps}"
