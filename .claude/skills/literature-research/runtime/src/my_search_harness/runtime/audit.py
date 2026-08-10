"""Append-only operational audit records that never reconstruct Research State."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, TypeAlias

from my_search_harness.domain.model import utc_now
from my_search_harness.domain.validation import validate_ref


AuditScalar: TypeAlias = str | int | float | bool | None


@dataclass(slots=True, frozen=True, kw_only=True)
class AuditEvent:
    run_id: str
    state_revision: int
    actor: str
    action: str
    outcome: str = "SUCCESS"
    reason: str | None = None
    provider_outcome: str | None = None
    details: dict[str, AuditScalar] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=utc_now)


class AuditSink(Protocol):
    def append(self, event: AuditEvent) -> None: ...


class AuditAppendError(RuntimeError):
    """State/action succeeded or was attempted, but its audit append failed."""

    def __init__(self, event: AuditEvent) -> None:
        super().__init__(
            f"audit append failed after {event.action} at revision "
            f"{event.state_revision}"
        )
        self.event = event


class AuditReadError(RuntimeError):
    """Audit diagnostics cannot parse the append-only log."""


class LocalAuditLog:
    """One JSON line per event; deliberately independent from state repository reads."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def append(self, event: AuditEvent) -> None:
        self._validate_event(event)
        path = self._event_path(event.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._event_to_json(event).encode("utf-8")
        try:
            descriptor = os.open(
                path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                written = 0
                while written < len(payload):
                    chunk_size = os.write(descriptor, payload[written:])
                    if chunk_size == 0:
                        raise OSError("audit append wrote zero bytes")
                    written += chunk_size
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise AuditAppendError(event) from exc

    def read(self, run_id: str) -> tuple[AuditEvent, ...]:
        path = self._event_path(run_id)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return ()
        except (OSError, UnicodeError) as exc:
            raise AuditReadError("events.jsonl cannot be read") from exc
        events: list[AuditEvent] = []
        for line_number, line in enumerate(lines, start=1):
            try:
                value = json.loads(line)
                if not isinstance(value, dict) or set(value) != {
                    "action",
                    "actor",
                    "details",
                    "outcome",
                    "provider_outcome",
                    "reason",
                    "run_id",
                    "state_revision",
                    "timestamp",
                }:
                    raise ValueError("unexpected event fields")
                event = AuditEvent(
                    run_id=value["run_id"],
                    state_revision=value["state_revision"],
                    actor=value["actor"],
                    action=value["action"],
                    outcome=value["outcome"],
                    reason=value["reason"],
                    provider_outcome=value["provider_outcome"],
                    details=value["details"],
                    timestamp=datetime.fromisoformat(value["timestamp"]),
                )
                self._validate_event(event)
            except (KeyError, TypeError, ValueError) as exc:
                raise AuditReadError(
                    f"events.jsonl line {line_number} is invalid"
                ) from exc
            events.append(event)
        return tuple(events)

    def _event_path(self, run_id: str) -> Path:
        validate_ref(run_id, "run", "run_id")
        return self._root / run_id / "events.jsonl"

    @staticmethod
    def _event_to_json(event: AuditEvent) -> str:
        value = asdict(event)
        value["timestamp"] = event.timestamp.isoformat()
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )

    @staticmethod
    def _validate_event(event: object) -> None:
        if not isinstance(event, AuditEvent):
            raise ValueError("audit sink requires AuditEvent")
        validate_ref(event.run_id, "run", "audit.run_id")
        if (
            not isinstance(event.state_revision, int)
            or isinstance(event.state_revision, bool)
            or event.state_revision < 1
        ):
            raise ValueError("audit state_revision must be a positive integer")
        for name, value in (
            ("actor", event.actor),
            ("action", event.action),
            ("outcome", event.outcome),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"audit {name} must be a non-empty string")
        for name, optional_value in (
            ("reason", event.reason),
            ("provider_outcome", event.provider_outcome),
        ):
            if optional_value is not None and not isinstance(optional_value, str):
                raise ValueError(f"audit {name} must be a string or None")
        if not isinstance(event.details, dict) or not all(
            isinstance(key, str)
            and isinstance(value, (str, int, float, bool, type(None)))
            and not (isinstance(value, float) and not math.isfinite(value))
            for key, value in event.details.items()
        ):
            raise ValueError("audit details must contain JSON scalar values")
        if (
            not isinstance(event.timestamp, datetime)
            or event.timestamp.tzinfo is None
            or event.timestamp.utcoffset() is None
        ):
            raise ValueError("audit timestamp must be timezone-aware")


def append_audit(sink: AuditSink | None, event: AuditEvent) -> None:
    """Append if configured and normalize sink failures into an explicit error."""

    if sink is None:
        return
    try:
        sink.append(event)
    except AuditAppendError:
        raise
    except Exception as exc:
        raise AuditAppendError(event) from exc
