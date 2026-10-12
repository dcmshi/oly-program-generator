"""Pure helpers of the OCR model comparison (ING-EVAL-1) — no model, no PDF."""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ocr_model_compare import GROUP, majority_numbers, number_recall, numbers, pick_groups


def test_numbers_normalise_decimal_commas():
    assert numbers("72,5 kg x 3, then 72.5 x 3") == Counter({"72.5": 2, "3": 2})


def test_majority_numbers_keeps_what_most_transcriptions_agree_on():
    texts = ["80 x 3 x 2", "80 x 3 x 2", "86 x 3 x 2"]
    assert majority_numbers(texts) == Counter({"80": 1, "3": 1, "2": 1})


def test_number_recall_counts_multiplicity_and_none_without_numbers():
    ref = Counter({"80": 2, "3": 1})
    assert number_recall("80 x 3", ref) == 2 / 3
    assert number_recall("anything", Counter()) is None


def test_pick_groups_takes_the_digit_densest_group_and_a_prose_group():
    pages = {}
    for i in range(30):
        pages[str(i)] = "word " * 200                     # prose everywhere
    for i in range(10, 15):
        pages[str(i)] = "60 70 80 85 90 " * 40            # one table-heavy group
    pages["27"] = ""                                      # a group with a blank page is skipped
    picks = dict(pick_groups(pages))
    assert picks["tables"] == 10
    assert picks["prose"] % GROUP == 0 and picks["prose"] not in (10, 25)
