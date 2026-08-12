"""Typed command façade for the V1 core research loop."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from typing import TypeAlias

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
    VersionedResearchContract,
    utc_now,
)
from my_search_harness.domain.paper_identity import (
    PaperIdentityKey,
    normalize_arxiv_id,
    normalize_doi,
    paper_identity_keys,
)

from .paper_search import PaperSearchHit
from .persistence import JsonResearchRunRepository, RevisionConflictError
from .audit import AuditEvent, AuditScalar, AuditSink, append_audit


class CommandRejectedError(RuntimeError):
    """A command is invalid for the current authoritative state."""


class CompletionSubmissionConflictError(RuntimeError):
    """A retry conflicts with an already completed CompletionCheck."""


@dataclass(slots=True, frozen=True, kw_only=True)
class CreateRunRequest:
    mission: str
    requirements: tuple[str, ...]
    scope: str
    deliverable_description: str
    required_artifacts: frozenset[ArtifactKind] = field(default_factory=frozenset)


@dataclass(slots=True, frozen=True, kw_only=True)
class CreateRunResult:
    run_id: str
    state_revision: int
    requirement_refs: tuple[str, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class RetainPapersResult:
    state_revision: int
    paper_refs: tuple[str, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class PutPaperAnalysis:
    paper_ref: str
    analysis: PaperAnalysis


@dataclass(slots=True, frozen=True, kw_only=True)
class PutLandscapeFinding:
    statement: str
    approach_refs: frozenset[str] = field(default_factory=frozenset)
    sources: frozenset[LiteratureSource] = field(default_factory=frozenset)


ResearchPut: TypeAlias = PutPaperAnalysis | PutLandscapeFinding


@dataclass(slots=True, frozen=True, kw_only=True)
class ResearchMutationBatch:
    puts: tuple[ResearchPut, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class ResearchMutationResult:
    state_revision: int
    finding_refs: tuple[str, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class RequestCompletionCheckResult:
    state_revision: int
    completion_check_ref: str


@dataclass(slots=True, frozen=True, kw_only=True)
class NewBlockingGap:
    description: str
    requirement_refs: frozenset[str] = field(default_factory=frozenset)
    approach_refs: frozenset[str] = field(default_factory=frozenset)


@dataclass(slots=True, frozen=True, kw_only=True)
class ReopenBlockingGap:
    gap_ref: str


BlockingGapSpec: TypeAlias = NewBlockingGap | ReopenBlockingGap


@dataclass(slots=True, frozen=True, kw_only=True)
class CompletionSubmissionResult:
    state_revision: int
    completion_check_ref: str
    verdict: CompletionVerdict
    reasons: tuple[str, ...]
    blocking_gap_refs: frozenset[str]


@dataclass(slots=True, frozen=True, kw_only=True)
class DomainMutationResult:
    """Result shared by one-revision semantic domain commands."""

    state_revision: int


@dataclass(slots=True, frozen=True, kw_only=True)
class PaperReconciliationResult(DomainMutationResult):
    paper_ref: str
    removed_paper_ref: str | None


@dataclass(slots=True, frozen=True, kw_only=True)
class EntityMutationResult(DomainMutationResult):
    entity_ref: str


class ResearchCommands:
    """Thin command boundary over the authoritative ResearchRun repository."""

    def __init__(
        self,
        repository: JsonResearchRunRepository,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._repository = repository
        self._audit_sink = audit_sink

    def create_run(self, request: CreateRunRequest) -> CreateRunResult:
        self._validate_create_request(request)
        requirements = tuple(
            ResearchRequirement(statement=statement)
            for statement in request.requirements
        )
        contract = ResearchContract(
            mission=request.mission,
            requirements={requirement.id: requirement for requirement in requirements},
            scope=request.scope,
            deliverable=Deliverable(
                description=request.deliverable_description,
                required_artifacts=set(request.required_artifacts),
            ),
        )
        run = ResearchRun(
            contract=VersionedResearchContract(
                current_revision=1,
                revisions=[
                    ContractRevision(
                        revision=1,
                        contract=contract,
                        reason="Initial contract",
                    )
                ],
            )
        )
        self._repository.create(run)
        self._append_audit(
            run,
            action="run_created",
            actor="researcher",
            details={"requirement_count": len(requirements)},
        )
        return CreateRunResult(
            run_id=run.id,
            state_revision=run.state_revision,
            requirement_refs=tuple(requirement.id for requirement in requirements),
        )

    def retain_papers(
        self,
        run_id: str,
        expected_revision: int,
        hits: tuple[PaperSearchHit, ...],
    ) -> RetainPapersResult:
        current = self._load_expected(run_id, expected_revision)
        self._require_lifecycle(current, LifecycleMode.RESEARCH, "retain_papers")
        if not isinstance(hits, tuple) or not hits:
            raise CommandRejectedError("retain_papers requires at least one search hit")

        proposed = deepcopy(current)
        identity_index = self._paper_identity_index(proposed)
        paper_refs: list[str] = []

        for hit in hits:
            source = self._source_from_hit(hit)
            incoming_keys = paper_identity_keys(source)
            self._require_nonempty_identity_values(incoming_keys)
            matching_refs = {
                identity_index[key] for key in incoming_keys if key in identity_index
            }
            if len(matching_refs) > 1:
                raise CommandRejectedError(
                    "incoming paper identities resolve to multiple persistent papers"
                )

            if matching_refs:
                paper_ref = next(iter(matching_refs))
                paper = proposed.papers[paper_ref]
                self._enrich_paper_identity(paper, source)
            else:
                paper = Paper(source=source)
                proposed.papers[paper.id] = paper
                paper_ref = paper.id

            for key in paper_identity_keys(paper.source):
                existing_ref = identity_index.get(key)
                if existing_ref is not None and existing_ref != paper_ref:
                    raise CommandRejectedError(
                        "paper identity enrichment conflicts with another paper"
                    )
                identity_index[key] = paper_ref
            paper_refs.append(paper_ref)

        state_revision = self._commit(
            current,
            proposed,
            expected_revision,
            action="papers_retained",
            details={
                "paper_count": len(paper_refs),
                "paper_refs": ",".join(paper_refs),
            },
        )
        return RetainPapersResult(
            state_revision=state_revision,
            paper_refs=tuple(paper_refs),
        )

    def apply_research_mutation(
        self,
        run_id: str,
        expected_revision: int,
        batch: ResearchMutationBatch,
    ) -> ResearchMutationResult:
        current = self._load_expected(run_id, expected_revision)
        self._require_lifecycle(
            current, LifecycleMode.RESEARCH, "apply_research_mutation"
        )
        if not isinstance(batch, ResearchMutationBatch) or not batch.puts:
            raise CommandRejectedError("research mutation batch must not be empty")

        proposed = deepcopy(current)
        finding_refs: list[str] = []
        for mutation in batch.puts:
            if isinstance(mutation, PutPaperAnalysis):
                paper = proposed.papers.get(mutation.paper_ref)
                if paper is None:
                    raise CommandRejectedError(
                        f"paper {mutation.paper_ref!r} does not exist"
                    )
                if not isinstance(mutation.analysis, PaperAnalysis):
                    raise CommandRejectedError("analysis must be a PaperAnalysis")
                # Deep reading produces a PaperAnalysis only for an ACTIVE
                # paper — a RETIRED paper is a closed candidate and must be
                # reactivated explicitly with set-paper-status before its
                # analysis can be updated. A historical RETIRED paper that
                # already carries an analysis (from before retirement) is left
                # untouched; this gate only blocks *new* analysis writes.
                if paper.research_status is not PaperResearchStatus.ACTIVE:
                    raise CommandRejectedError(
                        f"cannot put PaperAnalysis for paper {mutation.paper_ref!r} "
                        f"in {paper.research_status.value} state; reactivate the "
                        f"paper explicitly before updating PaperAnalysis"
                    )
                paper.analysis = deepcopy(mutation.analysis)
                continue

            if isinstance(mutation, PutLandscapeFinding):
                self._validate_finding_refs(proposed, mutation)
                finding = LandscapeFinding(
                    statement=mutation.statement,
                    approach_refs=set(mutation.approach_refs),
                    sources=set(mutation.sources),
                )
                proposed.literature_landscape.findings[finding.id] = finding
                finding_refs.append(finding.id)
                continue

            raise CommandRejectedError("unsupported research mutation type")

        state_revision = self._commit(
            current,
            proposed,
            expected_revision,
            action="research_mutation",
            details={"mutation_count": len(batch.puts)},
        )
        return ResearchMutationResult(
            state_revision=state_revision,
            finding_refs=tuple(finding_refs),
        )

    def amend_contract(
        self,
        run_id: str,
        expected_revision: int,
        contract: ResearchContract,
        reason: str,
    ) -> DomainMutationResult:
        """Append a Contract revision and invalidate any delivery authority."""

        current = self._load_expected(run_id, expected_revision)
        if current.lifecycle not in {LifecycleMode.RESEARCH, LifecycleMode.DELIVERY}:
            raise CommandRejectedError(
                "amend_contract requires RESEARCH or DELIVERY lifecycle"
            )
        if not isinstance(contract, ResearchContract):
            raise CommandRejectedError("contract must be a ResearchContract")
        if not isinstance(reason, str) or not reason:
            raise CommandRejectedError("amendment reason must be a non-empty string")
        current_contract = self._current_contract(current)
        if contract == current_contract:
            raise CommandRejectedError("contract amendment must change the contract")

        proposed = deepcopy(current)
        next_contract_revision = proposed.contract.current_revision + 1
        proposed.contract.revisions.append(
            ContractRevision(
                revision=next_contract_revision,
                contract=deepcopy(contract),
                reason=reason,
            )
        )
        proposed.contract.current_revision = next_contract_revision
        active_requirements = set(contract.requirements)
        for gap in proposed.investigation_gaps.values():
            gap.requirement_refs.intersection_update(active_requirements)
        if proposed.lifecycle is LifecycleMode.DELIVERY:
            proposed.lifecycle = LifecycleMode.RESEARCH
            proposed.delivery_basis = None
        return DomainMutationResult(
            state_revision=self._commit(
                current,
                proposed,
                expected_revision,
                action="contract_amended",
                actor="authority",
                reason=reason,
                details={"contract_revision": next_contract_revision},
            )
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
    ) -> PaperReconciliationResult:
        """Enrich or explicitly merge Papers using caller-supplied semantics.

        Identity reconciliation only — this command never changes a Paper's
        research_status or retirement_reason. Candidate disposition (retire /
        reactivate) belongs to ``set_paper_research_status`` so the Deep Reading
        invariants (RETIRED requires reason, referenced paper cannot be retired,
        landscape evidence must be ACTIVE+analyzed) cannot be bypassed here.
        """

        current = self._load_expected(run_id, expected_revision)
        self._require_lifecycle(
            current, LifecycleMode.RESEARCH, "reconcile_paper_identity"
        )
        if not isinstance(source, PaperSource):
            raise CommandRejectedError("source must be a PaperSource")
        if reconciled_analysis is not None and not isinstance(
            reconciled_analysis, PaperAnalysis
        ):
            raise CommandRejectedError(
                "reconciled_analysis must be a PaperAnalysis or None"
            )

        proposed = deepcopy(current)
        primary = proposed.papers.get(primary_paper_ref)
        if primary is None:
            raise CommandRejectedError(f"paper {primary_paper_ref!r} does not exist")
        duplicate = None
        if duplicate_paper_ref is not None:
            if duplicate_paper_ref == primary_paper_ref:
                raise CommandRejectedError("duplicate paper must differ from primary")
            duplicate = proposed.papers.get(duplicate_paper_ref)
            if duplicate is None:
                raise CommandRejectedError(
                    f"paper {duplicate_paper_ref!r} does not exist"
                )
            # Identity reconciliation must not silently resolve a candidate
            # disposition conflict. If the primary and duplicate carry different
            # research_status values, merging would either drop an unresolved
            # ACTIVE candidate into a RETIRED primary or bury a RETIRED
            # disposition under an ACTIVE primary. The caller must align the two
            # statuses explicitly with set-paper-status first; reconcile only
            # reconciles identity, not disposition.
            if primary.research_status is not duplicate.research_status:
                raise CommandRejectedError(
                    "identity reconciliation cannot resolve conflicting candidate "
                    "dispositions; align paper statuses explicitly with "
                    "set-paper-status before merging"
                )

        final_keys = set(paper_identity_keys(source))
        self._require_nonempty_identity_values(tuple(final_keys))
        if not final_keys:
            raise CommandRejectedError(
                "paper reconciliation requires at least one stable identity"
            )
        for existing in (primary, duplicate):
            if existing is None:
                continue
            missing_keys = set(paper_identity_keys(existing.source)) - final_keys
            if missing_keys:
                raise CommandRejectedError(
                    "paper reconciliation cannot discard stable identities"
                )
        for other_ref, other in proposed.papers.items():
            if other_ref in {primary_paper_ref, duplicate_paper_ref}:
                continue
            if final_keys.intersection(paper_identity_keys(other.source)):
                raise CommandRejectedError(
                    "reconciled identity conflicts with another persistent paper"
                )

        if duplicate is not None:
            analyses = tuple(
                analysis
                for analysis in (primary.analysis, duplicate.analysis)
                if analysis is not None
            )
            if (
                len(analyses) == 2
                and analyses[0] != analyses[1]
                and reconciled_analysis is None
            ):
                raise CommandRejectedError(
                    "conflicting PaperAnalysis values require reconciled_analysis"
                )
            if reconciled_analysis is None and analyses:
                reconciled_analysis = deepcopy(analyses[0])

        reconciled_source = deepcopy(source)
        for existing in (primary, duplicate):
            if existing is None:
                continue
            for kind, value in existing.source.other_identifiers.items():
                reconciled_source.other_identifiers.setdefault(kind, value)
        primary.source = reconciled_source
        if reconciled_analysis is not None:
            primary.analysis = deepcopy(reconciled_analysis)

        if duplicate is not None:
            assert duplicate_paper_ref is not None
            self._rewrite_paper_refs(
                proposed,
                duplicate_paper_ref,
                primary_paper_ref,
            )
            del proposed.papers[duplicate_paper_ref]

        # After a merge, rewritten Landscape refs may now point at the primary
        # paper. If the primary is not ACTIVE+analyzed, those refs would violate
        # landscape evidence eligibility — reject rather than silently leave the
        # Landscape citing an ineligible paper. This is identity reconciliation,
        # not disposition: the caller must set the paper's status separately.
        referencing = self._paper_referenced_by(proposed, primary_paper_ref)
        if referencing and (
            primary.research_status is not PaperResearchStatus.ACTIVE
            or primary.analysis is None
        ):
            raise CommandRejectedError(
                f"reconciled paper {primary_paper_ref!r} is referenced by "
                f"semantic objects {list(referencing)} but is not ACTIVE with a "
                f"PaperAnalysis; set its status separately before merging"
            )

        if proposed == current:
            raise CommandRejectedError("paper reconciliation must change state")
        state_revision = self._commit(
            current,
            proposed,
            expected_revision,
            action="paper_identity_reconciled",
            details={
                "paper_ref": primary_paper_ref,
                "removed_paper_ref": duplicate_paper_ref,
            },
        )
        return PaperReconciliationResult(
            state_revision=state_revision,
            paper_ref=primary_paper_ref,
            removed_paper_ref=duplicate_paper_ref,
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
        current = self._load_research(run_id, expected_revision, "put_approach_family")
        if not isinstance(name, str) or not isinstance(core_idea, str):
            raise CommandRejectedError("approach name and core_idea must be strings")
        self._validate_reference_frozenset(
            representative_paper_refs, "representative_paper_refs"
        )
        if not representative_paper_refs:
            raise CommandRejectedError(
                "approach family requires at least one representative paper"
            )
        missing = set(representative_paper_refs) - set(current.papers)
        if missing:
            raise CommandRejectedError(
                f"approach family has dangling paper refs: {sorted(missing)!r}"
            )
        # Landscape evidence must rest on ACTIVE, analyzed papers: a representative
        # chosen only by search metadata would let discovery substitute for
        # primary-source understanding (the failure mode exposed by bad_case).
        ineligible = {
            ref
            for ref in representative_paper_refs
            if current.papers[ref].research_status
            is not PaperResearchStatus.ACTIVE
            or current.papers[ref].analysis is None
        }
        if ineligible:
            raise CommandRejectedError(
                f"representative papers must be ACTIVE and have a PaperAnalysis; "
                f"ineligible: {sorted(ineligible)!r}"
            )

        proposed = deepcopy(current)
        if approach_ref is None:
            approach = ApproachFamily(
                name=name,
                core_idea=core_idea,
                representative_papers=set(representative_paper_refs),
            )
            proposed.literature_landscape.approach_families[approach.id] = approach
        else:
            existing_approach = proposed.literature_landscape.approach_families.get(
                approach_ref
            )
            if existing_approach is None:
                raise CommandRejectedError(
                    f"approach family {approach_ref!r} does not exist"
                )
            existing_approach.name = name
            existing_approach.core_idea = core_idea
            existing_approach.representative_papers = set(representative_paper_refs)
            approach = existing_approach
        self._reject_no_change(current, proposed, "approach family mutation")
        return EntityMutationResult(
            state_revision=self._commit(
                current,
                proposed,
                expected_revision,
                action="approach_family_maintained",
                details={"approach_ref": approach.id},
            ),
            entity_ref=approach.id,
        )

    def merge_approach_family(
        self,
        run_id: str,
        expected_revision: int,
        target_approach_ref: str,
        source_approach_ref: str,
    ) -> EntityMutationResult:
        current = self._load_research(
            run_id, expected_revision, "merge_approach_family"
        )
        if target_approach_ref == source_approach_ref:
            raise CommandRejectedError("source and target approach must differ")
        proposed = deepcopy(current)
        approaches = proposed.literature_landscape.approach_families
        target = approaches.get(target_approach_ref)
        source = approaches.get(source_approach_ref)
        if target is None or source is None:
            raise CommandRejectedError("source and target approach must both exist")
        target.representative_papers.update(source.representative_papers)
        for finding in proposed.literature_landscape.findings.values():
            if source_approach_ref in finding.approach_refs:
                finding.approach_refs.remove(source_approach_ref)
                finding.approach_refs.add(target_approach_ref)
        for problem in proposed.literature_landscape.open_problems.values():
            if source_approach_ref in problem.approach_refs:
                problem.approach_refs.remove(source_approach_ref)
                problem.approach_refs.add(target_approach_ref)
        for gap in proposed.investigation_gaps.values():
            if source_approach_ref in gap.approach_refs:
                gap.approach_refs.remove(source_approach_ref)
                gap.approach_refs.add(target_approach_ref)
        del approaches[source_approach_ref]
        return EntityMutationResult(
            state_revision=self._commit(
                current,
                proposed,
                expected_revision,
                action="approach_family_merged",
                details={
                    "target_approach_ref": target_approach_ref,
                    "source_approach_ref": source_approach_ref,
                },
            ),
            entity_ref=target_approach_ref,
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
        return self._put_landscape_item(
            run_id,
            expected_revision,
            item_kind="finding",
            statement=statement,
            approach_refs=approach_refs,
            sources=sources,
            item_ref=finding_ref,
        )

    def retire_landscape_finding(
        self,
        run_id: str,
        expected_revision: int,
        finding_ref: str,
    ) -> DomainMutationResult:
        return self._retire_landscape_item(
            run_id, expected_revision, "finding", finding_ref
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
        return self._put_landscape_item(
            run_id,
            expected_revision,
            item_kind="problem",
            statement=statement,
            approach_refs=approach_refs,
            sources=sources,
            item_ref=problem_ref,
        )

    def retire_open_problem(
        self,
        run_id: str,
        expected_revision: int,
        problem_ref: str,
    ) -> DomainMutationResult:
        return self._retire_landscape_item(
            run_id, expected_revision, "problem", problem_ref
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
        current = self._load_research(
            run_id, expected_revision, "put_investigation_gap"
        )
        self._validate_gap_metadata(
            current, description, requirement_refs, approach_refs
        )
        proposed = deepcopy(current)
        if gap_ref is None:
            gap = InvestigationGap(
                description=description,
                requirement_refs=set(requirement_refs),
                approach_refs=set(approach_refs),
            )
            proposed.investigation_gaps[gap.id] = gap
        else:
            existing_gap = proposed.investigation_gaps.get(gap_ref)
            if existing_gap is None:
                raise CommandRejectedError(f"gap {gap_ref!r} does not exist")
            existing_gap.description = description
            existing_gap.requirement_refs = set(requirement_refs)
            existing_gap.approach_refs = set(approach_refs)
            gap = existing_gap
        self._reject_no_change(current, proposed, "gap mutation")
        return EntityMutationResult(
            state_revision=self._commit(
                current,
                proposed,
                expected_revision,
                action="investigation_gap_maintained",
                details={"gap_ref": gap.id},
            ),
            entity_ref=gap.id,
        )

    def resolve_investigation_gap(
        self,
        run_id: str,
        expected_revision: int,
        gap_ref: str,
        resolution: str,
    ) -> DomainMutationResult:
        if not isinstance(resolution, str) or not resolution:
            raise CommandRejectedError("resolution must be a non-empty string")
        return self._set_gap_resolution(run_id, expected_revision, gap_ref, resolution)

    def reopen_investigation_gap(
        self,
        run_id: str,
        expected_revision: int,
        gap_ref: str,
    ) -> DomainMutationResult:
        return self._set_gap_resolution(run_id, expected_revision, gap_ref, None)

    def set_paper_research_status(
        self,
        run_id: str,
        expected_revision: int,
        paper_ref: str,
        status: PaperResearchStatus,
        *,
        retirement_reason: str | None = None,
    ) -> DomainMutationResult:
        current = self._load_research(
            run_id, expected_revision, "set_paper_research_status"
        )
        if not isinstance(status, PaperResearchStatus):
            raise CommandRejectedError("status must be a PaperResearchStatus")
        # A RETIRED paper carries a durable, semantic retirement_reason so a fresh
        # Completion Checker and a resumed session can see candidate closure. An
        # ACTIVE paper holds no retirement_reason (persist decisions, not
        # trajectories: the audit log already records the history).
        if status is PaperResearchStatus.RETIRED:
            if not isinstance(retirement_reason, str) or not retirement_reason.strip():
                raise CommandRejectedError(
                    "retiring a paper to RETIRED requires a non-empty "
                    "retirement_reason"
                )
        else:
            retirement_reason = None
        # A paper still cited by the current Landscape cannot be retired: the
        # Researcher must first update or retire the referencing ApproachFamily /
        # Finding / OpenProblem. Auto-removing those references would be a
        # semantic decision the Harness must not make.
        if status is PaperResearchStatus.RETIRED:
            referencing = self._paper_referenced_by(current, paper_ref)
            if referencing:
                raise CommandRejectedError(
                    f"paper {paper_ref!r} is still referenced by semantic "
                    f"objects {list(referencing)}; update or retire those "
                    f"before retiring the paper"
                )
        proposed = deepcopy(current)
        paper = proposed.papers.get(paper_ref)
        if paper is None:
            raise CommandRejectedError(f"paper {paper_ref!r} does not exist")
        paper.research_status = status
        paper.retirement_reason = retirement_reason
        self._reject_no_change(current, proposed, "paper status mutation")
        return DomainMutationResult(
            state_revision=self._commit(
                current,
                proposed,
                expected_revision,
                action="paper_status_changed",
                reason=retirement_reason,
                details={"paper_ref": paper_ref, "status": status.value},
            )
        )

    def authorize_partial_delivery(
        self,
        run_id: str,
        expected_revision: int,
        rationale: str | None,
    ) -> DomainMutationResult:
        """Record explicit authority to deliver a known-incomplete result."""

        current = self._load_research(
            run_id, expected_revision, "authorize_partial_delivery"
        )
        if rationale is not None and not isinstance(rationale, str):
            raise CommandRejectedError("rationale must be a string or None")
        proposed = deepcopy(current)
        resulting_revision = current.state_revision + 1
        proposed.lifecycle = LifecycleMode.DELIVERY
        proposed.delivery_basis = PartialAuthorizationBasis(
            basis_revision=resulting_revision,
            basis_contract_revision=proposed.contract.current_revision,
            authorized_at=utc_now(),
            rationale=rationale,
        )
        return DomainMutationResult(
            state_revision=self._commit(
                current,
                proposed,
                expected_revision,
                action="partial_delivery_authorized",
                actor="authority",
                reason=rationale,
            )
        )

    def request_completion_check(
        self,
        run_id: str,
        expected_revision: int,
        requester_rationale: str,
    ) -> RequestCompletionCheckResult:
        current = self._load_expected(run_id, expected_revision)
        self._require_lifecycle(
            current, LifecycleMode.RESEARCH, "request_completion_check"
        )
        if not isinstance(requester_rationale, str):
            raise CommandRejectedError("requester_rationale must be a string")
        # Completion hard gate: every retained paper must be closed before an
        # independent checker is asked to judge the corpus. Two closure rules:
        #   ACTIVE  -> must have a PaperAnalysis (unresolved otherwise)
        #   RETIRED -> must carry a non-empty retirement_reason
        # The second rule is the legacy-state boundary: an old snapshot with
        # RETIRED + reason=None still loads (validate_run stays weak so historical
        # runs remain readable), but a NEW request-completion transition must
        # satisfy the current closure invariant. This is deterministic state
        # consistency, not a paper count or score.
        unanalyzed_active = [
            ref
            for ref, paper in current.papers.items()
            if paper.research_status is PaperResearchStatus.ACTIVE
            and paper.analysis is None
        ]
        retired_without_reason = [
            ref
            for ref, paper in current.papers.items()
            if paper.research_status is PaperResearchStatus.RETIRED
            and (
                not isinstance(paper.retirement_reason, str)
                or not paper.retirement_reason.strip()
            )
        ]
        if unanalyzed_active or retired_without_reason:
            raise CommandRejectedError(
                f"cannot request completion: retained papers are not closed; "
                f"{len(unanalyzed_active)} ACTIVE paper(s) have no PaperAnalysis: "
                f"{sorted(unanalyzed_active)!r}; "
                f"{len(retired_without_reason)} RETIRED paper(s) lack a "
                f"retirement_reason: {sorted(retired_without_reason)!r}; "
                f"analyze or retire (with a reason) each first"
            )

        proposed = deepcopy(current)
        resulting_revision = current.state_revision + 1
        check = CompletionCheck(
            basis_revision=resulting_revision,
            basis_contract_revision=proposed.contract.current_revision,
            requester_rationale=requester_rationale,
        )
        proposed.completion_checks[check.id] = check
        proposed.lifecycle = LifecycleMode.COMPLETION_CHECK
        state_revision = self._commit(
            current,
            proposed,
            expected_revision,
            action="completion_check_requested",
            details={"completion_check_ref": check.id},
        )
        return RequestCompletionCheckResult(
            state_revision=state_revision,
            completion_check_ref=check.id,
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
        self._validate_completion_payload_types(verdict, reasons, blocking_gaps)
        current = self._repository.load(run_id)
        check = current.completion_checks.get(completion_check_ref)
        if check is None:
            raise CommandRejectedError(
                f"completion check {completion_check_ref!r} does not exist"
            )

        if check.completed_at is not None:
            if check.verdict is verdict and check.reasons == reasons:
                if verdict is not CompletionVerdict.CONTINUE and blocking_gaps:
                    raise CommandRejectedError(
                        f"{verdict.value} must not include blocking gap specs"
                    )
                return self._completion_result(current, check)
            raise CompletionSubmissionConflictError(
                f"completion check {completion_check_ref!r} has a conflicting result"
            )

        if current.state_revision != expected_revision:
            raise RevisionConflictError(
                f"expected revision {expected_revision}, found "
                f"{current.state_revision}"
            )
        self._require_lifecycle(
            current, LifecycleMode.COMPLETION_CHECK, "submit_completion_check"
        )

        pending_refs = {
            item.id
            for item in current.completion_checks.values()
            if item.completed_at is None
        }
        if pending_refs != {completion_check_ref}:
            raise CommandRejectedError(
                "submit_completion_check must target the current pending check"
            )
        self._validate_blocking_gap_rules(verdict, blocking_gaps)

        proposed = deepcopy(current)
        proposed_check = proposed.completion_checks[completion_check_ref]
        blocking_refs = self._apply_blocking_gaps(proposed, verdict, blocking_gaps)
        proposed_check.verdict = verdict
        proposed_check.reasons = reasons
        proposed_check.blocking_gap_refs = blocking_refs
        proposed_check.completed_at = utc_now()

        if verdict is CompletionVerdict.PASS:
            proposed.lifecycle = LifecycleMode.DELIVERY
            proposed.delivery_basis = CompletionPassBasis(
                completion_check_ref=completion_check_ref
            )
        else:
            proposed.lifecycle = LifecycleMode.RESEARCH
            proposed.delivery_basis = None

        self._commit(
            current,
            proposed,
            expected_revision,
            action="completion_check_submitted",
            actor="completion_checker",
            details={
                "completion_check_ref": completion_check_ref,
                "verdict": verdict.value,
                "blocking_gap_count": len(blocking_refs),
            },
        )
        return self._completion_result(proposed, proposed_check)

    def _put_landscape_item(
        self,
        run_id: str,
        expected_revision: int,
        *,
        item_kind: str,
        statement: str,
        approach_refs: frozenset[str],
        sources: frozenset[LiteratureSource],
        item_ref: str | None,
    ) -> EntityMutationResult:
        command_name = (
            f"put_{'landscape_finding' if item_kind == 'finding' else 'open_problem'}"
        )
        current = self._load_research(run_id, expected_revision, command_name)
        self._validate_landscape_item_values(current, statement, approach_refs, sources)
        proposed = deepcopy(current)
        if item_kind == "finding":
            items = proposed.literature_landscape.findings
            if item_ref is None:
                finding = LandscapeFinding(
                    statement=statement,
                    approach_refs=set(approach_refs),
                    sources=set(sources),
                )
                items[finding.id] = finding
            else:
                existing_finding = items.get(item_ref)
                if existing_finding is None:
                    raise CommandRejectedError(
                        f"landscape finding {item_ref!r} does not exist"
                    )
                existing_finding.statement = statement
                existing_finding.approach_refs = set(approach_refs)
                existing_finding.sources = set(sources)
                finding = existing_finding
            self._reject_no_change(current, proposed, "landscape item mutation")
            return EntityMutationResult(
                state_revision=self._commit(current, proposed, expected_revision),
                entity_ref=finding.id,
            )

        problems = proposed.literature_landscape.open_problems
        if item_ref is None:
            problem = OpenProblem(
                statement=statement,
                approach_refs=set(approach_refs),
                sources=set(sources),
            )
            problems[problem.id] = problem
        else:
            existing_problem = problems.get(item_ref)
            if existing_problem is None:
                raise CommandRejectedError(f"open problem {item_ref!r} does not exist")
            existing_problem.statement = statement
            existing_problem.approach_refs = set(approach_refs)
            existing_problem.sources = set(sources)
            problem = existing_problem
        self._reject_no_change(current, proposed, "landscape item mutation")
        return EntityMutationResult(
            state_revision=self._commit(current, proposed, expected_revision),
            entity_ref=problem.id,
        )

    def _retire_landscape_item(
        self,
        run_id: str,
        expected_revision: int,
        item_kind: str,
        item_ref: str,
    ) -> DomainMutationResult:
        current = self._load_research(run_id, expected_revision, f"retire_{item_kind}")
        proposed = deepcopy(current)
        if item_kind == "finding":
            if item_ref not in proposed.literature_landscape.findings:
                raise CommandRejectedError(f"finding {item_ref!r} does not exist")
            del proposed.literature_landscape.findings[item_ref]
        else:
            if item_ref not in proposed.literature_landscape.open_problems:
                raise CommandRejectedError(f"problem {item_ref!r} does not exist")
            del proposed.literature_landscape.open_problems[item_ref]
        return DomainMutationResult(
            state_revision=self._commit(current, proposed, expected_revision)
        )

    def _set_gap_resolution(
        self,
        run_id: str,
        expected_revision: int,
        gap_ref: str,
        resolution: str | None,
    ) -> DomainMutationResult:
        current = self._load_research(run_id, expected_revision, "set_gap_resolution")
        proposed = deepcopy(current)
        gap = proposed.investigation_gaps.get(gap_ref)
        if gap is None:
            raise CommandRejectedError(f"gap {gap_ref!r} does not exist")
        gap.resolution = resolution
        self._reject_no_change(current, proposed, "gap resolution mutation")
        return DomainMutationResult(
            state_revision=self._commit(
                current,
                proposed,
                expected_revision,
                action=(
                    "investigation_gap_reopened"
                    if resolution is None
                    else "investigation_gap_resolved"
                ),
                details={"gap_ref": gap_ref},
            )
        )

    def _load_research(
        self,
        run_id: str,
        expected_revision: int,
        command_name: str,
    ) -> ResearchRun:
        current = self._load_expected(run_id, expected_revision)
        self._require_lifecycle(current, LifecycleMode.RESEARCH, command_name)
        return current

    def _load_expected(self, run_id: str, expected_revision: int) -> ResearchRun:
        run = self._repository.load(run_id)
        if run.state_revision != expected_revision:
            raise RevisionConflictError(
                f"expected revision {expected_revision}, found {run.state_revision}"
            )
        return run

    @staticmethod
    def _current_contract(run: ResearchRun) -> ResearchContract:
        return next(
            entry.contract
            for entry in run.contract.revisions
            if entry.revision == run.contract.current_revision
        )

    @staticmethod
    def _reject_no_change(
        current: ResearchRun,
        proposed: ResearchRun,
        description: str,
    ) -> None:
        if proposed == current:
            raise CommandRejectedError(f"{description} must change state")

    @staticmethod
    def _validate_reference_frozenset(value: object, name: str) -> None:
        if not isinstance(value, frozenset) or not all(
            isinstance(ref, str) for ref in value
        ):
            raise CommandRejectedError(f"{name} must be a frozenset of references")

    @classmethod
    def _validate_landscape_item_values(
        cls,
        run: ResearchRun,
        statement: str,
        approach_refs: frozenset[str],
        sources: frozenset[LiteratureSource],
    ) -> None:
        if not isinstance(statement, str):
            raise CommandRejectedError("statement must be a string")
        cls._validate_reference_frozenset(approach_refs, "approach_refs")
        if not isinstance(sources, frozenset) or not all(
            isinstance(source, LiteratureSource) for source in sources
        ):
            raise CommandRejectedError(
                "sources must be a frozenset of LiteratureSource"
            )
        missing_approaches = set(approach_refs) - set(
            run.literature_landscape.approach_families
        )
        missing_papers = {
            source.paper_ref for source in sources if source.paper_ref not in run.papers
        }
        if missing_approaches:
            raise CommandRejectedError(
                f"landscape item has dangling approach refs: "
                f"{sorted(missing_approaches)!r}"
            )
        if missing_papers:
            raise CommandRejectedError(
                f"landscape item has dangling paper refs: {sorted(missing_papers)!r}"
            )
        # A Finding / OpenProblem source must point at an ACTIVE, analyzed paper.
        # Retired papers are closed candidates, not current evidence; unanalyzed
        # papers would let search metadata ground a landscape claim.
        ineligible_sources = {
            source.paper_ref
            for source in sources
            if run.papers[source.paper_ref].research_status
            is not PaperResearchStatus.ACTIVE
            or run.papers[source.paper_ref].analysis is None
        }
        if ineligible_sources:
            raise CommandRejectedError(
                f"landscape item sources must point at ACTIVE papers with a "
                f"PaperAnalysis; ineligible: {sorted(ineligible_sources)!r}"
            )

    @classmethod
    def _validate_gap_metadata(
        cls,
        run: ResearchRun,
        description: str,
        requirement_refs: frozenset[str],
        approach_refs: frozenset[str],
    ) -> None:
        if not isinstance(description, str):
            raise CommandRejectedError("gap description must be a string")
        cls._validate_reference_frozenset(requirement_refs, "requirement_refs")
        cls._validate_reference_frozenset(approach_refs, "approach_refs")
        current_requirements = set(cls._current_contract(run).requirements)
        missing_requirements = set(requirement_refs) - current_requirements
        missing_approaches = set(approach_refs) - set(
            run.literature_landscape.approach_families
        )
        if missing_requirements:
            raise CommandRejectedError(
                f"gap has dangling requirement refs: {sorted(missing_requirements)!r}"
            )
        if missing_approaches:
            raise CommandRejectedError(
                f"gap has dangling approach refs: {sorted(missing_approaches)!r}"
            )

    @staticmethod
    def _rewrite_paper_refs(
        run: ResearchRun,
        old_ref: str,
        new_ref: str,
    ) -> None:
        for approach in run.literature_landscape.approach_families.values():
            if old_ref in approach.representative_papers:
                approach.representative_papers.remove(old_ref)
                approach.representative_papers.add(new_ref)

        def rewrite_sources(
            sources: set[LiteratureSource],
        ) -> set[LiteratureSource]:
            return {
                LiteratureSource(
                    paper_ref=(
                        new_ref if source.paper_ref == old_ref else source.paper_ref
                    ),
                    relation=source.relation,
                    locator=source.locator,
                )
                for source in sources
            }

        for finding in run.literature_landscape.findings.values():
            finding.sources = rewrite_sources(finding.sources)
        for problem in run.literature_landscape.open_problems.values():
            problem.sources = rewrite_sources(problem.sources)

    @staticmethod
    def _paper_referenced_by(run: ResearchRun, paper_ref: str) -> tuple[str, ...]:
        """Stable refs of semantic objects that still cite this paper.

        A paper still used by the current Landscape (as an ApproachFamily
        representative, or as a Finding / OpenProblem source) cannot be
        retired until the Researcher updates or retires those objects first.
        Rewriting those references is a semantic decision, so the Harness
        rejects rather than auto-removes them.
        """
        refs: list[str] = []
        for approach in run.literature_landscape.approach_families.values():
            if paper_ref in approach.representative_papers:
                refs.append(approach.id)
        for finding in run.literature_landscape.findings.values():
            if any(source.paper_ref == paper_ref for source in finding.sources):
                refs.append(finding.id)
        for problem in run.literature_landscape.open_problems.values():
            if any(source.paper_ref == paper_ref for source in problem.sources):
                refs.append(problem.id)
        return tuple(sorted(refs))

    def _commit(
        self,
        current: ResearchRun,
        proposed: ResearchRun,
        expected_revision: int,
        *,
        action: str = "research_mutation",
        actor: str = "researcher",
        reason: str | None = None,
        details: dict[str, AuditScalar] | None = None,
    ) -> int:
        proposed.state_revision = current.state_revision + 1
        self._repository.save(proposed, expected_revision)
        self._append_audit(
            proposed,
            action=action,
            actor=actor,
            reason=reason,
            details={} if details is None else details,
        )
        return proposed.state_revision

    def _append_audit(
        self,
        run: ResearchRun,
        *,
        action: str,
        actor: str,
        reason: str | None = None,
        details: dict[str, AuditScalar] | None = None,
    ) -> None:
        append_audit(
            self._audit_sink,
            AuditEvent(
                run_id=run.id,
                state_revision=run.state_revision,
                actor=actor,
                action=action,
                reason=reason,
                details={} if details is None else details,
            ),
        )

    @staticmethod
    def _require_lifecycle(
        run: ResearchRun, required: LifecycleMode, command_name: str
    ) -> None:
        if run.lifecycle is not required:
            raise CommandRejectedError(
                f"{command_name} requires {required.value}; found {run.lifecycle.value}"
            )

    @staticmethod
    def _validate_create_request(request: CreateRunRequest) -> None:
        if not isinstance(request, CreateRunRequest):
            raise CommandRejectedError("create_run requires a CreateRunRequest")
        for name, value in (
            ("mission", request.mission),
            ("scope", request.scope),
            ("deliverable_description", request.deliverable_description),
        ):
            if not isinstance(value, str):
                raise CommandRejectedError(f"{name} must be a string")
        if not isinstance(request.requirements, tuple) or not all(
            isinstance(statement, str) for statement in request.requirements
        ):
            raise CommandRejectedError("requirements must be a tuple of strings")
        if not isinstance(request.required_artifacts, frozenset) or not all(
            isinstance(artifact, ArtifactKind)
            for artifact in request.required_artifacts
        ):
            raise CommandRejectedError(
                "required_artifacts must be a frozenset of ArtifactKind"
            )

    @staticmethod
    def _source_from_hit(hit: PaperSearchHit) -> PaperSource:
        if not isinstance(hit, PaperSearchHit):
            raise CommandRejectedError("hits must contain PaperSearchHit values")
        if not isinstance(hit.title, str):
            raise CommandRejectedError("paper title must be a string")
        if not isinstance(hit.authors, tuple) or not all(
            isinstance(author, str) for author in hit.authors
        ):
            raise CommandRejectedError("paper authors must be a tuple of strings")
        if hit.publication_year is not None and (
            not isinstance(hit.publication_year, int)
            or isinstance(hit.publication_year, bool)
        ):
            raise CommandRejectedError("publication_year must be an integer or None")
        if hit.publication_date is not None:
            if not isinstance(hit.publication_date, str):
                raise CommandRejectedError(
                    "publication_date must use YYYY-MM-DD or be None"
                )
            try:
                parsed_publication_date = date.fromisoformat(hit.publication_date)
            except ValueError:
                raise CommandRejectedError(
                    "publication_date must use YYYY-MM-DD or be None"
                ) from None
            if hit.publication_date != parsed_publication_date.isoformat():
                raise CommandRejectedError(
                    "publication_date must use YYYY-MM-DD or be None"
                )
        for name, value in (
            ("doi", hit.doi),
            ("arxiv_id", hit.arxiv_id),
            ("canonical_url", hit.canonical_url),
        ):
            if value is not None and not isinstance(value, str):
                raise CommandRejectedError(f"{name} must be a string or None")
        if not isinstance(hit.other_identifiers, Mapping) or not all(
            isinstance(kind, str) and isinstance(value, str)
            for kind, value in hit.other_identifiers.items()
        ):
            raise CommandRejectedError(
                "other_identifiers must be a string-to-string mapping"
            )
        return PaperSource(
            title=hit.title,
            authors=hit.authors,
            publication_year=hit.publication_year,
            publication_date=hit.publication_date,
            doi=hit.doi,
            arxiv_id=hit.arxiv_id,
            canonical_url=(
                None if hit.canonical_url is None else hit.canonical_url.strip()
            ),
            other_identifiers=dict(hit.other_identifiers),
        )

    @staticmethod
    def _require_nonempty_identity_values(
        identity_keys: tuple[PaperIdentityKey, ...]
    ) -> None:
        if any(not value for _, value in identity_keys):
            raise CommandRejectedError("stable paper identifiers must not be empty")

    @staticmethod
    def _paper_identity_index(
        run: ResearchRun,
    ) -> dict[PaperIdentityKey, str]:
        index: dict[PaperIdentityKey, str] = {}
        for paper_ref, paper in run.papers.items():
            for key in paper_identity_keys(paper.source):
                index[key] = paper_ref
        return index

    @staticmethod
    def _enrich_paper_identity(paper: Paper, incoming: PaperSource) -> None:
        if incoming.doi is not None:
            if paper.source.doi is None:
                paper.source.doi = incoming.doi
            elif normalize_doi(paper.source.doi) != normalize_doi(incoming.doi):
                raise CommandRejectedError(f"paper {paper.id!r} has a conflicting DOI")

        if incoming.arxiv_id is not None:
            if paper.source.arxiv_id is None:
                paper.source.arxiv_id = incoming.arxiv_id
            elif normalize_arxiv_id(paper.source.arxiv_id) != normalize_arxiv_id(
                incoming.arxiv_id
            ):
                raise CommandRejectedError(
                    f"paper {paper.id!r} has a conflicting arXiv ID"
                )

        if paper.source.canonical_url is None and incoming.canonical_url is not None:
            paper.source.canonical_url = incoming.canonical_url

        for kind, value in incoming.other_identifiers.items():
            paper.source.other_identifiers.setdefault(kind, value)

    @staticmethod
    def _validate_finding_refs(run: ResearchRun, mutation: PutLandscapeFinding) -> None:
        """Validate a Finding mutation through the unified landscape eligibility gate.

        This delegates to ``_validate_landscape_item_values`` so the mutation path
        (``apply_research_mutation(PutLandscapeFinding)``) and the command path
        (``put-finding``) enforce one identical rule set: every ``LiteratureSource``
        paper_ref must exist, be ACTIVE, and have a PaperAnalysis. No duplicate,
        weaker validator is kept.
        """
        cls = ResearchCommands
        cls._validate_reference_frozenset(mutation.approach_refs, "approach_refs")
        if not isinstance(mutation.sources, frozenset) or not all(
            isinstance(source, LiteratureSource) for source in mutation.sources
        ):
            raise CommandRejectedError(
                "finding sources must be a frozenset of LiteratureSource"
            )
        cls._validate_landscape_item_values(
            run,
            mutation.statement,
            mutation.approach_refs,
            mutation.sources,
        )

    @staticmethod
    def _validate_completion_payload_types(
        verdict: CompletionVerdict,
        reasons: tuple[str, ...],
        blocking_gaps: tuple[BlockingGapSpec, ...],
    ) -> None:
        if not isinstance(verdict, CompletionVerdict):
            raise CommandRejectedError("verdict must be a CompletionVerdict")
        if (
            not isinstance(reasons, tuple)
            or not reasons
            or not all(isinstance(reason, str) for reason in reasons)
        ):
            raise CommandRejectedError("reasons must be a non-empty tuple of strings")
        if not isinstance(blocking_gaps, tuple):
            raise CommandRejectedError("blocking_gaps must be a tuple")

    @staticmethod
    def _validate_blocking_gap_rules(
        verdict: CompletionVerdict,
        blocking_gaps: tuple[BlockingGapSpec, ...],
    ) -> None:
        if verdict is CompletionVerdict.CONTINUE and not blocking_gaps:
            raise CommandRejectedError("CONTINUE requires at least one blocking gap")
        if verdict is not CompletionVerdict.CONTINUE and blocking_gaps:
            raise CommandRejectedError(
                f"{verdict.value} must not include blocking gap specs"
            )

    @staticmethod
    def _apply_blocking_gaps(
        run: ResearchRun,
        verdict: CompletionVerdict,
        blocking_gaps: tuple[BlockingGapSpec, ...],
    ) -> set[str]:
        if verdict is not CompletionVerdict.CONTINUE:
            return set()

        current_contract = next(
            revision.contract
            for revision in run.contract.revisions
            if revision.revision == run.contract.current_revision
        )
        requirement_refs = set(current_contract.requirements)
        approach_refs = set(run.literature_landscape.approach_families)
        result: set[str] = set()

        for spec in blocking_gaps:
            if isinstance(spec, NewBlockingGap):
                if not isinstance(spec.description, str):
                    raise CommandRejectedError("gap description must be a string")
                if not isinstance(spec.requirement_refs, frozenset) or not all(
                    isinstance(requirement_ref, str)
                    for requirement_ref in spec.requirement_refs
                ):
                    raise CommandRejectedError(
                        "gap requirement_refs must be a frozenset of references"
                    )
                if not isinstance(spec.approach_refs, frozenset) or not all(
                    isinstance(approach_ref, str) for approach_ref in spec.approach_refs
                ):
                    raise CommandRejectedError(
                        "gap approach_refs must be a frozenset of references"
                    )
                missing_requirements = set(spec.requirement_refs) - requirement_refs
                missing_approaches = set(spec.approach_refs) - approach_refs
                if missing_requirements:
                    raise CommandRejectedError(
                        "new gap has dangling requirement refs: "
                        f"{sorted(missing_requirements)!r}"
                    )
                if missing_approaches:
                    raise CommandRejectedError(
                        "new gap has dangling approach refs: "
                        f"{sorted(missing_approaches)!r}"
                    )
                gap = InvestigationGap(
                    description=spec.description,
                    requirement_refs=set(spec.requirement_refs),
                    approach_refs=set(spec.approach_refs),
                    resolution=None,
                )
                run.investigation_gaps[gap.id] = gap
                result.add(gap.id)
                continue

            if isinstance(spec, ReopenBlockingGap):
                if not isinstance(spec.gap_ref, str):
                    raise CommandRejectedError("gap_ref must be a string")
                reopened_gap = run.investigation_gaps.get(spec.gap_ref)
                if reopened_gap is None:
                    raise CommandRejectedError(f"gap {spec.gap_ref!r} does not exist")
                reopened_gap.resolution = None
                result.add(reopened_gap.id)
                continue

            raise CommandRejectedError("unsupported blocking gap spec")

        return result

    @staticmethod
    def _completion_result(
        run: ResearchRun, check: CompletionCheck
    ) -> CompletionSubmissionResult:
        if check.verdict is None:
            raise AssertionError("completion result requires a completed check")
        return CompletionSubmissionResult(
            state_revision=run.state_revision,
            completion_check_ref=check.id,
            verdict=check.verdict,
            reasons=check.reasons,
            blocking_gap_refs=frozenset(check.blocking_gap_refs),
        )
