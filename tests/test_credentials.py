"""Deterministic tests for the DeepXiv credential boundary.

These tests exercise only the credential resolution / persistence logic and
the workspace-independent ``configure-token`` / ``doctor`` wiring. They use
obviously fake token values (never a real token) and point the credential
file at a temporary home directory so the developer's real credential is
never read or overwritten.

Run with the system Python (pytest is a dev dependency, not in the runtime
requirements):

    python -m pytest tests/test_credentials.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

SKILL_DIR = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "literature-research"
)
RUNTIME_SRC = SKILL_DIR / "runtime" / "src"
SCRIPTS_DIR = SKILL_DIR / "scripts"

# Make the runtime importable without the venv. credentials.py is stdlib-only.
if str(RUNTIME_SRC) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SRC))

from my_search_harness.runtime import credentials  # noqa: E402

FAKE_TOKEN = "test-token-value"
FAKE_TOKEN_2 = "another-test-token-value"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point ``Path.home()`` at a temp dir and clear the env token.

    The credential file is fixed at ``~/.literature-research/deepxiv-token``,
    so isolating ``Path.home()`` is all that is needed to keep the test away
    from the developer's real credential. We also clear ``DEEPXIV_TOKEN`` and
    the platform home env vars so ``Path.home()`` resolves to the temp dir.
    """

    monkeypatch.delenv("DEEPXIV_TOKEN", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    # Path.home() reads USERPROFILE on Windows, HOME on POSIX.
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return home


# --- Path resolution --------------------------------------------------------


def test_fixed_path_under_home(isolated_home):
    # mock Path.home() -> C:/Users/test style; expect the fixed subpath.
    with mock.patch.object(credentials.Path, "home", return_value=Path("C:/Users/test")):
        path = credentials.deepxiv_token_file()
    assert path == Path("C:/Users/test/.literature-research/deepxiv-token")


def test_path_resolves_under_isolated_home(isolated_home):
    path = credentials.deepxiv_token_file()
    assert path == isolated_home / ".literature-research" / "deepxiv-token"


# --- read / write -----------------------------------------------------------


def test_store_then_read_roundtrip(isolated_home):
    path = credentials.store_deepxiv_token(FAKE_TOKEN)
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == FAKE_TOKEN


def test_store_strips_surrounding_whitespace(isolated_home):
    path = credentials.store_deepxiv_token(f"  {FAKE_TOKEN}\n")
    assert path.read_text(encoding="utf-8") == FAKE_TOKEN


def test_store_rejects_empty_token(isolated_home):
    with pytest.raises(ValueError):
        credentials.store_deepxiv_token("")
    with pytest.raises(ValueError):
        credentials.store_deepxiv_token("   ")


def test_store_rejects_non_string(isolated_home):
    with pytest.raises(TypeError):
        credentials.store_deepxiv_token(123)  # type: ignore[arg-type]


# --- resolve: precedence ----------------------------------------------------


def test_stored_token_works_without_env(isolated_home):
    credentials.store_deepxiv_token(FAKE_TOKEN)
    assert credentials.resolve_deepxiv_token() == FAKE_TOKEN


def test_environment_override_wins_over_stored(isolated_home, monkeypatch):
    credentials.store_deepxiv_token(FAKE_TOKEN)  # stored = "test-token-value"
    monkeypatch.setenv("DEEPXIV_TOKEN", FAKE_TOKEN_2)  # env = A
    assert credentials.resolve_deepxiv_token() == FAKE_TOKEN_2


def test_missing_when_neither_env_nor_file(isolated_home):
    assert credentials.resolve_deepxiv_token() is None


def test_env_whitespace_only_falls_through_to_stored(isolated_home, monkeypatch):
    credentials.store_deepxiv_token(FAKE_TOKEN)
    monkeypatch.setenv("DEEPXIV_TOKEN", "   ")
    assert credentials.resolve_deepxiv_token() == FAKE_TOKEN


def test_empty_file_treated_as_missing(isolated_home):
    path = credentials.deepxiv_token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("  \n", encoding="utf-8")
    assert credentials.resolve_deepxiv_token() is None


# --- configure-token command wiring ----------------------------------------


def _load_harness_module():
    """Load scripts/harness.py as an isolated module.

    harness.py runs ``_bootstrap_runtime()`` and a credential bootstrap at
    import time. The credential bootstrap calls ``resolve_deepxiv_token()``,
    which is safe (returns None when nothing is configured). We load the
    module fresh so ``mock.patch`` on ``getpass`` applies cleanly.
    """

    spec = importlib.util.spec_from_file_location(
        "lr_harness_under_test", SCRIPTS_DIR / "harness.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configure_token_writes_file_and_does_not_print_token(
    isolated_home, capsys, monkeypatch
):
    harness = _load_harness_module()
    with mock.patch("getpass.getpass", return_value=FAKE_TOKEN):
        result = harness._configure_token()
    assert result["saved"] is True
    assert result["path"] == str(credentials.deepxiv_token_file())
    assert result["message"] == "DeepXiv credential saved."
    # The token must be persisted...
    assert credentials.deepxiv_token_file().read_text(encoding="utf-8") == FAKE_TOKEN
    # ...and must NOT appear in captured stdout/stderr.
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in captured.out
    assert FAKE_TOKEN not in captured.err


def test_configure_token_rejects_empty_input(isolated_home, monkeypatch):
    harness = _load_harness_module()
    with mock.patch("getpass.getpass", return_value=""):
        with pytest.raises(harness.AdapterInputError):
            harness._configure_token()
    # Nothing should have been written.
    assert not credentials.deepxiv_token_file().exists()


def test_configure_token_rejects_whitespace_only_input(isolated_home, monkeypatch):
    harness = _load_harness_module()
    with mock.patch("getpass.getpass", return_value="   "):
        with pytest.raises(harness.AdapterInputError):
            harness._configure_token()


def test_configure_token_does_not_require_workspace(isolated_home):
    """configure-token must parse and run without --workspace."""

    harness = _load_harness_module()
    with mock.patch("getpass.getpass", return_value=FAKE_TOKEN):
        exit_code = harness.main(["configure-token"])
    assert exit_code == 0
    assert credentials.deepxiv_token_file().read_text(encoding="utf-8") == FAKE_TOKEN


# --- doctor wiring ----------------------------------------------------------


def _load_doctor_module():
    spec = importlib.util.spec_from_file_location(
        "lr_doctor_under_test", SCRIPTS_DIR / "doctor.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_doctor_reports_stored_credential_present(isolated_home, tmp_path):
    doctor = _load_doctor_module()
    credentials.store_deepxiv_token(FAKE_TOKEN)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = doctor.run_checks(workspace, SKILL_DIR)
    token_check = result["checks"]["deepxiv_token"]
    assert token_check["present"] is True
    # No source metadata is tracked.
    assert "source" not in token_check


def test_doctor_reports_missing_credential(isolated_home, tmp_path):
    doctor = _load_doctor_module()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = doctor.run_checks(workspace, SKILL_DIR)
    token_check = result["checks"]["deepxiv_token"]
    assert token_check["present"] is False
    assert "source" not in token_check


def test_doctor_does_not_leak_token_in_output(isolated_home, tmp_path, capsys):
    doctor = _load_doctor_module()
    credentials.store_deepxiv_token(FAKE_TOKEN)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    doctor.run_checks(workspace, SKILL_DIR)
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in captured.out
    assert FAKE_TOKEN not in captured.err
