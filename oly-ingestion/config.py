# config.py
"""
Thin shim — Settings is defined in shared/config.py and re-exported here
so that all ingestion modules can continue using `from config import Settings`
without any changes.
"""

import sys
from pathlib import Path

# Ensure the repo root (parent of oly-ingestion/) is on sys.path so that
# `shared` is importable regardless of where the process was launched from.
_repo_root = Path(__file__).parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from shared.config import Settings  # noqa: E402, F401  (re-exported)

__all__ = ["Settings"]
