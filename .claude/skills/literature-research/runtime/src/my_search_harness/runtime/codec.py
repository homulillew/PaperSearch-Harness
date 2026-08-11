"""Explicit JSON codec for the frozen V1 ResearchRun model."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Any, TypeVar

from my_search_harness.domain.model import (
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
    PaperAnalysis,
    PaperResearchStatus,
    PaperSource,
    PartialAuthorizationBasis,
    ResearchContract,
    ResearchRequirement,
    ResearchRun,
    ResourceState,
    RunOutcome,
    SourceLocator,
    SourceRelation,
    VersionedResearchContract,
)
from my_search_harness.domain.validation import (
    DomainValidationError,
    validate_run,
)


JsonObject = dict[str, Any]
_E = TypeVar("_E", bound=StrEnum)
_T = TypeVar("_T")


def _datetime_to_json(value: datetime) -> str:
    return value.isoformat()


def _locator_to_dict(locator: SourceLocator) -> JsonObject:
    return {"kind": locator.kind, "value": locator.value}


def _paper_source_to_dict(source: PaperSource) -> JsonObject:
    return {
        "title": source.title,
        "authors": list(source.authors),
        "publication_year": source.publication_year,
        "publication_date": source.publication_date,
        "doi": source.doi,
        "arxiv_id": source.arxiv_id,
        "canonical_url": source.canonical_url,
        "other_identifiers": dict(sorted(source.other_identifiers.items())),
    }


def _paper_analysis_to_dict(analysis: PaperAnalysis) -> JsonObject:
    return {
        "summary": analysis.summary,
        "relevance_to_run": analysis.relevance_to_run,
        "contributions": list(analysis.contributions),
        "key_results": list(analysis.key_results),
        "limitations": list(analysis.limitations),
        "key_locators": [_locator_to_dict(value) for value in analysis.key_locators],
    }


def _paper_to_dict(paper: Paper) -> JsonObject:
    return {
        "id": paper.id,
        "source": _paper_source_to_dict(paper.source),
        "research_status": paper.research_status.value,
        "retirement_reason": paper.retirement_reason,
        "analysis": (
            None if paper.analysis is None else _paper_analysis_to_dict(paper.analysis)
        ),
    }


def _literature_source_to_dict(source: LiteratureSource) -> JsonObject:
    return {
        "paper_ref": source.paper_ref,
        "relation": source.relation.value,
        "locator": None if source.locator is None else _locator_to_dict(source.locator),
    }


def _literature_source_sort_key(source: LiteratureSource) -> tuple[str, str, str, str]:
    locator_kind = "" if source.locator is None else source.locator.kind
    locator_value = "" if source.locator is None else source.locator.value
    return source.paper_ref, source.relation.value, locator_kind, locator_value


def _approach_to_dict(approach: ApproachFamily) -> JsonObject:
    return {
        "id": approach.id,
        "name": approach.name,
        "core_idea": approach.core_idea,
        "representative_papers": sorted(approach.representative_papers),
    }


def _finding_to_dict(finding: LandscapeFinding) -> JsonObject:
    return {
        "id": finding.id,
        "statement": finding.statement,
        "approach_refs": sorted(finding.approach_refs),
        "sources": [
            _literature_source_to_dict(source)
            for source in sorted(finding.sources, key=_literature_source_sort_key)
        ],
    }


def _open_problem_to_dict(problem: OpenProblem) -> JsonObject:
    return {
        "id": problem.id,
        "statement": problem.statement,
        "approach_refs": sorted(problem.approach_refs),
        "sources": [
            _literature_source_to_dict(source)
            for source in sorted(problem.sources, key=_literature_source_sort_key)
        ],
    }


def _landscape_to_dict(landscape: LiteratureLandscape) -> JsonObject:
    return {
        "approach_families": {
            ref: _approach_to_dict(landscape.approach_families[ref])
            for ref in sorted(landscape.approach_families)
        },
        "findings": {
            ref: _finding_to_dict(landscape.findings[ref])
            for ref in sorted(landscape.findings)
        },
        "open_problems": {
            ref: _open_problem_to_dict(landscape.open_problems[ref])
            for ref in sorted(landscape.open_problems)
        },
    }


def _deliverable_to_dict(deliverable: Deliverable) -> JsonObject:
    return {
        "description": deliverable.description,
        "required_artifacts": sorted(
            artifact.value for artifact in deliverable.required_artifacts
        ),
    }


def _requirement_to_dict(requirement: ResearchRequirement) -> JsonObject:
    return {"id": requirement.id, "statement": requirement.statement}


def _research_contract_to_dict(contract: ResearchContract) -> JsonObject:
    return {
        "mission": contract.mission,
        "scope": contract.scope,
        "deliverable": _deliverable_to_dict(contract.deliverable),
        "requirements": {
            ref: _requirement_to_dict(contract.requirements[ref])
            for ref in sorted(contract.requirements)
        },
    }


def _contract_revision_to_dict(revision: ContractRevision) -> JsonObject:
    return {
        "revision": revision.revision,
        "contract": _research_contract_to_dict(revision.contract),
        "reason": revision.reason,
        "recorded_at": _datetime_to_json(revision.recorded_at),
    }


def _gap_to_dict(gap: InvestigationGap) -> JsonObject:
    return {
        "id": gap.id,
        "description": gap.description,
        "requirement_refs": sorted(gap.requirement_refs),
        "approach_refs": sorted(gap.approach_refs),
        "resolution": gap.resolution,
    }


def _check_to_dict(check: CompletionCheck) -> JsonObject:
    return {
        "id": check.id,
        "basis_revision": check.basis_revision,
        "basis_contract_revision": check.basis_contract_revision,
        "requester_rationale": check.requester_rationale,
        "requested_at": _datetime_to_json(check.requested_at),
        "verdict": None if check.verdict is None else check.verdict.value,
        "reasons": list(check.reasons),
        "blocking_gap_refs": sorted(check.blocking_gap_refs),
        "completed_at": (
            None
            if check.completed_at is None
            else _datetime_to_json(check.completed_at)
        ),
    }


def delivery_basis_to_dict(
    basis: CompletionPassBasis | PartialAuthorizationBasis | None,
) -> JsonObject | None:
    """Encode one DeliveryBasis using the frozen tagged representation."""

    if basis is None:
        return None
    if isinstance(basis, CompletionPassBasis):
        return {
            "type": "completion_pass",
            "completion_check_ref": basis.completion_check_ref,
        }
    if isinstance(basis, PartialAuthorizationBasis):
        return {
            "type": "partial_authorization",
            "basis_revision": basis.basis_revision,
            "basis_contract_revision": basis.basis_contract_revision,
            "authorized_at": _datetime_to_json(basis.authorized_at),
            "rationale": basis.rationale,
        }
    raise DomainValidationError("delivery_basis has an unknown variant")


def run_to_dict(run: ResearchRun) -> JsonObject:
    """Convert a valid ResearchRun into an explicit JSON-compatible object."""

    validate_run(run)
    return {
        "id": run.id,
        "state_revision": run.state_revision,
        "contract": {
            "current_revision": run.contract.current_revision,
            "revisions": [
                _contract_revision_to_dict(revision)
                for revision in run.contract.revisions
            ],
        },
        "lifecycle": run.lifecycle.value,
        "outcome": None if run.outcome is None else run.outcome.value,
        "resources": {
            "limits": dict(sorted(run.resources.limits.items())),
            "usage": dict(sorted(run.resources.usage.items())),
        },
        "papers": {ref: _paper_to_dict(run.papers[ref]) for ref in sorted(run.papers)},
        "literature_landscape": _landscape_to_dict(run.literature_landscape),
        "investigation_gaps": {
            ref: _gap_to_dict(run.investigation_gaps[ref])
            for ref in sorted(run.investigation_gaps)
        },
        "completion_checks": {
            ref: _check_to_dict(run.completion_checks[ref])
            for ref in sorted(run.completion_checks)
        },
        "delivery_basis": delivery_basis_to_dict(run.delivery_basis),
    }


def run_to_json(run: ResearchRun) -> str:
    """Serialize a valid ResearchRun to deterministic, human-readable JSON."""

    return json.dumps(run_to_dict(run), indent=2, sort_keys=True) + "\n"


def _object(value: object, path: str) -> JsonObject:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise DomainValidationError(f"{path} must be a JSON object")
    return value


def _fields(
    value: object,
    path: str,
    required: set[str],
    optional: set[str] | frozenset[str] = frozenset(),
) -> JsonObject:
    data = _object(value, path)
    missing = required - data.keys()
    unexpected = data.keys() - required - optional
    if missing:
        raise DomainValidationError(f"{path} is missing fields: {sorted(missing)!r}")
    if unexpected:
        raise DomainValidationError(
            f"{path} has unexpected fields: {sorted(unexpected)!r}"
        )
    return data


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise DomainValidationError(f"{path} must be a string")
    return value


def _optional_string(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _integer(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise DomainValidationError(f"{path} must be an integer")
    return value


def _optional_integer(value: object, path: str) -> int | None:
    if value is None:
        return None
    return _integer(value, path)


def _list(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise DomainValidationError(f"{path} must be a JSON array")
    return value


def _strings(value: object, path: str) -> tuple[str, ...]:
    return tuple(
        _string(item, f"{path}[{index}]")
        for index, item in enumerate(_list(value, path))
    )


def _string_set(value: object, path: str) -> set[str]:
    result = set(_strings(value, path))
    if len(result) != len(_list(value, path)):
        raise DomainValidationError(f"{path} must not contain duplicates")
    return result


def _string_map(value: object, path: str) -> dict[str, str]:
    return {
        key: _string(item, f"{path}.{key}")
        for key, item in _object(value, path).items()
    }


def _integer_map(value: object, path: str) -> dict[str, int]:
    return {
        key: _integer(item, f"{path}.{key}")
        for key, item in _object(value, path).items()
    }


def _enum(enum_type: type[_E], value: object, path: str) -> _E:
    raw = _string(value, path)
    try:
        return enum_type(raw)
    except ValueError as exc:
        raise DomainValidationError(f"{path} has unknown value {raw!r}") from exc


def _optional_enum(enum_type: type[_E], value: object, path: str) -> _E | None:
    if value is None:
        return None
    return _enum(enum_type, value, path)


def _datetime(value: object, path: str) -> datetime:
    raw = _string(value, path)
    try:
        result = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise DomainValidationError(f"{path} must be an ISO 8601 datetime") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise DomainValidationError(f"{path} must include a timezone offset")
    return result


def _optional_datetime(value: object, path: str) -> datetime | None:
    if value is None:
        return None
    return _datetime(value, path)


def _locator_from_dict(value: object, path: str) -> SourceLocator:
    data = _fields(value, path, {"kind", "value"})
    return SourceLocator(
        kind=_string(data["kind"], f"{path}.kind"),
        value=_string(data["value"], f"{path}.value"),
    )


def _paper_source_from_dict(value: object, path: str) -> PaperSource:
    data = _fields(
        value,
        path,
        {
            "title",
            "authors",
            "publication_year",
            "doi",
            "arxiv_id",
            "canonical_url",
            "other_identifiers",
        },
        {"publication_date"},
    )
    return PaperSource(
        title=_string(data["title"], f"{path}.title"),
        authors=_strings(data["authors"], f"{path}.authors"),
        publication_year=_optional_integer(
            data["publication_year"], f"{path}.publication_year"
        ),
        publication_date=_optional_string(
            data.get("publication_date"), f"{path}.publication_date"
        ),
        doi=_optional_string(data["doi"], f"{path}.doi"),
        arxiv_id=_optional_string(data["arxiv_id"], f"{path}.arxiv_id"),
        canonical_url=_optional_string(data["canonical_url"], f"{path}.canonical_url"),
        other_identifiers=_string_map(
            data["other_identifiers"], f"{path}.other_identifiers"
        ),
    )


def _paper_analysis_from_dict(value: object, path: str) -> PaperAnalysis:
    data = _fields(
        value,
        path,
        {
            "summary",
            "relevance_to_run",
            "contributions",
            "key_results",
            "limitations",
            "key_locators",
        },
    )
    return PaperAnalysis(
        summary=_string(data["summary"], f"{path}.summary"),
        relevance_to_run=_string(data["relevance_to_run"], f"{path}.relevance_to_run"),
        contributions=_strings(data["contributions"], f"{path}.contributions"),
        key_results=_strings(data["key_results"], f"{path}.key_results"),
        limitations=_strings(data["limitations"], f"{path}.limitations"),
        key_locators=tuple(
            _locator_from_dict(item, f"{path}.key_locators[{index}]")
            for index, item in enumerate(_list(data["key_locators"], path))
        ),
    )


def _paper_from_dict(value: object, path: str) -> Paper:
    data = _fields(
        value,
        path,
        {"id", "source", "research_status", "analysis"},
        {"retirement_reason"},
    )
    analysis = data["analysis"]
    return Paper(
        id=_string(data["id"], f"{path}.id"),
        source=_paper_source_from_dict(data["source"], f"{path}.source"),
        research_status=_enum(
            PaperResearchStatus, data["research_status"], f"{path}.research_status"
        ),
        retirement_reason=_optional_string(
            data.get("retirement_reason"), f"{path}.retirement_reason"
        ),
        analysis=(
            None
            if analysis is None
            else _paper_analysis_from_dict(analysis, f"{path}.analysis")
        ),
    )


def _literature_source_from_dict(value: object, path: str) -> LiteratureSource:
    data = _fields(value, path, {"paper_ref", "relation", "locator"})
    locator = data["locator"]
    return LiteratureSource(
        paper_ref=_string(data["paper_ref"], f"{path}.paper_ref"),
        relation=_enum(SourceRelation, data["relation"], f"{path}.relation"),
        locator=(
            None if locator is None else _locator_from_dict(locator, f"{path}.locator")
        ),
    )


def _entity_map(
    value: object,
    path: str,
    decoder: Callable[[object, str], _T],
) -> dict[str, _T]:
    data = _object(value, path)
    return {key: decoder(item, f"{path}.{key}") for key, item in data.items()}


def _approach_from_dict(value: object, path: str) -> ApproachFamily:
    data = _fields(value, path, {"id", "name", "core_idea", "representative_papers"})
    return ApproachFamily(
        id=_string(data["id"], f"{path}.id"),
        name=_string(data["name"], f"{path}.name"),
        core_idea=_string(data["core_idea"], f"{path}.core_idea"),
        representative_papers=_string_set(
            data["representative_papers"], f"{path}.representative_papers"
        ),
    )


def _finding_from_dict(value: object, path: str) -> LandscapeFinding:
    data = _fields(value, path, {"id", "statement", "approach_refs", "sources"})
    return LandscapeFinding(
        id=_string(data["id"], f"{path}.id"),
        statement=_string(data["statement"], f"{path}.statement"),
        approach_refs=_string_set(data["approach_refs"], f"{path}.approach_refs"),
        sources={
            _literature_source_from_dict(item, f"{path}.sources[{index}]")
            for index, item in enumerate(_list(data["sources"], f"{path}.sources"))
        },
    )


def _open_problem_from_dict(value: object, path: str) -> OpenProblem:
    data = _fields(value, path, {"id", "statement", "approach_refs", "sources"})
    return OpenProblem(
        id=_string(data["id"], f"{path}.id"),
        statement=_string(data["statement"], f"{path}.statement"),
        approach_refs=_string_set(data["approach_refs"], f"{path}.approach_refs"),
        sources={
            _literature_source_from_dict(item, f"{path}.sources[{index}]")
            for index, item in enumerate(_list(data["sources"], f"{path}.sources"))
        },
    )


def _landscape_from_dict(value: object, path: str) -> LiteratureLandscape:
    data = _fields(value, path, {"approach_families", "findings", "open_problems"})
    return LiteratureLandscape(
        approach_families=_entity_map(
            data["approach_families"],
            f"{path}.approach_families",
            _approach_from_dict,
        ),
        findings=_entity_map(data["findings"], f"{path}.findings", _finding_from_dict),
        open_problems=_entity_map(
            data["open_problems"], f"{path}.open_problems", _open_problem_from_dict
        ),
    )


def _deliverable_from_dict(value: object, path: str) -> Deliverable:
    data = _fields(value, path, {"description", "required_artifacts"})
    return Deliverable(
        description=_string(data["description"], f"{path}.description"),
        required_artifacts={
            _enum(ArtifactKind, item, f"{path}.required_artifacts[{index}]")
            for index, item in enumerate(
                _list(data["required_artifacts"], f"{path}.required_artifacts")
            )
        },
    )


def _requirement_from_dict(value: object, path: str) -> ResearchRequirement:
    data = _fields(value, path, {"id", "statement"})
    return ResearchRequirement(
        id=_string(data["id"], f"{path}.id"),
        statement=_string(data["statement"], f"{path}.statement"),
    )


def _research_contract_from_dict(value: object, path: str) -> ResearchContract:
    data = _fields(value, path, {"mission", "scope", "deliverable", "requirements"})
    return ResearchContract(
        mission=_string(data["mission"], f"{path}.mission"),
        scope=_string(data["scope"], f"{path}.scope"),
        deliverable=_deliverable_from_dict(data["deliverable"], f"{path}.deliverable"),
        requirements=_entity_map(
            data["requirements"], f"{path}.requirements", _requirement_from_dict
        ),
    )


def _contract_revision_from_dict(value: object, path: str) -> ContractRevision:
    data = _fields(value, path, {"revision", "contract", "reason", "recorded_at"})
    return ContractRevision(
        revision=_integer(data["revision"], f"{path}.revision"),
        contract=_research_contract_from_dict(data["contract"], f"{path}.contract"),
        reason=_string(data["reason"], f"{path}.reason"),
        recorded_at=_datetime(data["recorded_at"], f"{path}.recorded_at"),
    )


def _gap_from_dict(value: object, path: str) -> InvestigationGap:
    data = _fields(
        value,
        path,
        {"id", "description", "requirement_refs", "approach_refs", "resolution"},
    )
    return InvestigationGap(
        id=_string(data["id"], f"{path}.id"),
        description=_string(data["description"], f"{path}.description"),
        requirement_refs=_string_set(
            data["requirement_refs"], f"{path}.requirement_refs"
        ),
        approach_refs=_string_set(data["approach_refs"], f"{path}.approach_refs"),
        resolution=_optional_string(data["resolution"], f"{path}.resolution"),
    )


def _check_from_dict(value: object, path: str) -> CompletionCheck:
    data = _fields(
        value,
        path,
        {
            "id",
            "basis_revision",
            "basis_contract_revision",
            "requester_rationale",
            "requested_at",
            "verdict",
            "reasons",
            "blocking_gap_refs",
            "completed_at",
        },
    )
    return CompletionCheck(
        id=_string(data["id"], f"{path}.id"),
        basis_revision=_integer(data["basis_revision"], f"{path}.basis_revision"),
        basis_contract_revision=_integer(
            data["basis_contract_revision"], f"{path}.basis_contract_revision"
        ),
        requester_rationale=_string(
            data["requester_rationale"], f"{path}.requester_rationale"
        ),
        requested_at=_datetime(data["requested_at"], f"{path}.requested_at"),
        verdict=_optional_enum(CompletionVerdict, data["verdict"], f"{path}.verdict"),
        reasons=_strings(data["reasons"], f"{path}.reasons"),
        blocking_gap_refs=_string_set(
            data["blocking_gap_refs"], f"{path}.blocking_gap_refs"
        ),
        completed_at=_optional_datetime(data["completed_at"], f"{path}.completed_at"),
    )


def delivery_basis_from_dict(
    value: object, path: str = "delivery_basis"
) -> CompletionPassBasis | PartialAuthorizationBasis | None:
    """Decode one DeliveryBasis from the frozen tagged representation."""

    if value is None:
        return None
    data = _object(value, path)
    basis_type = _string(data.get("type"), f"{path}.type")
    if basis_type == "completion_pass":
        checked = _fields(data, path, {"type", "completion_check_ref"})
        return CompletionPassBasis(
            completion_check_ref=_string(
                checked["completion_check_ref"], f"{path}.completion_check_ref"
            )
        )
    if basis_type == "partial_authorization":
        checked = _fields(
            data,
            path,
            {
                "type",
                "basis_revision",
                "basis_contract_revision",
                "authorized_at",
                "rationale",
            },
        )
        return PartialAuthorizationBasis(
            basis_revision=_integer(
                checked["basis_revision"], f"{path}.basis_revision"
            ),
            basis_contract_revision=_integer(
                checked["basis_contract_revision"],
                f"{path}.basis_contract_revision",
            ),
            authorized_at=_datetime(checked["authorized_at"], f"{path}.authorized_at"),
            rationale=_optional_string(checked["rationale"], f"{path}.rationale"),
        )
    raise DomainValidationError(f"{path}.type has unknown value {basis_type!r}")


def _run_from_dict(value: object) -> ResearchRun:
    data = _fields(
        value,
        "run",
        {
            "id",
            "state_revision",
            "contract",
            "lifecycle",
            "outcome",
            "resources",
            "papers",
            "literature_landscape",
            "investigation_gaps",
            "completion_checks",
            "delivery_basis",
        },
    )
    contract_data = _fields(
        data["contract"], "run.contract", {"current_revision", "revisions"}
    )
    resources_data = _fields(data["resources"], "run.resources", {"limits", "usage"})
    return ResearchRun(
        id=_string(data["id"], "run.id"),
        state_revision=_integer(data["state_revision"], "run.state_revision"),
        contract=VersionedResearchContract(
            current_revision=_integer(
                contract_data["current_revision"], "run.contract.current_revision"
            ),
            revisions=[
                _contract_revision_from_dict(item, f"run.contract.revisions[{index}]")
                for index, item in enumerate(
                    _list(contract_data["revisions"], "run.contract.revisions")
                )
            ],
        ),
        lifecycle=_enum(LifecycleMode, data["lifecycle"], "run.lifecycle"),
        outcome=_optional_enum(RunOutcome, data["outcome"], "run.outcome"),
        resources=ResourceState(
            limits=_integer_map(resources_data["limits"], "run.resources.limits"),
            usage=_integer_map(resources_data["usage"], "run.resources.usage"),
        ),
        papers=_entity_map(data["papers"], "run.papers", _paper_from_dict),
        literature_landscape=_landscape_from_dict(
            data["literature_landscape"], "run.literature_landscape"
        ),
        investigation_gaps=_entity_map(
            data["investigation_gaps"], "run.investigation_gaps", _gap_from_dict
        ),
        completion_checks=_entity_map(
            data["completion_checks"], "run.completion_checks", _check_from_dict
        ),
        delivery_basis=delivery_basis_from_dict(
            data["delivery_basis"], "run.delivery_basis"
        ),
    )


def run_from_dict(value: object) -> ResearchRun:
    """Decode and validate an explicit JSON-compatible ResearchRun object."""

    try:
        run = _run_from_dict(value)
        validate_run(run)
        return run
    except DomainValidationError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise DomainValidationError("invalid serialized ResearchRun") from exc


def run_from_json(payload: str) -> ResearchRun:
    """Decode and validate ResearchRun JSON."""

    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as exc:
        raise DomainValidationError("invalid ResearchRun JSON") from exc
    return run_from_dict(value)
