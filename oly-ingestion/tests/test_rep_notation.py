"""processors.rep_notation — Roman's fraction notation → readable rep schemes (DOG-2r).

The samples are the extraction's own layouts, from chunks 12831 / 12833 /
12844 / 12845 (The Training of the Weightlifter, pp. 47–61)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from processors.rep_notation import normalize_fraction_notation, scheme

VERTICAL = """For Beginners:

32.5 (55)
——— 1
4

40 (65)
——— 1
4

45 (75)
——— 5
3

(60)*;

30 (50)
——— 1
3"""


def test_vertical_sets_beside_the_bar_reps_under_it():
    out = normalize_fraction_notation(VERTICAL)
    assert out == ("For Beginners:\n\n"
                   "32.5 kg (55%) × 4 reps × 1 set; 40 kg (65%) × 4 reps × 1 set; "
                   "45 kg (75%) × 3 reps × 5 sets (max 60 kg)*;\n\n"
                   "30 kg (50%) × 3 reps × 1 set")


def test_vertical_ranges_lists_and_a_bare_max():
    text = ("102.5-105 (102.5-105)\n——————————— 1-2\n1\n\n100;\n\n"
            "77.5 (70)\n——————————— 1\n4, 5, 2, 4, 1, 5\n\n(110).")
    out = normalize_fraction_notation(text)
    assert out == ("102.5-105 kg (102.5-105%) × 1 rep × 1-2 sets (max 100 kg);\n\n"
                   "77.5 kg (70%) × 4, 5, 2, 4, 1, 5 reps × 1 set (max 110 kg).")


def test_a_page_number_is_not_a_max():
    out = normalize_fraction_notation("90 (82.5)\n——— 1\n1\n\n50.\n\nThe execution of pulls")
    assert out == "90 kg (82.5%) × 1 rep × 1 set\n\n50.\n\nThe execution of pulls"


def test_horizontal_columns_with_bars():
    text = ("squat clean + front squat + jerk:    90 (45)   1    120 (60)   1\n"
            "                                    —————          —————\n"
            "                                   1+2+1          1+2+1\n"
            "or:   120 (65)   3        (185);\n"
            "      —————\n"
            "        5")
    out = normalize_fraction_notation(text)
    assert out == ("squat clean + front squat + jerk:  90 kg (45%) × 1+2+1 reps × 1 set; "
                   "120 kg (60%) × 1+2+1 reps × 1 set\n"
                   "or:  120 kg (65%) × 5 reps × 3 sets (max 185 kg);")


def test_horizontal_without_a_bar_needs_an_indented_reps_line():
    text = "Snatch    57.5 (72.5) 4    (80).\n                           4"
    assert normalize_fraction_notation(text) == "Snatch  57.5 kg (72.5%) × 4 reps × 4 sets (max 80 kg)."
    flush = "Snatch    57.5 (72.5) 4\n4"
    assert normalize_fraction_notation(flush) == flush


def test_stacked_layout_only_beside_a_barred_fraction():
    stacked = "Front squat + push jerk\n140 (70)\n2+2\n1\n160 (80)\n2+2\n3\n\n(200);"
    assert normalize_fraction_notation(stacked) == stacked          # alone: three bare lines aren't enough
    with_bar = "45 (75)\n——— 5\n3\n\n" + stacked
    out = normalize_fraction_notation(with_bar)
    assert out.endswith("Front squat + push jerk\n140 kg (70%) × 2+2 reps × 1 set; "
                        "160 kg (80%) × 2+2 reps × 3 sets (max 200 kg);")


def test_text_without_fractions_is_unchanged():
    for text in (
        "Lower classified athletes can do 5-6 lifts (in one workout) with 95% (of max) 3 times.",
        "1*    2*    3*    4*\n2. Snatch: —— warm up wts. ——— ——— base training wt. ———————",
        "Week 1\n70 (75)\n3\n2",
        "",
    ):
        assert normalize_fraction_notation(text) == text


def test_scheme_words():
    assert scheme("80", "70", "1", "1") == "80 kg (70%) × 1 rep × 1 set"
    assert scheme("80", "70", "2", "3-4") == "80 kg (70%) × 2 reps × 3-4 sets"
