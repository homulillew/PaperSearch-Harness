"""Deterministic, lifecycle-aware projections of authoritative research state."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeAlias, TypeVar

from my_search_harness.domain.model import (
    ApproachFamily,
    CompletionCheck,
    DeliveryBasis,
    InvestigationGap,
    LandscapeFinding,
    LifecycleMode,
    LiteratureSource,
    OpenProblem,
    Paper,
    PaperResearchStatus,
    ResearchRequirement,
    ResearchContract,
    ResearchRun,
)

from .persistence import JsonResearchRunRepository, RevisionConflictError


class ContextProjectionError(RuntimeError):
    """A requested projection cannot be produced safely."""


class ContextLimitExceededError(ContextProjectionError):
    """A complete required semantic unit cannot fit within configured bounds."""


class StableRefNotFoundError(ContextProjectionError):
    """A requested stable reference does not resolve in current state."""


class ContextSection(StrEnum):
    APPROACH_FAMILIES = "approach_families"
    FINDINGS = "findings"
    OPEN_PROBLEMS = "open_problems"
    OPEN_GAPS = "open_gaps"
    PAPERS = "papers"


@dataclass(slots=True, frozen=True, kw_only=True)
class ContextLimits:
    research_page_size: int = 20
    completion_max_items: int = 500
    delivery_max_items: int = 500
    inspect_max_refs: int = 20
    max_characters: int = 100_000


@dataclass(slots=True, frozen=True, kw_only=True)
class ContextContinuation:
    state_revision: int
    section: ContextSection
    after: str


_T = TypeVar("_T")
_EntityT = TypeVar("_EntityT")
_ContextT = TypeVar("_ContextT")


@dataclass(slots=True, frozen=True, kw_only=True)
class ContextPage(Generic[_T]):
    items: tuple[_T, ...]
    total: int
    continuation: ContextContinuation | None

    @property
    def shown(self) -> int:
        return len(self.items)


@dataclass(slots=True, frozen=True, kw_only=True)
class RequirementContext:
    ref: str
    statement: str


@dataclass(slots=True, frozen=True, kw_only=True)
class ContractContext:
    contract_revision: int
    mission: str
    requirements: tuple[RequirementContext, ...]
    scope: str
    deliverable_description: str
    required_artifacts: tuple[str, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class ResourceContext:
    limits: tuple[tuple[str, int], ...]
    usage: tuple[tuple[str, int], ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class PaperIndexEntry:
    ref: str
    title: str
    authors: tuple[str, ...]
    publication_year: int | None
    publication_date: str | None
    doi: str | None
    arxiv_id: str | None
    canonical_url: str | None
    research_status: PaperResearchStatus
    retirement_reason: str | None
    has_analysis: bool


@dataclass(slots=True, frozen=True, kw_only=True)
class ApproachContext:
    ref: str
    name: str
    core_idea: str
    representative_paper_refs: tuple[str, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class FindingContext:
    ref: str
    statement: str
    approach_refs: tuple[str, ...]
    sources: tuple[LiteratureSource, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class OpenProblemContext:
    ref: str
    statement: str
    approach_refs: tuple[str, ...]
    sources: tuple[LiteratureSource, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class GapContext:
    ref: str
    description: str
    requirement_refs: tuple[str, ...]
    approach_refs: tuple[str, ...]
    resolution: str | None


@dataclass(slots=True, frozen=True, kw_only=True)
class RepresentativePaperEvidence:
    """Derived structural facts about a representative paper's evidence grounding.

    These are observations derived from authoritative state, not quality scores or
    thresholds. The Completion Checker applies semantic criteria to judge whether the
    grounding is sufficient; an empty locator count is a signal to inspect the paper,
    not an automatic block.
    """

    paper_ref: str
    has_analysis: bool
    analysis_locator_count: int
    landscape_source_count: int
    landscape_source_with_locator_count: int


@dataclass(slots=True, frozen=True, kw_only=True)
class CompletionFeedbackContext:
    completion_check_ref: str
    verdict: str
    reasons: tuple[str, ...]
    blocking_gap_refs: tuple[str, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class ResearchView:
    state_revision: int
    lifecycle: LifecycleMode
    contract: ContractContext
    resources: ResourceContext
    approach_families: ContextPage[ApproachContext]
    findings: ContextPage[FindingContext]
    open_problems: ContextPage[OpenProblemContext]
    open_gaps: ContextPage[GapContext]
    papers: ContextPage[PaperIndexEntry]
    latest_completion_feedback: CompletionFeedbackContext | None


@dataclass(slots=True, frozen=True, kw_only=True)
class CompletionView:
    state_revision: int
    lifecycle: LifecycleMode
    contract: ContractContext
    approach_families: tuple[ApproachContext, ...]
    findings: tuple[FindingContext, ...]
    open_problems: tuple[OpenProblemContext, ...]
    open_gaps: tuple[GapContext, ...]
    papers: tuple[PaperIndexEntry, ...]
    representative_paper_refs: tuple[str, ...]
    evidence_diagnostics: tuple[RepresentativePaperEvidence, ...]
    completion_check_ref: str
    requester_rationale: str


@dataclass(slots=True, frozen=True, kw_only=True)
class DeliveryView:
    state_revision: int
    lifecycle: LifecycleMode
    contract: ContractContext
    delivery_basis: DeliveryBasis
    approach_families: tuple[ApproachContext, ...]
    findings: tuple[FindingContext, ...]
    open_problems: tuple[OpenProblemContext, ...]
    open_gaps: tuple[GapContext, ...]
    papers: tuple[PaperIndexEntry, ...]


ContextView: TypeAlias = ResearchView | CompletionView | DeliveryView
InspectableDomainObject: TypeAlias = (
    ResearchRequirement
    | Paper
    | ApproachFamily
    | LandscapeFinding
    | OpenProblem
    | InvestigationGap
    | CompletionCheck
)


@dataclass(slots=True, frozen=True, kw_only=True)
class InspectedObject:
    ref: str
    kind: str
    value: InspectableDomainObject


@dataclass(slots=True, frozen=True, kw_only=True)
class InspectResult:
    state_revision: int
    objects: tuple[InspectedObject, ...]


class ContextProjectionService:
    """Build bounded views and stable-ref drilldowns without reinterpretation."""

    def __init__(
        self,
        repository: JsonResearchRunRepository,
        *,
        limits: ContextLimits = ContextLimits(),
    ) -> None:
        self._validate_limits(limits)
        self._repository = repository
        self._limits = limits

    def view(
        self,
        run_id: str,
        continuation: ContextContinuation | None = None,
    ) -> ContextView:
        run = self._repository.load(run_id)
        if continuation is not None:
            if not isinstance(continuation, ContextContinuation):
                raise ContextProjectionError(
                    "continuation must be a ContextContinuation"
                )
            if (
                not isinstance(continuation.state_revision, int)
                or isinstance(continuation.state_revision, bool)
                or continuation.state_revision < 1
                or not isinstance(continuation.section, ContextSection)
                or not isinstance(continuation.after, str)
                or not continuation.after
            ):
                raise ContextProjectionError("continuation fields are invalid")
            if continuation.state_revision != run.state_revision:
                raise RevisionConflictError(
                    f"expected revision {continuation.state_revision}, found "
                    f"{run.state_revision}"
                )

        if run.lifecycle is LifecycleMode.RESEARCH:
            result: ContextView = self._research_view(run, continuation)
        elif run.lifecycle is LifecycleMode.COMPLETION_CHECK:
            if continuation is not None:
                raise ContextProjectionError(
                    "Completion View is complete and does not accept continuation"
                )
            result = self._completion_view(run)
        elif run.lifecycle is LifecycleMode.DELIVERY:
            if continuation is not None:
                raise ContextProjectionError(
                    "Delivery View is complete and does not accept continuation"
                )
            result = self._delivery_view(run)
        else:
            raise ContextProjectionError(
                "CLOSED runs do not have an active context view"
            )
        self._enforce_character_limit(result)
        return result

    def inspect(
        self,
        run_id: str,
        expected_revision: int,
        refs: tuple[str, ...],
    ) -> InspectResult:
        run = self._repository.load(run_id)
        if run.state_revision != expected_revision:
            raise RevisionConflictError(
                f"expected revision {expected_revision}, found {run.state_revision}"
            )
        if (
            not isinstance(refs, tuple)
            or not refs
            or not all(isinstance(ref, str) for ref in refs)
        ):
            raise ContextProjectionError("refs must be a non-empty tuple of strings")
        if len(refs) > self._limits.inspect_max_refs:
            raise ContextLimitExceededError(
                f"inspect supports at most {self._limits.inspect_max_refs} refs"
            )
        if len(set(refs)) != len(refs):
            raise ContextProjectionError("inspect refs must be unique")

        objects = tuple(self._inspect_one(run, ref) for ref in refs)
        result = InspectResult(
            state_revision=run.state_revision,
            objects=objects,
        )
        self._enforce_character_limit(result)
        return result

    def _research_view(
        self,
        run: ResearchRun,
        continuation: ContextContinuation | None,
    ) -> ResearchView:
        page_size = self._limits.research_page_size
        open_gaps = {
            ref: gap
            for ref, gap in run.investigation_gaps.items()
            if gap.resolution is None
        }
        landscape = run.literature_landscape
        return ResearchView(
            state_revision=run.state_revision,
            lifecycle=run.lifecycle,
            contract=self._contract_context(run),
            resources=ResourceContext(
                limits=tuple(sorted(run.resources.limits.items())),
                usage=tuple(sorted(run.resources.usage.items())),
            ),
            approach_families=self._page(
                run,
                ContextSection.APPROACH_FAMILIES,
                landscape.approach_families,
                self._approach_context,
                continuation,
                page_size,
            ),
            findings=self._page(
                run,
                ContextSection.FINDINGS,
                landscape.findings,
                self._finding_context,
                continuation,
                page_size,
            ),
            open_problems=self._page(
                run,
                ContextSection.OPEN_PROBLEMS,
                landscape.open_problems,
                self._open_problem_context,
                continuation,
                page_size,
            ),
            open_gaps=self._page(
                run,
                ContextSection.OPEN_GAPS,
                open_gaps,
                self._gap_context,
                continuation,
                page_size,
            ),
            papers=self._page(
                run,
                ContextSection.PAPERS,
                run.papers,
                self._paper_context,
                continuation,
                page_size,
            ),
            latest_completion_feedback=self._latest_completion_feedback(run),
        )

    def _completion_view(self, run: ResearchRun) -> CompletionView:
        landscape = run.literature_landscape
        approaches = tuple(
            self._approach_context(landscape.approach_families[ref])
            for ref in sorted(landscape.approach_families)
        )
        findings = tuple(
            self._finding_context(landscape.findings[ref])
            for ref in sorted(landscape.findings)
        )
        problems = tuple(
            self._open_problem_context(landscape.open_problems[ref])
            for ref in sorted(landscape.open_problems)
        )
        gaps = tuple(
            self._gap_context(run.investigation_gaps[ref])
            for ref in sorted(run.investigation_gaps)
            if run.investigation_gaps[ref].resolution is None
        )
        # The fresh Checker sees every retained Paper, not only representative
        # ones — otherwise retained-but-unanalyzed candidates can silently
        # disappear from Completion judgment. Detailed
        # PaperAnalysis stays behind completion-inspect; here we expose only
        # the per-paper closure summary: status, has_analysis, retirement_reason.
        papers = tuple(
            self._paper_context(run.papers[ref]) for ref in sorted(run.papers)
        )
        pending = tuple(
            check for check in run.completion_checks.values() if check.verdict is None
        )
        if len(pending) != 1:
            raise ContextProjectionError(
                "Completion View requires exactly one pending CompletionCheck"
            )
        representative_refs = tuple(
            sorted(
                {
                    paper_ref
                    for approach in approaches
                    for paper_ref in approach.representative_paper_refs
                }
            )
        )
        evidence_diagnostics = tuple(
            self._representative_paper_evidence(run, paper_ref)
            for paper_ref in representative_refs
        )
        self._enforce_item_limit(
            "Completion View",
            len(approaches)
            + len(findings)
            + len(problems)
            + len(gaps)
            + len(papers)
            + len(representative_refs)
            + len(evidence_diagnostics)
            + len(self._current_contract(run).requirements),
            self._limits.completion_max_items,
        )
        return CompletionView(
            state_revision=run.state_revision,
            lifecycle=run.lifecycle,
            contract=self._contract_context(run),
            approach_families=approaches,
            findings=findings,
            open_problems=problems,
            open_gaps=gaps,
            papers=papers,
            representative_paper_refs=representative_refs,
            evidence_diagnostics=evidence_diagnostics,
            completion_check_ref=pending[0].id,
            requester_rationale=pending[0].requester_rationale,
        )

    def _delivery_view(self, run: ResearchRun) -> DeliveryView:
        if run.delivery_basis is None:
            raise ContextProjectionError("Delivery View requires a DeliveryBasis")
        landscape = run.literature_landscape
        approaches = tuple(
            self._approach_context(landscape.approach_families[ref])
            for ref in sorted(landscape.approach_families)
        )
        findings = tuple(
            self._finding_context(landscape.findings[ref])
            for ref in sorted(landscape.findings)
        )
        problems = tuple(
            self._open_problem_context(landscape.open_problems[ref])
            for ref in sorted(landscape.open_problems)
        )
        gaps = tuple(
            self._gap_context(run.investigation_gaps[ref])
            for ref in sorted(run.investigation_gaps)
            if run.investigation_gaps[ref].resolution is None
        )
        papers = tuple(
            self._paper_context(run.papers[ref]) for ref in sorted(run.papers)
        )
        self._enforce_item_limit(
            "Delivery View",
            len(approaches)
            + len(findings)
            + len(problems)
            + len(gaps)
            + len(papers)
            + len(self._current_contract(run).requirements),
            self._limits.delivery_max_items,
        )
        return DeliveryView(
            state_revision=run.state_revision,
            lifecycle=run.lifecycle,
            contract=self._contract_context(run),
            delivery_basis=deepcopy(run.delivery_basis),
            approach_families=approaches,
            findings=findings,
            open_problems=problems,
            open_gaps=gaps,
            papers=papers,
        )

    def _page(
        self,
        run: ResearchRun,
        section: ContextSection,
        values: Mapping[str, _EntityT],
        converter: Callable[[_EntityT], _ContextT],
        requested: ContextContinuation | None,
        page_size: int,
    ) -> ContextPage[_ContextT]:
        refs = sorted(values)
        start = 0
        if requested is not None and requested.section is section:
            try:
                start = refs.index(requested.after) + 1
            except ValueError as exc:
                raise ContextProjectionError(
                    f"continuation ref {requested.after!r} is not in {section.value}"
                ) from exc
        selected_refs = refs[start : start + page_size]
        items = tuple(converter(values[ref]) for ref in selected_refs)
        next_value = None
        if start + len(selected_refs) < len(refs):
            next_value = ContextContinuation(
                state_revision=run.state_revision,
                section=section,
                after=selected_refs[-1],
            )
        return ContextPage(
            items=items,
            total=len(refs),
            continuation=next_value,
        )

    def _inspect_one(self, run: ResearchRun, ref: str) -> InspectedObject:
        current_contract = self._current_contract(run)
        namespaces: tuple[tuple[str, Mapping[str, InspectableDomainObject]], ...] = (
            ("requirement", current_contract.requirements),
            ("paper", run.papers),
            ("approach_family", run.literature_landscape.approach_families),
            ("landscape_finding", run.literature_landscape.findings),
            ("open_problem", run.literature_landscape.open_problems),
            ("investigation_gap", run.investigation_gaps),
            ("completion_check", run.completion_checks),
        )
        matches = [(kind, values[ref]) for kind, values in namespaces if ref in values]
        if len(matches) != 1:
            raise StableRefNotFoundError(
                f"stable ref {ref!r} does not resolve in current state"
            )
        kind, value = matches[0]
        return InspectedObject(
            ref=ref,
            kind=kind,
            value=deepcopy(value),
        )

    @staticmethod
    def _current_contract(run: ResearchRun) -> ResearchContract:
        matches = [
            revision.contract
            for revision in run.contract.revisions
            if revision.revision == run.contract.current_revision
        ]
        if len(matches) != 1:
            raise ContextProjectionError("current Contract cannot be resolved")
        return matches[0]

    def _contract_context(self, run: ResearchRun) -> ContractContext:
        contract = self._current_contract(run)
        return ContractContext(
            contract_revision=run.contract.current_revision,
            mission=contract.mission,
            requirements=tuple(
                RequirementContext(
                    ref=ref, statement=contract.requirements[ref].statement
                )
                for ref in sorted(contract.requirements)
            ),
            scope=contract.scope,
            deliverable_description=contract.deliverable.description,
            required_artifacts=tuple(
                sorted(kind.value for kind in contract.deliverable.required_artifacts)
            ),
        )

    @staticmethod
    def _paper_context(paper: Paper) -> PaperIndexEntry:
        return PaperIndexEntry(
            ref=paper.id,
            title=paper.source.title,
            authors=paper.source.authors,
            publication_year=paper.source.publication_year,
            publication_date=paper.source.publication_date,
            doi=paper.source.doi,
            arxiv_id=paper.source.arxiv_id,
            canonical_url=paper.source.canonical_url,
            research_status=paper.research_status,
            retirement_reason=paper.retirement_reason,
            has_analysis=paper.analysis is not None,
        )

    @staticmethod
    def _approach_context(approach: ApproachFamily) -> ApproachContext:
        return ApproachContext(
            ref=approach.id,
            name=approach.name,
            core_idea=approach.core_idea,
            representative_paper_refs=tuple(sorted(approach.representative_papers)),
        )

    @classmethod
    def _finding_context(cls, finding: LandscapeFinding) -> FindingContext:
        return FindingContext(
            ref=finding.id,
            statement=finding.statement,
            approach_refs=tuple(sorted(finding.approach_refs)),
            sources=tuple(sorted(finding.sources, key=cls._source_sort_key)),
        )

    @classmethod
    def _open_problem_context(cls, problem: OpenProblem) -> OpenProblemContext:
        return OpenProblemContext(
            ref=problem.id,
            statement=problem.statement,
            approach_refs=tuple(sorted(problem.approach_refs)),
            sources=tuple(sorted(problem.sources, key=cls._source_sort_key)),
        )

    @staticmethod
    def _gap_context(gap: InvestigationGap) -> GapContext:
        return GapContext(
            ref=gap.id,
            description=gap.description,
            requirement_refs=tuple(sorted(gap.requirement_refs)),
            approach_refs=tuple(sorted(gap.approach_refs)),
            resolution=gap.resolution,
        )

    @staticmethod
    def _representative_paper_evidence(
        run: ResearchRun,
        paper_ref: str,
    ) -> RepresentativePaperEvidence:
        """Derive structural evidence facts for a representative paper.

        No scores or thresholds: these are counts the Checker scans to decide which
        papers to inspect. An empty locator count signals that grounding may need
        inspection, not that the paper is automatically deficient.
        """
        paper = run.papers.get(paper_ref)
        has_analysis = paper is not None and paper.analysis is not None
        analysis_locator_count = (
            len(paper.analysis.key_locators)
            if has_analysis and paper is not None
            else 0
        )
        landscape_sources: list[LiteratureSource] = []
        for finding in run.literature_landscape.findings.values():
            landscape_sources.extend(
                source for source in finding.sources if source.paper_ref == paper_ref
            )
        for problem in run.literature_landscape.open_problems.values():
            landscape_sources.extend(
                source for source in problem.sources if source.paper_ref == paper_ref
            )
        landscape_source_count = len(landscape_sources)
        landscape_source_with_locator_count = sum(
            1 for source in landscape_sources if source.locator is not None
        )
        return RepresentativePaperEvidence(
            paper_ref=paper_ref,
            has_analysis=has_analysis,
            analysis_locator_count=analysis_locator_count,
            landscape_source_count=landscape_source_count,
            landscape_source_with_locator_count=landscape_source_with_locator_count,
        )

    @staticmethod
    def _source_sort_key(source: LiteratureSource) -> tuple[str, str, str, str]:
        locator_kind = "" if source.locator is None else source.locator.kind
        locator_value = "" if source.locator is None else source.locator.value
        return source.paper_ref, source.relation.value, locator_kind, locator_value

    @staticmethod
    def _latest_completion_feedback(
        run: ResearchRun,
    ) -> CompletionFeedbackContext | None:
        completed = [
            check
            for check in run.completion_checks.values()
            if check.verdict is not None and check.completed_at is not None
        ]
        if not completed:
            return None
        latest = max(completed, key=lambda check: (check.completed_at, check.id))
        assert latest.verdict is not None
        return CompletionFeedbackContext(
            completion_check_ref=latest.id,
            verdict=latest.verdict.value,
            reasons=latest.reasons,
            blocking_gap_refs=tuple(sorted(latest.blocking_gap_refs)),
        )

    def _enforce_character_limit(self, value: object) -> None:
        if len(repr(value)) > self._limits.max_characters:
            raise ContextLimitExceededError(
                "projection exceeds max_characters without a safe semantic boundary"
            )

    @staticmethod
    def _enforce_item_limit(name: str, count: int, limit: int) -> None:
        if count > limit:
            raise ContextLimitExceededError(
                f"{name} requires {count} semantic items; configured limit is {limit}"
            )

    @staticmethod
    def _validate_limits(limits: ContextLimits) -> None:
        if not isinstance(limits, ContextLimits):
            raise ContextProjectionError("limits must be ContextLimits")
        for name, value in (
            ("research_page_size", limits.research_page_size),
            ("completion_max_items", limits.completion_max_items),
            ("delivery_max_items", limits.delivery_max_items),
            ("inspect_max_refs", limits.inspect_max_refs),
            ("max_characters", limits.max_characters),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ContextProjectionError(f"{name} must be a positive integer")
