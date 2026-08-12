"""Provider-neutral paper search observation and action boundary."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Protocol

from my_search_harness.domain.model import LifecycleMode, ResearchRun

from .audit import AuditEvent, AuditScalar, AuditSink, append_audit
from .persistence import JsonResearchRunRepository, RevisionConflictError


_PAPER_SEARCH_ATTEMPTS = "paper_search_attempts"


@dataclass(slots=True, frozen=True, kw_only=True)
class PaperSearchHit:
    """Ephemeral provider observation; not authoritative research state."""

    title: str
    authors: tuple[str, ...] = ()
    publication_year: int | None = None
    publication_date: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    canonical_url: str | None = None
    other_identifiers: Mapping[str, str] = field(default_factory=dict)
    abstract: str | None = None
    provider_summary: str | None = None
    provider_score: float | None = None
    citation_count: int | None = None
    categories: tuple[str, ...] = ()


class ProviderFailureKind(StrEnum):
    AUTHENTICATION = "AUTHENTICATION"
    RATE_LIMIT = "RATE_LIMIT"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    OTHER = "OTHER"


class PaperSearchProviderError(RuntimeError):
    """Provider-neutral failure raised by a paper search adapter."""

    def __init__(self, failure_kind: ProviderFailureKind, message: str) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind


class PaperSearchConfigurationError(RuntimeError):
    """Paper search cannot be configured locally without external I/O."""


class PaperSearchRejectedError(RuntimeError):
    """Paper search is invalid for the current local state or request."""


class PaperSearchAttemptError(RuntimeError):
    """An accounted provider attempt failed after its revision was committed.

    Carries the provider's already-sanitized failure reason (the same safe
    message the adapter raised in ``PaperSearchProviderError``) so callers,
    the audit log, and the CLI error JSON can diagnose the failure without
    ever touching the raw provider response or HTTP body. The reason is a
    human-readable description of *what* the provider returned or how it
    failed (e.g. "invalid paper search provider response: top-level status
    must be success"), not a tool-failure trajectory and not Research State.
    """

    def __init__(
        self,
        *,
        state_revision: int,
        failure_kind: ProviderFailureKind,
        reason: str | None = None,
    ) -> None:
        message = f"paper search attempt failed: {failure_kind.value}"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message)
        self.state_revision = state_revision
        self.failure_kind = failure_kind
        self.reason = reason


class PaperSearchProvider(Protocol):
    def search(
        self,
        query: str,
        *,
        limit: int,
        offset: int = 0,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> PaperSearchPage: ...


@dataclass(slots=True, frozen=True, kw_only=True)
class PaperSearchPage:
    """One provider page with the provider-reported matching-result count."""

    total_count: int
    hits: tuple[PaperSearchHit, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class PaperSearchResult:
    state_revision: int
    total_count: int
    hits: tuple[PaperSearchHit, ...]


class PaperSearchService:
    """Authorize, account, and execute exactly one provider search attempt."""

    def __init__(
        self,
        repository: JsonResearchRunRepository,
        provider: PaperSearchProvider | None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._audit_sink = audit_sink

    def search_papers(
        self,
        run_id: str,
        expected_revision: int,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> PaperSearchResult:
        current = self._repository.load(run_id)
        if current.state_revision != expected_revision:
            raise RevisionConflictError(
                f"expected revision {expected_revision}, found "
                f"{current.state_revision}"
            )
        if current.lifecycle is not LifecycleMode.RESEARCH:
            raise PaperSearchRejectedError(
                f"search_papers requires RESEARCH; found {current.lifecycle.value}"
            )
        if not isinstance(query, str) or not query.strip():
            raise PaperSearchRejectedError("query must be a non-empty string")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > 100
        ):
            raise PaperSearchRejectedError("limit must be an integer from 1 to 100")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise PaperSearchRejectedError("offset must be a non-negative integer")
        normalized_date_from = self._validate_date("date_from", date_from)
        normalized_date_to = self._validate_date("date_to", date_to)
        if (
            normalized_date_from is not None
            and normalized_date_to is not None
            and normalized_date_from > normalized_date_to
        ):
            raise PaperSearchRejectedError(
                "date_from must be earlier than or equal to date_to"
            )
        if self._provider is None:
            raise PaperSearchRejectedError("paper search provider is not configured")

        current_usage = current.resources.usage.get(_PAPER_SEARCH_ATTEMPTS, 0)
        local_limit = current.resources.limits.get(_PAPER_SEARCH_ATTEMPTS)
        if local_limit is not None and current_usage >= local_limit:
            raise PaperSearchRejectedError("paper search attempt limit is exhausted")

        attempted = deepcopy(current)
        attempted.state_revision = current.state_revision + 1
        attempted.resources.usage[_PAPER_SEARCH_ATTEMPTS] = current_usage + 1
        self._repository.save(attempted, expected_revision)

        try:
            page = self._provider.search(
                query.strip(),
                limit=limit,
                offset=offset,
                date_from=date_from,
                date_to=date_to,
            )
        except PaperSearchProviderError as exc:
            # The adapter already produced a sanitized, token-free reason in
            # the PaperSearchProviderError message (e.g. "invalid paper search
            # provider response: top-level status must be success"). Propagate
            # that same safe description into the audit record and the attempt
            # error so it survives past the attempt boundary — without ever
            # persisting the raw provider response or HTTP body.
            safe_reason = str(exc) or None
            self._audit_attempt(
                attempted,
                outcome="FAILURE",
                provider_outcome=exc.failure_kind.value,
                query=query.strip(),
                limit=limit,
                offset=offset,
                date_from=date_from,
                date_to=date_to,
                reason=safe_reason,
            )
            raise PaperSearchAttemptError(
                state_revision=attempted.state_revision,
                failure_kind=exc.failure_kind,
                reason=safe_reason,
            ) from None
        except Exception:
            # An unrecognized failure has no adapter-sanitized message; do
            # not surface the raw exception text (which may carry provider
            # internals) as a reason. Record the attempt as OTHER and let the
            # CLI's _safe_message redaction handle the error text.
            self._audit_attempt(
                attempted,
                outcome="FAILURE",
                provider_outcome=ProviderFailureKind.OTHER.value,
                query=query.strip(),
                limit=limit,
                offset=offset,
                date_from=date_from,
                date_to=date_to,
            )
            raise PaperSearchAttemptError(
                state_revision=attempted.state_revision,
                failure_kind=ProviderFailureKind.OTHER,
            ) from None

        if (
            not isinstance(page, PaperSearchPage)
            or not isinstance(page.total_count, int)
            or isinstance(page.total_count, bool)
            or page.total_count < 0
            or not isinstance(page.hits, tuple)
            or not all(isinstance(hit, PaperSearchHit) for hit in page.hits)
        ):
            # The provider returned a structurally invalid page that the
            # adapter did not itself reject with a field-specific reason.
            # Use a fixed, safe description rather than echoing the page
            # object (which could carry provider internals).
            safe_reason = "provider returned a structurally invalid search page"
            self._audit_attempt(
                attempted,
                outcome="FAILURE",
                provider_outcome=ProviderFailureKind.INVALID_RESPONSE.value,
                query=query.strip(),
                limit=limit,
                offset=offset,
                date_from=date_from,
                date_to=date_to,
                reason=safe_reason,
            )
            raise PaperSearchAttemptError(
                state_revision=attempted.state_revision,
                failure_kind=ProviderFailureKind.INVALID_RESPONSE,
                reason=safe_reason,
            )
        self._audit_attempt(
            attempted,
            outcome="SUCCESS",
            provider_outcome="SUCCESS",
            query=query.strip(),
            limit=limit,
            offset=offset,
            date_from=date_from,
            date_to=date_to,
            hit_count=len(page.hits),
            total_count=page.total_count,
        )
        return PaperSearchResult(
            state_revision=attempted.state_revision,
            total_count=page.total_count,
            hits=page.hits,
        )

    def _audit_attempt(
        self,
        run: ResearchRun,
        *,
        outcome: str,
        provider_outcome: str,
        query: str,
        limit: int,
        offset: int,
        date_from: str | None,
        date_to: str | None,
        hit_count: int | None = None,
        total_count: int | None = None,
        reason: str | None = None,
    ) -> None:
        details: dict[str, AuditScalar] = {
            "query": query,
            "limit": limit,
            "offset": offset,
        }
        if date_from is not None:
            details["date_from"] = date_from
        if date_to is not None:
            details["date_to"] = date_to
        if hit_count is not None:
            details["hit_count"] = hit_count
        if total_count is not None:
            details["total_count"] = total_count
        append_audit(
            self._audit_sink,
            AuditEvent(
                run_id=run.id,
                state_revision=run.state_revision,
                actor="paper_search_provider",
                action="paper_search_attempt",
                outcome=outcome,
                provider_outcome=provider_outcome,
                reason=reason,
                details=details,
            ),
        )

    @staticmethod
    def _validate_date(name: str, value: str | None) -> date | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise PaperSearchRejectedError(f"{name} must be an ISO date")
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise PaperSearchRejectedError(f"{name} must use YYYY-MM-DD") from None
        if value != parsed.isoformat():
            raise PaperSearchRejectedError(f"{name} must use YYYY-MM-DD")
        return parsed
