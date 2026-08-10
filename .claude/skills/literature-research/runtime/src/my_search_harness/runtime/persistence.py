"""Atomic file repository for ResearchRun state."""

from __future__ import annotations

import os
from pathlib import Path

from my_search_harness.domain.model import ResearchRun
from my_search_harness.domain.validation import (
    DomainValidationError,
    validate_ref,
    validate_run,
    validate_transition,
)

from .codec import run_from_json, run_to_json


class RevisionConflictError(RuntimeError):
    """The caller attempted to save against a stale state revision."""


class RunAlreadyExistsError(RuntimeError):
    """A run with the requested ID already exists."""


class RunNotFoundError(RuntimeError):
    """No persisted run exists for the requested ID."""


class JsonResearchRunRepository:
    """One-file-per-run repository with optimistic revision checks."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """Filesystem root for colocated non-authoritative runtime records."""

        return self._root

    def _run_directory(self, run_id: str) -> Path:
        validate_ref(run_id, "run", "run_id")
        return self._root / run_id

    def _state_path(self, run_id: str) -> Path:
        return self._run_directory(run_id) / "state.json"

    def load(self, run_id: str) -> ResearchRun:
        path = self._state_path(run_id)
        try:
            payload = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise RunNotFoundError(f"research run {run_id!r} was not found") from exc
        return run_from_json(payload)

    def list_run_ids(self) -> tuple[str, ...]:
        """List authoritative Run snapshots without consulting audit/artifacts."""

        run_ids: list[str] = []
        for path in self._root.iterdir():
            if not path.is_dir() or not (path / "state.json").is_file():
                continue
            validate_ref(path.name, "run", "run directory name")
            run_ids.append(path.name)
        return tuple(sorted(run_ids))

    def create(self, run: ResearchRun) -> None:
        validate_run(run)
        if run.state_revision != 1:
            raise DomainValidationError("a newly created run must start at revision 1")
        run_directory = self._run_directory(run.id)
        state_path = run_directory / "state.json"
        if state_path.exists():
            raise RunAlreadyExistsError(f"research run {run.id!r} already exists")
        run_directory.mkdir(exist_ok=True)
        try:
            self._write_atomic(state_path, run_to_json(run))
        except BaseException:
            temporary = run_directory / "state.json.tmp"
            temporary.unlink(missing_ok=True)
            try:
                run_directory.rmdir()
            except OSError:
                pass
            raise

    def save(self, run: ResearchRun, expected_revision: int) -> None:
        current = self.load(run.id)
        if current.state_revision != expected_revision:
            raise RevisionConflictError(
                f"expected revision {expected_revision}, found "
                f"{current.state_revision}"
            )
        validate_run(run)
        validate_transition(current, run)
        self._write_atomic(self._state_path(run.id), run_to_json(run))

    @staticmethod
    def _write_atomic(path: Path, payload: str) -> None:
        temporary = path.with_name(f"{path.name}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
