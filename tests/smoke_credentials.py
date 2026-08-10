"""Real local smoke test for the DeepXiv credential flow.

Drives the actual code paths (configure-token, then doctor, then external
runtime token loading) with a FAKE token and a temporary home directory, so
the developer's real credential is never touched. Uses the Skill venv
interpreter so the deepxiv_sdk import check passes and `healthy` can be true.

`configure-token` is driven in-process (importing harness.main and patching
`getpass.getpass`) because on Windows `getpass` reads from the console, not
from a piped stdin. `doctor` and the bootstrap-injection check are driven as
real subprocesses.

Run:

    ./.venv/Scripts/python.exe tests/smoke_credentials.py   (Windows)
    ./.venv/bin/python tests/smoke_credentials.py           (POSIX)

Exits 0 on success, nonzero on failure. Cleans up the temp home dir.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

SKILL_DIR = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "literature-research"
)
HARNESS = SKILL_DIR / "scripts" / "harness.py"
DOCTOR = SKILL_DIR / "scripts" / "doctor.py"
RUNTIME_SRC = SKILL_DIR / "runtime" / "src"
VENV_PY = SKILL_DIR / ".venv" / "Scripts" / "python.exe"
if not VENV_PY.is_file():
    VENV_PY = SKILL_DIR / ".venv" / "bin" / "python"

FAKE_TOKEN = "smoke-fake-token-value"


def _load_harness():
    if str(RUNTIME_SRC) not in sys.path:
        sys.path.insert(0, str(RUNTIME_SRC))
    spec = importlib.util.spec_from_file_location("lr_harness_smoke", HARNESS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _home_env(home: Path) -> dict[str, str]:
    """Build a subprocess env that isolates Path.home() and clears the token."""

    env = os.environ.copy()
    env.pop("DEEPXIV_TOKEN", None)
    env["USERPROFILE"] = str(home)
    env["HOME"] = str(home)
    env["CLAUDE_SKILL_DIR"] = str(SKILL_DIR)
    return env


def main() -> int:
    if not VENV_PY.is_file():
        print(f"venv python not found at {VENV_PY}; run scripts/setup.py first", file=sys.stderr)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="lr-smoke-"))
    try:
        home = tmp / "home"
        home.mkdir()
        workspace = tmp / "ws"
        workspace.mkdir()

        # --- In-process: configure-token with a fake token via getpass mock.
        # Isolate Path.home() in THIS process before importing/calling harness.
        os.environ["USERPROFILE"] = str(home)
        os.environ["HOME"] = str(home)
        os.environ.pop("DEEPXIV_TOKEN", None)
        os.environ["CLAUDE_SKILL_DIR"] = str(SKILL_DIR)

        harness = _load_harness()
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        print(">> configure-token (in-process, getpass mocked)")
        with mock.patch("getpass.getpass", return_value=FAKE_TOKEN):
            exit_code = harness.main(["configure-token"], stdout=out_buf, stderr=err_buf)
        print("stdout:", out_buf.getvalue().strip())
        print("stderr:", err_buf.getvalue().strip())
        if exit_code != 0:
            print(f"configure-token failed (exit {exit_code})", file=sys.stderr)
            return 1
        payload = json.loads(out_buf.getvalue())
        assert payload["ok"] is True, payload
        assert payload["result"]["saved"] is True, payload
        assert FAKE_TOKEN not in out_buf.getvalue(), "token leaked to stdout!"
        assert FAKE_TOKEN not in err_buf.getvalue(), "token leaked to stderr!"
        cred_path = Path(payload["result"]["path"])
        assert cred_path.is_file(), "credential file not written"
        assert cred_path == home / ".literature-research" / "deepxiv-token", cred_path
        print(f"   credential file: {cred_path}")

        # --- Subprocess: doctor with the stored credential alone.
        env = _home_env(home)
        print(">> doctor (subprocess, stored credential only)")
        proc = subprocess.run(
            [str(VENV_PY), str(DOCTOR), "--workspace", str(workspace)],
            capture_output=True,
            text=True,
            env=env,
        )
        print("stdout:", proc.stdout.strip())
        print("stderr:", proc.stderr.strip())
        if proc.returncode != 0:
            print(f"doctor failed (exit {proc.returncode})", file=sys.stderr)
            return 1
        result = json.loads(proc.stdout)
        checks = result["checks"]
        assert checks["deepxiv_token"]["present"] is True, checks["deepxiv_token"]
        assert "source" not in checks["deepxiv_token"], checks["deepxiv_token"]
        assert result["healthy"] is True, result
        assert FAKE_TOKEN not in proc.stdout, "token leaked to doctor stdout!"
        assert FAKE_TOKEN not in proc.stderr, "token leaked to doctor stderr!"
        print("   deepxiv_token.present = True")
        print("   healthy = True")

        # --- Subprocess: external runtime token loading.
        # The harness bootstrap must inject the stored token into
        # os.environ["DEEPXIV_TOKEN"] for the process when the env is unset,
        # so LocalV1Runtime.from_deepxiv_env() can build the DeepXiv providers.
        # We do NOT call the real DeepXiv API; we only confirm the bootstrap
        # makes the token visible to the process before any provider is built.
        print(">> external runtime token loading (subprocess, stored credential only)")
        probe = (
            "import os, sys; "
            f"sys.path.insert(0, {str(RUNTIME_SRC)!r}); "
            "import importlib.util; "
            f"spec = importlib.util.spec_from_file_location('h', {str(HARNESS)!r}); "
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
            "print('DEEPXIV_TOKEN_SET=' + ('yes' if os.environ.get('DEEPXIV_TOKEN') else 'no'))"
        )
        proc = subprocess.run(
            [str(VENV_PY), "-c", probe],
            capture_output=True,
            text=True,
            env=env,
        )
        print("stdout:", proc.stdout.strip())
        print("stderr:", proc.stderr.strip())
        if proc.returncode != 0:
            print(f"bootstrap probe failed (exit {proc.returncode})", file=sys.stderr)
            return 1
        assert "DEEPXIV_TOKEN_SET=yes" in proc.stdout, proc.stdout
        assert FAKE_TOKEN not in proc.stdout, "token leaked by bootstrap probe!"
        print("   DEEPXIV_TOKEN visible to process from stored file")

        print("\nSMOKE OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
