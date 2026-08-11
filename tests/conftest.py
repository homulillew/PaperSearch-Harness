"""Shared pytest configuration for the literature-research test suite.

These tests run with the system Python (pytest is a dev dependency, not part
of the runtime requirements). The runtime package is made importable by
putting its ``src`` directory on ``sys.path``; ``credentials.py`` is stdlib-only
so no venv is needed for the credential tests. The filesystem-boundary tests
drive ``scripts/harness.py`` as a subprocess with ``--workspace`` pointed at a
temp directory, so they never touch the developer's real workspace.
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = (
    Path(__file__).resolve().parents[1]
    / ".claude"
    / "skills"
    / "literature-research"
)
RUNTIME_SRC = SKILL_DIR / "runtime" / "src"

if str(RUNTIME_SRC) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SRC))
