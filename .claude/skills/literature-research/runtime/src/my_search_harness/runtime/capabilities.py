"""Narrow external capability façades for the three V1 authority roles."""

from __future__ import annotations

from dataclasses import dataclass

from my_search_harness.domain.model import (
    CompletionVerdict,
    LifecycleMode,
    LiteratureSource,
    PaperAnalysis,
    PaperResearchStatus,
    PaperSource,
    SourceLocator,
)

from .artifacts import LocalArtifactStore
from .audit import AuditSink, LocalAuditLog
from .commands import (
    BlockingGapSpec,
    CompletionSubmissionResult,
    CreateRunRequest,
    CreateRunResult,
    DomainMutationResult,
    EntityMutationResult,
    PaperReconciliationResult,
    ResearchCommands,
    ResearchMutationBatch,
    ResearchMutationResult,
    RequestCompletionCheckResult,
    RetainPapersResult,
)
from .context import (
    CompletionView,
    ContextContinuation,
    ContextLimits,
    ContextProjectionService,
    DeliveryView,
    InspectResult,
    ResearchView,
)
from .delivery import (
    CloseRunResult,
    DeliveryCommands,
    DeliveryValidationResult,
    PublishReportResult,
    ReopenResearchResult,
)
from .paper_search import (
    PaperSearchHit,
    PaperSearchProvider,
    PaperSearchResult,
    PaperSearchService,
)
from .persistence import JsonResearchRunRepository
from .source_access import (
    InspectSourceResult,
    ReadSourceResult,
    SourceAccessProvider,
    SourceAccessService,
)


class CapabilityUnavailableError(RuntimeError):
    """The façade does not own authority in the current lifecycle."""


class ResearcherCapabilities:
    """Actions available to the semantic Researcher."""

    def __init__(
        self,
        commands: ResearchCommands,
        paper_search: PaperSearchService,
        source_access: SourceAccessService,
        context: ContextProjectionService,
        repository: JsonResearchRunRepository,
    ) -> None:
        self._commands = commands
        self._paper_search = paper_search
        self._source_access = source_access
        self._context = context
        self._repository = repository

    def create_run(self, request: CreateRunRequest) -> CreateRunResult:
        return self._commands.create_run(request)

    def view(
        self,
        run_id: str,
        continuation: ContextContinuation | None = None,
    ) -> ResearchView:
        view = self._context.view(run_id, continuation)
        if not isinstance(view, ResearchView):
            raise CapabilityUnavailableError(
                "Researcher view requires RESEARCH lifecycle"
            )
        return view

    def inspect(
        self,
        run_id: str,
        expected_revision: int,
        refs: tuple[str, ...],
    ) -> InspectResult:
        self._require_research(run_id)
        return self._context.inspect(run_id, expected_revision, refs)

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
        return self._paper_search.search_papers(
            run_id,
            expected_revision,
            query,
            limit=limit,
            offset=offset,
            date_from=date_from,
            date_to=date_to,
        )

    def retain_papers(
        self,
        run_id: str,
        expected_revision: int,
        hits: tuple[PaperSearchHit, ...],
    ) -> RetainPapersResult:
        return self._commands.retain_papers(run_id, expected_revision, hits)

    def inspect_source(
        self,
        run_id: str,
        expected_revision: int,
        paper_ref: str,
    ) -> InspectSourceResult:
        self._require_research(run_id)
        return self._source_access.inspect_source(
            run_id,
            expected_revision,
            paper_ref,
        )

    def read_source(
        self,
        run_id: str,
        expected_revision: int,
        paper_ref: str,
        locator: SourceLocator | None = None,
    ) -> ReadSourceResult:
        self._require_research(run_id)
        return self._source_access.read_source(
            run_id,
            expected_revision,
            paper_ref,
            locator,
        )

    def apply_research_mutation(
        self,
        run_id: str,
        expected_revision: int,
        batch: ResearchMutationBatch,
    ) -> ResearchMutationResult:
        return self._commands.apply_research_mutation(
            run_id,
            expected_revision,
            batch,
        )

    def reconcile_paper_identity(
        self,
        run_id: str,
        expected_revision: int,
        primary_paper_ref: str,
        source: PaperSource,
        *,
        duplicate_paper_ref: str | None = None,
        reconciled_analysis: PaperAnalysis | None = None,
        research_status: PaperResearchStatus | None = None,
    ) -> PaperReconciliationResult:
        return self._commands.reconcile_paper_identity(
            run_id,
            expected_revision,
            primary_paper_ref,
            source,
            duplicate_paper_ref=duplicate_paper_ref,
            reconciled_analysis=reconciled_analysis,
            research_status=research_status,
        )

    def put_approach_family(
        self,
        run_id: str,
        expected_revision: int,
        *,
        name: str,
        core_idea: str,
        representative_paper_refs: frozenset[str],
        approach_ref: str | None = None,
    ) -> EntityMutationResult:
        return self._commands.put_approach_family(
            run_id,
            expected_revision,
            name=name,
            core_idea=core_idea,
            representative_paper_refs=representative_paper_refs,
            approach_ref=approach_ref,
        )

    def merge_approach_family(
        self,
        run_id: str,
        expected_revision: int,
        target_approach_ref: str,
        source_approach_ref: str,
    ) -> EntityMutationResult:
        return self._commands.merge_approach_family(
            run_id,
            expected_revision,
            target_approach_ref,
            source_approach_ref,
        )

    def put_landscape_finding(
        self,
        run_id: str,
        expected_revision: int,
        *,
        statement: str,
        approach_refs: frozenset[str] = frozenset(),
        sources: frozenset[LiteratureSource] = frozenset(),
        finding_ref: str | None = None,
    ) -> EntityMutationResult:
        return self._commands.put_landscape_finding(
            run_id,
            expected_revision,
            statement=statement,
            approach_refs=approach_refs,
            sources=sources,
            finding_ref=finding_ref,
        )

    def retire_landscape_finding(
        self,
        run_id: str,
        expected_revision: int,
        finding_ref: str,
    ) -> DomainMutationResult:
        return self._commands.retire_landscape_finding(
            run_id, expected_revision, finding_ref
        )

    def put_open_problem(
        self,
        run_id: str,
        expected_revision: int,
        *,
        statement: str,
        approach_refs: frozenset[str] = frozenset(),
        sources: frozenset[LiteratureSource] = frozenset(),
        problem_ref: str | None = None,
    ) -> EntityMutationResult:
        return self._commands.put_open_problem(
            run_id,
            expected_revision,
            statement=statement,
            approach_refs=approach_refs,
            sources=sources,
            problem_ref=problem_ref,
        )

    def retire_open_problem(
        self,
        run_id: str,
        expected_revision: int,
        problem_ref: str,
    ) -> DomainMutationResult:
        return self._commands.retire_open_problem(
            run_id, expected_revision, problem_ref
        )

    def put_investigation_gap(
        self,
        run_id: str,
        expected_revision: int,
        *,
        description: str,
        requirement_refs: frozenset[str] = frozenset(),
        approach_refs: frozenset[str] = frozenset(),
        gap_ref: str | None = None,
    ) -> EntityMutationResult:
        return self._commands.put_investigation_gap(
            run_id,
            expected_revision,
            description=description,
            requirement_refs=requirement_refs,
            approach_refs=approach_refs,
            gap_ref=gap_ref,
        )

    def resolve_investigation_gap(
        self,
        run_id: str,
        expected_revision: int,
        gap_ref: str,
        resolution: str,
    ) -> DomainMutationResult:
        return self._commands.resolve_investigation_gap(
            run_id, expected_revision, gap_ref, resolution
        )

    def reopen_investigation_gap(
        self,
        run_id: str,
        expected_revision: int,
        gap_ref: str,
    ) -> DomainMutationResult:
        return self._commands.reopen_investigation_gap(
            run_id, expected_revision, gap_ref
        )

    def set_paper_research_status(
        self,
        run_id: str,
        expected_revision: int,
        paper_ref: str,
        status: PaperResearchStatus,
    ) -> DomainMutationResult:
        return self._commands.set_paper_research_status(
            run_id, expected_revision, paper_ref, status
        )

    def request_completion_check(
        self,
        run_id: str,
        expected_revision: int,
        requester_rationale: str,
    ) -> RequestCompletionCheckResult:
        return self._commands.request_completion_check(
            run_id,
            expected_revision,
            requester_rationale,
        )

    def _require_research(self, run_id: str) -> None:
        _require_lifecycle(
            self._repository,
            run_id,
            LifecycleMode.RESEARCH,
            "Researcher",
        )


class CompletionCheckerCapabilities:
    """Verification-only actions available to the fresh Completion Checker."""

    def __init__(
        self,
        commands: ResearchCommands,
        source_access: SourceAccessService,
        context: ContextProjectionService,
        repository: JsonResearchRunRepository,
    ) -> None:
        self._commands = commands
        self._source_access = source_access
        self._context = context
        self._repository = repository

    def view(self, run_id: str) -> CompletionView:
        view = self._context.view(run_id)
        if not isinstance(view, CompletionView):
            raise CapabilityUnavailableError(
                "Completion Checker view requires COMPLETION_CHECK lifecycle"
            )
        return view

    def inspect(
        self,
        run_id: str,
        expected_revision: int,
        refs: tuple[str, ...],
    ) -> InspectResult:
        self._require_completion(run_id)
        return self._context.inspect(run_id, expected_revision, refs)

    def read_source(
        self,
        run_id: str,
        expected_revision: int,
        paper_ref: str,
        locator: SourceLocator | None = None,
    ) -> ReadSourceResult:
        self._require_completion(run_id)
        return self._source_access.read_source(
            run_id,
            expected_revision,
            paper_ref,
            locator,
        )

    def submit_completion_check(
        self,
        run_id: str,
        expected_revision: int,
        completion_check_ref: str,
        verdict: CompletionVerdict,
        reasons: tuple[str, ...],
        blocking_gaps: tuple[BlockingGapSpec, ...] = (),
    ) -> CompletionSubmissionResult:
        return self._commands.submit_completion_check(
            run_id,
            expected_revision,
            completion_check_ref,
            verdict,
            reasons,
            blocking_gaps,
        )

    def _require_completion(self, run_id: str) -> None:
        _require_lifecycle(
            self._repository,
            run_id,
            LifecycleMode.COMPLETION_CHECK,
            "Completion Checker",
        )


class DeliveryCapabilities:
    """Artifact and closure actions available in DELIVERY."""

    def __init__(
        self,
        commands: DeliveryCommands,
        source_access: SourceAccessService,
        context: ContextProjectionService,
        repository: JsonResearchRunRepository,
    ) -> None:
        self._commands = commands
        self._source_access = source_access
        self._context = context
        self._repository = repository

    def view(self, run_id: str) -> DeliveryView:
        view = self._context.view(run_id)
        if not isinstance(view, DeliveryView):
            raise CapabilityUnavailableError(
                "Delivery view requires DELIVERY lifecycle"
            )
        return view

    def inspect(
        self,
        run_id: str,
        expected_revision: int,
        refs: tuple[str, ...],
    ) -> InspectResult:
        self._require_delivery(run_id)
        return self._context.inspect(run_id, expected_revision, refs)

    def read_source(
        self,
        run_id: str,
        expected_revision: int,
        paper_ref: str,
        locator: SourceLocator | None = None,
    ) -> ReadSourceResult:
        self._require_delivery(run_id)
        return self._source_access.read_source(
            run_id,
            expected_revision,
            paper_ref,
            locator,
        )

    def publish_report(
        self,
        run_id: str,
        expected_revision: int,
        content: str,
    ) -> PublishReportResult:
        return self._commands.publish_report(run_id, expected_revision, content)

    def validate_delivery(self, run_id: str) -> DeliveryValidationResult:
        self._require_delivery(run_id)
        return self._commands.validate_delivery(run_id)

    def reopen_research(
        self,
        run_id: str,
        expected_revision: int,
    ) -> ReopenResearchResult:
        return self._commands.reopen_research(run_id, expected_revision)

    def close_run(
        self,
        run_id: str,
        expected_revision: int,
    ) -> CloseRunResult:
        return self._commands.close_run(run_id, expected_revision)

    def _require_delivery(self, run_id: str) -> None:
        _require_lifecycle(
            self._repository,
            run_id,
            LifecycleMode.DELIVERY,
            "Delivery",
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class RuntimeCapabilities:
    researcher: ResearcherCapabilities
    completion_checker: CompletionCheckerCapabilities
    delivery: DeliveryCapabilities


def build_runtime_capabilities(
    repository: JsonResearchRunRepository,
    artifact_store: LocalArtifactStore,
    *,
    paper_search_provider: PaperSearchProvider | None,
    source_access_provider: SourceAccessProvider | None,
    context_limits: ContextLimits = ContextLimits(),
    audit_sink: AuditSink | None = None,
) -> RuntimeCapabilities:
    """Compose narrow façades while keeping persistence primitives internal."""

    effective_audit_sink = (
        audit_sink if audit_sink is not None else LocalAuditLog(repository.root)
    )
    research_commands = ResearchCommands(repository, effective_audit_sink)
    source_access = SourceAccessService(
        repository,
        source_access_provider,
        effective_audit_sink,
    )
    context = ContextProjectionService(repository, limits=context_limits)
    delivery_commands = DeliveryCommands(
        repository,
        artifact_store,
        effective_audit_sink,
    )
    return RuntimeCapabilities(
        researcher=ResearcherCapabilities(
            research_commands,
            PaperSearchService(
                repository,
                paper_search_provider,
                effective_audit_sink,
            ),
            source_access,
            context,
            repository,
        ),
        completion_checker=CompletionCheckerCapabilities(
            research_commands,
            source_access,
            context,
            repository,
        ),
        delivery=DeliveryCapabilities(
            delivery_commands,
            source_access,
            context,
            repository,
        ),
    )


def _require_lifecycle(
    repository: JsonResearchRunRepository,
    run_id: str,
    required: LifecycleMode,
    capability_name: str,
) -> None:
    actual = repository.load(run_id).lifecycle
    if actual is not required:
        raise CapabilityUnavailableError(
            f"{capability_name} capability requires {required.value}; "
            f"found {actual.value}"
        )
