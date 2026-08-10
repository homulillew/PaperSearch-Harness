"""Crash-recoverable orchestration for one fresh Completion Checker invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from my_search_harness.domain.model import CompletionVerdict, SourceLocator

from .capabilities import (
    CompletionCheckerCapabilities,
    ResearcherCapabilities,
    RuntimeCapabilities,
)
from .commands import BlockingGapSpec, CompletionSubmissionResult
from .context import CompletionView, InspectResult
from .source_access import ReadSourceResult, SourceAccessAttemptError


class CompletionOrchestrationError(RuntimeError):
    """A semantic runner violated the typed completion boundary."""


@dataclass(slots=True, frozen=True, kw_only=True)
class CompletionCheckDecision:
    verdict: CompletionVerdict
    reasons: tuple[str, ...]
    blocking_gaps: tuple[BlockingGapSpec, ...] = ()


class CompletionEvidenceAccess:
    """Ephemeral evidence surface with revision tracking and no mutation authority."""

    def __init__(
        self,
        capabilities: CompletionCheckerCapabilities,
        run_id: str,
        state_revision: int,
    ) -> None:
        self._capabilities = capabilities
        self._run_id = run_id
        self._state_revision = state_revision

    @property
    def state_revision(self) -> int:
        return self._state_revision

    def inspect(self, refs: tuple[str, ...]) -> InspectResult:
        result = self._capabilities.inspect(
            self._run_id,
            self._state_revision,
            refs,
        )
        self._state_revision = result.state_revision
        return result

    def read_source(
        self,
        paper_ref: str,
        locator: SourceLocator | None = None,
    ) -> ReadSourceResult:
        try:
            result = self._capabilities.read_source(
                self._run_id,
                self._state_revision,
                paper_ref,
                locator,
            )
        except SourceAccessAttemptError as exc:
            self._state_revision = exc.state_revision
            raise
        self._state_revision = result.state_revision
        return result


class FreshCompletionChecker(Protocol):
    """One-use semantic evaluator; implementations may be LLM-backed or fake."""

    def evaluate(
        self,
        view: CompletionView,
        evidence: CompletionEvidenceAccess,
    ) -> CompletionCheckDecision: ...


class FreshCompletionCheckerFactory(Protocol):
    """Create a checker with no authority carried over from earlier checks."""

    def create(self) -> FreshCompletionChecker: ...


class CompletionCheckRuntime:
    """Persist request, invoke one fresh checker, then atomically submit its result."""

    def __init__(
        self,
        researcher: ResearcherCapabilities,
        completion_checker: CompletionCheckerCapabilities,
    ) -> None:
        self._researcher = researcher
        self._completion_checker = completion_checker

    @classmethod
    def from_capabilities(
        cls,
        capabilities: RuntimeCapabilities,
    ) -> CompletionCheckRuntime:
        return cls(capabilities.researcher, capabilities.completion_checker)

    def request_and_run(
        self,
        run_id: str,
        expected_revision: int,
        requester_rationale: str,
        factory: FreshCompletionCheckerFactory,
    ) -> CompletionSubmissionResult:
        self._researcher.request_completion_check(
            run_id,
            expected_revision,
            requester_rationale,
        )
        return self.resume_pending(run_id, factory)

    def resume_pending(
        self,
        run_id: str,
        factory: FreshCompletionCheckerFactory,
    ) -> CompletionSubmissionResult:
        view = self._completion_checker.view(run_id)
        checker = factory.create()
        evidence = CompletionEvidenceAccess(
            self._completion_checker,
            run_id,
            view.state_revision,
        )
        decision = checker.evaluate(view, evidence)
        if not isinstance(decision, CompletionCheckDecision):
            raise CompletionOrchestrationError(
                "fresh checker must return CompletionCheckDecision"
            )
        return self._completion_checker.submit_completion_check(
            run_id,
            evidence.state_revision,
            view.completion_check_ref,
            decision.verdict,
            decision.reasons,
            decision.blocking_gaps,
        )
