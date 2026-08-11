"""Typed V1 research state.

This module mirrors the frozen domain model without adding workflow behavior.
Normal constructors generate opaque UUID-backed references; persisted references
are restored by the explicit JSON codec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import TypeAlias
from uuid import uuid4


RunRef: TypeAlias = str
RequirementRef: TypeAlias = str
PaperRef: TypeAlias = str
ApproachFamilyRef: TypeAlias = str
FindingRef: TypeAlias = str
OpenProblemRef: TypeAlias = str
GapRef: TypeAlias = str
CompletionCheckRef: TypeAlias = str


def _new_ref(prefix: str) -> str:
    return f"{prefix}_{uuid4()}"


def new_run_ref() -> RunRef:
    return _new_ref("run")


def new_requirement_ref() -> RequirementRef:
    return _new_ref("requirement")


def new_paper_ref() -> PaperRef:
    return _new_ref("paper")


def new_approach_family_ref() -> ApproachFamilyRef:
    return _new_ref("approach")


def new_finding_ref() -> FindingRef:
    return _new_ref("finding")


def new_open_problem_ref() -> OpenProblemRef:
    return _new_ref("problem")


def new_gap_ref() -> GapRef:
    return _new_ref("gap")


def new_completion_check_ref() -> CompletionCheckRef:
    return _new_ref("check")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PaperResearchStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class LifecycleMode(StrEnum):
    RESEARCH = "RESEARCH"
    COMPLETION_CHECK = "COMPLETION_CHECK"
    DELIVERY = "DELIVERY"
    CLOSED = "CLOSED"


class CompletionVerdict(StrEnum):
    PASS = "PASS"
    CONTINUE = "CONTINUE"
    UNCERTAIN = "UNCERTAIN"


class RunOutcome(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"


class SourceRelation(StrEnum):
    SUPPORTS = "SUPPORTS"
    CHALLENGES = "CHALLENGES"
    QUALIFIES = "QUALIFIES"


class ArtifactKind(StrEnum):
    REPORT = "REPORT"


@dataclass(slots=True, kw_only=True)
class ResearchRequirement:
    statement: str
    id: RequirementRef = field(default_factory=new_requirement_ref)


@dataclass(slots=True, kw_only=True)
class Deliverable:
    description: str
    required_artifacts: set[ArtifactKind] = field(default_factory=set)


@dataclass(slots=True, kw_only=True)
class ResearchContract:
    mission: str
    scope: str
    deliverable: Deliverable
    requirements: dict[RequirementRef, ResearchRequirement] = field(
        default_factory=dict
    )


@dataclass(slots=True, kw_only=True)
class ContractRevision:
    revision: int
    contract: ResearchContract
    reason: str
    recorded_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True, kw_only=True)
class VersionedResearchContract:
    current_revision: int
    revisions: list[ContractRevision]


@dataclass(slots=True, kw_only=True, frozen=True)
class SourceLocator:
    kind: str
    value: str


@dataclass(slots=True, kw_only=True)
class PaperSource:
    title: str
    authors: tuple[str, ...] = ()
    publication_year: int | None = None
    publication_date: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    canonical_url: str | None = None
    other_identifiers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class PaperAnalysis:
    summary: str
    relevance_to_run: str
    contributions: tuple[str, ...] = ()
    key_results: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    key_locators: tuple[SourceLocator, ...] = ()


@dataclass(slots=True, kw_only=True)
class Paper:
    source: PaperSource
    research_status: PaperResearchStatus = PaperResearchStatus.ACTIVE
    # Durable disposition for a retired candidate: why this run no longer
    # investigates/uses it. None while ACTIVE. Persisted (not a trajectory) so
    # a fresh Completion Checker and a resumed session can see candidate closure.
    retirement_reason: str | None = None
    analysis: PaperAnalysis | None = None
    id: PaperRef = field(default_factory=new_paper_ref)


@dataclass(slots=True, kw_only=True, frozen=True)
class LiteratureSource:
    paper_ref: PaperRef
    relation: SourceRelation
    locator: SourceLocator | None = None


@dataclass(slots=True, kw_only=True)
class ApproachFamily:
    name: str
    core_idea: str
    representative_papers: set[PaperRef] = field(default_factory=set)
    id: ApproachFamilyRef = field(default_factory=new_approach_family_ref)


@dataclass(slots=True, kw_only=True)
class LandscapeFinding:
    statement: str
    approach_refs: set[ApproachFamilyRef] = field(default_factory=set)
    sources: set[LiteratureSource] = field(default_factory=set)
    id: FindingRef = field(default_factory=new_finding_ref)


@dataclass(slots=True, kw_only=True)
class OpenProblem:
    statement: str
    approach_refs: set[ApproachFamilyRef] = field(default_factory=set)
    sources: set[LiteratureSource] = field(default_factory=set)
    id: OpenProblemRef = field(default_factory=new_open_problem_ref)


@dataclass(slots=True, kw_only=True)
class LiteratureLandscape:
    approach_families: dict[ApproachFamilyRef, ApproachFamily] = field(
        default_factory=dict
    )
    findings: dict[FindingRef, LandscapeFinding] = field(default_factory=dict)
    open_problems: dict[OpenProblemRef, OpenProblem] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class InvestigationGap:
    description: str
    requirement_refs: set[RequirementRef] = field(default_factory=set)
    approach_refs: set[ApproachFamilyRef] = field(default_factory=set)
    resolution: str | None = None
    id: GapRef = field(default_factory=new_gap_ref)


@dataclass(slots=True, kw_only=True)
class CompletionCheck:
    basis_revision: int
    basis_contract_revision: int
    requester_rationale: str
    requested_at: datetime = field(default_factory=utc_now)
    verdict: CompletionVerdict | None = None
    reasons: tuple[str, ...] = ()
    blocking_gap_refs: set[GapRef] = field(default_factory=set)
    completed_at: datetime | None = None
    id: CompletionCheckRef = field(default_factory=new_completion_check_ref)


@dataclass(slots=True, kw_only=True)
class ResourceState:
    limits: dict[str, int] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True, frozen=True)
class CompletionPassBasis:
    completion_check_ref: CompletionCheckRef


@dataclass(slots=True, kw_only=True, frozen=True)
class PartialAuthorizationBasis:
    basis_revision: int
    basis_contract_revision: int
    authorized_at: datetime
    rationale: str | None = field(default=None, compare=False)


DeliveryBasis: TypeAlias = CompletionPassBasis | PartialAuthorizationBasis


@dataclass(slots=True, kw_only=True)
class ResearchRun:
    contract: VersionedResearchContract
    state_revision: int = 1
    lifecycle: LifecycleMode = LifecycleMode.RESEARCH
    outcome: RunOutcome | None = None
    resources: ResourceState = field(default_factory=ResourceState)
    papers: dict[PaperRef, Paper] = field(default_factory=dict)
    literature_landscape: LiteratureLandscape = field(
        default_factory=LiteratureLandscape
    )
    investigation_gaps: dict[GapRef, InvestigationGap] = field(default_factory=dict)
    completion_checks: dict[CompletionCheckRef, CompletionCheck] = field(
        default_factory=dict
    )
    delivery_basis: DeliveryBasis | None = None
    id: RunRef = field(default_factory=new_run_ref)
