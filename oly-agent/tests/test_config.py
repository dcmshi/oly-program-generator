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


def test_compose_pins_pgvector_image_version():
    """RAG-L6: the pgvector image must carry an explicit extension version (the
    floating :pg16 tag silently changes the minor; >= 0.8 is needed for
    hnsw.iterative_scan, which RAG-H5 relies on)."""
    import re

    compose = (Path(__file__).parent.parent.parent / "oly-ingestion" / "docker-compose.yml").read_text(encoding="utf-8")
    m = re.search(r"image:\s*pgvector/pgvector:(\d+)\.(\d+)\.(\d+)-pg16", compose)
    assert m, "pgvector image is not pinned to a semver tag"
    assert (int(m.group(1)), int(m.group(2))) >= (0, 8), "pgvector >= 0.8 required for hnsw.iterative_scan"

def test_model_roles_resolve_arg_env_default():
    """Model roles resolve explicit arg > env > default; light_model defaults to a
    Haiku-class model so classifier/context/relabel/grading stop defaulting to Sonnet."""
    import os

    from shared.config import DEFAULT_GENERATION_MODEL, DEFAULT_LIGHT_MODEL, DEFAULT_LLM_MODEL

    saved = {k: os.environ.pop(k, None) for k in ("LLM_MODEL", "LIGHT_MODEL", "GENERATION_MODEL", "EXPLANATION_MODEL")}
    try:
        s = Settings()
        assert s.llm_model == DEFAULT_LLM_MODEL == "claude-sonnet-5"      # MODEL-1b (2026-09-20)
        assert s.light_model == DEFAULT_LIGHT_MODEL == "claude-haiku-4-5-20251001"
        # MODEL-1 (2026-09-16 baseline): the agent roles moved to Sonnet 5
        assert s.generation_model == DEFAULT_GENERATION_MODEL == "claude-sonnet-5"
        assert s.explanation_model == DEFAULT_GENERATION_MODEL

        os.environ["LIGHT_MODEL"] = "light-from-env"
        os.environ["GENERATION_MODEL"] = "gen-from-env"
        s = Settings()
        assert s.light_model == "light-from-env" and s.generation_model == "gen-from-env"
        assert s.explanation_model == DEFAULT_GENERATION_MODEL  # untouched role keeps its default

        s = Settings(light_model="explicit", generation_model="explicit-gen")
        assert s.light_model == "explicit" and s.generation_model == "explicit-gen"
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

def test_thinking_and_effort_settings_resolve_arg_env_default():
    import os
    keys = ("GENERATION_THINKING", "GENERATION_EFFORT", "EXPLANATION_THINKING", "EXPLANATION_EFFORT")
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        s = Settings()
        # thinking defaults to disabled on both agent roles (MODEL-1): adaptive
        # thinking on Sonnet 5 spent the whole 4,096 budget before any text
        assert (s.generation_thinking, s.generation_effort) == ("disabled", "")
        assert (s.explanation_thinking, s.explanation_effort) == ("disabled", "")
        assert s.explanation_max_tokens == 2048

        os.environ["GENERATION_THINKING"] = "disabled"
        os.environ["GENERATION_EFFORT"] = "low"
        s = Settings()
        assert (s.generation_thinking, s.generation_effort) == ("disabled", "low")
        assert s.explanation_thinking == "disabled"    # untouched role keeps its default

        s = Settings(generation_thinking="adaptive")
        assert s.generation_thinking == "adaptive"      # explicit arg beats env
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


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


def test_openrouter_provider_rewrites_model_ids_and_picks_the_key():
    """LLM_PROVIDER=openrouter routes the same Anthropic-SDK calls through
    OpenRouter: model roles become OpenRouter ids, the client uses
    OPENROUTER_API_KEY + the OpenRouter base URL, and Message Batches are off."""
    import os
    from unittest.mock import patch

    from shared.llm import OPENROUTER_BASE_URL, create_llm_client, supports_batches

    saved = {k: os.environ.pop(k, None) for k in ("LLM_PROVIDER", "OPENROUTER_API_KEY", "LLM_BASE_URL", "TEMPLATE_MODEL",
                                                  "LLM_MODEL", "LIGHT_MODEL", "GENERATION_MODEL", "EXPLANATION_MODEL")}
    try:
        s = Settings()
        assert s.llm_provider == "anthropic" and supports_batches(s)
        assert s.llm_model == "claude-sonnet-5"

        os.environ["LLM_PROVIDER"] = "openrouter"
        os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
        s = Settings()
        assert s.llm_provider == "openrouter" and not supports_batches(s)
        assert s.llm_model == "anthropic/claude-sonnet-5"
        assert s.light_model == "anthropic/claude-haiku-4.5"          # dated snapshot dropped, dotted minor
        assert s.generation_model == s.explanation_model == "anthropic/claude-sonnet-5"
        assert s.template_model == s.llm_model                      # blank TEMPLATE_MODEL follows LLM_MODEL
        os.environ["TEMPLATE_MODEL"] = "claude-sonnet-5"
        os.environ["LLM_MODEL"] = "moonshotai/kimi-k3"
        s = Settings()
        assert s.llm_model == "moonshotai/kimi-k3" and s.template_model == "anthropic/claude-sonnet-5"
        os.environ.pop("TEMPLATE_MODEL")
        os.environ.pop("LLM_MODEL")
        with patch("shared.llm.Anthropic") as client_cls:
            create_llm_client(s)
        assert client_cls.call_args.kwargs == {"api_key": "sk-or-test", "base_url": OPENROUTER_BASE_URL}

        os.environ["LLM_BASE_URL"] = "https://proxy.example/api"
        with patch("shared.llm.Anthropic") as client_cls:
            create_llm_client(Settings())
        assert client_cls.call_args.kwargs["base_url"] == "https://proxy.example/api"

        os.environ.pop("OPENROUTER_API_KEY")
        try:
            create_llm_client(Settings())
            raise AssertionError("expected ValueError without OPENROUTER_API_KEY")
        except ValueError as e:
            assert "OPENROUTER_API_KEY" in str(e)

        os.environ["LLM_PROVIDER"] = "bogus"
        try:
            Settings()
            raise AssertionError("expected ValueError for an unknown provider")
        except ValueError:
            pass
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
