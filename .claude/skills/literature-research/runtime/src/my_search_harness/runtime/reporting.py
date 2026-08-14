"""Semantic report orchestration over the frozen Delivery boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, TypeAlias

from my_search_harness.domain.model import DeliveryBasis, SourceLocator

from .capabilities import DeliveryCapabilities
from .context import DeliveryView, InspectResult, PaperIndexEntry
from .delivery import PublishReportResult
from .source_access import (
    InspectSourceResult,
    ReadSourceResult,
    SourceAccessAttemptError,
)


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


# ---------------------------------------------------------------------------
# Report Brief — the single report-semantic intermediate work product.
#
# These are lightweight Delivery value objects, NOT Research Domain entities:
# no stable identity, no independent lifecycle, no cross-run authority, no
# domain commands, no MaterialRole enum, no scores. See ADR-012.
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportEvidenceRef:
    """A pointer from Brief material back into accepted Research State."""

    paper_ref: str
    locator: SourceLocator | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportMaterial:
    """High-density fact / condition / mechanism / number a section argument needs.

    ``role`` is free-form natural language (e.g. "mechanism contrast",
    "limitation", "independent validation"); V1 defines no MaterialRole enum.
    Material is Delivery-specific distillation, not new Research truth, and
    does not cache large source excerpts.
    """

    role: str
    content: str
    evidence: tuple[ReportEvidenceRef, ...] = ()


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportBriefSection:
    title: str
    requirement_refs: tuple[str, ...] = ()
    purpose: str = ""
    reader_takeaway: str = ""
    argument_flow: tuple[str, ...] = ()
    research_refs: tuple[str, ...] = ()
    material: tuple[ReportMaterial, ...] = ()
    evidence_boundary: str = ""


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportBrief:
    """What the report must establish, how, with what material, and where evidence stops.

    Brief freshness binds ``delivery_basis`` (not ordinary ``state_revision``):
    source access may advance ``state_revision`` without invalidating an
    unchanged DeliveryBasis. Re-entering RESEARCH clears the DeliveryBasis and
    invalidates any Brief built against it.
    """

    delivery_basis: DeliveryBasis
    audience: str
    report_goal: str
    reader_takeaway: str
    narrative_logic: tuple[str, ...] = ()
    sections: tuple[ReportBriefSection, ...]
    terminology: tuple[tuple[str, str], ...] = ()
    intentional_omissions: tuple[str, ...] = ()


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
class BlindRead:
    """Phase 1 cold read: Reviewer has NOT seen the Brief, only the deliverable."""

    issues: tuple[EditorialIssue, ...] = ()


@dataclass(slots=True, frozen=True, kw_only=True)
class EditorialReview:
    """Phase 2 review: same Reviewer instance now checks the manuscript against the Brief."""

    issues: tuple[EditorialIssue, ...] = ()


class IntegrityDisposition(StrEnum):
    PASS = "PASS"
    REVISE_DELIVERY = "REVISE_DELIVERY"
    REOPEN_RESEARCH = "REOPEN_RESEARCH"


@dataclass(slots=True, frozen=True, kw_only=True)
class ResearchIntegrityReview:
    disposition: IntegrityDisposition
    issues: tuple[str, ...] = ()
    # Transient implementation control data, NOT a Domain enum. Routes a
    # REVISE_DELIVERY repair to the earliest faulty Delivery layer:
    # MANUSCRIT -> Reviser, BRIEF -> Report Constructor. PASS leaves it None.
    repair_target: Literal["MANUSCRIPT", "BRIEF"] | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportConstructionRequest:
    """Transient Writer signal that Brief material is insufficient.

    NOT a persistent MaterialRequest system: the Writer returns this instead
    of a Manuscript when a section's material is missing, and the pipeline
    routes it back to the Report Constructor with the named section (if any).
    """

    reason: str
    section_title: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class PublishedReportPipelineResult:
    report_brief: ReportBrief
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

    def inspect_source(self, paper_ref: str) -> InspectSourceResult:
        try:
            result = self._capabilities.inspect_source(
                self._run_id,
                self._state_revision,
                paper_ref,
            )
        except SourceAccessAttemptError as exc:
            self._state_revision = exc.state_revision
            raise
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


class ReportConstructor(Protocol):
    """Replaces NarrativePlanner: derives the report-semantic Brief from Research State.

    May inspect Research refs, inspect-source, and do targeted read-source to
    recover material density. Must NOT silently mutate Research State; if
    semantics would change, it raises ResearchEscalationRequired so the pipeline
    routes back through the existing Research escalation/reopen path.
    """

    def construct(
        self,
        view: DeliveryView,
        writing_guideline: str,
        evidence: DeliveryEvidenceAccess,
        *,
        previous_brief: ReportBrief | None = None,
        feedback: tuple[str, ...] = (),
    ) -> ReportBrief: ...


class ReportWriter(Protocol):
    """Replaces ReportComposer: article-reasoning authority, NO Research authority.

    Receives the Brief, the Writing Guide, and safe PaperIndexEntry metadata
    (NOT broad DeliveryView / DeliveryEvidenceAccess). Returns a Manuscript, or
    a transient ReportConstructionRequest when a section's Brief material is
    insufficient — it does not fabricate research interpretation.
    """

    def write(
        self,
        brief: ReportBrief,
        writing_guideline: str,
        paper_metadata: tuple[PaperIndexEntry, ...],
    ) -> ReportManuscript | ReportConstructionRequest: ...


class ReportReviewer(Protocol):
    """Replaces FreshEditorialReviewer: two-phase cold reading in one fresh instance.

    Phase 1 (blind_read) receives NO Brief. Phase 2 (check_brief) is called on
    the SAME instance after blind_read, now with the Brief. A new instance is
    requested after every manuscript revision.
    """

    def blind_read(
        self,
        deliverable_description: str,
        writing_guideline: str,
        manuscript: ReportManuscript,
    ) -> BlindRead: ...

    def check_brief(
        self,
        brief: ReportBrief,
        manuscript: ReportManuscript,
    ) -> EditorialReview: ...


class ReportReviewerFactory(Protocol):
    """Called again after every manuscript revision to get a fresh Reviewer instance."""

    def create(self) -> ReportReviewer: ...


class ReportReviser(Protocol):
    """Narrow: fixes specific manuscript problems against the Brief, no new research.

    May fix prose/structure/form, restore omitted Brief material, narrow wording.
    May NOT do open-ended research or change Research State. If the earliest
    faulty layer is the Brief, the pipeline routes to the Constructor instead.
    """

    def revise(
        self,
        brief: ReportBrief,
        manuscript: ReportManuscript,
        issues: tuple[EditorialIssue, ...],
        writing_guideline: str,
        paper_metadata: tuple[PaperIndexEntry, ...],
    ) -> ReportManuscript: ...


class ResearchIntegrityReviewer(Protocol):
    """Independent of Editorial Review. Brief is a traceability map, not a re-plan entry."""

    def review(
        self,
        view: DeliveryView,
        brief: ReportBrief,
        manuscript: ReportManuscript,
        evidence: DeliveryEvidenceAccess,
    ) -> ResearchIntegrityReview: ...


class ReportCitationRenderer(Protocol):
    """Deterministic implementation is supplied by the citation boundary."""

    def render(self, view: DeliveryView, manuscript: ReportManuscript) -> str: ...


class ReportPipeline:
    """Thin coordinator with explicit control flow. NOT a Report FSM.

    Implements the ADR-012 flow:

        Research State → Report Brief → Writer → Report Reviewer ↔ Reviser
        → Research Integrity Reviewer → Citation Renderer → Final Report

    The two gates (editorial + integrity) are independent. Problems return to
    the earliest layer authorized to repair them: Manuscript → Reviser,
    Brief → Report Constructor, State → Research. Any manuscript change
    invalidates the prior Editorial PASS, so a revised Manuscript always gets a
    fresh Reviewer instance. Resource exhaustion terminates the loop but is
    never interpreted as PASS.
    """

    _MAX_EDITORIAL_ROUNDS = 32

    def __init__(
        self,
        delivery: DeliveryCapabilities,
        *,
        constructor: ReportConstructor,
        writer: ReportWriter,
        reviewer_factory: ReportReviewerFactory,
        reviser: ReportReviser,
        integrity_reviewer: ResearchIntegrityReviewer,
        citation_renderer: ReportCitationRenderer,
        writing_guideline: str,
    ) -> None:
        if not isinstance(writing_guideline, str) or not writing_guideline.strip():
            raise ValueError("writing_guideline must be a non-empty string")
        self._delivery = delivery
        self._constructor = constructor
        self._writer = writer
        self._reviewer_factory = reviewer_factory
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
            brief = self._construct_brief(view, evidence, None, ())
            manuscript = self._write_manuscript(brief, view, evidence)
            editorial_review = self._editorial_loop(view, brief, manuscript)
            integrity_review = self._integrity_reviewer.review(
                view,
                brief,
                manuscript,
                evidence,
            )
            self._validate_integrity_review(integrity_review)
            if (
                integrity_review.disposition
                is IntegrityDisposition.REOPEN_RESEARCH
            ):
                rationale = "; ".join(integrity_review.issues)
                return self._reopen(run_id, evidence.state_revision, rationale)
            if (
                integrity_review.disposition
                is IntegrityDisposition.REVISE_DELIVERY
            ):
                manuscript, brief = self._integrity_repair(
                    view,
                    evidence,
                    brief,
                    manuscript,
                    integrity_review,
                )
                # Any manuscript change invalidates the prior Editorial PASS.
                editorial_review = self._editorial_loop(view, brief, manuscript)
                integrity_review = self._integrity_reviewer.review(
                    view,
                    brief,
                    manuscript,
                    evidence,
                )
                self._validate_integrity_review(integrity_review)
                if (
                    integrity_review.disposition
                    is IntegrityDisposition.REOPEN_RESEARCH
                ):
                    rationale = "; ".join(integrity_review.issues)
                    return self._reopen(
                        run_id, evidence.state_revision, rationale
                    )
                if (
                    integrity_review.disposition
                    is IntegrityDisposition.REVISE_DELIVERY
                ):
                    raise ReportPipelineError(
                        "integrity repair did not clear REVISE_DELIVERY; "
                        "the earliest faulty layer could not be resolved in "
                        "DELIVERY (Brief or Manuscript repair looped)"
                    )

            rendered = self._citation_renderer.render(view, manuscript)
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
                report_brief=brief,
                editorial_review=editorial_review,
                integrity_review=integrity_review,
                artifact=artifact,
            )
        except ResearchEscalationRequired as exc:
            return self._reopen(run_id, evidence.state_revision, exc.rationale)

    def _construct_brief(
        self,
        view: DeliveryView,
        evidence: DeliveryEvidenceAccess,
        previous_brief: ReportBrief | None,
        feedback: tuple[str, ...],
    ) -> ReportBrief:
        brief = self._constructor.construct(
            view,
            self._writing_guideline,
            evidence,
            previous_brief=previous_brief,
            feedback=feedback,
        )
        self._validate_brief(view, brief)
        return brief

    def _write_manuscript(
        self,
        brief: ReportBrief,
        view: DeliveryView,
        evidence: DeliveryEvidenceAccess,
    ) -> ReportManuscript:
        paper_metadata = view.papers
        outcome = self._writer.write(
            brief,
            self._writing_guideline,
            paper_metadata,
        )
        if isinstance(outcome, ReportConstructionRequest):
            # Writer signalled insufficient Brief material. Route back to the
            # Constructor (the earliest layer authorized to repair material),
            # then re-write. This is a transient signal, not a persistent queue.
            feedback: list[str] = [outcome.reason]
            if outcome.section_title is not None:
                feedback.append(f"section: {outcome.section_title}")
            revised_brief = self._construct_brief(
                view,
                evidence,
                brief,
                tuple(feedback),
            )
            return self._write_manuscript(revised_brief, view, evidence)
        self._validate_manuscript(outcome)
        return outcome

    def _editorial_loop(
        self,
        view: DeliveryView,
        brief: ReportBrief,
        manuscript: ReportManuscript,
    ) -> EditorialReview:
        """Run Reviewer ↔ Reviser until Blocking Issues == ().

        No scores, no fixed N rounds, no voting. No blockers → Reviser is NOT
        called. Blockers → fresh Reviewer → Reviser → new Manuscript → fresh
        Reviewer. Resource exhaustion (the round cap) raises, never PASSes.
        """
        for _ in range(self._MAX_EDITORIAL_ROUNDS):
            reviewer = self._reviewer_factory.create()
            blind = reviewer.blind_read(
                view.contract.deliverable_description,
                self._writing_guideline,
                manuscript,
            )
            self._validate_blind_read(blind)
            review = reviewer.check_brief(brief, manuscript)
            self._validate_editorial_review(review)
            if not review.issues:
                return review
            revised = self._reviser.revise(
                brief,
                manuscript,
                review.issues,
                self._writing_guideline,
                view.papers,
            )
            self._validate_manuscript(revised)
            manuscript = revised
        raise ReportPipelineError(
            "editorial loop exhausted without clearing blocking issues; "
            "resource exhaustion is not a PASS"
        )

    def _integrity_repair(
        self,
        view: DeliveryView,
        evidence: DeliveryEvidenceAccess,
        brief: ReportBrief,
        manuscript: ReportManuscript,
        review: ResearchIntegrityReview,
    ) -> tuple[ReportManuscript, ReportBrief]:
        """Route a REVISE_DELIVERY to the earliest faulty Delivery layer.

        repair_target MANUSCRIPT → Reviser. repair_target BRIEF (or None with a
        Brief-level fault) → Report Constructor, then re-write. Either way the
        caller re-runs the editorial loop and integrity review on the new
        Manuscript, because any manuscript change invalidates the editorial PASS.
        """
        target = review.repair_target
        if target == "BRIEF":
            feedback = tuple(review.issues)
            revised_brief = self._construct_brief(
                view,
                evidence,
                brief,
                feedback,
            )
            revised_manuscript = self._write_manuscript(
                revised_brief, view, evidence
            )
            return revised_manuscript, revised_brief
        # Default to MANUSCRIPT repair: the Reviser fixes the specific issues.
        revised = self._reviser.revise(
            brief,
            manuscript,
            tuple(EditorialIssue(description=issue) for issue in review.issues),
            self._writing_guideline,
            view.papers,
        )
        self._validate_manuscript(revised)
        return revised, brief

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
    def _validate_brief(view: DeliveryView, brief: object) -> None:
        if not isinstance(brief, ReportBrief):
            raise ReportPipelineError(
                "report constructor must return ReportBrief"
            )
        if (
            not brief.delivery_basis
            or not isinstance(brief.audience, str)
            or not brief.audience
            or not isinstance(brief.report_goal, str)
            or not brief.report_goal
            or not isinstance(brief.reader_takeaway, str)
            or not brief.reader_takeaway
            or not isinstance(brief.sections, tuple)
            or not brief.sections
        ):
            raise ReportPipelineError(
                "ReportBrief requires delivery_basis, audience, report_goal, "
                "reader_takeaway, and non-empty sections"
            )
        if brief.delivery_basis != view.delivery_basis:
            raise ReportPipelineError(
                "ReportBrief delivery_basis does not match the current Delivery"
            )
        known_refs = {
            *(requirement.ref for requirement in view.contract.requirements),
            *(approach.ref for approach in view.approach_families),
            *(finding.ref for finding in view.findings),
            *(problem.ref for problem in view.open_problems),
            *(gap.ref for gap in view.open_gaps),
            *(paper.ref for paper in view.papers),
        }
        for section in brief.sections:
            if (
                not isinstance(section, ReportBriefSection)
                or not isinstance(section.title, str)
                or not section.title
                or not isinstance(section.requirement_refs, tuple)
                or not all(
                    isinstance(ref, str) for ref in section.requirement_refs
                )
                or not isinstance(section.research_refs, tuple)
                or not all(
                    isinstance(ref, str) for ref in section.research_refs
                )
                or not isinstance(section.argument_flow, tuple)
                or not all(
                    isinstance(move, str) for move in section.argument_flow
                )
                or not isinstance(section.material, tuple)
                or not all(
                    isinstance(item, ReportMaterial) for item in section.material
                )
            ):
                raise ReportPipelineError(
                    "ReportBrief contains an invalid section"
                )
            missing = set(section.research_refs) - known_refs
            if missing:
                raise ReportPipelineError(
                    f"ReportBrief has unknown research refs: {sorted(missing)!r}"
                )
            missing_reqs = set(section.requirement_refs) - {
                requirement.ref for requirement in view.contract.requirements
            }
            if missing_reqs:
                raise ReportPipelineError(
                    f"ReportBrief has unknown requirement refs: "
                    f"{sorted(missing_reqs)!r}"
                )
            for material in section.material:
                if (
                    not isinstance(material.role, str)
                    or not material.role
                    or not isinstance(material.content, str)
                    or not material.content
                    or not isinstance(material.evidence, tuple)
                    or not all(
                        isinstance(ref, ReportEvidenceRef)
                        and isinstance(ref.paper_ref, str)
                        and ref.paper_ref
                        for ref in material.evidence
                    )
                ):
                    raise ReportPipelineError(
                        "ReportBrief material is invalid"
                    )
                unknown_papers = {
                    ref.paper_ref for ref in material.evidence
                } - {paper.ref for paper in view.papers}
                if unknown_papers:
                    raise ReportPipelineError(
                        f"ReportBrief material evidence targets unknown "
                        f"papers: {sorted(unknown_papers)!r}"
                    )
        if not isinstance(brief.narrative_logic, tuple) or not all(
            isinstance(move, str) for move in brief.narrative_logic
        ):
            raise ReportPipelineError("ReportBrief narrative_logic is invalid")
        if not isinstance(brief.terminology, tuple) or not all(
            isinstance(term, tuple)
            and len(term) == 2
            and all(isinstance(value, str) and value for value in term)
            for term in brief.terminology
        ):
            raise ReportPipelineError("ReportBrief terminology is invalid")
        if not isinstance(brief.intentional_omissions, tuple) or not all(
            isinstance(item, str) and item for item in brief.intentional_omissions
        ):
            raise ReportPipelineError(
                "ReportBrief intentional_omissions is invalid"
            )

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
    def _validate_blind_read(read: object) -> None:
        if not isinstance(read, BlindRead):
            raise ReportPipelineError("reviewer blind_read must return BlindRead")
        if not isinstance(read.issues, tuple) or not all(
            isinstance(issue, EditorialIssue)
            and isinstance(issue.description, str)
            and bool(issue.description)
            and (issue.location is None or isinstance(issue.location, str))
            for issue in read.issues
        ):
            raise ReportPipelineError("BlindRead contains invalid issues")

    @staticmethod
    def _validate_editorial_review(review: object) -> None:
        if not isinstance(review, EditorialReview):
            raise ReportPipelineError("reviewer check_brief must return EditorialReview")
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
        if review.repair_target is not None and review.repair_target not in (
            "MANUSCRIPT",
            "BRIEF",
        ):
            raise ReportPipelineError(
                "integrity repair_target must be MANUSCRIPT, BRIEF, or None"
            )
        if (
            review.disposition is IntegrityDisposition.PASS
            and review.repair_target is not None
        ):
            raise ReportPipelineError(
                "integrity PASS must not carry a repair_target"
            )
        if (
            review.disposition is IntegrityDisposition.REOPEN_RESEARCH
            and review.repair_target is not None
        ):
            raise ReportPipelineError(
                "integrity REOPEN_RESEARCH must not carry a Delivery repair_target"
            )
