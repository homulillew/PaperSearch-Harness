"""Semantic report orchestration over the frozen Delivery boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypeAlias

from my_search_harness.domain.model import SourceLocator

from .capabilities import DeliveryCapabilities
from .context import DeliveryView, InspectResult
from .delivery import PublishReportResult
from .source_access import ReadSourceResult, SourceAccessAttemptError


class ReportPipelineError(RuntimeError):
    """A report-stage boundary rejected semantic runner output."""


class ReportWritingGuideLoadError(RuntimeError):
    """The configured report writing guideline cannot be loaded."""


def load_report_writing_guide(path: str | Path) -> str:
    """Load the authoritative writing guideline without interpreting it."""

    guide_path = Path(path)
    try:
        guideline = guide_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ReportWritingGuideLoadError(
            f"report writing guide not found: {guide_path}"
        ) from exc
    if not guideline.strip():
        raise ReportWritingGuideLoadError(
            f"report writing guide is empty: {guide_path}"
        )
    return guideline


class ResearchEscalationRequired(RuntimeError):
    """A semantic stage found an issue that must return to RESEARCH."""

    def __init__(self, rationale: str) -> None:
        if not isinstance(rationale, str) or not rationale:
            raise ValueError("research escalation rationale must be non-empty")
        super().__init__(rationale)
        self.rationale = rationale


@dataclass(slots=True, frozen=True, kw_only=True)
class NarrativeSection:
    title: str
    purpose: str
    research_refs: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True, kw_only=True)
class NarrativePlan:
    audience: str
    reader_takeaway: str
    sections: tuple[NarrativeSection, ...]
    terminology: tuple[tuple[str, str], ...] = ()


@dataclass(slots=True, frozen=True, kw_only=True)
class CitationReference:
    citation_id: str
    paper_ref: str
    locator: SourceLocator | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportManuscript:
    markdown: str
    citations: tuple[CitationReference, ...] = ()


@dataclass(slots=True, frozen=True, kw_only=True)
class EditorialIssue:
    description: str
    location: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class EditorialReview:
    issues: tuple[EditorialIssue, ...] = ()


class IntegrityDisposition(StrEnum):
    PASS = "PASS"
    REVISE_DELIVERY = "REVISE_DELIVERY"
    REOPEN_RESEARCH = "REOPEN_RESEARCH"


@dataclass(slots=True, frozen=True, kw_only=True)
class ResearchIntegrityReview:
    disposition: IntegrityDisposition
    issues: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True, kw_only=True)
class PublishedReportPipelineResult:
    narrative_plan: NarrativePlan
    editorial_review: EditorialReview
    integrity_review: ResearchIntegrityReview
    artifact: PublishReportResult


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportResearchReopenedResult:
    state_revision: int
    rationale: str


ReportPipelineResult: TypeAlias = (
    PublishedReportPipelineResult | ReportResearchReopenedResult
)


class DeliveryEvidenceAccess:
    """Revision-aware drilldown for semantic Delivery stages."""

    def __init__(
        self,
        capabilities: DeliveryCapabilities,
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


class NarrativePlanner(Protocol):
    def plan(self, view: DeliveryView, writing_guideline: str) -> NarrativePlan: ...


class ReportComposer(Protocol):
    def compose(
        self,
        view: DeliveryView,
        plan: NarrativePlan,
        writing_guideline: str,
        evidence: DeliveryEvidenceAccess,
    ) -> ReportManuscript: ...


class EditorialIntegrator(Protocol):
    def integrate(
        self,
        view: DeliveryView,
        plan: NarrativePlan,
        manuscript: ReportManuscript,
        writing_guideline: str,
        evidence: DeliveryEvidenceAccess,
    ) -> ReportManuscript: ...


class FreshEditorialReviewer(Protocol):
    def review(
        self,
        deliverable_description: str,
        plan: NarrativePlan,
        writing_guideline: str,
        manuscript: ReportManuscript,
    ) -> EditorialReview: ...


class FreshEditorialReviewerFactory(Protocol):
    def create(self) -> FreshEditorialReviewer: ...


class ReportReviser(Protocol):
    def revise(
        self,
        view: DeliveryView,
        plan: NarrativePlan,
        manuscript: ReportManuscript,
        review: EditorialReview,
        writing_guideline: str,
        evidence: DeliveryEvidenceAccess,
    ) -> ReportManuscript: ...


class ResearchIntegrityReviewer(Protocol):
    def review(
        self,
        view: DeliveryView,
        manuscript: ReportManuscript,
        evidence: DeliveryEvidenceAccess,
    ) -> ResearchIntegrityReview: ...


class ReportCitationRenderer(Protocol):
    """Deterministic implementation is supplied by the citation boundary."""

    def render(self, view: DeliveryView, manuscript: ReportManuscript) -> str: ...


class ReportRevisionRequiredError(ReportPipelineError):
    """Integrity issues can be fixed in DELIVERY without new research."""

    def __init__(self, review: ResearchIntegrityReview) -> None:
        super().__init__("research integrity review requires Delivery revision")
        self.review = review


class ReportPipeline:
    """Run semantic stages once; it is an Action pipeline, not a Report FSM."""

    def __init__(
        self,
        delivery: DeliveryCapabilities,
        *,
        planner: NarrativePlanner,
        composer: ReportComposer,
        integrator: EditorialIntegrator,
        editor_factory: FreshEditorialReviewerFactory,
        reviser: ReportReviser,
        integrity_reviewer: ResearchIntegrityReviewer,
        citation_renderer: ReportCitationRenderer,
        writing_guideline: str,
    ) -> None:
        if not isinstance(writing_guideline, str) or not writing_guideline.strip():
            raise ValueError("writing_guideline must be a non-empty string")
        self._delivery = delivery
        self._planner = planner
        self._composer = composer
        self._integrator = integrator
        self._editor_factory = editor_factory
        self._reviser = reviser
        self._integrity_reviewer = integrity_reviewer
        self._citation_renderer = citation_renderer
        self._writing_guideline = writing_guideline

    def run(self, run_id: str) -> ReportPipelineResult:
        view = self._delivery.view(run_id)
        evidence = DeliveryEvidenceAccess(
            self._delivery,
            run_id,
            view.state_revision,
        )
        try:
            plan = self._planner.plan(view, self._writing_guideline)
            self._validate_plan(view, plan)
            composed = self._composer.compose(
                view,
                plan,
                self._writing_guideline,
                evidence,
            )
            self._validate_manuscript(composed)
            integrated = self._integrator.integrate(
                view,
                plan,
                composed,
                self._writing_guideline,
                evidence,
            )
            self._validate_manuscript(integrated)
            editor = self._editor_factory.create()
            editorial_review = editor.review(
                view.contract.deliverable_description,
                plan,
                self._writing_guideline,
                integrated,
            )
            self._validate_editorial_review(editorial_review)
            revised = self._reviser.revise(
                view,
                plan,
                integrated,
                editorial_review,
                self._writing_guideline,
                evidence,
            )
            self._validate_manuscript(revised)
            integrity_review = self._integrity_reviewer.review(
                view,
                revised,
                evidence,
            )
            self._validate_integrity_review(integrity_review)
            if integrity_review.disposition is IntegrityDisposition.REOPEN_RESEARCH:
                rationale = "; ".join(integrity_review.issues)
                return self._reopen(run_id, evidence.state_revision, rationale)
            if integrity_review.disposition is IntegrityDisposition.REVISE_DELIVERY:
                raise ReportRevisionRequiredError(integrity_review)

            rendered = self._citation_renderer.render(view, revised)
            if not isinstance(rendered, str) or not rendered.strip():
                raise ReportPipelineError(
                    "citation renderer must return non-empty report content"
                )
            artifact = self._delivery.publish_report(
                run_id,
                evidence.state_revision,
                rendered,
            )
            return PublishedReportPipelineResult(
                narrative_plan=plan,
                editorial_review=editorial_review,
                integrity_review=integrity_review,
                artifact=artifact,
            )
        except ResearchEscalationRequired as exc:
            return self._reopen(run_id, evidence.state_revision, exc.rationale)

    def _reopen(
        self,
        run_id: str,
        expected_revision: int,
        rationale: str,
    ) -> ReportResearchReopenedResult:
        if not rationale:
            raise ReportPipelineError("research escalation requires a rationale")
        result = self._delivery.reopen_research(run_id, expected_revision)
        return ReportResearchReopenedResult(
            state_revision=result.state_revision,
            rationale=rationale,
        )

    @staticmethod
    def _validate_plan(view: DeliveryView, plan: object) -> None:
        if not isinstance(plan, NarrativePlan):
            raise ReportPipelineError("planner must return NarrativePlan")
        if not plan.audience or not plan.reader_takeaway or not plan.sections:
            raise ReportPipelineError(
                "NarrativePlan requires audience, takeaway, and sections"
            )
        known_refs = {
            *(requirement.ref for requirement in view.contract.requirements),
            *(approach.ref for approach in view.approach_families),
            *(finding.ref for finding in view.findings),
            *(problem.ref for problem in view.open_problems),
            *(gap.ref for gap in view.open_gaps),
            *(paper.ref for paper in view.papers),
        }
        for section in plan.sections:
            if (
                not isinstance(section, NarrativeSection)
                or not section.title
                or not section.purpose
                or not isinstance(section.research_refs, tuple)
                or not all(isinstance(ref, str) for ref in section.research_refs)
            ):
                raise ReportPipelineError("NarrativePlan contains an invalid section")
            missing = set(section.research_refs) - known_refs
            if missing:
                raise ReportPipelineError(
                    f"NarrativePlan has unknown research refs: {sorted(missing)!r}"
                )
        if not isinstance(plan.terminology, tuple) or not all(
            isinstance(term, tuple)
            and len(term) == 2
            and all(isinstance(value, str) and value for value in term)
            for term in plan.terminology
        ):
            raise ReportPipelineError("NarrativePlan terminology is invalid")

    @staticmethod
    def _validate_manuscript(manuscript: object) -> None:
        if not isinstance(manuscript, ReportManuscript):
            raise ReportPipelineError("writer stage must return ReportManuscript")
        if not isinstance(manuscript.markdown, str) or not manuscript.markdown.strip():
            raise ReportPipelineError("ReportManuscript markdown must be non-empty")
        if not isinstance(manuscript.citations, tuple) or not all(
            isinstance(citation, CitationReference) for citation in manuscript.citations
        ):
            raise ReportPipelineError(
                "ReportManuscript citations must contain CitationReference"
            )

    @staticmethod
    def _validate_editorial_review(review: object) -> None:
        if not isinstance(review, EditorialReview):
            raise ReportPipelineError("fresh editor must return EditorialReview")
        if not isinstance(review.issues, tuple) or not all(
            isinstance(issue, EditorialIssue)
            and isinstance(issue.description, str)
            and bool(issue.description)
            and (issue.location is None or isinstance(issue.location, str))
            for issue in review.issues
        ):
            raise ReportPipelineError("EditorialReview contains invalid issues")

    @staticmethod
    def _validate_integrity_review(review: object) -> None:
        if not isinstance(review, ResearchIntegrityReview):
            raise ReportPipelineError(
                "integrity reviewer must return ResearchIntegrityReview"
            )
        if not isinstance(review.disposition, IntegrityDisposition):
            raise ReportPipelineError("integrity disposition is invalid")
        if (
            not isinstance(review.issues, tuple)
            or not all(
                isinstance(issue, str) and bool(issue) for issue in review.issues
            )
            or (review.disposition is IntegrityDisposition.PASS and bool(review.issues))
            or (
                review.disposition is not IntegrityDisposition.PASS
                and not review.issues
            )
        ):
            raise ReportPipelineError(
                "integrity issues must match the review disposition"
            )
