# oly-ingestion/processors/rep_notation.py
"""
Soviet fraction notation → readable rep schemes (DOG-2r).

Roman's *The Training of the Weightlifter* writes a set as a fraction, as its
own footnote explains: "The first number in the numerator is the amount of
weight; the second (in parenthesis) is the percentage of the maximum …; the
number in the denominator represents the number of repetitions; the number to
the right of the fraction is the number of sets; and the number to the far
right … in parenthesis represents the maximum result in this exercise."

The PDF text layer keeps the fraction's geometry, so the chunks (and the
session prompt) held tables like

    32.5 (55)
    ——— 1
    4

— about half the chunk digits, dashes and whitespace (12831 was in 16 of
program 58's 24 prompts). `normalize_fraction_notation` rewrites the
layouts the extraction produces into `32.5 kg (55%) × 4 reps × 1 set`:

  vertical    `W (P)` / `——— S` / `R` — the sets sit beside the bar, the reps
              under it (pp. 47–50);
  horizontal  `label  W (P)  S    W (P)  S   (M)` / `—————  —————` / `R  R`
              — columns side by side (pp. 59–61); the bar line may be missing
              (p. 50), in which case the reps line must be indented;
  stacked     `W (P)` / `R` / `S` — the bar was lost and the reps come first
              (p. 59: "Front squat + push jerk 140 (70) / 2+2 / 1", where
              2+2 can only be reps). Three bare lines of numbers are too
              common to trust alone, so this layout is converted only in text
              that also holds a barred or column fraction.

A run of sets becomes one line joined by "; ", and the exercise max that
closes a variant — `(60)*;`, `(110).` or a bare `100;` — becomes
`(max 60 kg)`. A bare number ending in "." is a page number, not a max.
Text with no fraction comes back unchanged.
"""

import re

_NUM = r"\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?"     # 77.5 · 102.5-105
_SETS = r"\d+(?:-\d+)?"                          # 1 · 1-2
_REPS = r"\d+(?:[+-]\d+)*(?:,[ \t]*\d+)*"        # 4 · 1+2+1 · 4, 5, 2, 4
_REPS_TOKEN = r"\d+(?:[+-]\d+)*"
_ENTRY_END = rf"× {_SETS} sets?"

_VERTICAL = re.compile(
    rf"^[ \t]*(?P<w>{_NUM})[ \t]*\((?P<p>{_NUM})\)[ \t]*\n"
    rf"[ \t]*—{{3,}}[ \t]*(?P<s>{_SETS})[ \t]*\n"
    rf"[ \t]*(?P<r>{_REPS})[ \t]*$",
    re.M,
)
_STACKED = re.compile(
    rf"^[ \t]*(?P<w>{_NUM})[ \t]*\((?P<p>{_NUM})\)[ \t]*\n"
    rf"[ \t]*(?P<r>{_REPS})[ \t]*\n"
    rf"[ \t]*(?P<s>{_SETS})[ \t]*$",
    re.M,
)
# the max that closes a variant, on its own line after the last set: (60)*;  (110).  100;
_MAX_AFTER = re.compile(
    rf"(?P<end>{_ENTRY_END})[ \t]*\n(?:[ \t]*\n)*[ \t]*"
    rf"(?:\((?P<m>{_NUM})\)(?P<star>\*?)|(?P<bare>\d+(?:\.\d+)?)(?=[ \t]*;))"
    rf"(?P<tail>[;.,]?)[ \t]*$",
    re.M,
)
_RUN_JOIN = re.compile(rf"(?P<end>{_ENTRY_END})[ \t]*\n(?:[ \t]*\n)*(?=[ \t]*{_NUM} kg \()")

_COLUMN = re.compile(rf"(?<![\d.])(?P<w>{_NUM})[ \t]*\((?P<p>{_NUM})\)[ \t]+(?P<s>{_SETS})(?![\d.(])")
_BAR_LINE = re.compile(r"^[ \t]*—{3,}(?:[ \t]+—{3,})*[ \t]*$")
_REPS_LINE = re.compile(rf"^[ \t]*{_REPS_TOKEN}(?:[ \t]+{_REPS_TOKEN})*[ \t]*$")
_MAX_INLINE = re.compile(rf"(?P<end>{_ENTRY_END})(?P<gap>[ \t]+)\((?P<m>{_NUM})\)")


def scheme(weight: str, pct: str, reps: str, sets: str) -> str:
    """`32.5 kg (55%) × 4 reps × 1 set`."""
    rep_word = "rep" if reps == "1" else "reps"
    set_word = "set" if sets == "1" else "sets"
    return f"{weight} kg ({pct}%) × {reps} {rep_word} × {sets} {set_word}"


def _sub_sets(pattern: re.Pattern, text: str) -> str:
    return pattern.sub(lambda m: scheme(m["w"], m["p"], " ".join(m["r"].split()), m["s"]), text)


def _max(m: re.Match) -> str:
    if m["m"] is not None:
        return f"{m['end']} (max {m['m']} kg){m['star']}{m['tail']}"
    return f"{m['end']} (max {m['bare']} kg){m['tail']}"


def _horizontal(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        cols = list(_COLUMN.finditer(line))
        if cols:
            j = i + 1
            has_bar = j < len(lines) and bool(_BAR_LINE.match(lines[j]))
            if has_bar:
                j += 1
            if j < len(lines) and _REPS_LINE.match(lines[j]) and (has_bar or lines[j][:1] in (" ", "\t")):
                reps = lines[j].split()
                bars = lines[i + 1].split() if has_bar else reps
                if len(reps) == len(cols) == len(bars):
                    rebuilt, pos = [], 0
                    for col, r in zip(cols, reps, strict=True):
                        between = line[pos:col.start()]
                        rebuilt.append("; " if pos and not between.strip() else between)
                        rebuilt.append(scheme(col["w"], col["p"], r, col["s"]))
                        pos = col.end()
                    rebuilt.append(line[pos:])
                    new = _MAX_INLINE.sub(lambda m: f"{m['end']} (max {m['m']} kg)", "".join(rebuilt))
                    out.append(re.sub(r"[ \t]{2,}", "  ", new.strip()))
                    i = j + 1
                    continue
        out.append(line)
        i += 1
    return "\n".join(out)


def normalize_fraction_notation(text: str) -> str:
    """Rewrite fraction-notation sets as `W kg (P%) × R reps × S sets` (module docstring)."""
    if not text or (not _COLUMN.search(text) and "———" not in text):
        return text
    out = _sub_sets(_VERTICAL, _horizontal(text))
    if out == text:
        return text                                   # no fraction here: leave stacked numbers alone
    out = _sub_sets(_STACKED, out)
    out = _MAX_AFTER.sub(_max, out)
    return _RUN_JOIN.sub(lambda m: f"{m['end']}; ", out)
