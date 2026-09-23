# tests/test_principle_audit.py
"""No-key unit tests for principle_audit.py (PRIN-AUDIT): which numbers a
principle claims, and when the source text supports them."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from principle_audit import (
    SOURCE_FILES,
    Claim,
    audit_principle,
    evaluate,
    match_source_files,
    numeric_claims,
    strip_unsupported,
    text_numbers,
)
from principle_model_compare import densest_window


def test_text_numbers_reads_digits_ranges_thousands_and_words():
    nums = text_numbers("Pulls at 80-105% of the best clean; 1,500 lifts a month, twice a week, 16.5% GPP.")
    assert {80, 105, 92.5, 1500, 2, 16.5} <= nums


def test_numeric_claims_skips_booleans_and_lists_and_reads_comparisons():
    claims = numeric_claims(
        {"intensity_ceiling": 90, "include_deload_week": True, "prefer_exercises": ["snatch"]},
        {"weeks_out_from_competition": {"lte": 2}, "training_age_years": {"between": [1, 3]}, "phase": "accumulation"},
    )
    assert Claim("recommendation", "intensity_ceiling", 90.0) in claims
    assert {c.key for c in claims} == {"intensity_ceiling", "weeks_out_from_competition", "training_age_years"}
    assert len(claims) == 4


def test_volume_modifier_supported_as_percent_or_reduction():
    nums = text_numbers("In the taper, reduce volume by 40%.")
    sup, unsup = audit_principle({"volume_modifier": 0.6}, {}, nums)
    assert sup and not unsup
    sup, unsup = audit_principle({"volume_modifier": 0.4}, {}, text_numbers("GPP is 40% of the time"))
    assert sup                                   # the audit checks numbers, not their meaning


def test_invented_numbers_are_flagged():
    """The source 808 cases: nothing in the text says 85 or 6."""
    nums = text_numbers("Usage of maximum weights by 12-15 year olds should be strictly regulated.")
    _, unsup = audit_principle({"intensity_ceiling": 85, "sessions_per_week_max": 6}, {}, nums)
    assert {c.key for c in unsup} == {"intensity_ceiling", "sessions_per_week_max"}


def test_rest_seconds_supported_by_minutes():
    _, unsup = audit_principle({"rest_between_sets_min": 180}, {}, text_numbers("rest 3 minutes between sets"))
    assert not unsup


def test_strip_unsupported_keeps_condition_and_other_keys():
    rec = {"intensity_ceiling": 85, "avoid_exercises": ["push jerk"], "total_reps_max": 20}
    out = strip_unsupported(rec, [Claim("recommendation", "intensity_ceiling", 85),
                                  Claim("condition", "week_of_block", 3)])
    assert out == {"avoid_exercises": ["push jerk"], "total_reps_max": 20}


def test_source_without_text_is_unverifiable_not_flagged():
    rows = [(1, 10, "Rule", {"intensity_ceiling": 85}, {}, "m", "website")]
    stats, changes, flagged, _ = evaluate(rows, {10: ""})
    s = stats["m · website"]
    assert s["unverifiable"] == 1 and s["unsupported_claims"] == 0
    assert not changes and not flagged


def test_source_files_map_is_explicit():
    files = [Path("Tudor Bompa, Carlo Buzzichelli - Periodization.epub"),
             Path("Hornsby 2017 - Strength RFD and power.txt")]
    bompa = SOURCE_FILES[("Periodization of Strength Training for Sports", None)]
    assert match_source_files(bompa, files) == [files[0]]
    assert match_source_files("", files) == []
    # the never-obtained *A System of…* book has no entry (fuzzy matching paired it with *Program of…*)
    assert not any("A System of" in title for title, _a in SOURCE_FILES)


_SOURCE_ROWS = [   # (id, title, author) as `SELECT id, title, author FROM sources` returns them
    (3, "Managing the Training of Weightlifters", "Nikolai Laputin, Valentin Oleshko"),
    (499, "Managing the Training of Weightlifters", "N.P. Laputin, V.G. Oleshko"),
    (531, "Managing the Training of Weightlifters", "Andrew Charniga"),        # a web article
    (802, "Periodization of Strength Training for Sports", "Tudor Bompa"),
    (902, "Periodization of Strength Training for Sports", "Tudor Bompa"),     # a re-ingest beside the old row
]


def test_resolve_source_ids_keys_by_title_and_author_where_titles_repeat():
    from unittest.mock import MagicMock

    from principle_audit import resolve_source_ids

    cur = MagicMock()
    cur.fetchall.return_value = _SOURCE_ROWS
    laputin = ("Managing the Training of Weightlifters", "N.P. Laputin, V.G. Oleshko")
    bompa = ("Periodization of Strength Training for Sports", None)
    missing = ("Weightlifting, Olympic Style", None)
    ids = resolve_source_ids(cur, [laputin, bompa, missing])
    assert ids == {laputin: [499], bompa: [802, 902], missing: []}
    assert cur.execute.call_count == 1
    assert sorted(cur.execute.call_args.args[1][0]) == sorted({laputin[0], bompa[0], missing[0]})


def test_resolve_source_files_survives_renumbering_and_honours_only():
    from unittest.mock import MagicMock

    from principle_audit import resolve_source_files

    cur = MagicMock()
    cur.fetchall.return_value = _SOURCE_ROWS
    assert resolve_source_files(cur) == {499: "N.P. Laputin", 802: "Tudor Bompa", 902: "Tudor Bompa"}
    assert resolve_source_files(cur, [902]) == {902: "Tudor Bompa"}


def test_load_source_texts_reads_each_file_once_and_can_skip_files():
    from unittest.mock import MagicMock, patch

    from principle_audit import load_source_texts

    files = [Path("Tudor Bompa, Carlo Buzzichelli - Periodization.epub"), Path("Other - Book.pdf")]
    cur = MagicMock()
    cur.fetchall.side_effect = [[(802, "chunk text")], _SOURCE_ROWS]
    with patch("principle_audit.file_text", return_value="file text") as ft:
        texts = load_source_texts(cur, files)
    ft.assert_called_once_with(files[0])                 # 802 and 902 share the file
    assert "chunk text" in texts[802] and "file text" in texts[802] and "file text" in texts[902]
    assert 499 not in texts or "file text" not in texts[499]

    cur = MagicMock()
    cur.fetchall.side_effect = [[(802, "chunk text")]]   # only the chunk query runs
    with patch("principle_audit.file_text") as ft:
        assert load_source_texts(cur, None, [802]) == {802: "chunk text"}
    ft.assert_not_called()
    assert cur.execute.call_count == 1


def test_load_source_texts_keeps_going_when_a_file_cannot_be_read(caplog):
    from unittest.mock import MagicMock, patch

    from principle_audit import load_source_texts

    cur = MagicMock()
    cur.fetchall.side_effect = [[], _SOURCE_ROWS]
    with patch("principle_audit.file_text", side_effect=OSError("locked")):
        texts = load_source_texts(cur, [Path("Tudor Bompa - x.epub")])
    assert texts[802].strip() == ""
    assert "Could not read" in caplog.text


def test_file_text_reads_the_ocr_cache_for_a_scan(tmp_path):
    """A PDF whose text layer is under SCAN_TEXT_MIN_CHARS_PER_PAGE per page is
    a scan: its text comes from sources/.ocr_cache/<sha256>.json."""
    import json

    import fitz
    from extractors.ocr_cache import CACHE_DIRNAME, file_sha256
    from principle_audit import file_text

    pdf = tmp_path / "Scan - Book.pdf"
    doc = fitz.open()
    for _ in range(3):
        doc.new_page()
    doc.save(pdf)
    doc.close()
    (tmp_path / CACHE_DIRNAME).mkdir()
    (tmp_path / CACHE_DIRNAME / f"{file_sha256(pdf)}.json").write_text(
        json.dumps({"pages": {"2": "page two", "10": "page ten", "1": "page one"}}), encoding="utf-8")
    text = file_text(pdf)
    assert text.index("page one") < text.index("page two") < text.index("page ten")

    txt = tmp_path / "Paper.txt"
    txt.write_text("plain 80%", encoding="utf-8")
    assert file_text(txt) == "plain 80%"
    assert file_text(tmp_path / "notes.docx") == ""


def test_audit_source_skips_sources_scan_when_document_text_is_given():
    """End-of-ingest path: the document text is the file's content, so
    sources/ is neither scanned nor re-extracted; without it the file side runs."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from principle_audit import audit_source

    def run(document_text):
        cur = MagicMock()
        cur.fetchall.side_effect = [[], [], []]          # chunks, (sources), principles
        conn = MagicMock()
        conn.cursor.return_value = cur
        with patch("psycopg2.connect", return_value=conn), \
             patch("principle_audit.source_files", return_value=[Path("x.pdf")]) as sf, \
             patch("principle_audit.file_text") as ft:
            audit_source(1, SimpleNamespace(database_url="db"), document_text, apply=False)
        return sf.call_count, ft.call_count

    assert run("Pulls at 80-90%.") == (0, 0)
    assert run("")[0] == 1


def test_densest_window_picks_the_numeric_part():
    text = "prose " * 4000 + "70% 75% 80% 85% " * 50 + "prose " * 4000
    w = densest_window(text, size=2000)
    assert w.count("%") >= 100 and len(w) == 2000


def test_pick_windows_resolves_books_by_title_and_takes_one_article_per_host():
    """principle_model_compare.pick_windows: books resolved by title (the latest
    row), Medvedev from its .txt not the scan PDF, a missing book skipped; per
    web host the most number-dense article that can still be fetched."""
    from unittest.mock import MagicMock, patch

    import principle_model_compare as pmc

    roman, vorobyev, bompa, medvedev, winwood = pmc.BOOK_SOURCES
    cur = MagicMock()
    cur.fetchall.side_effect = [
        [(799, roman[0], "R.A. Roman"), (820, roman[0], "R.A. Roman"),   # re-ingested: 820 is live
         (802, bompa[0], "Tudor Bompa"), (501, medvedev[0], "A.S. Medvedev"), (745, winwood[0], "Paul Winwood")],
        [(11, "https://www.catalystathletics.com/a/", "Cat A", 90),       # web rows, densest first
         (12, "https://www.catalystathletics.com/b/", "Cat B", 80),
         (13, "https://www.strongerbyscience.com/t/", "SBS", 70)],
    ]
    files = [Path("R.A. Roman - Training.pdf"), Path("Tudor Bompa - Periodization.epub"),
             Path("A.S. Medvedev - A Program of Multi-Year Training.pdf"),
             Path("A.S. Medvedev - A Program of Multi-Year Training (OCR text).txt"),
             Path("Winwood 2026 - Tapering.txt")]
    dense = "prose " * 3000 + "80% " * 200 + "prose " * 3000

    def fake_file_text(path):
        return dense if path.suffix == ".txt" or "Roman" in path.name else "few numbers " * 2000

    web = {"https://www.catalystathletics.com/a/": None,              # unfetchable → next row for the host
           "https://www.catalystathletics.com/b/": "cat 70% text",
           "https://www.strongerbyscience.com/t/": "sbs 60% text"}
    with patch.object(pmc, "source_files", return_value=files), \
         patch.object(pmc, "file_text", side_effect=fake_file_text) as ft, \
         patch.object(pmc, "fetch_web_text", side_effect=web.get):
        windows = pmc.pick_windows(cur)

    labels = [w["label"] for w in windows]
    assert labels == ["book:820", "book:802", "book:501", "book:745",       # Vorobyev has no row → skipped
                      "web:catalystathletics:12", "web:strongerbyscience:13"]
    by = {w["label"]: w for w in windows}
    assert by["book:820"]["title"] == roman[0] and by["book:820"]["source_id"] == 820
    assert by["book:820"]["text"].count("%") >= 150 and len(by["book:820"]["text"]) == pmc._PRINCIPLE_WINDOW
    read = [c.args[0].name for c in ft.call_args_list]
    assert "A.S. Medvedev - A Program of Multi-Year Training.pdf" not in read      # the scan is skipped
    assert "A.S. Medvedev - A Program of Multi-Year Training (OCR text).txt" in read
    assert by["web:catalystathletics:12"]["text"] == "cat 70% text"


def test_competition_day_condition_is_not_a_numeric_claim_to_check():
    _, unsup = audit_principle({}, {"weeks_out_from_competition": {"eq": 0}}, text_numbers("the warm-up room"))
    assert not unsup


def test_audit_source_strips_unsupported_keys_and_backs_them_up(tmp_path):
    """The end-of-ingest pass: numbers checked against chunks + the document
    text the ingest classified; unsupported keys stripped, previous values
    appended to the JSON-lines backup."""
    import json
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from principle_audit import audit_source

    cur = MagicMock()
    cur.fetchall.side_effect = [
        [(9999, "Pulls at 80-90% of the best clean.")],                        # chunk text
        [(1, 9999, "Pull range", {"intensity_floor": 80, "intensity_ceiling": 90}, {}, "m", "book"),
         (2, 9999, "Juvenile cap", {"intensity_ceiling": 75, "avoid_exercises": ["x"]}, {}, "m", "book"),
         (3, 9999, "Taper", {"volume_modifier": 0.6}, {}, "m", "book")],       # 0.6 ← "reduce by 40%" in the document
    ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    backup = tmp_path / "b.jsonl"
    with patch("psycopg2.connect", return_value=conn):
        out = audit_source(9999, SimpleNamespace(database_url="db"), "In the taper reduce volume by 40%.",
                           backup_path=backup)
    assert out == {"claims": 4, "unsupported": 1, "rows_changed": 1}
    updates = [c for c in cur.execute.call_args_list if "UPDATE" in c.args[0]]
    assert len(updates) == 1 and json.loads(updates[0].args[1][0]) == {"avoid_exercises": ["x"]}
    assert json.loads(backup.read_text().splitlines()[0])["before"] == {"intensity_ceiling": 75, "avoid_exercises": ["x"]}
    conn.commit.assert_called_once()


def test_run_audit_pass_never_raises():
    from types import SimpleNamespace
    from unittest.mock import patch

    from principle_audit import run_audit_pass

    with patch("psycopg2.connect", side_effect=RuntimeError("no db")):
        assert run_audit_pass(1, SimpleNamespace(database_url="db")) is None
