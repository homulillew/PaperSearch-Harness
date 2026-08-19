#!/usr/bin/env python3
"""Install the standalone literature-research Skill runtime environment.

Creates a Skill-local ``.venv`` using the current Python interpreter, upgrades
pip, and installs ``runtime/requirements.txt`` into it. Cross-platform: the
same command works on Windows PowerShell, Linux, and macOS. The venv is
self-contained afterwards; callers never need to activate it.
"""

from __future__ import annotations

import os
import shutil
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


def _install_math_renderer(skill: Path) -> bool:
    """Provision the MathJax renderer used by the math renderability preflight.

    The validator script and its ``package.json`` live under
    ``runtime/src/my_search_harness/runtime/math``. ``node_modules`` is never
    shipped (the packager excludes it); consumers install it here, mirroring the
    Python ``.venv`` pattern.

    Node.js is a required dependency of a fully ready installation: report
    manuscripts may contain math, and the preflight cannot certify renderability
    without the configured MathJax renderer. If Node/npm are absent, this
    returns ``False`` so the caller reports a clear failure rather than
    claiming readiness. Installation stays binary — there is no "math-free
    ready" tier. A Skill build that expects the math renderer but is missing its
    bundled assets (``package.json`` or ``validate.js``) is also a failure: the
    renderer cannot be provisioned, so setup must not report success.
    """
    math_dir = (
        skill / "runtime" / "src" / "my_search_harness" / "runtime" / "math"
    )
    package_json = math_dir / "package.json"
    validator = math_dir / "validate.js"
    if not package_json.is_file() or not validator.is_file():
        print(
            "Bundled math renderer assets not found under "
            f"{math_dir} (expected package.json and validate.js). "
            "This Skill build expects the configured MathJax renderer but "
            "cannot provision it; report math rendering validation cannot be "
            "certified. Reinstall this Skill from a complete build.",
            file=sys.stderr,
        )
        return False
    npm = shutil.which("npm")
    node = shutil.which("node")
    if npm is None or node is None:
        print(
            "Node.js/npm not found on PATH. The configured MathJax renderer "
            "is required for report math rendering validation: manuscripts "
            "containing math cannot be certified without it. Install Node.js "
            "(https://nodejs.org) and re-run setup.",
            file=sys.stderr,
        )
        return False
    subprocess.run(
        [npm, "install", "--production", "--no-audit", "--no-fund"],
        cwd=str(math_dir),
        check=True,
    )
    print("Math renderer (MathJax) installed for the math renderability preflight.")
    return True


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

    if not _install_math_renderer(skill):
        return 1

    print(
        "Standalone runtime ready. Configure the DeepXiv credential once with:\n"
        "    python scripts/harness.py configure-token\n"
        "Then verify with: python scripts/doctor.py --workspace PATH\n"
        "An explicit DEEPXIV_TOKEN environment variable still takes precedence."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
