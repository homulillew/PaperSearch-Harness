"""User-local DeepXiv credential resolution and persistence.

The standalone Skill owns a thin credential boundary so that a one-time
interactive ``configure-token`` setup is enough: the credential is stored in
the user's local configuration directory and re-injected into the process
environment at harness bootstrap, so the DeepXiv providers (which read
``DEEPXIV_TOKEN`` from ``os.environ``) need no changes.

Resolution order is strict and two-layered:

1. ``DEEPXIV_TOKEN`` environment variable (always wins, never overwritten).
2. User-local credential file (fallback).
3. Unavailable.

The credential file is a single plain-text file holding only the token.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

_TOKEN_FILENAME = "deepxiv-token"
_APP_DIR_NAME = "literature-research"


def deepxiv_token_file() -> Path:
    """Resolve the user-local credential file path deterministically.

    Windows: ``%LOCALAPPDATA%\\literature-research\\deepxiv-token`` (falls back
    to a ``.config``-style directory under the user home when ``LOCALAPPDATA``
    is unset). POSIX: ``${XDG_CONFIG_HOME:-~/.config}/literature-research/deepxiv-token``.

    No ``platformdirs`` dependency; just the small deterministic mapping the
    Skill needs.
    """

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            base = Path(local_app_data)
        else:
            base = Path.home() / ".config"
        return base / _APP_DIR_NAME / _TOKEN_FILENAME

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        base = Path(xdg_config_home)
    else:
        base = Path.home() / ".config"
    return base / _APP_DIR_NAME / _TOKEN_FILENAME


def read_stored_deepxiv_token() -> str | None:
    """Return the stored token, or ``None`` if absent / empty / whitespace-only."""

    path = deepxiv_token_file()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    token = text.strip()
    return token or None


def store_deepxiv_token(token: str) -> Path:
    """Persist ``token`` to the user-local credential file and return its path.

    The token is stripped of surrounding whitespace before storage. The
    parent directory is created with user-only traversal where practical. On
    POSIX the file is chmod ``0600`` as cheap best-effort; on Windows the
    user-profile ACL already restricts ``%LOCALAPPDATA%`` to the owner.
    """

    if not isinstance(token, str):
        raise TypeError("token must be a string")
    normalized = token.strip()
    if not normalized:
        raise ValueError("token must be a non-empty string")

    path = deepxiv_token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write atomically-ish: write then fsync so a crash mid-write does not
    # leave a partial token that reads as present-but-garbage.
    path.write_text(normalized, encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Best-effort restriction; the user-config dir ACL is the real
        # boundary on Windows and on shared POSIX hosts.
        pass
    return path


def resolve_deepxiv_token() -> tuple[str | None, str | None]:
    """Resolve the active DeepXiv token and report its source.

    Returns ``(token, source)`` where ``source`` is one of:

    - ``"environment"`` — ``DEEPXIV_TOKEN`` was set and non-empty.
    - ``"user_credentials"`` — the stored credential file supplied the token.
    - ``None`` — no credential is available.

    Environment always wins and is never overwritten by this call.
    """

    env_token = os.environ.get("DEEPXIV_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip(), "environment"
    stored = read_stored_deepxiv_token()
    if stored is not None:
        return stored, "user_credentials"
    return None, None
