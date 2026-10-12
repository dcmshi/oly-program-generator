# tests/test_checkpoint.py
"""shared.checkpoint — the append-only progress file behind --resume (RESUME-1)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.checkpoint import Checkpoint, CheckpointMismatch


def test_put_survives_an_abort_and_resume_reads_it_back(tmp_path):
    out = tmp_path / "result.json"
    cp = Checkpoint(out, resume=False, signature={"reasoning": "default"})
    cp.put("sol|g1", {"pages": ["a"]})
    cp.put("sol|g2", {"pages": ["b"]})
    # the process dies here: no finish(), no result file
    again = Checkpoint(out, resume=True, signature={"reasoning": "default"})
    assert "sol|g1" in again and again.get("sol|g2") == {"pages": ["b"]} and len(again) == 2
    again.put("sol|g3", {"pages": ["c"]})
    assert len(Checkpoint(out, resume=True, signature={"reasoning": "default"})) == 3
    again.finish()
    assert not again.path.exists()


def test_without_resume_the_old_partial_is_discarded(tmp_path):
    out = tmp_path / "result.json"
    Checkpoint(out, resume=False).put("k", 1)
    assert len(Checkpoint(out, resume=False)) == 0
    assert len(Checkpoint(out, resume=True)) == 0


def test_resume_refuses_a_partial_from_other_settings(tmp_path):
    out = tmp_path / "result.json"
    Checkpoint(out, resume=False, signature={"reasoning": "default"}).put("k", 1)
    with pytest.raises(CheckpointMismatch):
        Checkpoint(out, resume=True, signature={"reasoning": "pipeline"})


def test_a_line_cut_off_mid_write_is_skipped(tmp_path):
    out = tmp_path / "result.json"
    cp = Checkpoint(out, resume=False)
    cp.put("a", 1)
    with open(cp.path, "a", encoding="utf-8") as f:
        f.write('{"key": "b", "val')                      # the abort landed mid-line
    again = Checkpoint(out, resume=True)
    assert again.get("a") == 1 and "b" not in again
