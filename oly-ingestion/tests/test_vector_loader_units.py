# tests/test_vector_loader_units.py
"""
No-key unit tests for VectorLoader pure helpers.

Covers the dedup partition behind I-M9 (single existing-hash lookup + intra-batch
dedup) and I-M5 (accurate skipped count). No DB or OPENAI_API_KEY needed.

Run: python tests/test_vector_loader_units.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loaders.vector_loader import VectorLoader

RESULTS = []


def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, f"{type(e).__name__}: {e}"))


# chunk objects are opaque to the partitioner — plain strings stand in.

def test_partition_all_new():
    new, skipped = VectorLoader._partition_new_chunks(["a", "b"], ["h1", "h2"], set())
    assert skipped == 0
    assert [h for _, h in new] == ["h1", "h2"]


def test_partition_skips_existing_db_hashes():
    new, skipped = VectorLoader._partition_new_chunks(["a", "b"], ["h1", "h2"], {"h1"})
    assert skipped == 1
    assert [h for _, h in new] == ["h2"]


def test_partition_dedups_within_batch():
    # 3rd chunk duplicates the 1st within the same call — must be skipped so it
    # doesn't collide on the UNIQUE(content_hash) insert after embedding (I-M9).
    new, skipped = VectorLoader._partition_new_chunks(["a", "b", "a"], ["h1", "h2", "h1"], set())
    assert skipped == 1
    assert [h for _, h in new] == ["h1", "h2"]


def test_partition_all_duplicates_counts_all_skipped():
    # I-M5: a fully-duplicate batch reports the real skipped count, not 0.
    new, skipped = VectorLoader._partition_new_chunks(["a", "a", "a"], ["h1", "h1", "h1"], set())
    assert skipped == 2
    assert len(new) == 1


def test_partition_empty():
    new, skipped = VectorLoader._partition_new_chunks([], [], set())
    assert new == [] and skipped == 0


if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        _test(name, fn)

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    failed = sum(1 for r in RESULTS if r[0] in ("FAIL", "ERROR"))
    for r in RESULTS:
        detail = f"  → {r[2]}" if len(r) > 2 else ""
        print(f"  {r[0]}  {r[1]}{detail}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
