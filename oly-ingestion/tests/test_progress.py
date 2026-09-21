"""processors/progress — stage banners and per-item progress lines."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from processors.progress import Progress, Stage, fmt_duration


def test_fmt_duration_units():
    assert fmt_duration(42) == "42s"
    assert fmt_duration(782) == "13m 02s"
    assert fmt_duration(3 * 3600 + 5 * 60) == "3h 05m"


def test_stage_logs_start_and_done(caplog):
    log = logging.getLogger("t")
    with caplog.at_level(logging.INFO, logger="t"):
        with Stage("Extract", log, "book.pdf"):
            pass
    assert caplog.messages[0] == "── Extract — book.pdf …"
    assert caplog.messages[1].startswith("── Extract: done in ")


def test_stage_reports_failure(caplog):
    log = logging.getLogger("t")
    with caplog.at_level(logging.INFO, logger="t"):
        try:
            with Stage("Route", log):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
    assert caplog.records[-1].levelno == logging.ERROR and "failed in" in caplog.messages[-1]


def test_progress_ticks_every_n_and_on_last(caplog):
    log = logging.getLogger("t")
    p = Progress(7, log, label="page", every=3)
    with caplog.at_level(logging.INFO, logger="t"):
        for i in range(1, 8):
            p.tick(i, f"n={i}")
    assert [m.split("]")[0] for m in caplog.messages] == ["[page 3/7 · 42%", "[page 6/7 · 85%", "[page 7/7 · 100%"]
    assert "ETA ~" in caplog.messages[0] and "ETA" not in caplog.messages[-1]
    assert "elapsed" in caplog.messages[-1]
