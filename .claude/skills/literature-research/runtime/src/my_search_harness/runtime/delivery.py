"""Explicit actions for the V1 delivery runtime."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from my_search_harness.domain.model import (
    ArtifactKind,
    CompletionPassBasis,
    DeliveryBasis,
    LifecycleMode,
    PartialAuthorizationBasis,
    ResearchRun,
    RunOutcome,
)

from .artifacts import LocalArtifactStore
from .audit import AuditEvent, AuditScalar, AuditSink, append_audit
from .commands import CommandRejectedError
from .persistence import JsonResearchRunRepository, RevisionConflictError


@dataclass(slots=True, frozen=True, kw_only=True)
class PublishReportResult:
    artifact_kind: ArtifactKind
    path: Path
    delivery_basis: DeliveryBasis
    content_sha256: str


@dataclass(slots=True, frozen=True, kw_only=True)
class _ReportPublicationAuthorization:
    """Version-bound proof required to create the formal REPORT artifact.

    This is a runtime capability input, not Research state.  It deliberately
    repeats the certified dependency edges so the artifact boundary can reject
    a fabricated mix of individually plausible but non-matching gate results.
    """

    delivery_basis: DeliveryBasis
    brief_digest: str
    manuscript_digest: str
    reader_brief_digest: str
    reader_manuscript_digest: str
    integrity_delivery_basis: DeliveryBasis
    integrity_brief_digest: str
    integrity_manuscript_digest: str


@dataclass(slots=True, frozen=True, kw_only=True)
class DeliveryValidationResult:
    delivery_basis: DeliveryBasis | None
    validated_artifacts: frozenset[ArtifactKind]


@dataclass(slots=True, frozen=True, kw_only=True)
class ReopenResearchResult:
    state_revision: int


@dataclass(slots=True, frozen=True, kw_only=True)
class CloseRunResult:
    state_revision: int
    outcome: RunOutcome


class DeliveryCommands:
    """Thin authority boundary over artifact mechanics and run persistence."""

    def __init__(
        self,
        repository: JsonResearchRunRepository,
        artifact_store: LocalArtifactStore,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._repository = repository
        self._artifact_store = artifact_store
        self._audit_sink = audit_sink

    def _publish_certified_report(
        self,
        run_id: str,
        expected_revision: int,
        content: str,
        authorization: _ReportPublicationAuthorization,
    ) -> PublishReportResult:
        run = self._load_expected(run_id, expected_revision)
        self._require_lifecycle(run, LifecycleMode.DELIVERY, "publish_certified_report")
        if run.delivery_basis is None:
            raise CommandRejectedError(
                "publish_certified_report requires a delivery basis"
            )
        if not isinstance(content, str) or not content.strip():
            raise CommandRejectedError(
                "publish_certified_report content must be a non-empty string"
            )
        if not isinstance(authorization, _ReportPublicationAuthorization):
            raise CommandRejectedError(
                "formal REPORT publication requires version-bound certification"
            )
        if authorization.delivery_basis != run.delivery_basis:
            raise CommandRejectedError(
                "report certification binds a stale DeliveryBasis"
            )
        if authorization.integrity_delivery_basis != run.delivery_basis:
            raise CommandRejectedError(
                "integrity certification binds a stale DeliveryBasis"
            )
        if (
            authorization.reader_brief_digest != authorization.brief_digest
            or authorization.reader_manuscript_digest != authorization.manuscript_digest
        ):
            raise CommandRejectedError(
                "Reader PASS does not match the current Brief and Manuscript"
            )
        if (
            authorization.integrity_brief_digest != authorization.brief_digest
            or authorization.integrity_manuscript_digest
            != authorization.manuscript_digest
        ):
            raise CommandRejectedError(
                "Integrity PASS does not match the current Brief and Manuscript"
            )

        artifact = self._artifact_store.write_report(
            run.id, content, run.delivery_basis
        )
        self._append_audit(
            run,
            action="report_published",
            details={"content_sha256": artifact.content_sha256},
        )
        return PublishReportResult(
            artifact_kind=artifact.artifact_kind,
            path=artifact.path,
            delivery_basis=artifact.delivery_basis,
            content_sha256=artifact.content_sha256,
        )

    def validate_delivery(self, run_id: str) -> DeliveryValidationResult:
        return self._validate_delivery(self._repository.load(run_id))

    def reopen_research(
        self, run_id: str, expected_revision: int
    ) -> ReopenResearchResult:
        current = self._load_expected(run_id, expected_revision)
        self._require_lifecycle(current, LifecycleMode.DELIVERY, "reopen_research")

        proposed = deepcopy(current)
        proposed.state_revision = current.state_revision + 1
        proposed.lifecycle = LifecycleMode.RESEARCH
        proposed.delivery_basis = None
        proposed.outcome = None
        self._repository.save(proposed, expected_revision)
        self._append_audit(proposed, action="research_reopened")
        return ReopenResearchResult(state_revision=proposed.state_revision)

    def close_run(self, run_id: str, expected_revision: int) -> CloseRunResult:
        current = self._load_expected(run_id, expected_revision)
        self._require_lifecycle(current, LifecycleMode.DELIVERY, "close_run")
        basis = current.delivery_basis
        if basis is None:
            raise CommandRejectedError("close_run requires a delivery basis")
        self._validate_delivery(current)

        if isinstance(basis, CompletionPassBasis):
            outcome = RunOutcome.COMPLETE
        elif isinstance(basis, PartialAuthorizationBasis):
            outcome = RunOutcome.PARTIAL
        else:
            raise CommandRejectedError("close_run found an unknown delivery basis")

        proposed = deepcopy(current)
        proposed.state_revision = current.state_revision + 1
        proposed.lifecycle = LifecycleMode.CLOSED
        proposed.outcome = outcome
        self._repository.save(proposed, expected_revision)
        self._append_audit(
            proposed,
            action="run_closed",
            details={"outcome": outcome.value},
        )
        return CloseRunResult(
            state_revision=proposed.state_revision,
            outcome=outcome,
        )

    def _validate_delivery(self, run: ResearchRun) -> DeliveryValidationResult:
        current_contract_revision = run.contract.current_revision
        basis = run.delivery_basis
        basis_contract_revision: int | None = None
        if isinstance(basis, CompletionPassBasis):
            basis_contract_revision = run.completion_checks[
                basis.completion_check_ref
            ].basis_contract_revision
        elif isinstance(basis, PartialAuthorizationBasis):
            basis_contract_revision = basis.basis_contract_revision

        if (
            basis_contract_revision is not None
            and basis_contract_revision != current_contract_revision
        ):
            raise CommandRejectedError(
                f"delivery basis contract revision {basis_contract_revision} "
                f"does not match current contract revision {current_contract_revision}"
            )

        current_contracts = [
            revision.contract
            for revision in run.contract.revisions
            if revision.revision == current_contract_revision
        ]
        if len(current_contracts) != 1:
            raise CommandRejectedError(
                "current contract revision cannot be resolved for delivery"
            )

        validated: set[ArtifactKind] = set()
        for artifact_kind in current_contracts[0].deliverable.required_artifacts:
            if artifact_kind is ArtifactKind.REPORT:
                self._artifact_store.validate_report(run.id, run.delivery_basis)
                validated.add(artifact_kind)
            else:
                raise CommandRejectedError(
                    f"unsupported required artifact kind {artifact_kind!r}"
                )
        return DeliveryValidationResult(
            delivery_basis=run.delivery_basis,
            validated_artifacts=frozenset(validated),
        )

    def _load_expected(self, run_id: str, expected_revision: int) -> ResearchRun:
        run = self._repository.load(run_id)
        if run.state_revision != expected_revision:
            raise RevisionConflictError(
                f"expected revision {expected_revision}, found {run.state_revision}"
            )
        return run

    def _append_audit(
        self,
        run: ResearchRun,
        *,
        action: str,
        details: dict[str, AuditScalar] | None = None,
    ) -> None:
        append_audit(
            self._audit_sink,
            AuditEvent(
                run_id=run.id,
                state_revision=run.state_revision,
                actor="delivery",
                action=action,
                details={} if details is None else details,
            ),
        )

    @staticmethod
    def _require_lifecycle(
        run: ResearchRun, required: LifecycleMode, command_name: str
    ) -> None:
        if run.lifecycle is not required:
            raise CommandRejectedError(
                f"{command_name} requires {required.value}; found {run.lifecycle.value}"
            )
