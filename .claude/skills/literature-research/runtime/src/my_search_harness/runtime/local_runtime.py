"""Small local composition root for the complete runtime."""

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
from .certified_delivery import CertifiedReportDelivery
from .completion_runtime import CompletionCheckRuntime
from .context import ContextLimits
from .deepxiv import DeepXivPaperSearchProvider, DeepXivSourceAccessProvider
from .paper_search import PaperSearchProvider
from .persistence import JsonResearchRunRepository
from .reporting import (
    LocalReportCaptureSink,
    ReportConstructor,
    ReportPipeline,
    ReportReviewerFactory,
    ReportReviser,
    ReportWriter,
    ResearchIntegrityReviewer,
    load_report_writing_guide,
)
from .source_access import SourceAccessProvider
from .wiki import (
    LocalWikiPublisher,
    WikiProjectionService,
    WikiService,
)


def _read_guide(path: str | Path) -> str:
    """Load a Delivery guide (quality standard / review / integrity) as text.

    Mirrors ``load_report_writing_guide`` semantics: the pipeline validates
    non-emptiness, so a missing or empty guide fails fast here with a clear
    error rather than at pipeline construction.
    """

    guide_path = Path(path)
    try:
        text = guide_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"guide not found: {guide_path}") from exc
    if not text.strip():
        raise ValueError(f"guide is empty: {guide_path}")
    return text


class LocalV1Runtime:
    """Compose runtime capabilities over one local workspace without exposing storage."""

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
        self._workspace_root = root
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
        self._certified_report_delivery = CertifiedReportDelivery(root, self._delivery)
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
    def certified_report_delivery(self) -> CertifiedReportDelivery:
        """The only supported production path for formal REPORT publication."""

        return self._certified_report_delivery

    @property
    def completion(self) -> CompletionCheckRuntime:
        return self._completion

    @property
    def wiki(self) -> WikiService:
        return self._wiki

    def report_pipeline(
        self,
        *,
        constructor: ReportConstructor,
        writer: ReportWriter,
        reviewer_factory: ReportReviewerFactory,
        reviser: ReportReviser,
        integrity_reviewer: ResearchIntegrityReviewer,
        quality_standard_path: str | Path,
        writing_guide_path: str | Path,
        review_guide_path: str | Path,
        integrity_guide_path: str | Path,
    ) -> ReportPipeline:
        """Bind report actors and the explicitly configured Delivery guides.

        The four guides are loaded here (not interpreted): the Report Quality
        Standard drives the Constructor, the Writing Guide drives the Writer,
        the Review Guide drives the two-phase Reader, and the Integrity Guide
        drives the Research Integrity Reviewer. Each is a non-empty string the
        pipeline validates at construction time.
        """

        return ReportPipeline(
            self._delivery,
            constructor=constructor,
            writer=writer,
            reviewer_factory=reviewer_factory,
            reviser=reviser,
            integrity_reviewer=integrity_reviewer,
            citation_renderer=DeterministicCitationRenderer(),
            quality_standard=_read_guide(quality_standard_path),
            writing_guide=load_report_writing_guide(writing_guide_path),
            review_guide=_read_guide(review_guide_path),
            integrity_guide=_read_guide(integrity_guide_path),
            capture_sink=LocalReportCaptureSink(self._workspace_root / "scratch"),
        )
