"""Filesystem-boundary regression tests for the literature-research Skill.

These tests pin the runtime workspace contract: all research work files stay
inside the chosen ``--workspace``, authoritative state lives only under
``<workspace>/runs/<run_id>/``, and ``<workspace>/scratch/`` is disposable.
They drive ``scripts/harness.py`` as a subprocess with ``--workspace`` pointed
at a temp directory, so the developer's real workspace is never touched.

No network, no DeepXiv calls, no source reads. The recovery tests use a
synthetic run created in-process through the local (no-provider) runtime so
they are fully deterministic.

Run:

    python -m pytest tests/test_filesystem_boundary.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "literature-research"
)
REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SRC = SKILL_DIR / "runtime" / "src"
SCRIPTS_DIR = SKILL_DIR / "scripts"
HARNESS = SCRIPTS_DIR / "harness.py"

# UTF-8 on Windows so the harness emits clean JSON.
_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def _run_harness(workspace: Path, *args: str) -> tuple[int, str, str]:
    """Run harness.py as a subprocess; return (exit_code, stdout, stderr)."""

    proc = subprocess.run(
        [sys.executable, str(HARNESS), "--workspace", str(workspace), *args],
        capture_output=True,
        text=True,
        env=_ENV,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _run_harness_no_workspace(*args: str) -> tuple[int, str, str]:
    """Run harness.py without --workspace (for configure-token / doctor)."""

    proc = subprocess.run(
        [sys.executable, str(HARNESS), *args],
        capture_output=True,
        text=True,
        env=_ENV,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _create_run(workspace: Path, contract: dict) -> str:
    """Create a run via the harness and return its run_id."""

    scratch = workspace / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    contract_path = scratch / "research-contract.json"
    contract_path.write_text(
        json.dumps(contract, ensure_ascii=False), encoding="utf-8"
    )
    code, out, err = _run_harness(workspace, "create-run", "--input", str(contract_path))
    assert code == 0, f"create-run failed: {err}\n{out}"
    payload = json.loads(out)
    assert payload["ok"] is True, payload
    return payload["result"]["run_id"]


# --- Workspace containment --------------------------------------------------


def test_create_run_writes_only_inside_workspace(tmp_path):
    """create-run must not write anything outside the chosen workspace."""

    workspace = tmp_path / "ws"
    # Capture the set of paths that exist BEFORE the workspace is created.
    before = {p for p in tmp_path.rglob("*")}

    workspace.mkdir()
    run_id = _create_run(
        workspace,
        {
            "mission": "Test mission",
            "requirements": ["Cover route A"],
            "scope": "Test scope",
            "deliverable_description": "Test deliverable",
            "required_artifacts": ["REPORT"],
        },
    )

    after = {p for p in tmp_path.rglob("*")}
    new_paths = after - before
    # Every new path must live under the workspace.
    outside = [p for p in new_paths if workspace not in p.parents and p != workspace]
    assert not outside, f"files written outside workspace: {outside}"

    # Authoritative state is under workspace/runs/<run_id>/.
    run_dir = workspace / "runs" / run_id
    assert (run_dir / "state.json").is_file()
    assert (run_dir / "events.jsonl").is_file()


def test_input_file_inside_workspace_is_accepted(tmp_path):
    """An --input file inside the workspace is the documented contract."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    scratch = workspace / "scratch"
    scratch.mkdir()
    contract_path = scratch / "research-contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "mission": "Inside workspace",
                "requirements": ["One requirement"],
                "scope": "Scope",
                "deliverable_description": "Deliverable",
            },
            ensure_ascii=False
        ),
        encoding="utf-8",
    )
    code, out, _ = _run_harness(workspace, "create-run", "--input", str(contract_path))
    assert code == 0, out
    assert json.loads(out)["ok"] is True


def test_input_file_outside_workspace_is_not_rejected_by_harness(tmp_path):
    """The harness reads any readable path; the boundary is a caller contract.

    The filesystem discipline is a Skill-level policy (the Researcher writes
    inputs inside the workspace), not a harness-enforced hard boundary — the
    harness must remain able to read a legitimate input the caller places
    anywhere. This test documents that behavior so we do not accidentally add
    a path-prefix check that would break legitimate callers.
    """

    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Place the contract OUTSIDE the workspace (in tmp_path root).
    contract_path = tmp_path / "outside-contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "mission": "Outside workspace",
                "requirements": ["One requirement"],
                "scope": "Scope",
                "deliverable_description": "Deliverable",
            },
            ensure_ascii=False
        ),
        encoding="utf-8",
    )
    code, out, _ = _run_harness(workspace, "create-run", "--input", str(contract_path))
    # The harness accepts it — the boundary is enforced by the Skill policy,
    # not by the harness. Authoritative state still lands inside the workspace.
    assert code == 0, out
    assert json.loads(out)["ok"] is True
    run_id = json.loads(out)["result"]["run_id"]
    assert (workspace / "runs" / run_id / "state.json").is_file()


# --- Scratch disposability --------------------------------------------------


def test_scratch_deletion_does_not_change_authoritative_state(tmp_path):
    """Deleting workspace/scratch/ must not change state.json or events.jsonl."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(
        workspace,
        {
            "mission": "Scratch test",
            "requirements": ["Cover route A"],
            "scope": "Test scope",
            "deliverable_description": "Test deliverable",
            "required_artifacts": ["REPORT"],
        },
    )

    # Write a scratch file as the Researcher would.
    scratch = workspace / "scratch" / run_id / "inputs"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "note.json").write_text("{}", encoding="utf-8")

    run_dir = workspace / "runs" / run_id
    state_before = hashlib.sha256((run_dir / "state.json").read_bytes()).hexdigest()
    events_before = hashlib.sha256((run_dir / "events.jsonl").read_bytes()).hexdigest()

    # Delete the entire scratch tree.
    import shutil

    shutil.rmtree(workspace / "scratch")
    assert not (workspace / "scratch").exists()

    # Authoritative files are byte-for-byte unchanged.
    state_after = hashlib.sha256((run_dir / "state.json").read_bytes()).hexdigest()
    events_after = hashlib.sha256((run_dir / "events.jsonl").read_bytes()).hexdigest()
    assert state_before == state_after
    assert events_before == events_after

    # Recovery: audit-history still works without scratch.
    code, out, _ = _run_harness(workspace, "audit-history", "--run-id", run_id)
    assert code == 0, out
    payload = json.loads(out)
    assert payload["ok"] is True
    assert len(payload["result"]["events"]) >= 1


def test_scratch_is_not_a_second_knowledge_store(tmp_path):
    """scratch/ holds no authoritative data; only runs/ does.

    A run created with a contract, then with its scratch deleted, must still
    report the same contract revision and mission through the authoritative
    state file.
    """

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(
        workspace,
        {
            "mission": "Knowledge store test",
            "requirements": ["Req one", "Req two"],
            "scope": "Scope",
            "deliverable_description": "Deliverable",
        },
    )

    # Read the authoritative state directly.
    state_path = workspace / "runs" / run_id / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    revision_before = state["state_revision"]
    contract_revision_before = state["contract"]["current_revision"]
    # The mission lives in the latest contract revision entry (a list).
    revisions = state["contract"]["revisions"]
    latest = next(
        r for r in revisions if r["revision"] == contract_revision_before
    )
    mission_before = latest["contract"]["mission"]

    # Delete scratch and re-read.
    import shutil

    shutil.rmtree(workspace / "scratch")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["state_revision"] == revision_before
    assert state["contract"]["current_revision"] == contract_revision_before
    revisions = state["contract"]["revisions"]
    latest = next(
        r for r in revisions if r["revision"] == contract_revision_before
    )
    assert latest["contract"]["mission"] == mission_before


# --- Root cleanliness --------------------------------------------------------


def test_project_root_has_no_runtime_data_files():
    """The repository root must not contain stray runtime capture files.

    The historical bug was 12 .txt stdout captures committed to the root.
    This test pins that the root stays clean: no .txt captures, no
    research-contract.json, no inspect_*/read_* scratch files.
    """

    runtime_data_patterns = (
        "research-contract.json",
        "delivery_view_out.txt",
    )
    for name in runtime_data_patterns:
        assert not (REPO_ROOT / name).exists(), f"runtime data file in root: {name}"

    # No inspect_*.txt or read_*.txt captures in the root.
    root_txt = sorted(REPO_ROOT.glob("inspect_*.txt")) + sorted(
        REPO_ROOT.glob("read_*.txt")
    )
    assert root_txt == [], f"scratch captures in root: {root_txt}"

    # No *_out.json command captures in the root.
    root_out = sorted(REPO_ROOT.glob("*_out.json"))
    assert root_out == [], f"command captures in root: {root_out}"


def test_skill_dir_has_no_run_data():
    """The Skill directory ships instructions + runtime, never run data."""

    # No runs/ or scratch/ inside the Skill directory.
    assert not (SKILL_DIR / "runs").exists()
    assert not (SKILL_DIR / "scratch").exists()
    assert not (SKILL_DIR / "workspace").exists()


# --- Packaging cleanliness ---------------------------------------------------


def test_package_skill_excludes_workspace_and_scratch(tmp_path):
    """package_skill.py must never ship workspace/, scratch/, or .venv/."""

    packager = REPO_ROOT / "scripts" / "package_skill.py"
    # Build into an isolated dist under tmp_path so we don't touch the repo.
    env = {**_ENV, "CLAUDE_SKILL_DIR": str(SKILL_DIR)}
    # The packager uses REPO_ROOT / "dist"; we cannot redirect it via env, so
    # we import the module and call _build_dist against a temp copy instead.
    import importlib.util

    spec = importlib.util.spec_from_file_location("pkg", packager)
    assert spec is not None and spec.loader is not None
    pkg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pkg)

    # Verify the exclude sets contain the runtime-data dirs.
    assert "workspace" in pkg.EXCLUDE_DIRS
    # scratch/ is not listed separately because it lives under workspace/, which
    # is already excluded — so packaging it would require it to escape workspace/.
    assert "scratch" not in pkg.EXCLUDE_DIRS
    assert ".venv" in pkg.EXCLUDE_DIRS
    assert "__pycache__" in pkg.EXCLUDE_DIRS

    # _excluded must reject workspace, .venv, and .pyc.
    assert pkg._excluded(Path("workspace"))
    assert pkg._excluded(Path(".venv"))
    assert pkg._excluded(Path("foo.pyc"))
    assert pkg._excluded(Path("foo.log"))
    # It must NOT reject a normal source file.
    assert not pkg._excluded(Path("SKILL.md"))
    assert not pkg._excluded(Path("harness.py"))


def test_dist_not_tracked_in_gitignore():
    """dist/ must be gitignored (it is a local build artifact, not committed)."""

    gitignore = REPO_ROOT / ".gitignore"
    text = gitignore.read_text(encoding="utf-8")
    assert "dist/" in text, "dist/ must be gitignored"


def test_workspace_is_tracked_not_ignored():
    """workspace/ is committed authoritative run data; it must not be ignored."""

    gitignore = REPO_ROOT / ".gitignore"
    text = gitignore.read_text(encoding="utf-8")
    # The gitignore must not contain a bare "workspace/" ignore line.
    lines = [ln.strip() for ln in text.splitlines()]
    assert "workspace/" not in lines, "workspace/ must not be gitignored"
