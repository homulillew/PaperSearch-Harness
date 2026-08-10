"""Small local composition root for the complete V1 runtime."""

from __future__ import annotations

from pathlib import Path

from .artifacts import LocalArtifactStore
from .audit import AuditSink
from .capabilities import (
    CompletionCheckerCapabilities,
    DeliveryCapabilities,
    ResearcherCapabilities,
    build_runtime_capabilities,
)
from .citations import DeterministicCitationRenderer
from .completion_runtime import CompletionCheckRuntime
from .context import ContextLimits
from .deepxiv import DeepXivPaperSearchProvider, DeepXivSourceAccessProvider
from .paper_search import PaperSearchProvider
from .persistence import JsonResearchRunRepository
from .reporting import (
    EditorialIntegrator,
    FreshEditorialReviewerFactory,
    NarrativePlanner,
    ReportComposer,
    ReportPipeline,
    ReportReviser,
    ResearchIntegrityReviewer,
    load_report_writing_guide,
)
from .source_access import SourceAccessProvider
from .wiki import (
    LocalWikiPublisher,
    WikiProjectionService,
    WikiService,
)


class LocalV1Runtime:
    """Compose V1 capabilities over one local workspace without exposing storage."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        paper_search_provider: PaperSearchProvider | None,
        source_access_provider: SourceAccessProvider | None,
        context_limits: ContextLimits = ContextLimits(),
        audit_sink: AuditSink | None = None,
    ) -> None:
        root = Path(workspace_root)
        repository = JsonResearchRunRepository(root / "runs")
        artifacts = LocalArtifactStore(repository.root)
        capabilities = build_runtime_capabilities(
            repository,
            artifacts,
            paper_search_provider=paper_search_provider,
            source_access_provider=source_access_provider,
            context_limits=context_limits,
            audit_sink=audit_sink,
        )
        self._repository = repository
        self._researcher = capabilities.researcher
        self._completion_checker = capabilities.completion_checker
        self._delivery = capabilities.delivery
        self._completion = CompletionCheckRuntime.from_capabilities(capabilities)
        self._wiki = WikiService(
            WikiProjectionService(self._repository),
            LocalWikiPublisher(root / "wiki"),
        )

    @classmethod
    def from_deepxiv_env(
        cls,
        workspace_root: str | Path,
        *,
        context_limits: ContextLimits = ContextLimits(),
        audit_sink: AuditSink | None = None,
    ) -> LocalV1Runtime:
        """Build the production external-I/O boundary from ``DEEPXIV_TOKEN``."""

        return cls(
            workspace_root,
            paper_search_provider=DeepXivPaperSearchProvider.from_env(),
            source_access_provider=DeepXivSourceAccessProvider.from_env(),
            context_limits=context_limits,
            audit_sink=audit_sink,
        )

    @property
    def researcher(self) -> ResearcherCapabilities:
        return self._researcher

    @property
    def completion_checker(self) -> CompletionCheckerCapabilities:
        return self._completion_checker

    @property
    def delivery(self) -> DeliveryCapabilities:
        return self._delivery

    @property
    def completion(self) -> CompletionCheckRuntime:
        return self._completion

    @property
    def wiki(self) -> WikiService:
        return self._wiki

    def report_pipeline(
        self,
        *,
        planner: NarrativePlanner,
        composer: ReportComposer,
        integrator: EditorialIntegrator,
        editor_factory: FreshEditorialReviewerFactory,
        reviser: ReportReviser,
        integrity_reviewer: ResearchIntegrityReviewer,
        writing_guideline_path: str | Path,
    ) -> ReportPipeline:
        """Bind report actors and the explicitly configured writing guide."""

        return ReportPipeline(
            self._delivery,
            planner=planner,
            composer=composer,
            integrator=integrator,
            editor_factory=editor_factory,
            reviser=reviser,
            integrity_reviewer=integrity_reviewer,
            citation_renderer=DeterministicCitationRenderer(),
            writing_guideline=load_report_writing_guide(writing_guideline_path),
        )
