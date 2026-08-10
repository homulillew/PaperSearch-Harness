"""Deterministic validation for V1 research state.

Snapshot validation checks facts that every persisted aggregate must satisfy.
Transition validation checks the small set of invariants that need both the
previous and proposed aggregate.  Neither layer attempts to judge research
quality.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import NoReturn, Protocol, TypeVar
from uuid import UUID

from .paper_identity import paper_identity_keys
from .model import (
    ApproachFamily,
    ArtifactKind,
    CompletionCheck,
    CompletionPassBasis,
    CompletionVerdict,
    ContractRevision,
    Deliverable,
    InvestigationGap,
    LandscapeFinding,
    LifecycleMode,
    LiteratureLandscape,
    LiteratureSource,
    OpenProblem,
    Paper,
    PartialAuthorizationBasis,
    PaperAnalysis,
    PaperResearchStatus,
    PaperSource,
    ResearchContract,
    ResearchRequirement,
    ResearchRun,
    ResourceState,
    RunOutcome,
    SourceLocator,
    SourceRelation,
    VersionedResearchContract,
)


class DomainValidationError(ValueError):
    """The proposed aggregate violates the frozen V1 domain model."""


class _Entity(Protocol):
    id: str


_T = TypeVar("_T", bound=_Entity)


def _fail(message: str) -> NoReturn:
    raise DomainValidationError(message)


def validate_ref(value: object, prefix: str, path: str) -> str:
    """Validate one opaque, namespace-prefixed UUID4 reference."""

    marker = f"{prefix}_"
    if not isinstance(value, str) or not value.startswith(marker):
        _fail(f"{path} must be an opaque {prefix} UUID4 reference")
    try:
        parsed = UUID(value[len(marker) :])
    except (ValueError, AttributeError) as exc:
        raise DomainValidationError(
            f"{path} must be an opaque {prefix} UUID4 reference"
        ) from exc
    if parsed.version != 4:
        _fail(f"{path} must use UUID version 4")
    return value


def _aware(value: object, path: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail(f"{path} must be a timezone-aware datetime")
    if value.utcoffset() is None:
        _fail(f"{path} must be a timezone-aware datetime")
    return value


def _positive_revision(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        _fail(f"{path} must be a positive integer")
    return value


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        _fail(f"{path} must be a string")
    return value


def _string_tuple(value: object, path: str) -> None:
    if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
        _fail(f"{path} must be a tuple of strings")


def _reference_set(value: object, path: str) -> set[str]:
    if not isinstance(value, set) or not all(isinstance(item, str) for item in value):
        _fail(f"{path} must be a set of references")
    return value


def _validate_locator(locator: object, path: str) -> None:
    if not isinstance(locator, SourceLocator):
        _fail(f"{path} must be a SourceLocator")
    _string(locator.kind, f"{path}.kind")
    _string(locator.value, f"{path}.value")


def _entity_values(
    entities: Mapping[str, _T],
    entity_type: type[_T],
    prefix: str,
    path: str,
) -> Iterable[_T]:
    if not isinstance(entities, dict):
        _fail(f"{path} must be a dictionary keyed by stable reference")
    for key, entity in entities.items():
        validate_ref(key, prefix, f"{path} key")
        if not isinstance(entity, entity_type):
            _fail(f"{path}[{key!r}] has the wrong entity type")
        if entity.id != key:
            _fail(f"{path}[{key!r}] key must equal entity.id")
        yield entity


def _validate_requirement_map(
    requirements: Mapping[str, ResearchRequirement], path: str
) -> None:
    for requirement in _entity_values(
        requirements, ResearchRequirement, "requirement", path
    ):
        if not isinstance(requirement.statement, str):
            _fail(f"{path}[{requirement.id!r}].statement must be a string")


def _validate_research_contract(contract: object, path: str) -> ResearchContract:
    if not isinstance(contract, ResearchContract):
        _fail(f"{path} must be a ResearchContract")
    _string(contract.mission, f"{path}.mission")
    _string(contract.scope, f"{path}.scope")
    if not isinstance(contract.deliverable, Deliverable):
        _fail(f"{path}.deliverable must be a Deliverable")
    _string(contract.deliverable.description, f"{path}.deliverable.description")
    if not isinstance(contract.deliverable.required_artifacts, set) or not all(
        isinstance(artifact, ArtifactKind)
        for artifact in contract.deliverable.required_artifacts
    ):
        _fail(f"{path}.deliverable.required_artifacts must contain ArtifactKind")
    _validate_requirement_map(contract.requirements, f"{path}.requirements")
    return contract


def _validate_contract(run: ResearchRun) -> set[str]:
    versioned = run.contract
    if not isinstance(versioned, VersionedResearchContract):
        _fail("contract must be a VersionedResearchContract")
    _positive_revision(versioned.current_revision, "contract.current_revision")
    revisions = versioned.revisions
    if not isinstance(revisions, list) or not revisions:
        _fail("contract.revisions must contain at least one revision")

    revision_numbers: list[int] = []
    current_requirements: set[str] = set()
    for index, entry in enumerate(revisions):
        if not isinstance(entry, ContractRevision):
            _fail(f"contract.revisions[{index}] must be a ContractRevision")
        number = _positive_revision(
            entry.revision, f"contract.revisions[{index}].revision"
        )
        if number in revision_numbers:
            _fail("contract revision numbers must be unique")
        revision_numbers.append(number)
        _string(entry.reason, f"contract.revisions[{index}].reason")
        _aware(entry.recorded_at, f"contract.revisions[{index}].recorded_at")
        contract = _validate_research_contract(
            entry.contract, f"contract.revisions[{index}].contract"
        )

        if number == versioned.current_revision:
            current_requirements = set(contract.requirements)

    if revision_numbers != sorted(revision_numbers):
        _fail("contract revision numbers must be strictly increasing")
    if revision_numbers[0] != 1:
        _fail("contract revision history must start at revision 1")
    if versioned.current_revision not in revision_numbers:
        _fail("contract.current_revision must reference an existing revision")
    if versioned.current_revision != revision_numbers[-1]:
        _fail("contract.current_revision must identify the latest revision")
    return current_requirements


def _validate_paper_source(source: object, path: str) -> PaperSource:
    if not isinstance(source, PaperSource):
        _fail(f"{path} must be a PaperSource")
    _string(source.title, f"{path}.title")
    _string_tuple(source.authors, f"{path}.authors")
    if source.publication_year is not None and (
        not isinstance(source.publication_year, int)
        or isinstance(source.publication_year, bool)
    ):
        _fail(f"{path}.publication_year must be an integer or None")
    if source.publication_date is not None:
        if not isinstance(source.publication_date, str):
            _fail(f"{path}.publication_date must use YYYY-MM-DD or be None")
        try:
            parsed_publication_date = date.fromisoformat(source.publication_date)
        except ValueError:
            _fail(f"{path}.publication_date must use YYYY-MM-DD or be None")
        if source.publication_date != parsed_publication_date.isoformat():
            _fail(f"{path}.publication_date must use YYYY-MM-DD or be None")
    for name, value in (
        ("doi", source.doi),
        ("arxiv_id", source.arxiv_id),
        ("canonical_url", source.canonical_url),
    ):
        if value is not None:
            _string(value, f"{path}.{name}")
    if not isinstance(source.other_identifiers, dict) or not all(
        isinstance(kind, str) and isinstance(value, str)
        for kind, value in source.other_identifiers.items()
    ):
        _fail(f"{path}.other_identifiers must be a string dictionary")
    return source


def _validate_paper_analysis(analysis: object, path: str) -> None:
    if not isinstance(analysis, PaperAnalysis):
        _fail(f"{path} must be a PaperAnalysis")
    _string(analysis.summary, f"{path}.summary")
    _string(analysis.relevance_to_run, f"{path}.relevance_to_run")
    _string_tuple(analysis.contributions, f"{path}.contributions")
    _string_tuple(analysis.key_results, f"{path}.key_results")
    _string_tuple(analysis.limitations, f"{path}.limitations")
    if not isinstance(analysis.key_locators, tuple):
        _fail(f"{path}.key_locators must be a tuple")
    for index, locator in enumerate(analysis.key_locators):
        _validate_locator(locator, f"{path}.key_locators[{index}]")


def _validate_papers(run: ResearchRun) -> set[str]:
    paper_refs: set[str] = set()
    identities: dict[tuple[str, str], str] = {}
    for paper in _entity_values(run.papers, Paper, "paper", "papers"):
        paper_refs.add(paper.id)
        _validate_paper_source(paper.source, f"papers[{paper.id!r}].source")
        if not isinstance(paper.research_status, PaperResearchStatus):
            _fail(f"papers[{paper.id!r}].research_status must be PaperResearchStatus")
        if paper.analysis is not None:
            _validate_paper_analysis(paper.analysis, f"papers[{paper.id!r}].analysis")
        for kind, normalized in paper_identity_keys(paper.source):
            if not normalized:
                _fail(f"papers[{paper.id!r}] contains an empty stable identifier")
            identity = (kind, normalized)
            existing = identities.get(identity)
            if existing is not None and existing != paper.id:
                _fail(
                    f"papers {existing!r} and {paper.id!r} share stable identifier "
                    f"{kind}:{normalized}"
                )
            identities[identity] = paper.id
    return paper_refs


def _validate_literature_sources(
    sources: set[LiteratureSource], paper_refs: set[str], path: str
) -> None:
    if not isinstance(sources, set):
        _fail(f"{path} must be a set")
    for source in sources:
        if not isinstance(source, LiteratureSource):
            _fail(f"{path} contains an invalid LiteratureSource")
        if not isinstance(source.relation, SourceRelation):
            _fail(f"{path} contains an invalid SourceRelation")
        validate_ref(source.paper_ref, "paper", f"{path}.paper_ref")
        if source.locator is not None:
            _validate_locator(source.locator, f"{path}.locator")
        if source.paper_ref not in paper_refs:
            _fail(f"{path} contains dangling paper ref {source.paper_ref!r}")


def _validate_landscape(run: ResearchRun, paper_refs: set[str]) -> set[str]:
    landscape = run.literature_landscape
    approach_refs: set[str] = set()
    for approach in _entity_values(
        landscape.approach_families,
        ApproachFamily,
        "approach",
        "literature_landscape.approach_families",
    ):
        approach_refs.add(approach.id)
        _string(approach.name, f"approach family {approach.id!r}.name")
        _string(approach.core_idea, f"approach family {approach.id!r}.core_idea")
        _reference_set(
            approach.representative_papers,
            f"approach family {approach.id!r}.representative_papers",
        )
        for paper_ref in approach.representative_papers:
            validate_ref(
                paper_ref,
                "paper",
                f"approach family {approach.id!r}.representative_papers",
            )
        if not approach.representative_papers:
            _fail(f"approach family {approach.id!r} must have a representative paper")
        dangling = approach.representative_papers - paper_refs
        if dangling:
            _fail(
                f"approach family {approach.id!r} has dangling representative "
                f"paper refs: {sorted(dangling)!r}"
            )

    def validate_item(item: LandscapeFinding | OpenProblem, path: str) -> None:
        _string(item.statement, f"{path}[{item.id!r}].statement")
        _reference_set(item.approach_refs, f"{path}[{item.id!r}].approach_refs")
        for approach_ref in item.approach_refs:
            validate_ref(approach_ref, "approach", f"{path}[{item.id!r}].approach_refs")
        dangling_approaches = item.approach_refs - approach_refs
        if dangling_approaches:
            _fail(
                f"{path}[{item.id!r}] has dangling approach refs: "
                f"{sorted(dangling_approaches)!r}"
            )
        _validate_literature_sources(
            item.sources, paper_refs, f"{path}[{item.id!r}].sources"
        )

    for finding in _entity_values(
        landscape.findings,
        LandscapeFinding,
        "finding",
        "literature_landscape.findings",
    ):
        validate_item(finding, "literature_landscape.findings")
    for problem in _entity_values(
        landscape.open_problems,
        OpenProblem,
        "problem",
        "literature_landscape.open_problems",
    ):
        validate_item(problem, "literature_landscape.open_problems")
    return approach_refs


def _validate_gaps(
    run: ResearchRun, requirement_refs: set[str], approach_refs: set[str]
) -> set[str]:
    gap_refs: set[str] = set()
    for gap in _entity_values(
        run.investigation_gaps, InvestigationGap, "gap", "investigation_gaps"
    ):
        gap_refs.add(gap.id)
        _string(gap.description, f"gap {gap.id!r}.description")
        _reference_set(gap.requirement_refs, f"gap {gap.id!r}.requirement_refs")
        _reference_set(gap.approach_refs, f"gap {gap.id!r}.approach_refs")
        for requirement_ref in gap.requirement_refs:
            validate_ref(
                requirement_ref, "requirement", f"gap {gap.id!r}.requirement_refs"
            )
        for approach_ref in gap.approach_refs:
            validate_ref(approach_ref, "approach", f"gap {gap.id!r}.approach_refs")
        if gap.resolution is not None:
            _string(gap.resolution, f"gap {gap.id!r}.resolution")
        dangling_requirements = gap.requirement_refs - requirement_refs
        dangling_approaches = gap.approach_refs - approach_refs
        if dangling_requirements:
            _fail(
                f"gap {gap.id!r} has dangling current requirement refs: "
                f"{sorted(dangling_requirements)!r}"
            )
        if dangling_approaches:
            _fail(
                f"gap {gap.id!r} has dangling approach refs: "
                f"{sorted(dangling_approaches)!r}"
            )
    return gap_refs


def _validate_checks(
    run: ResearchRun, contract_revisions: set[int], gap_refs: set[str]
) -> dict[str, CompletionCheck]:
    pending: list[CompletionCheck] = []
    checks: dict[str, CompletionCheck] = {}
    for check in _entity_values(
        run.completion_checks,
        CompletionCheck,
        "check",
        "completion_checks",
    ):
        checks[check.id] = check
        _positive_revision(check.basis_revision, f"checks[{check.id!r}].basis_revision")
        if check.basis_revision > run.state_revision:
            _fail(f"check {check.id!r} basis_revision cannot be in the future")
        _positive_revision(
            check.basis_contract_revision,
            f"checks[{check.id!r}].basis_contract_revision",
        )
        if check.basis_contract_revision not in contract_revisions:
            _fail(
                f"check {check.id!r} basis_contract_revision must reference "
                "contract history"
            )
        _aware(check.requested_at, f"checks[{check.id!r}].requested_at")
        _string(check.requester_rationale, f"checks[{check.id!r}].requester_rationale")
        if check.verdict is not None and not isinstance(
            check.verdict, CompletionVerdict
        ):
            _fail(f"check {check.id!r} verdict must be CompletionVerdict or None")
        _string_tuple(check.reasons, f"checks[{check.id!r}].reasons")
        _reference_set(
            check.blocking_gap_refs, f"checks[{check.id!r}].blocking_gap_refs"
        )
        for gap_ref in check.blocking_gap_refs:
            validate_ref(gap_ref, "gap", f"checks[{check.id!r}].blocking_gap_refs")
        if (check.verdict is None) != (check.completed_at is None):
            _fail(
                f"check {check.id!r} verdict and completed_at must both exist "
                "or both be absent"
            )
        if check.completed_at is None:
            pending.append(check)
        else:
            _aware(check.completed_at, f"checks[{check.id!r}].completed_at")
        dangling = check.blocking_gap_refs - gap_refs
        if dangling:
            _fail(
                f"check {check.id!r} has dangling blocking gap refs: "
                f"{sorted(dangling)!r}"
            )

    expected_pending = 1 if run.lifecycle is LifecycleMode.COMPLETION_CHECK else 0
    if len(pending) != expected_pending:
        _fail(
            f"lifecycle {run.lifecycle.value} requires exactly {expected_pending} "
            f"pending completion checks; found {len(pending)}"
        )
    return checks


def _validate_delivery(
    run: ResearchRun,
    contract_revisions: set[int],
    checks: Mapping[str, CompletionCheck],
) -> None:
    if run.lifecycle in {LifecycleMode.RESEARCH, LifecycleMode.COMPLETION_CHECK}:
        if run.delivery_basis is not None:
            _fail(f"lifecycle {run.lifecycle.value} must not retain a delivery basis")
    elif run.delivery_basis is None:
        _fail(f"lifecycle {run.lifecycle.value} requires a delivery basis")

    basis = run.delivery_basis
    if isinstance(basis, CompletionPassBasis):
        validate_ref(
            basis.completion_check_ref,
            "check",
            "delivery_basis.completion_check_ref",
        )
        check = checks.get(basis.completion_check_ref)
        if (
            check is None
            or check.completed_at is None
            or check.verdict is not CompletionVerdict.PASS
        ):
            _fail("CompletionPassBasis must reference a completed PASS check")
    elif isinstance(basis, PartialAuthorizationBasis):
        _positive_revision(basis.basis_revision, "delivery_basis.basis_revision")
        if basis.basis_revision > run.state_revision:
            _fail("delivery_basis.basis_revision cannot be in the future")
        _positive_revision(
            basis.basis_contract_revision,
            "delivery_basis.basis_contract_revision",
        )
        if basis.basis_contract_revision not in contract_revisions:
            _fail(
                "PartialAuthorizationBasis.basis_contract_revision must reference "
                "contract history"
            )
        _aware(basis.authorized_at, "delivery_basis.authorized_at")
        if basis.rationale is not None:
            _string(basis.rationale, "delivery_basis.rationale")
    elif basis is not None:
        _fail("delivery_basis has an unknown variant")

    if run.lifecycle is LifecycleMode.CLOSED:
        if run.outcome is None:
            _fail("CLOSED run requires an outcome")
        if run.outcome is RunOutcome.COMPLETE and not isinstance(
            basis, CompletionPassBasis
        ):
            _fail("COMPLETE closure requires CompletionPassBasis")
        if run.outcome is RunOutcome.PARTIAL and not isinstance(
            basis, PartialAuthorizationBasis
        ):
            _fail("PARTIAL closure requires PartialAuthorizationBasis")
    elif run.outcome is not None:
        _fail("non-CLOSED run must not retain an outcome")


def _validate_resources(run: ResearchRun) -> None:
    if not isinstance(run.resources, ResourceState):
        _fail("resources must be a ResourceState")
    if not isinstance(run.resources.limits, dict):
        _fail("resource limits must be a dictionary")
    if not isinstance(run.resources.usage, dict):
        _fail("resource usage must be a dictionary")
    for name, limit in run.resources.limits.items():
        if not isinstance(name, str) or not name:
            _fail("resource limit names must be non-empty strings")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            _fail(f"resource limit {name!r} must be a non-negative integer")
    for name, usage in run.resources.usage.items():
        if not isinstance(name, str) or not name:
            _fail("resource usage names must be non-empty strings")
        if not isinstance(usage, int) or isinstance(usage, bool) or usage < 0:
            _fail(f"resource usage {name!r} must be a non-negative integer")


def validate_run(run: ResearchRun) -> None:
    """Validate all persistent structural invariants for one aggregate."""

    if not isinstance(run, ResearchRun):
        _fail("run must be a ResearchRun")
    validate_ref(run.id, "run", "run.id")
    _positive_revision(run.state_revision, "run.state_revision")
    if not isinstance(run.lifecycle, LifecycleMode):
        _fail("run.lifecycle must be a LifecycleMode")
    if run.outcome is not None and not isinstance(run.outcome, RunOutcome):
        _fail("run.outcome must be a RunOutcome or None")
    if not isinstance(run.literature_landscape, LiteratureLandscape):
        _fail("run.literature_landscape must be a LiteratureLandscape")
    requirement_refs = _validate_contract(run)
    paper_refs = _validate_papers(run)
    approach_refs = _validate_landscape(run, paper_refs)
    gap_refs = _validate_gaps(run, requirement_refs, approach_refs)
    contract_revisions = {entry.revision for entry in run.contract.revisions}
    checks = _validate_checks(run, contract_revisions, gap_refs)
    _validate_delivery(run, contract_revisions, checks)
    _validate_resources(run)


def validate_transition(before: ResearchRun, after: ResearchRun) -> None:
    """Validate history/revision invariants for one atomic state transition."""

    validate_run(before)
    validate_run(after)
    if before.id != after.id:
        _fail("a transition cannot change run.id")
    if after.state_revision != before.state_revision + 1:
        _fail("a transition must increment state_revision by exactly one")

    previous_revisions = before.contract.revisions
    proposed_prefix = after.contract.revisions[: len(previous_revisions)]
    if proposed_prefix != previous_revisions:
        _fail("existing contract revision history is immutable")
    if len(after.contract.revisions) < len(previous_revisions):
        _fail("contract revision history is append-only")

    for check_ref, check in before.completion_checks.items():
        proposed_check = after.completion_checks.get(check_ref)
        if proposed_check is None:
            _fail(f"completion check {check_ref!r} cannot be deleted")
        if check.completed_at is not None:
            if proposed_check != check:
                _fail(f"completed completion check {check_ref!r} is immutable")
            continue

        request_metadata = (
            check.id,
            check.basis_revision,
            check.basis_contract_revision,
            check.requested_at,
            check.requester_rationale,
        )
        proposed_request_metadata = (
            proposed_check.id,
            proposed_check.basis_revision,
            proposed_check.basis_contract_revision,
            proposed_check.requested_at,
            proposed_check.requester_rationale,
        )
        if proposed_request_metadata != request_metadata:
            _fail(
                f"pending completion check {check_ref!r} request metadata is immutable"
            )
        if proposed_check.completed_at is None and proposed_check != check:
            _fail(
                f"pending completion check {check_ref!r} must remain unchanged "
                "until completion"
            )

    removed_gaps = set(before.investigation_gaps) - set(after.investigation_gaps)
    if removed_gaps:
        _fail(f"investigation gaps cannot be deleted: {sorted(removed_gaps)!r}")
