"""Explicit actions for the delivery runtime."""

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


@dataclass(slots=True, frozen=True, kw_only=True)
class ReopenDeliveryResult:
    state_revision: int


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

    def reopen_delivery(
        self, run_id: str, expected_revision: int
    ) -> ReopenDeliveryResult:
        # Reopen an already CLOSED run into DELIVERY, reusing the accepted
        # Research State and the existing DeliveryBasis. Only Delivery work is
        # rerun; Research is not reopened and no new CompletionCheck is created.
        # The basis is preserved (not cleared) so the prior accepted
        # Completion/Partial authorization remains the authoritative basis for
        # the new Delivery pass.
        current = self._load_expected(run_id, expected_revision)
        self._require_lifecycle(current, LifecycleMode.CLOSED, "reopen_delivery")
        basis = current.delivery_basis
        if basis is None:
            raise CommandRejectedError("reopen_delivery requires a delivery basis")
        outcome = current.outcome
        if outcome not in (RunOutcome.COMPLETE, RunOutcome.PARTIAL):
            raise CommandRejectedError(
                "reopen_delivery requires a closed COMPLETE or PARTIAL outcome"
            )
        # Enforce outcome <-> basis consistency. A CLOSED run must carry a basis
        # whose type matches its recorded outcome; a corrupted CLOSED state
        # (e.g. CompletionPassBasis + PARTIAL) is rejected rather than repaired.
        if isinstance(basis, CompletionPassBasis):
            if outcome is not RunOutcome.COMPLETE:
                raise CommandRejectedError(
                    "reopen_delivery: CompletionPassBasis requires outcome COMPLETE"
                )
        elif isinstance(basis, PartialAuthorizationBasis):
            if outcome is not RunOutcome.PARTIAL:
                raise CommandRejectedError(
                    "reopen_delivery: PartialAuthorizationBasis requires outcome PARTIAL"
                )
        else:
            raise CommandRejectedError("reopen_delivery found an unknown delivery basis")
        # The stored basis must still support Delivery under the existing
        # invariants, but the OLD report artifact is NOT required here: the
        # purpose of reopening is to regenerate the report. If the basis is
        # invalid, reject rather than repair.
        self._validate_delivery_basis(current)

        proposed = deepcopy(current)
        proposed.state_revision = current.state_revision + 1
        proposed.lifecycle = LifecycleMode.DELIVERY
        proposed.outcome = None
        # delivery_basis is intentionally preserved.
        self._repository.save(proposed, expected_revision)
        self._append_audit(
            proposed,
            action="delivery_reopened",
            details={"outcome": outcome.value},
        )
        return ReopenDeliveryResult(state_revision=proposed.state_revision)

    def _validate_delivery_basis(self, run: ResearchRun) -> None:
        # Validate only the stored DeliveryBasis against the current
        # ResearchRun / Contract invariants. This does NOT require the published
        # report artifact: reopening Delivery is a valid way to regenerate a
        # report whose old artifact is missing or corrupted. Malformed or
        # unknown DeliveryBasis values are rejected rather than repaired.
        current_contract_revision = run.contract.current_revision
        basis = run.delivery_basis
        if isinstance(basis, CompletionPassBasis):
            if basis.completion_check_ref not in run.completion_checks:
                raise CommandRejectedError(
                    "delivery basis references an unknown completion check "
                    f"{basis.completion_check_ref!r}"
                )
            basis_contract_revision = run.completion_checks[
                basis.completion_check_ref
            ].basis_contract_revision
        elif isinstance(basis, PartialAuthorizationBasis):
            basis_contract_revision = basis.basis_contract_revision
        else:
            raise CommandRejectedError("delivery basis has an unknown type")

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

    def _validate_delivery(self, run: ResearchRun) -> DeliveryValidationResult:
        # Full Delivery validation: the stored basis must be valid, AND the
        # required published artifacts must be present and intact. Used by
        # validate_delivery and close_run, where a current report artifact is
        # required. reopen_delivery uses _validate_delivery_basis only, so a
        # missing old report artifact does not block reopening.
        self._validate_delivery_basis(run)
        current_contract_revision = run.contract.current_revision
        current_contracts = [
            revision.contract
            for revision in run.contract.revisions
            if revision.revision == current_contract_revision
        ]

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
