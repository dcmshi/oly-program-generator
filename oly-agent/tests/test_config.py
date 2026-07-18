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


def test_tzdata_available():
    """INF-M1: without the tzdata package, ZoneInfo silently falls back to UTC
    on Windows and the entire per-athlete timezone feature (W-L5) is inert."""
    from zoneinfo import ZoneInfo
    ZoneInfo("America/New_York")  # raises ZoneInfoNotFoundError if tzdata missing


def test_migration_url_rewrites_only_local_hosts():
    """INF-M3: the PgBouncer 5432→5433 rewrite is for the local compose stack —
    a production DB on the standard port must not be silently redirected."""
    import os
    from unittest.mock import patch as _patch

    sys.path.insert(0, str(Path(__file__).parent.parent / "migrations"))
    from db_url import resolve_migration_url

    with _patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@db.prod.internal:5432/app",
                                  "ALEMBIC_DATABASE_URL": ""}):
        url = resolve_migration_url()
    assert ":5433/" not in url, f"non-local host must not be rewritten: {url}"

    with _patch.dict(os.environ, {"DATABASE_URL": "postgresql://oly:oly@localhost:5432/oly_programming",
                                  "ALEMBIC_DATABASE_URL": ""}):
        url = resolve_migration_url()
    assert "localhost:5433/" in url, f"local compose URL must hit Postgres directly: {url}"

    with _patch.dict(os.environ, {"ALEMBIC_DATABASE_URL": "postgresql://x@explicit:5432/db"}):
        url = resolve_migration_url()
    assert url == "postgresql://x@explicit:5432/db", "explicit override must pass through untouched"

    # audit2-L4: userinfo-less localhost URLs must also be rewritten
    with _patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost:5432/oly_programming",
                                  "ALEMBIC_DATABASE_URL": ""}):
        url = resolve_migration_url()
    assert "localhost:5433/" in url, f"no-userinfo local URL must hit Postgres directly: {url}"


def test_log_env_does_not_override_explicit_args():
    """INF-L8: explicit constructor args must beat LOG_FORMAT/LOG_LEVEL env —
    every other Settings field resolves in that order."""
    import os
    from unittest.mock import patch as _patch

    with _patch.dict(os.environ, {"LOG_FORMAT": "json", "LOG_LEVEL": "DEBUG"}):
        s = Settings(log_format="text", log_level="WARNING")
    assert s.log_format == "text", s.log_format
    assert s.log_level == "WARNING", s.log_level

    with _patch.dict(os.environ, {"LOG_FORMAT": "json", "LOG_LEVEL": "DEBUG"}):
        s2 = Settings()
    assert s2.log_format == "json" and s2.log_level == "DEBUG"


def test_placeholder_secret_key_rejected():
    """INF-L9: a copied-but-unedited .env must not sign sessions with the
    committed public placeholder string."""
    s = Settings(secret_key="change_me_to_a_random_64_char_hex_string")
    assert s.secret_key != "change_me_to_a_random_64_char_hex_string"
    assert s.secret_key, "a random key should replace the rejected placeholder"


def test_makefile_runs_all_no_key_suites():
    """INF-M2/M6: every no-key regression suite must be in the Makefile lists
    (CI runs make test-agent / test-ingestion), and reset must wait for health."""
    mk = (Path(__file__).parent.parent.parent / "Makefile").read_text(encoding="utf-8")
    for suite in ("test_config", "test_formulas", "test_phase_progression",
                  "test_log", "test_web_queries"):
        assert f"tests/{suite}.py" in mk, f"agent suite {suite} missing from Makefile (INF-M2)"
    for suite in ("test_ingest_web", "test_llm_helpers", "test_vector_loader_units"):
        assert f"tests/{suite}.py" in mk, f"ingestion suite {suite} missing from Makefile (INF-M2)"
    assert "up -d --wait" in mk, "make reset must wait for container health (INF-M6)"


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
