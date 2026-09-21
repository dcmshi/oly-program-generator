"""processors/ocr_quality — no-reference OCR page checks (OCR-QA)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from processors.ocr_quality import assess_pages, choose_view, garbled_ratio, summarize, view_agreement

PAGE = ("The snatch is performed in one continuous movement from the floor to arms length overhead. "
        "The lifter pulls the bar, drops under it and recovers. ") * 12


def test_assess_pages_flags_blank_short_echo_and_garbled_but_not_captions():
    pages = [
        "A Textbook on\n\nWEIGHTLIFTING",            # title page: under OCR_MIN_PAGE_CHARS → blank
        PAGE, PAGE.replace("snatch", "clean").replace("pulls", "racks"),
        "",                                          # blank with ink → blank
        "Diag. 12. The Split-style Clean",           # caption page → not flagged
        PAGE,
        PAGE,                                        # identical to previous → echo
        "xq tbxrq vvvvvvv zzzzzz qkrt " * 40,         # garbled
        PAGE,
        "",                                          # blank page with no ink → fine
    ]
    ink = [True] * 9 + [False]
    q = assess_pages(pages, ink)
    reasons = {p.index + 1: p.reasons for p in q if p.suspect}
    assert set(reasons) == {1, 4, 7, 8}
    assert reasons[1] == ["blank"]                 # 28-char title page counts as blank (gets a second view)
    assert reasons[4] == ["blank"]
    assert reasons[7][0].startswith("echoes previous page")
    assert reasons[8][0].startswith("garbled")
    assert summarize(q)["suspect_pages"] == [1, 4, 7, 8]


def test_garbled_ratio_is_low_on_prose_and_numbers():
    assert garbled_ratio(PAGE) < 0.02
    assert garbled_ratio("Snatch 70% x 3 x 5, 80% x 2 x 3, 85% x 1 x 2 (kg) 32.5 (55) ——— 1 4") < 0.15


def test_choose_view_recovers_blank_and_flags_disagreement():
    text, agreement, verdict = choose_view("", PAGE)
    assert verdict == "recovered" and text == PAGE
    text, agreement, verdict = choose_view(PAGE, PAGE + " Extra sentence at the end.")
    assert verdict == "agree" and agreement > 0.8 and text.endswith("end.")
    other = "Completely different transcription of an unrelated page about the jerk dip and drive. " * 10
    text, agreement, verdict = choose_view(PAGE, other)
    assert verdict == "disagree" and agreement < 0.1
    assert view_agreement("", "") == 1.0
