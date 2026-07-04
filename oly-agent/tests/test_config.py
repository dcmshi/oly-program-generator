# oly-agent/tests/test_config.py
"""
Tests for shared/config.py — Settings working-directory behavior (R9).

Run: python tests/test_config.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import Settings

RESULTS = []


def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, f"{type(e).__name__}: {e}"))


def test_init_does_not_create_dirs():
    # R9: constructing Settings must NOT scatter ./sources and ./logs — only the
    # explicit ingestion entry points should create them.
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        logs = Path(tmp) / "logs"
        Settings(sources_dir=src, logs_dir=logs)
        assert not src.exists(), "Settings() should not create sources_dir"
        assert not logs.exists(), "Settings() should not create logs_dir"


def test_ensure_working_dirs_creates_them():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        logs = Path(tmp) / "logs"
        s = Settings(sources_dir=src, logs_dir=logs)
        s.ensure_working_dirs()
        assert src.exists() and logs.exists()


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
