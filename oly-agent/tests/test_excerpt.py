# oly-agent/tests/test_excerpt.py
"""
Tests for shared/excerpt.focused_excerpt (AUD-2) and its use in the session
prompt's knowledge-context block. No DB or API keys needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))   # the prompt helpers in test_generate_utils

from shared.constants import EXCERPT_GAP_MARKER, SNIPPET_MAX_CHARS
from shared.excerpt import content_terms, focused_excerpt

FILLER = "The coach reviewed general preparation notes with the group before training began. "


def _long_text(match_sentence: str, filler_count: int = 40, heading: str | None = None) -> str:
    body = FILLER * filler_count + match_sentence + " " + FILLER * filler_count
    return (heading + "\n\n" if heading else "") + body


# ── focused_excerpt ─────────────────────────────────────────────

def test_short_text_is_returned_unchanged():
    text = "Snatch pulls at 90-100% build the second pull.\n\nKeep the bar close."
    assert focused_excerpt(text, "snatch pull", SNIPPET_MAX_CHARS) is text


def test_picks_the_matching_part_of_a_long_text():
    match = "Snatch pulls from blocks strengthen the second pull and fix early arm bend."
    text = _long_text(match)
    assert text.index(match) > SNIPPET_MAX_CHARS          # a head cut would miss it
    out = focused_excerpt(text, "correcting early arm bend in the snatch pull", SNIPPET_MAX_CHARS)
    assert match in out
    assert out.startswith(EXCERPT_GAP_MARKER.lstrip())     # the omitted head is marked
    assert out.endswith(EXCERPT_GAP_MARKER.rstrip())       # and so is the omitted tail


def test_respects_the_budget():
    text = _long_text("Front squats at 80% for triples in accumulation.", filler_count=80)
    for budget in (200, 500, 1000, SNIPPET_MAX_CHARS):
        out = focused_excerpt(text, "front squat accumulation triples", budget)
        assert len(out) <= budget, (budget, len(out))


def test_keeps_original_order_and_joins_gaps():
    first = "Jerk dips must stay vertical under heavy load."
    second = "Recovery squats close the jerk session."
    text = FILLER * 30 + first + " " + FILLER * 30 + second + " " + FILLER * 30
    out = focused_excerpt(text, "jerk dip vertical recovery squat", 600)
    assert first in out and second in out
    assert out.index(first) < out.index(second)
    assert EXCERPT_GAP_MARKER in out[out.index(first):out.index(second)]


def test_keeps_the_heading_line_when_budget_allows():
    heading = "Chapter 7: Pulling Strength"
    text = _long_text("Clean pulls should reach full extension before the pull under.", heading=heading)
    out = focused_excerpt(text, "clean pull extension", SNIPPET_MAX_CHARS)
    assert out.startswith(heading)
    assert "Clean pulls should reach full extension" in out


def test_no_match_falls_back_to_the_head():
    text = _long_text("Nothing relevant here either.")
    out = focused_excerpt(text, "zzzqx blorft", SNIPPET_MAX_CHARS)
    assert out.endswith(EXCERPT_GAP_MARKER.rstrip())
    assert text.startswith(out[: -len(EXCERPT_GAP_MARKER.rstrip())])
    assert len(out) <= SNIPPET_MAX_CHARS
    # an empty query is a no-match too
    assert focused_excerpt(text, "", SNIPPET_MAX_CHARS) == out


def test_is_deterministic():
    text = _long_text("Snatch balance builds confidence in the receiving position.", filler_count=60)
    query = "snatch receiving position confidence"
    outs = {focused_excerpt(text, query, SNIPPET_MAX_CHARS) for _ in range(5)}
    assert len(outs) == 1


def test_oversized_single_line_is_split_and_budgeted():
    # an OCR'd table row with no sentence punctuation or newlines
    text = " ".join(f"cell{i}" for i in range(600)) + " deadlift variant " + " ".join(
        f"cell{i}" for i in range(600, 1200))
    out = focused_excerpt(text, "deadlift variant", 800)
    assert "deadlift variant" in out
    assert len(out) <= 800


def test_content_terms_drop_stop_words_and_stem():
    terms = content_terms("exercise selection for a snatch session with pulling support, cleans")
    assert terms == {"snatch", "pull", "clean"}


# ── the prompt block uses the excerpt ────────────────────────────

def test_session_prompt_shows_the_matching_sentence_past_the_head():
    from test_generate_utils import _make_athlete, _make_prompt, _make_retrieval

    match = "UNIQUE_MARKER Snatch high pulls restore the second pull in accumulation."
    text = _long_text(match)
    assert text.index(match) > SNIPPET_MAX_CHARS
    chunk = {
        "id": 1, "chunk_type": "periodization", "raw_content": text, "similarity": 0.8,
        "session_query": "exercise selection for a snatch session with pull support, "
                         "during the accumulation phase at 70-80% intensity, intermediate athlete",
    }
    prompt = _make_prompt(_make_athlete(), _make_retrieval(), context_chunks=[chunk])
    assert "UNIQUE_MARKER" in prompt
    block = prompt[prompt.index("<knowledge_base>"):prompt.index("</knowledge_base>")]
    assert len(block) < len(text)


def test_fault_chunk_uses_its_own_retrieval_query():
    from test_generate_utils import _make_athlete, _make_prompt, _make_retrieval

    fault_sentence = "FAULT_MARKER Early arm bend is corrected with tall muscle snatches."
    session_sentence = "SESSION_MARKER Back squats anchor the accumulation block."
    text = FILLER * 40 + fault_sentence + " " + FILLER * 40 + session_sentence + " " + FILLER * 40
    chunk = {
        "id": 2, "chunk_type": "fault_correction", "raw_content": text, "similarity": 0.8,
        "retrieval_query": "correcting early arm bend in weightlifting, intermediate athlete",
        "session_query": "exercise selection for a squat session, back squats, accumulation",
    }
    prompt = _make_prompt(_make_athlete(faults=["early_arm_bend"]), _make_retrieval(), context_chunks=[chunk])
    assert "FAULT_MARKER" in prompt
