# tests/conftest.py
"""
Keep the no-key suite independent of the developer's `.env`.

`shared/config.py` loads `oly-ingestion/.env` once at import (dotenv, no
override of existing variables). The dev `.env` sets LLM_PROVIDER and the
model roles (OpenRouter + Kimi / DeepSeek since 2026-09-20), which rewrites
model ids ("claude-sonnet-5" -> "anthropic/claude-sonnet-5") and broke every
test that builds a real Settings(). Import the config so the file is loaded,
then pin the provider to the first-party default and drop the role
overrides; tests that need a provider set it explicitly.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # repo root for shared.*

import shared.config  # noqa: E402,F401  (loads .env once)

os.environ["LLM_PROVIDER"] = "anthropic"
for _var in ("LLM_MODEL", "LIGHT_MODEL", "JUDGE_MODEL", "TEMPLATE_MODEL", "GENERATION_MODEL", "EXPLANATION_MODEL",
             "GENERATION_THINKING", "GENERATION_EFFORT", "EXPLANATION_THINKING", "EXPLANATION_EFFORT",
             "LLM_BASE_URL"):
    os.environ.pop(_var, None)
