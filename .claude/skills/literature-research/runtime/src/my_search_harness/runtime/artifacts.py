"""Local filesystem mechanics for derived delivery artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from my_search_harness.domain.model import ArtifactKind, DeliveryBasis
from my_search_harness.domain.validation import DomainValidationError, validate_ref

from .codec import delivery_basis_from_dict, delivery_basis_to_dict


class ArtifactValidationError(RuntimeError):
    """A derived artifact is missing, corrupt, or stale for the current run."""


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportArtifactMetadata:
    artifact_kind: ArtifactKind
    delivery_basis: DeliveryBasis
    content_sha256: str


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportArtifact:
    artifact_kind: ArtifactKind
    path: Path
    delivery_basis: DeliveryBasis
    content_sha256: str


class LocalArtifactStore:
    """Thin local store for the report artifact."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def write_report(
        self,
        run_id: str,
        content: str,
        delivery_basis: DeliveryBasis,
    ) -> ReportArtifact:
        content_bytes = content.encode("utf-8")
        content_sha256 = hashlib.sha256(content_bytes).hexdigest()
        artifact_directory = self._artifact_directory(run_id)
        artifact_directory.mkdir(parents=True, exist_ok=True)
        report_path = artifact_directory / "report.md"
        metadata_path = artifact_directory / "report.meta.json"

        self._write_atomic(report_path, content_bytes)
        basis_data = delivery_basis_to_dict(delivery_basis)
        if basis_data is None:
            raise ArtifactValidationError("report delivery basis must not be null")
        metadata = {
            "artifact_kind": ArtifactKind.REPORT.value,
            "delivery_basis": basis_data,
            "content_sha256": content_sha256,
        }
        metadata_bytes = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        self._write_atomic(metadata_path, metadata_bytes)

        return ReportArtifact(
            artifact_kind=ArtifactKind.REPORT,
            path=report_path,
            delivery_basis=delivery_basis,
            content_sha256=content_sha256,
        )

    def read_report_metadata(self, run_id: str) -> ReportArtifactMetadata:
        metadata_path = self._artifact_directory(run_id) / "report.meta.json"
        try:
            payload = metadata_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ArtifactValidationError("report metadata is missing") from exc
        except (OSError, UnicodeError) as exc:
            raise ArtifactValidationError("report metadata cannot be read") from exc

        try:
            value = json.loads(payload)
            if not isinstance(value, dict) or set(value) != {
                "artifact_kind",
                "delivery_basis",
                "content_sha256",
            }:
                raise ArtifactValidationError(
                    "report metadata must contain exactly the V1 provenance fields"
                )
            artifact_kind = ArtifactKind(value["artifact_kind"])
            delivery_basis = delivery_basis_from_dict(value["delivery_basis"])
            content_sha256 = value["content_sha256"]
            if delivery_basis is None:
                raise ArtifactValidationError(
                    "report metadata delivery basis must not be null"
                )
            if (
                not isinstance(content_sha256, str)
                or len(content_sha256) != 64
                or any(
                    character not in "0123456789abcdef" for character in content_sha256
                )
            ):
                raise ArtifactValidationError(
                    "report metadata content_sha256 must be a lowercase SHA-256 digest"
                )
        except ArtifactValidationError:
            raise
        except (DomainValidationError, KeyError, TypeError, ValueError) as exc:
            raise ArtifactValidationError("report metadata is invalid") from exc

        return ReportArtifactMetadata(
            artifact_kind=artifact_kind,
            delivery_basis=delivery_basis,
            content_sha256=content_sha256,
        )

    def validate_report(
        self, run_id: str, expected_basis: DeliveryBasis | None
    ) -> ReportArtifact:
        report_path = self._artifact_directory(run_id) / "report.md"
        try:
            content = report_path.read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactValidationError("report content is missing") from exc
        except OSError as exc:
            raise ArtifactValidationError("report content cannot be read") from exc

        metadata = self.read_report_metadata(run_id)
        if metadata.artifact_kind is not ArtifactKind.REPORT:
            raise ArtifactValidationError("report metadata has the wrong artifact kind")
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != metadata.content_sha256:
            raise ArtifactValidationError(
                "report content digest does not match metadata"
            )
        if metadata.delivery_basis != expected_basis:
            raise ArtifactValidationError(
                "report delivery basis does not match the current run"
            )

        return ReportArtifact(
            artifact_kind=ArtifactKind.REPORT,
            path=report_path,
            delivery_basis=metadata.delivery_basis,
            content_sha256=metadata.content_sha256,
        )

    def _artifact_directory(self, run_id: str) -> Path:
        validate_ref(run_id, "run", "run_id")
        return self._root / run_id / "artifacts"

    @staticmethod
    def _write_atomic(path: Path, payload: bytes) -> None:
        temporary = path.with_name(f"{path.name}.tmp")
        try:
            with temporary.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
