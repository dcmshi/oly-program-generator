# Makefile — common development tasks
#
# Works in Git Bash on Windows and bash/zsh on Unix.
# Requires: docker, uv
#
# Usage: make <target>
#   make up          start Postgres + PgBouncer + Redis
#   make web         run the FastAPI web server (port 8080)
#   make test        run all no-key/no-DB tests

# Set once — propagates to every recipe shell.
# Avoids the PYTHONUTF8=1 prefix on every invocation on Windows.
export PYTHONUTF8 := 1

.PHONY: help up down reset migrate sync web worker \
        test test-agent test-ingestion \
        coverage coverage-agent coverage-ingestion

# ── Help ──────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "  Infrastructure"
	@echo "    make up               docker compose up -d (Postgres + PgBouncer + Redis)"
	@echo "    make down             docker compose down"
	@echo "    make reset            down -v, up, migrate (full wipe + rebuild)"
	@echo "    make migrate          alembic upgrade head"
	@echo "    make sync             uv sync for both subsystems"
	@echo ""
	@echo "  Web (run 'make up' first)"
	@echo "    make web              uvicorn on :8080 (--reload)"
	@echo "    make worker           ARQ background worker"
	@echo ""
	@echo "  Tests (no DB or API keys needed)"
	@echo "    make test             run both test suites"
	@echo "    make test-agent       oly-agent unit + web router tests"
	@echo "    make test-ingestion   oly-ingestion unit tests"
	@echo ""
	@echo "  Coverage"
	@echo "    make coverage         coverage for both subsystems"
	@echo "    make coverage-agent"
	@echo "    make coverage-ingestion"
	@echo ""

# ── Infrastructure ────────────────────────────────────────────────────────────

up:
	docker compose -f oly-ingestion/docker-compose.yml up -d

down:
	docker compose -f oly-ingestion/docker-compose.yml down

reset:
	docker compose -f oly-ingestion/docker-compose.yml down -v
	docker compose -f oly-ingestion/docker-compose.yml up -d
	cd oly-agent && uv run alembic upgrade head

migrate:
	cd oly-agent && uv run alembic upgrade head

sync:
	cd oly-ingestion && uv sync --extra dev
	cd oly-agent && uv sync --extra dev --extra web

# ── Web ───────────────────────────────────────────────────────────────────────

web:
	cd oly-agent && uv run uvicorn web.app:app --reload --port 8080

worker:
	cd oly-agent && uv run arq web.worker.WorkerSettings

# ── Tests (no DB or API keys) ─────────────────────────────────────────────────

AGENT_TESTS := \
	tests/test_validate.py \
	tests/test_phase_profiles.py \
	tests/test_weight_resolver.py \
	tests/test_generate_utils.py \
	tests/test_assess.py \
	tests/test_plan.py \
	tests/test_retrieve.py \
	tests/test_explain.py \
	tests/test_orchestrator.py \
	tests/test_web_routers.py

INGESTION_TESTS := \
	tests/test_chunker.py \
	tests/test_classifier.py \
	tests/test_pdf_extractor.py \
	tests/test_epub_extractor.py \
	tests/test_retag_chunks.py \
	tests/test_html_extractor.py \
	tests/test_parse_exercise.py \
	tests/test_pipeline_unit.py \
	tests/test_structured_loader_unit.py

test: test-agent test-ingestion

test-agent:
	cd oly-agent && uv run pytest $(AGENT_TESTS) -q

test-ingestion:
	cd oly-ingestion && uv run pytest $(INGESTION_TESTS) -q

# ── Coverage ──────────────────────────────────────────────────────────────────

coverage: coverage-agent coverage-ingestion

coverage-agent:
	cd oly-agent && uv run coverage run -m pytest $(AGENT_TESTS) -q && uv run coverage report

coverage-ingestion:
	cd oly-ingestion && uv run coverage run -m pytest $(INGESTION_TESTS) -q && uv run coverage report
