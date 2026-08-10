#!/usr/bin/env python3
"""Install the standalone literature-research Skill runtime environment.

Creates a Skill-local ``.venv`` using the current Python interpreter, upgrades
pip, and installs ``runtime/requirements.txt`` into it. Cross-platform: the
same command works on Windows PowerShell, Linux, and macOS. The venv is
self-contained afterwards; callers never need to activate it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _skill_dir() -> Path:
    configured = os.environ.get("CLAUDE_SKILL_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _venv_python(skill: Path) -> Path:
    """Resolve the venv interpreter cross-platform (Windows / POSIX)."""
    candidates = (
        skill / ".venv" / "Scripts" / "python.exe",
        skill / ".venv" / "bin" / "python",
    )
    return next(path for path in candidates if path.is_file())


def main() -> int:
    skill = _skill_dir()
    venv_dir = skill / ".venv"
    requirements = skill / "runtime" / "requirements.txt"

    if not requirements.is_file():
        print(
            "runtime/requirements.txt not found; this Skill has no bundled "
            "runtime to install. Run from a packaged standalone export.",
            file=sys.stderr,
        )
        return 1

    # Create the Skill-local venv with the current interpreter.
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    venv_python = _venv_python(skill)
    subprocess.run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-r", str(requirements)],
        check=True,
    )

    print(
        "Standalone runtime ready. Configure the DeepXiv credential once with:\n"
        "    python scripts/harness.py configure-token\n"
        "Then verify with: python scripts/doctor.py --workspace PATH\n"
        "An explicit DEEPXIV_TOKEN environment variable still takes precedence."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
