#!/usr/bin/env python3
"""Package the literature-research Skill into a publishable, zipped export.

Recreates ``dist/literature-research/`` from the tracked Skill and the
authoritative ``my_search_harness`` source tree, then writes
``dist/literature-research.zip`` next to it.

The export is a clean standalone copy: it deliberately excludes the
Skill-local virtualenv, Python bytecode caches, test/coverage caches, and
runtime logs/workspace artifacts. Generated ``dist`` content is not meant to
be committed.

Cross-platform: the same command works on Windows PowerShell, Linux, and
macOS. No third-party dependencies; stdlib only.

Usage::

    python scripts/package_skill.py            # build dist + zip
    python scripts/package_skill.py --no-zip   # build dist only
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path


# --- Source layout ---------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "literature-research"
SKILL_SRC = REPO_ROOT / ".claude" / "skills" / SKILL_NAME
DIST_ROOT = REPO_ROOT / "dist"
DIST_SKILL = DIST_ROOT / SKILL_NAME
ZIP_PATH = DIST_ROOT / f"{SKILL_NAME}.zip"

# An authoritative runtime source tree at the repository root, if present,
# overrides the bundled copy inside the Skill directory. In this repository
# the bundled ``runtime/src/my_search_harness`` is itself authoritative, so
# this is a no-op; the overlay keeps the packager correct if the source is
# ever hoisted to the repo root.
ROOT_RUNTIME_SRC = REPO_ROOT / "src" / "my_search_harness"


# --- What never ships in an export ----------------------------------------

# Directory names that must never be copied into a release.
EXCLUDE_DIRS = {
    ".venv",
    "venv",
    "env",
    "ENV",
    "__pycache__",
    ".pytest_cache",
    ".pytest-tmp",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".git",
    ".idea",
    ".vscode",
    "node_modules",
    "workspace",
    "build",
    "dist",
    "htmlcov",
    "secrets",
}

# Filename suffixes/patterns to skip. Matched by suffix for speed and by
# exact name for the OS/coverage artifacts.
EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".egg",
    ".egg-info",
    ".manifest",
    ".spec",
    ".log",
    ".coverage",
    ".cover",
    ".swp",
    ".swo",
}
EXCLUDE_NAMES = {
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    ".env",
    "pip-log.txt",
    "pip-delete-this-directory.txt",
    "coverage.xml",
}


def _excluded(path: Path) -> bool:
    """True if a file/dir must not ship in the export."""
    if path.name in EXCLUDE_DIRS or path.name in EXCLUDE_NAMES:
        return True
    return path.suffix in EXCLUDE_SUFFIXES


def _copy_tree(src: Path, dst: Path) -> list[Path]:
    """Copy ``src`` into ``dst`` (recursive), skipping excluded entries.

    Returns the list of files actually written, as Path objects inside
    ``dst``. Empty excluded directories are pruned.
    """
    written: list[Path] = []
    for root, dirs, files in os.walk(src):
        # Prune excluded directories in place so os.walk does not descend.
        dirs[:] = sorted(d for d in dirs if not _excluded(Path(d)))
        rel = Path(root).relative_to(src)
        for name in sorted(files):
            src_file = Path(root) / name
            if _excluded(src_file):
                continue
            dst_file = dst / rel / name
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
            written.append(dst_file)
    return written


def _overlay_runtime(dist_skill: Path) -> Path | None:
    """If a repo-root runtime source tree exists, overlay it as authoritative."""
    if not ROOT_RUNTIME_SRC.is_dir():
        return None
    target = dist_skill / "runtime" / "src" / "my_search_harness"
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT_RUNTIME_SRC, target, ignore=shutil.ignore_patterns("__pycache__"))
    return target


def _build_dist() -> list[Path]:
    if not SKILL_SRC.is_dir():
        raise SystemExit(
            f"Skill source not found: {SKILL_SRC}\n"
            "Run this script from the repository root."
        )
    if DIST_SKILL.exists():
        shutil.rmtree(DIST_SKILL)
    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    written = _copy_tree(SKILL_SRC, DIST_SKILL)
    _overlay_runtime(DIST_SKILL)
    return sorted(written)


def _make_zip() -> int:
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    count = 0
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(DIST_SKILL.rglob("*")):
            if path.is_file():
                # Archive name: literature-research/<rel>, so the zip unpacks
                # to a single top-level directory.
                arcname = Path(SKILL_NAME) / path.relative_to(DIST_SKILL)
                archive.write(path, arcname.as_posix())
                count += 1
    return count


def _print_manifest(written: list[Path]) -> None:
    print(f"Built {DIST_SKILL}")
    print(f"  {len(written)} files written")
    for path in written:
        print(f"    + {path.relative_to(DIST_ROOT).as_posix()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-zip", action="store_true", help="build dist only, skip the zip")
    args = parser.parse_args()

    written = _build_dist()
    _print_manifest(written)

    if args.no_zip:
        print("\nSkipped zip (--no-zip).")
        return 0

    count = _make_zip()
    print(f"\nWrote {ZIP_PATH} ({count} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
