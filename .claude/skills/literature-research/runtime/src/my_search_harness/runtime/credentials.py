"""User-local DeepXiv credential resolution and persistence.

The DeepXiv token lives in a single fixed user file:

    ~/.literature-research/deepxiv-token

A one-time interactive ``configure-token`` setup writes that file. The harness
reads it back when ``DEEPXIV_TOKEN`` is not already in the process environment,
so the DeepXiv providers (which read ``os.environ["DEEPXIV_TOKEN"]``) need no
changes. Resolution order is strict:

1. ``DEEPXIV_TOKEN`` environment variable (always wins, never overwritten).
2. ``~/.literature-research/deepxiv-token`` (fallback).
3. Unavailable.

The credential file is a single plain-text file holding only the token.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

_TOKEN_FILENAME = "deepxiv-token"
_APP_DIR_NAME = ".literature-research"


def deepxiv_token_file() -> Path:
    """Return the fixed user credential file path.

    ``Path.home() / ".literature-research" / "deepxiv-token"`` on every
    platform: ``C:\\Users\\<user>\\.literature-research\\deepxiv-token`` on
    Windows, ``/home/<user>/.literature-research/deepxiv-token`` on POSIX.
    """

    return Path.home() / _APP_DIR_NAME / _TOKEN_FILENAME


def resolve_deepxiv_token() -> str | None:
    """Return the active DeepXiv token, or ``None`` if unavailable.

    ``DEEPXIV_TOKEN`` in the environment always wins and is never overwritten
    by this call. The stored file is the fallback.
    """

    env_token = os.environ.get("DEEPXIV_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()

    path = deepxiv_token_file()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def store_deepxiv_token(token: str) -> Path:
    """Persist ``token`` to the fixed user credential file and return its path.

    The token is stripped of surrounding whitespace before storage. The
    parent directory is created. On POSIX the file is chmod ``0600`` as
    best-effort; Windows ACLs are not touched.
    """

    if not isinstance(token, str):
        raise TypeError("token must be a string")
    normalized = token.strip()
    if not normalized:
        raise ValueError("token must be a non-empty string")

    path = deepxiv_token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized, encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Best-effort restriction on POSIX; no-op on Windows.
        pass
    return path
