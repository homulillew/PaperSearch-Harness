"""Real local smoke test for the DeepXiv credential flow.

Drives the actual code paths (configure-token, then doctor) with a FAKE token
and a temporary user-config directory, so the developer's real credential is
never touched. Uses the Skill venv interpreter so the deepxiv_sdk import check
passes and `healthy` can be true.

`configure-token` is driven in-process (importing harness.main and patching
`getpass.getpass`) because on Windows `getpass` reads from the console, not
from a piped stdin. `doctor` is driven as a real subprocess since it does not
read secrets.

Run:

    ./.venv/Scripts/python.exe tests/smoke_credentials.py

Exits 0 on success, nonzero on failure. Cleans up the temp config dir.
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

SKILL_DIR = Path(__file__).resolve().parents[1]
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


def main() -> int:
    if not VENV_PY.is_file():
        print(f"venv python not found at {VENV_PY}; run scripts/setup.py first", file=sys.stderr)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="lr-smoke-"))
    try:
        config_root = tmp / "config"
        config_root.mkdir()
        workspace = tmp / "ws"
        workspace.mkdir()

        # --- In-process: configure-token with a fake token via getpass mock.
        # We isolate the credential file by setting LOCALAPPDATA / XDG_CONFIG_HOME
        # in THIS process before importing/calling harness.
        os.environ["LOCALAPPDATA"] = str(config_root)
        os.environ["XDG_CONFIG_HOME"] = str(config_root)
        os.environ["HOME"] = str(tmp / "home")
        (tmp / "home").mkdir(exist_ok=True)
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
        print(f"   credential file: {cred_path}")

        # --- Subprocess: doctor with the stored credential alone.
        env = os.environ.copy()
        env.pop("DEEPXIV_TOKEN", None)  # prove stored credential alone suffices
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
        assert checks["deepxiv_token"]["source"] == "user_credentials", checks["deepxiv_token"]
        assert result["healthy"] is True, result
        assert FAKE_TOKEN not in proc.stdout, "token leaked to doctor stdout!"
        assert FAKE_TOKEN not in proc.stderr, "token leaked to doctor stderr!"
        print("   deepxiv_token.present = True, source = user_credentials")
        print("   healthy = True")

        # --- Subprocess: doctor with an env override (env must win, file untouched).
        print(">> doctor (subprocess, DEEPXIV_TOKEN env override)")
        env["DEEPXIV_TOKEN"] = "env-override-token"
        proc = subprocess.run(
            [str(VENV_PY), str(DOCTOR), "--workspace", str(workspace)],
            capture_output=True,
            text=True,
            env=env,
        )
        result = json.loads(proc.stdout)
        assert result["checks"]["deepxiv_token"]["source"] == "environment", result
        assert cred_path.read_text(encoding="utf-8") == FAKE_TOKEN, "stored file mutated"
        print("   source = environment (env wins, stored file untouched)")

        print("\nSMOKE OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
