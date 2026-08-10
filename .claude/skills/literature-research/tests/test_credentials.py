"""Deterministic tests for the DeepXiv credential boundary.

These tests exercise only the credential resolution / persistence logic and
the workspace-independent ``configure-token`` / ``doctor`` wiring. They use
obviously fake token values (never a real token) and point the credential
file at a temporary user-config directory so the developer's real credential
is never read or overwritten.

Run with the system Python (pytest is a dev dependency, not in the runtime
requirements):

    python -m pytest .claude/skills/literature-research/tests/test_credentials.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

SKILL_DIR = (
    Path(__file__).resolve().parents[1]
    if "__file__" in globals()
    else Path.cwd()
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
def isolated_config(tmp_path, monkeypatch):
    """Point the credential file at a temp dir; clear env token.

    On Windows the resolver keys off LOCALAPPDATA; on POSIX off XDG_CONFIG_HOME
    (then ~/.config). We set both so the test is platform-independent.
    """

    monkeypatch.delenv("DEEPXIV_TOKEN", raising=False)
    config_root = tmp_path / "user-config"
    config_root.mkdir()
    # Windows path: <LOCALAPPDATA>/literature-research/deepxiv-token
    monkeypatch.setenv("LOCALAPPDATA", str(config_root))
    # POSIX path: <XDG_CONFIG_HOME>/literature-research/deepxiv-token
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))
    # Also set HOME so Path.home() is deterministic on POSIX fallback.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    return config_root


# --- Path resolution --------------------------------------------------------


def test_windows_path_uses_localappdata(isolated_config):
    # On Windows the resolver keys off LOCALAPPDATA; on POSIX it keys off
    # XDG_CONFIG_HOME. isolated_config sets both to the same root, so the
    # expected path is the same regardless of host platform.
    path = credentials.deepxiv_token_file()
    assert path == isolated_config / "literature-research" / "deepxiv-token"


def test_windows_path_falls_back_to_home_without_localappdata(tmp_path, monkeypatch):
    # Only meaningful on Windows (POSIX uses XDG/HOME, not LOCALAPPDATA). We
    # drive the Windows branch by unsetting LOCALAPPDATA and pointing HOME at
    # a temp dir via USERPROFILE, which is what Path.home() reads on Windows.
    if os.name != "nt":
        pytest.skip("LOCALAPPDATA fallback is a Windows-only path")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    path = credentials.deepxiv_token_file()
    assert path == tmp_path / "profile" / ".config" / "literature-research" / "deepxiv-token"


def test_posix_path_uses_xdg_config_home(isolated_config):
    # XDG_CONFIG_HOME is honored on POSIX. On Windows the resolver uses
    # LOCALAPPDATA instead (isolated_config sets both to the same root), so
    # the expected path is identical.
    path = credentials.deepxiv_token_file()
    assert path == isolated_config / "literature-research" / "deepxiv-token"


def test_posix_path_falls_back_to_home_config(tmp_path, monkeypatch):
    # POSIX ~/.config fallback. Only runnable where Path.home() yields a
    # PosixPath, i.e. on a real POSIX host.
    if os.name != "posix":
        pytest.skip("~/.config fallback requires a POSIX Path.home()")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    path = credentials.deepxiv_token_file()
    assert path == tmp_path / "home" / ".config" / "literature-research" / "deepxiv-token"


# --- read / write -----------------------------------------------------------


def test_store_then_read_roundtrip(isolated_config):
    path = credentials.store_deepxiv_token(FAKE_TOKEN)
    assert path.is_file()
    assert credentials.read_stored_deepxiv_token() == FAKE_TOKEN


def test_store_strips_surrounding_whitespace(isolated_config):
    credentials.store_deepxiv_token(f"  {FAKE_TOKEN}\n")
    assert credentials.read_stored_deepxiv_token() == FAKE_TOKEN


def test_read_missing_file_returns_none(isolated_config):
    assert credentials.read_stored_deepxiv_token() is None


def test_read_empty_file_returns_none(isolated_config):
    path = credentials.deepxiv_token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    assert credentials.read_stored_deepxiv_token() is None


def test_read_whitespace_only_file_returns_none(isolated_config):
    path = credentials.deepxiv_token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("   \n\t  \n", encoding="utf-8")
    assert credentials.read_stored_deepxiv_token() is None


def test_store_rejects_empty_token(isolated_config):
    with pytest.raises(ValueError):
        credentials.store_deepxiv_token("")
    with pytest.raises(ValueError):
        credentials.store_deepxiv_token("   ")


def test_store_rejects_non_string(isolated_config):
    with pytest.raises(TypeError):
        credentials.store_deepxiv_token(123)  # type: ignore[arg-type]


# --- resolve: precedence ----------------------------------------------------


def test_environment_wins_over_stored(isolated_config, monkeypatch):
    credentials.store_deepxiv_token(FAKE_TOKEN)
    monkeypatch.setenv("DEEPXIV_TOKEN", FAKE_TOKEN_2)
    token, source = credentials.resolve_deepxiv_token()
    assert token == FAKE_TOKEN_2
    assert source == "environment"


def test_stored_fallback_when_env_missing(isolated_config):
    credentials.store_deepxiv_token(FAKE_TOKEN)
    token, source = credentials.resolve_deepxiv_token()
    assert token == FAKE_TOKEN
    assert source == "user_credentials"


def test_missing_when_neither_env_nor_file(isolated_config):
    token, source = credentials.resolve_deepxiv_token()
    assert token is None
    assert source is None


def test_env_whitespace_only_falls_through_to_stored(isolated_config, monkeypatch):
    credentials.store_deepxiv_token(FAKE_TOKEN)
    monkeypatch.setenv("DEEPXIV_TOKEN", "   ")
    token, source = credentials.resolve_deepxiv_token()
    assert token == FAKE_TOKEN
    assert source == "user_credentials"


def test_empty_file_treated_as_missing(isolated_config):
    path = credentials.deepxiv_token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("  \n", encoding="utf-8")
    token, source = credentials.resolve_deepxiv_token()
    assert token is None
    assert source is None


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
    isolated_config, capsys, monkeypatch
):
    harness = _load_harness_module()
    with mock.patch("getpass.getpass", return_value=FAKE_TOKEN):
        result = harness._configure_token()
    assert result["saved"] is True
    assert result["path"] == str(credentials.deepxiv_token_file())
    assert result["message"] == "DeepXiv credential saved to user configuration."
    # The token must be persisted...
    assert credentials.read_stored_deepxiv_token() == FAKE_TOKEN
    # ...and must NOT appear in captured stdout/stderr.
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in captured.out
    assert FAKE_TOKEN not in captured.err


def test_configure_token_rejects_empty_input(isolated_config, monkeypatch):
    harness = _load_harness_module()
    with mock.patch("getpass.getpass", return_value=""):
        with pytest.raises(harness.AdapterInputError):
            harness._configure_token()
    # Nothing should have been written.
    assert credentials.read_stored_deepxiv_token() is None


def test_configure_token_rejects_whitespace_only_input(isolated_config, monkeypatch):
    harness = _load_harness_module()
    with mock.patch("getpass.getpass", return_value="   "):
        with pytest.raises(harness.AdapterInputError):
            harness._configure_token()


def test_configure_token_does_not_require_workspace(isolated_config):
    """configure-token must parse and run without --workspace.

    We invoke the real ``main`` with argv that omits --workspace entirely and
    confirm it does not raise an argparse error about a missing workspace.
    """

    harness = _load_harness_module()
    with mock.patch("getpass.getpass", return_value=FAKE_TOKEN):
        exit_code = harness.main(["configure-token"])
    assert exit_code == 0
    assert credentials.read_stored_deepxiv_token() == FAKE_TOKEN


# --- doctor wiring ----------------------------------------------------------


def _load_doctor_module():
    spec = importlib.util.spec_from_file_location(
        "lr_doctor_under_test", SCRIPTS_DIR / "doctor.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_doctor_reports_stored_credential_present(isolated_config, tmp_path):
    doctor = _load_doctor_module()
    credentials.store_deepxiv_token(FAKE_TOKEN)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = doctor.run_checks(workspace, SKILL_DIR)
    token_check = result["checks"]["deepxiv_token"]
    assert token_check["present"] is True
    assert token_check["source"] == "user_credentials"


def test_doctor_reports_environment_credential_present(
    isolated_config, tmp_path, monkeypatch
):
    doctor = _load_doctor_module()
    monkeypatch.setenv("DEEPXIV_TOKEN", FAKE_TOKEN)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = doctor.run_checks(workspace, SKILL_DIR)
    token_check = result["checks"]["deepxiv_token"]
    assert token_check["present"] is True
    assert token_check["source"] == "environment"


def test_doctor_reports_missing_credential(isolated_config, tmp_path):
    doctor = _load_doctor_module()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = doctor.run_checks(workspace, SKILL_DIR)
    token_check = result["deepxiv_token"] if "deepxiv_token" in result else result["checks"]["deepxiv_token"]
    assert token_check["present"] is False
    assert token_check["source"] is None


def test_doctor_does_not_leak_token_in_output(isolated_config, tmp_path, capsys):
    doctor = _load_doctor_module()
    credentials.store_deepxiv_token(FAKE_TOKEN)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    doctor.run_checks(workspace, SKILL_DIR)
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in captured.out
    assert FAKE_TOKEN not in captured.err
