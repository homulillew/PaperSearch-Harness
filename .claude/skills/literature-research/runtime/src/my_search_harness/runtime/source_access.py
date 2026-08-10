"""Provider-neutral source inspection and reading boundary."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from my_search_harness.domain.model import (
    LifecycleMode,
    PaperSource,
    ResearchRun,
    SourceLocator,
)

from .audit import AuditEvent, AuditScalar, AuditSink, append_audit
from .persistence import JsonResearchRunRepository, RevisionConflictError


_SOURCE_INSPECT_ATTEMPTS = "source_inspect_attempts"
_SOURCE_READ_ATTEMPTS = "source_read_attempts"


@dataclass(slots=True, frozen=True, kw_only=True)
class SourceOutlineEntry:
    """One provider-supported navigation target within a primary source."""

    title: str
    locator: SourceLocator


@dataclass(slots=True, frozen=True, kw_only=True)
class SourceOutline:
    """Ephemeral primary-source navigation metadata."""

    paper_ref: str
    sections: tuple[SourceOutlineEntry, ...]
    total_tokens: int | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class SourceContent:
    """Ephemeral primary-source content with minimum retrieval provenance."""

    paper_ref: str
    content: str
    locator: SourceLocator | None = None


class SourceAccessFailureKind(StrEnum):
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    LOCATOR_NOT_FOUND = "LOCATOR_NOT_FOUND"
    UNSUPPORTED_LOCATOR = "UNSUPPORTED_LOCATOR"
    INVALID_RESPONSE = "INVALID_RESPONSE"


class SourceAccessProviderError(RuntimeError):
    """Provider-neutral source failure raised by an adapter."""

    def __init__(
        self,
        failure_kind: SourceAccessFailureKind,
        message: str,
    ) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind


class SourceAccessConfigurationError(RuntimeError):
    """Source access cannot be configured locally without external I/O."""


class SourceAccessRejectedError(RuntimeError):
    """Source access failed local validation before an external attempt."""

    def __init__(
        self,
        message: str,
        *,
        failure_kind: SourceAccessFailureKind | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind


class SourceAccessAttemptError(RuntimeError):
    """An accounted source attempt failed after its revision was committed."""

    def __init__(
        self,
        *,
        state_revision: int,
        failure_kind: SourceAccessFailureKind,
    ) -> None:
        super().__init__(f"source access attempt failed: {failure_kind.value}")
        self.state_revision = state_revision
        self.failure_kind = failure_kind


class SourceAccessProvider(Protocol):
    """Stable source capability implemented by provider adapters."""

    def validate_inspect(self, source: PaperSource) -> None: ...

    def validate_read(
        self,
        source: PaperSource,
        locator: SourceLocator | None,
    ) -> None: ...

    def inspect_source(
        self,
        paper_ref: str,
        source: PaperSource,
    ) -> SourceOutline: ...

    def read_source(
        self,
        paper_ref: str,
        source: PaperSource,
        locator: SourceLocator | None,
    ) -> SourceContent: ...


@dataclass(slots=True, frozen=True, kw_only=True)
class InspectSourceResult:
    state_revision: int
    outline: SourceOutline


@dataclass(slots=True, frozen=True, kw_only=True)
class ReadSourceResult:
    state_revision: int
    source_content: SourceContent


class SourceAccessService:
    """Authorize, account, and execute primary-source access."""

    def __init__(
        self,
        repository: JsonResearchRunRepository,
        provider: SourceAccessProvider | None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._audit_sink = audit_sink

    def inspect_source(
        self,
        run_id: str,
        expected_revision: int,
        paper_ref: str,
    ) -> InspectSourceResult:
        current, source = self._validate_common(
            run_id,
            expected_revision,
            paper_ref,
            operation="inspect_source",
        )
        provider = self._require_provider()
        try:
            provider.validate_inspect(source)
        except SourceAccessProviderError as exc:
            raise SourceAccessRejectedError(
                str(exc),
                failure_kind=exc.failure_kind,
            ) from None

        attempted_revision = self._record_attempt(
            current,
            expected_revision,
            _SOURCE_INSPECT_ATTEMPTS,
        )
        try:
            outline = provider.inspect_source(paper_ref, source)
        except SourceAccessProviderError as exc:
            self._audit_attempt(
                run_id,
                attempted_revision,
                action="source_inspect_attempt",
                paper_ref=paper_ref,
                outcome="FAILURE",
                provider_outcome=exc.failure_kind.value,
            )
            raise SourceAccessAttemptError(
                state_revision=attempted_revision,
                failure_kind=exc.failure_kind,
            ) from None
        except Exception:
            self._audit_attempt(
                run_id,
                attempted_revision,
                action="source_inspect_attempt",
                paper_ref=paper_ref,
                outcome="FAILURE",
                provider_outcome=SourceAccessFailureKind.SOURCE_UNAVAILABLE.value,
            )
            raise SourceAccessAttemptError(
                state_revision=attempted_revision,
                failure_kind=SourceAccessFailureKind.SOURCE_UNAVAILABLE,
            ) from None

        if not self._valid_outline(outline, paper_ref):
            self._audit_attempt(
                run_id,
                attempted_revision,
                action="source_inspect_attempt",
                paper_ref=paper_ref,
                outcome="FAILURE",
                provider_outcome=SourceAccessFailureKind.INVALID_RESPONSE.value,
            )
            raise SourceAccessAttemptError(
                state_revision=attempted_revision,
                failure_kind=SourceAccessFailureKind.INVALID_RESPONSE,
            )
        self._audit_attempt(
            run_id,
            attempted_revision,
            action="source_inspect_attempt",
            paper_ref=paper_ref,
            outcome="SUCCESS",
            provider_outcome="SUCCESS",
        )
        return InspectSourceResult(
            state_revision=attempted_revision,
            outline=outline,
        )

    def read_source(
        self,
        run_id: str,
        expected_revision: int,
        paper_ref: str,
        locator: SourceLocator | None = None,
    ) -> ReadSourceResult:
        current, source = self._validate_common(
            run_id,
            expected_revision,
            paper_ref,
            operation="read_source",
        )
        self._validate_locator(locator)
        provider = self._require_provider()
        try:
            provider.validate_read(source, locator)
        except SourceAccessProviderError as exc:
            raise SourceAccessRejectedError(
                str(exc),
                failure_kind=exc.failure_kind,
            ) from None

        attempted_revision = self._record_attempt(
            current,
            expected_revision,
            _SOURCE_READ_ATTEMPTS,
        )
        try:
            content = provider.read_source(paper_ref, source, locator)
        except SourceAccessProviderError as exc:
            self._audit_attempt(
                run_id,
                attempted_revision,
                action="source_read_attempt",
                paper_ref=paper_ref,
                locator=locator,
                outcome="FAILURE",
                provider_outcome=exc.failure_kind.value,
            )
            raise SourceAccessAttemptError(
                state_revision=attempted_revision,
                failure_kind=exc.failure_kind,
            ) from None
        except Exception:
            self._audit_attempt(
                run_id,
                attempted_revision,
                action="source_read_attempt",
                paper_ref=paper_ref,
                locator=locator,
                outcome="FAILURE",
                provider_outcome=SourceAccessFailureKind.SOURCE_UNAVAILABLE.value,
            )
            raise SourceAccessAttemptError(
                state_revision=attempted_revision,
                failure_kind=SourceAccessFailureKind.SOURCE_UNAVAILABLE,
            ) from None

        if not self._valid_content(content, paper_ref, locator):
            self._audit_attempt(
                run_id,
                attempted_revision,
                action="source_read_attempt",
                paper_ref=paper_ref,
                locator=locator,
                outcome="FAILURE",
                provider_outcome=SourceAccessFailureKind.INVALID_RESPONSE.value,
            )
            raise SourceAccessAttemptError(
                state_revision=attempted_revision,
                failure_kind=SourceAccessFailureKind.INVALID_RESPONSE,
            )
        self._audit_attempt(
            run_id,
            attempted_revision,
            action="source_read_attempt",
            paper_ref=paper_ref,
            locator=locator,
            outcome="SUCCESS",
            provider_outcome="SUCCESS",
        )
        return ReadSourceResult(
            state_revision=attempted_revision,
            source_content=content,
        )

    def _validate_common(
        self,
        run_id: str,
        expected_revision: int,
        paper_ref: str,
        *,
        operation: str,
    ) -> tuple[ResearchRun, PaperSource]:
        current = self._repository.load(run_id)
        if current.state_revision != expected_revision:
            raise RevisionConflictError(
                f"expected revision {expected_revision}, found "
                f"{current.state_revision}"
            )
        if current.lifecycle not in {
            LifecycleMode.RESEARCH,
            LifecycleMode.COMPLETION_CHECK,
            LifecycleMode.DELIVERY,
        }:
            raise SourceAccessRejectedError(
                f"{operation} is not allowed in {current.lifecycle.value}"
            )
        if not isinstance(paper_ref, str):
            raise SourceAccessRejectedError("paper_ref must be a string")
        paper = current.papers.get(paper_ref)
        if paper is None:
            raise SourceAccessRejectedError(
                f"paper {paper_ref!r} is not retained in this research run"
            )
        return current, paper.source

    def _require_provider(self) -> SourceAccessProvider:
        if self._provider is None:
            raise SourceAccessRejectedError("source access provider is not configured")
        return self._provider

    def _record_attempt(
        self,
        current: ResearchRun,
        expected_revision: int,
        resource_key: str,
    ) -> int:
        current_usage = current.resources.usage.get(resource_key, 0)
        local_limit = current.resources.limits.get(resource_key)
        if local_limit is not None and current_usage >= local_limit:
            raise SourceAccessRejectedError(
                f"{resource_key.replace('_', ' ')} limit is exhausted"
            )

        attempted = deepcopy(current)
        attempted.state_revision = current.state_revision + 1
        attempted.resources.usage[resource_key] = current_usage + 1
        self._repository.save(attempted, expected_revision)
        return attempted.state_revision

    def _audit_attempt(
        self,
        run_id: str,
        state_revision: int,
        *,
        action: str,
        paper_ref: str,
        outcome: str,
        provider_outcome: str,
        locator: SourceLocator | None = None,
    ) -> None:
        details: dict[str, AuditScalar] = {"paper_ref": paper_ref}
        if locator is not None:
            details["locator_kind"] = locator.kind
            details["locator_value"] = locator.value
        append_audit(
            self._audit_sink,
            AuditEvent(
                run_id=run_id,
                state_revision=state_revision,
                actor="source_access_provider",
                action=action,
                outcome=outcome,
                provider_outcome=provider_outcome,
                details=details,
            ),
        )

    @staticmethod
    def _validate_locator(locator: SourceLocator | None) -> None:
        if locator is None:
            return
        if not isinstance(locator, SourceLocator):
            raise SourceAccessRejectedError("locator must be a SourceLocator or None")
        if not locator.kind.strip() or not locator.value.strip():
            raise SourceAccessRejectedError("locator kind and value must be non-empty")

    @staticmethod
    def _valid_outline(outline: object, paper_ref: str) -> bool:
        if not isinstance(outline, SourceOutline) or outline.paper_ref != paper_ref:
            return False
        if not isinstance(outline.sections, tuple):
            return False
        if outline.total_tokens is not None and (
            not isinstance(outline.total_tokens, int)
            or isinstance(outline.total_tokens, bool)
            or outline.total_tokens < 0
        ):
            return False
        return all(
            isinstance(entry, SourceOutlineEntry)
            and isinstance(entry.title, str)
            and bool(entry.title.strip())
            and isinstance(entry.locator, SourceLocator)
            and bool(entry.locator.kind.strip())
            and bool(entry.locator.value.strip())
            for entry in outline.sections
        )

    @staticmethod
    def _valid_content(
        content: object,
        paper_ref: str,
        requested_locator: SourceLocator | None,
    ) -> bool:
        if not isinstance(content, SourceContent) or content.paper_ref != paper_ref:
            return False
        if not isinstance(content.content, str) or not content.content.strip():
            return False
        if requested_locator is None:
            return content.locator is None
        return (
            isinstance(content.locator, SourceLocator)
            and content.locator.kind.casefold() == requested_locator.kind.casefold()
            and bool(content.locator.value.strip())
        )
