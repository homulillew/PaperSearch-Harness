#!/usr/bin/env python3
"""Check a project or standalone literature-research Skill installation."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path


def _skill_dir() -> Path:
    configured = os.environ.get("CLAUDE_SKILL_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _bootstrap_runtime(skill: Path) -> None:
    bundled = skill / "runtime" / "src"
    project = skill.parents[2] / "src" if len(skill.parents) >= 3 else Path()
    for candidate in (bundled, project):
        if (candidate / "my_search_harness").is_dir():
            sys.path.insert(0, str(candidate))
            return


def _reexec_skill_venv(skill: Path) -> None:
    # Cross-platform venv Python discovery: Windows uses Scripts\python.exe,
    # POSIX uses bin/python. Try both so the same doctor works regardless of
    # which shell created the venv.
    candidates = (
        skill / ".venv" / "Scripts" / "python.exe",
        skill / ".venv" / "bin" / "python",
    )
    venv_python = next((path for path in candidates if path.is_file()), None)
    if venv_python is None:
        return
    if Path(sys.executable).resolve() == venv_python.resolve():
        return
    # os.execv replaces the process cleanly on POSIX. On Windows execv does not
    # fully replace the running image, so spawn the venv Python as a child and
    # forward its exit code, preserving all original argv.
    if os.name == "posix":
        os.execv(
            str(venv_python),
            [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        )
    else:
        import subprocess

        result = subprocess.run(
            [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]
        )
        raise SystemExit(result.returncode)


def _workspace_writable(workspace: Path) -> bool:
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=workspace):
            pass
        return True
    except OSError:
        return False


def run_checks(workspace: Path, skill: Path | None = None) -> dict[str, object]:
    skill = _skill_dir() if skill is None else skill
    _bootstrap_runtime(skill)
    try:
        import my_search_harness  # noqa: F401

        harness_importable = True
    except ImportError:
        harness_importable = False
    references = (
        "RESEARCH_PROTOCOL.md",
        "RUNTIME_API.md",
        "COMPLETION_GUIDE.md",
        "REPORT_WRITING_GUIDE.md",
        "RESEARCH_INTEGRITY_GUIDE.md",
    )
    reference_results = {
        name: (skill / "references" / name).is_file() for name in references
    }
    from my_search_harness.runtime.credentials import resolve_deepxiv_token

    token, token_source = resolve_deepxiv_token()
    checks: dict[str, object] = {
        "python": {
            "ok": sys.version_info >= (3, 11),
            "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        },
        "my_search_harness": {"importable": harness_importable},
        "deepxiv_sdk": {
            "importable": importlib.util.find_spec("deepxiv_sdk") is not None
        },
        "deepxiv_token": {
            "present": token is not None,
            "source": token_source,
        },
        "workspace": {
            "path": str(workspace),
            "writable": _workspace_writable(workspace),
        },
        "writing_guide": {"exists": reference_results["REPORT_WRITING_GUIDE.md"]},
        "research_integrity_guide": {
            "exists": reference_results["RESEARCH_INTEGRITY_GUIDE.md"]
        },
        "references": reference_results,
    }
    ok = (
        checks["python"]["ok"]  # type: ignore[index]
        and harness_importable
        and checks["deepxiv_sdk"]["importable"]  # type: ignore[index]
        and checks["deepxiv_token"]["present"]  # type: ignore[index]
        and checks["workspace"]["writable"]  # type: ignore[index]
        and all(reference_results.values())
    )
    return {"healthy": bool(ok), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="workspace")
    args = parser.parse_args()
    result = run_checks(Path(args.workspace).expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    _reexec_skill_venv(_skill_dir())
    raise SystemExit(main())
