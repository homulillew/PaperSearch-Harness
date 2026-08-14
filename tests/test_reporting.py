"""ADR-012 Report Brief dual-review loop — pipeline invariant tests.

These tests pin the ADR-012 report-generation refactor as an in-process
orchestration test (style of ``test_credentials.py`` / the codec round-trip
tests in ``test_deep_reading.py``). They build a run to DELIVERY through the
real command path, then drive ``ReportPipeline`` with fake semantic actors to
assert the five architecture invariants and the control-flow rules:

  1. Research State is the only research authority.
  2. Report Brief is the only report-semantic intermediate work product.
  3. Writer has article-reasoning authority, not Research authority.
  4. Every final Manuscript passes the reader gate AND the integrity gate.
  5. Problems return to the earliest layer authorized to repair them:
     Manuscript -> Reviser, Brief -> Constructor, State -> Research.

Plus the mechanical rules: two-phase blind read (Phase 1 has no Brief), a new
Reviewer instance after every revision, editorial loop stops only on empty
Blocking Issues (no scores / fixed rounds), resource exhaustion is never PASS,
any manuscript change invalidates the editorial PASS, integrity dispositions
(PASS / REVISE_DELIVERY / REOPEN_RESEARCH) and repair_target routing, and
``DeliveryEvidenceAccess.inspect_source`` revision tracking.

No network, no DeepXiv, no real source reads. The fake actors are pure
Python callables that record their inputs and return typed value objects.

Run:

    python -m pytest tests/test_reporting.py --basetemp=./.pytest_tmp
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

SKILL_DIR = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "literature-research"
)
RUNTIME_SRC = SKILL_DIR / "runtime" / "src"

if str(RUNTIME_SRC) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SRC))

from my_search_harness.domain.model import (  # noqa: E402
    CompletionVerdict,
    DeliveryBasis,
    PaperAnalysis,
)
from my_search_harness.runtime import (  # noqa: E402
    BlindRead,
    DeliveryEvidenceAccess,
    EditorialIssue,
    EditorialReview,
    IntegrityDisposition,
    PublishedReportPipelineResult,
    ReportBrief,
    ReportBriefSection,
    ReportConstructionRequest,
    ReportManuscript,
    ReportPipeline,
    ReportPipelineError,
    ReportResearchReopenedResult,
    ResearchEscalationRequired,
    ResearchIntegrityReview,
)
from my_search_harness.runtime.capabilities import (  # noqa: E402
    CapabilityUnavailableError,
)
from my_search_harness.runtime.commands import (  # noqa: E402
    CreateRunRequest,
    PutPaperAnalysis,
    ResearchMutationBatch,
)
from my_search_harness.runtime.local_runtime import LocalV1Runtime  # noqa: E402
from my_search_harness.runtime.paper_search import PaperSearchHit  # noqa: E402
from my_search_harness.runtime.source_access import (  # noqa: E402
    SourceAccessAttemptError,
    SourceAccessFailureKind,
)


# ---------------------------------------------------------------------------
# In-process DELIVERY fixture.
# ---------------------------------------------------------------------------


def _make_delivery_run(workspace: Path) -> tuple[LocalV1Runtime, str, DeliveryBasis]:
    """Drive a real run through create -> request -> submit(PASS) -> DELIVERY.

    Returns (runtime, run_id, delivery_basis). The run carries one requirement
    and one ACTIVE+analyzed paper so the Brief validator has known refs.
    """

    runtime = LocalV1Runtime(
        workspace, paper_search_provider=None, source_access_provider=None
    )
    researcher = runtime.researcher
    create = researcher.create_run(
        CreateRunRequest(
            mission="map route A",
            requirements=("cover route A",),
            scope="primary literature",
            deliverable_description="a survey of route A",
            required_artifacts=frozenset(),
        )
    )
    run_id = create.run_id

    # Retain one paper and analyze it so it is ACTIVE+analyzed (eligible
    # formal evidence and a known ref for the Brief).
    retain = researcher.retain_papers(
        run_id,
        create.state_revision,
        (PaperSearchHit(title="Paper A"),),
    )
    paper_ref = retain.paper_refs[0]

    researcher.apply_research_mutation(
        run_id,
        retain.state_revision,
        ResearchMutationBatch(
            puts=(
                PutPaperAnalysis(
                    paper_ref=paper_ref,
                    analysis=PaperAnalysis(
                        summary="summary",
                        relevance_to_run="relevant",
                        contributions=("c1",),
                        key_results=("r1",),
                    ),
                ),
            ),
        ),
    )

    # Request and PASS completion to reach DELIVERY.
    request = researcher.request_completion_check(
        run_id, _rev_via_view(runtime, run_id), "ok"
    )
    completion_check_ref = request.completion_check_ref
    submit = runtime.completion_checker.submit_completion_check(
        run_id,
        request.state_revision,
        completion_check_ref,
        CompletionVerdict.PASS,
        ("state is sufficient",),
    )
    assert submit.verdict is CompletionVerdict.PASS
    # The delivery_basis is set on the run; read it from the DeliveryView.
    basis = runtime.delivery.view(run_id).delivery_basis
    assert basis is not None, "PASS must set a delivery_basis"
    return runtime, run_id, basis


def _rev_via_view(runtime: LocalV1Runtime, run_id: str) -> int:
    from my_search_harness.runtime.context import ResearchView

    view = runtime.researcher.view(run_id)
    assert isinstance(view, ResearchView)
    return view.state_revision


# ---------------------------------------------------------------------------
# Fake semantic actors.
# ---------------------------------------------------------------------------


@dataclass
class FakeConstructor:
    """Records calls and returns a Brief built from the view."""

    briefs: list[ReportBrief] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    escalate: bool = False

    def construct(
        self,
        view,
        writing_guideline: str,
        evidence: DeliveryEvidenceAccess,
        *,
        previous_brief: ReportBrief | None = None,
        feedback: tuple[str, ...] = (),
    ) -> ReportBrief:
        self.calls.append(
            {"previous_brief": previous_brief, "feedback": feedback}
        )
        if self.escalate:
            raise ResearchEscalationRequired("state insufficient for brief")
        paper_ref = view.papers[0].ref
        req_ref = view.contract.requirements[0].ref
        brief = ReportBrief(
            delivery_basis=view.delivery_basis,
            audience="researcher",
            report_goal="survey route A",
            reader_takeaway="route A trades X for Y",
            narrative_logic=("establish A", "contrast with B"),
            sections=(
                ReportBriefSection(
                    title="Route A",
                    requirement_refs=(req_ref,),
                    purpose="establish route A",
                    reader_takeaway="A works",
                    argument_flow=("claim A", "evidence A"),
                    research_refs=(paper_ref,),
                    material=(),
                    evidence_boundary="only under condition Z",
                ),
            ),
            terminology=(("A", "alpha"),),
            intentional_omissions=(),
        )
        self.briefs.append(brief)
        return brief


@dataclass
class FakeWriter:
    """Returns a Manuscript, or a construction request on the first call if flagged."""

    manuscripts: list[ReportManuscript] = field(default_factory=list)
    requests: int = 0  # how many construction requests to emit before writing
    calls: list[dict] = field(default_factory=list)

    def write(self, brief, writing_guideline: str, paper_metadata):
        self.calls.append(
            {
                "brief": brief,
                "has_view": False,  # Writer must NOT receive a DeliveryView
                "paper_metadata": paper_metadata,
            }
        )
        if self.requests > 0:
            self.requests -= 1
            return ReportConstructionRequest(
                reason="section material missing", section_title="Route A"
            )
        paper_ref = paper_metadata[0].ref
        manuscript = ReportManuscript(
            markdown="Route A works {{cite:a}}.",
            citations=(
                _cite("a", paper_ref),
            ),
        )
        self.manuscripts.append(manuscript)
        return manuscript


@dataclass
class FakeReviewer:
    """Two-phase reviewer. ``rounds`` lists the issue-counts per editorial round."""

    rounds: list[int]  # number of blocking issues per round; last entry is 0
    _index: int = 0
    blind_calls: list[dict] = field(default_factory=list)
    brief_calls: list[dict] = field(default_factory=list)

    def blind_read(self, deliverable_description, writing_guideline, manuscript):
        # Invariant: Phase 1 must NOT receive the Brief.
        self.blind_calls.append(
            {"has_brief": False, "manuscript": manuscript}
        )
        return BlindRead(issues=())

    def check_brief(self, brief, manuscript):
        issues = self._current_issues()
        self.brief_calls.append({"brief": brief, "manuscript": manuscript})
        return EditorialReview(
            issues=tuple(
                EditorialIssue(description=f"blocker {i}") for i in range(issues)
            )
        )

    def _current_issues(self) -> int:
        if self._index < len(self.rounds):
            n = self.rounds[self._index]
            self._index += 1
            return n
        return 0


@dataclass
class FakeReviewerFactory:
    """Counts how many fresh Reviewer instances are created."""

    rounds: list[int]
    created: list[FakeReviewer] = field(default_factory=list)

    def create(self) -> FakeReviewer:
        # Each new instance continues the shared round schedule.
        reviewer = FakeReviewer(rounds=self.rounds)
        # Carry over the index so the schedule is shared across instances.
        if self.created:
            reviewer._index = self.created[-1]._index
        self.created.append(reviewer)
        return reviewer


@dataclass
class FakeReviser:
    calls: list[dict] = field(default_factory=list)

    def revise(self, brief, manuscript, issues, writing_guideline, paper_metadata):
        self.calls.append(
            {"brief": brief, "manuscript": manuscript, "issues": issues}
        )
        # Return a revised manuscript (changed markdown so it's a new one).
        return ReportManuscript(
            markdown=manuscript.markdown.replace("works", "works (revised)"),
            citations=manuscript.citations,
        )


@dataclass
class FakeIntegrity:
    """Returns a sequence of dispositions, one per review call.

    ``dispositions`` is consumed in order; the last value is sticky for any
    extra calls. ``repair_targets`` and ``issues_seq`` parallel it. For a
    single-call happy path, pass a one-element tuple (or rely on the PASS
    defaults).
    """

    dispositions: tuple[IntegrityDisposition, ...] = (IntegrityDisposition.PASS,)
    repair_targets: tuple[str | None, ...] = (None,)
    issues_seq: tuple[tuple[str, ...], ...] = ((),)
    calls: list[dict] = field(default_factory=list)
    _index: int = 0

    def review(self, view, brief, manuscript, evidence):
        idx = self._index
        self._index += 1
        disposition = (
            self.dispositions[idx]
            if idx < len(self.dispositions)
            else self.dispositions[-1]
        )
        repair_target = (
            self.repair_targets[idx]
            if idx < len(self.repair_targets)
            else self.repair_targets[-1]
        )
        issues = (
            self.issues_seq[idx]
            if idx < len(self.issues_seq)
            else self.issues_seq[-1]
        )
        self.calls.append(
            {"brief": brief, "manuscript": manuscript, "has_view": True}
        )
        return ResearchIntegrityReview(
            disposition=disposition,
            issues=issues,
            repair_target=repair_target,
        )


def _cite(citation_id: str, paper_ref: str):
    from my_search_harness.runtime import CitationReference

    return CitationReference(citation_id=citation_id, paper_ref=paper_ref)


def _build_pipeline(
    runtime: LocalV1Runtime,
    *,
    constructor,
    writer,
    reviewer_factory,
    reviser,
    integrity,
) -> ReportPipeline:
    from my_search_harness.runtime.citations import DeterministicCitationRenderer

    return ReportPipeline(
        runtime.delivery,
        constructor=constructor,
        writer=writer,
        reviewer_factory=reviewer_factory,
        reviser=reviser,
        integrity_reviewer=integrity,
        citation_renderer=DeterministicCitationRenderer(),
        writing_guideline="writing guide",
    )


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


def test_pipeline_happy_path_publishes(tmp_path: Path):
    """Invariant 4: a PASS through both gates publishes a final report."""
    runtime, run_id, basis = _make_delivery_run(tmp_path)
    constructor = FakeConstructor()
    writer = FakeWriter()
    factory = FakeReviewerFactory(rounds=[0])  # no blockers on round 1
    reviser = FakeReviser()
    integrity = FakeIntegrity(
        dispositions=(IntegrityDisposition.PASS,), issues_seq=((),)
    )
    pipeline = _build_pipeline(
        runtime,
        constructor=constructor,
        writer=writer,
        reviewer_factory=factory,
        reviser=reviser,
        integrity=integrity,
    )
    result = pipeline.run(run_id)
    assert isinstance(result, PublishedReportPipelineResult)
    assert result.report_brief.delivery_basis == basis
    assert result.integrity_review.disposition is IntegrityDisposition.PASS
    # Reviser is NOT called when there are no blockers.
    assert reviser.calls == []
    # Exactly one fresh Reviewer instance was created.
    assert len(factory.created) == 1
    # The published artifact path exists.
    assert result.artifact.path.exists()


def test_editorial_loop_runs_until_no_blockers(tmp_path: Path):
    """Editorial loop stops only on empty Blocking Issues; reviser called per round."""
    runtime, run_id, _ = _make_delivery_run(tmp_path)
    constructor = FakeConstructor()
    writer = FakeWriter()
    # Round 1: 2 blockers; round 2: 1 blocker; round 3: 0 blockers.
    factory = FakeReviewerFactory(rounds=[2, 1, 0])
    reviser = FakeReviser()
    integrity = FakeIntegrity()
    pipeline = _build_pipeline(
        runtime,
        constructor=constructor,
        writer=writer,
        reviewer_factory=factory,
        reviser=reviser,
        integrity=integrity,
    )
    result = pipeline.run(run_id)
    assert isinstance(result, PublishedReportPipelineResult)
    # Reviser called twice (once per blocker round), then no more.
    assert len(reviser.calls) == 2
    # Three fresh Reviewer instances: one per round including the final clean one.
    assert len(factory.created) == 3


def test_editorial_loop_resource_exhaustion_is_not_pass(tmp_path: Path):
    """Resource exhaustion (round cap) raises, never PASSes."""
    runtime, run_id, _ = _make_delivery_run(tmp_path)
    constructor = FakeConstructor()
    writer = FakeWriter()
    # Every round returns blockers; never converges.
    factory = FakeReviewerFactory(rounds=[1] * 100)
    reviser = FakeReviser()
    integrity = FakeIntegrity()
    pipeline = _build_pipeline(
        runtime,
        constructor=constructor,
        writer=writer,
        reviewer_factory=factory,
        reviser=reviser,
        integrity=integrity,
    )
    with pytest.raises(ReportPipelineError, match="resource exhaustion"):
        pipeline.run(run_id)


def test_blind_read_phase1_receives_no_brief(tmp_path: Path):
    """Invariant: Phase 1 blind read must NOT receive the Brief."""
    runtime, run_id, _ = _make_delivery_run(tmp_path)
    constructor = FakeConstructor()
    writer = FakeWriter()
    factory = FakeReviewerFactory(rounds=[0])
    reviser = FakeReviser()
    integrity = FakeIntegrity()
    pipeline = _build_pipeline(
        runtime,
        constructor=constructor,
        writer=writer,
        reviewer_factory=factory,
        reviser=reviser,
        integrity=integrity,
    )
    pipeline.run(run_id)
    reviewer = factory.created[0]
    assert reviewer.blind_calls, "blind_read must be called"
    assert reviewer.blind_calls[0]["has_brief"] is False
    assert reviewer.brief_calls, "check_brief must be called"
    assert isinstance(reviewer.brief_calls[0]["brief"], ReportBrief)


def test_writer_receives_brief_and_paper_metadata_not_view(tmp_path: Path):
    """Invariant 3: Writer gets Brief + safe paper metadata, NOT the DeliveryView."""
    runtime, run_id, _ = _make_delivery_run(tmp_path)
    constructor = FakeConstructor()
    writer = FakeWriter()
    factory = FakeReviewerFactory(rounds=[0])
    reviser = FakeReviser()
    integrity = FakeIntegrity()
    pipeline = _build_pipeline(
        runtime,
        constructor=constructor,
        writer=writer,
        reviewer_factory=factory,
        reviser=reviser,
        integrity=integrity,
    )
    pipeline.run(run_id)
    assert writer.calls, "Writer must be called"
    call = writer.calls[0]
    assert isinstance(call["brief"], ReportBrief)
    assert call["has_view"] is False
    # paper_metadata is the tuple of PaperIndexEntry, not a DeliveryView.
    assert isinstance(call["paper_metadata"], tuple)
    from my_search_harness.runtime.context import PaperIndexEntry

    assert isinstance(call["paper_metadata"][0], PaperIndexEntry)


def test_writer_construction_request_routes_to_constructor(tmp_path: Path):
    """Invariant 5: insufficient Brief material returns to Constructor, not research."""
    runtime, run_id, _ = _make_delivery_run(tmp_path)
    constructor = FakeConstructor()
    # Writer emits one construction request, then writes.
    writer = FakeWriter(requests=1)
    factory = FakeReviewerFactory(rounds=[0])
    reviser = FakeReviser()
    integrity = FakeIntegrity()
    pipeline = _build_pipeline(
        runtime,
        constructor=constructor,
        writer=writer,
        reviewer_factory=factory,
        reviser=reviser,
        integrity=integrity,
    )
    result = pipeline.run(run_id)
    assert isinstance(result, PublishedReportPipelineResult)
    # Constructor called twice: once initial, once with feedback.
    assert len(constructor.calls) == 2
    assert constructor.calls[1]["previous_brief"] is not None
    assert constructor.calls[1]["feedback"], "feedback must be passed"


def test_integrity_reopen_research_returns_to_research(tmp_path: Path):
    """Invariant 5: REOPEN_RESEARCH returns to the research loop."""
    runtime, run_id, _ = _make_delivery_run(tmp_path)
    constructor = FakeConstructor()
    writer = FakeWriter()
    factory = FakeReviewerFactory(rounds=[0])
    reviser = FakeReviser()
    integrity = FakeIntegrity(
        dispositions=(IntegrityDisposition.REOPEN_RESEARCH,),
        issues_seq=(("missing primary evidence",),),
    )
    pipeline = _build_pipeline(
        runtime,
        constructor=constructor,
        writer=writer,
        reviewer_factory=factory,
        reviser=reviser,
        integrity=integrity,
    )
    result = pipeline.run(run_id)
    assert isinstance(result, ReportResearchReopenedResult)
    assert "missing primary evidence" in result.rationale
    # The run is back in RESEARCH.
    from my_search_harness.runtime.context import ResearchView

    view = runtime.researcher.view(run_id)
    assert isinstance(view, ResearchView)


def test_integrity_revise_delivery_manuscript_routes_to_reviser(tmp_path: Path):
    """REVISE_DELIVERY + MANUSCRIPT routes to Reviser, then re-runs both gates."""
    runtime, run_id, _ = _make_delivery_run(tmp_path)
    constructor = FakeConstructor()
    writer = FakeWriter()
    factory = FakeReviewerFactory(rounds=[0, 0])  # clean before and after repair
    reviser = FakeReviser()
    integrity = FakeIntegrity(
        dispositions=(
            IntegrityDisposition.REVISE_DELIVERY,
            IntegrityDisposition.PASS,
        ),
        repair_targets=("MANUSCRIPT", None),
        issues_seq=(("tone too strong",), ()),
    )
    pipeline = _build_pipeline(
        runtime,
        constructor=constructor,
        writer=writer,
        reviewer_factory=factory,
        reviser=reviser,
        integrity=integrity,
    )
    result = pipeline.run(run_id)
    assert isinstance(result, PublishedReportPipelineResult)
    # Reviser was called for the integrity repair.
    assert len(reviser.calls) == 1
    assert reviser.calls[0]["issues"][0].description == "tone too strong"
    # Constructor was NOT called again (earliest faulty layer was Manuscript).
    assert len(constructor.calls) == 1
    # Integrity was called twice: initial + after repair.
    assert len(integrity.calls) == 2


def test_integrity_revise_delivery_brief_routes_to_constructor(tmp_path: Path):
    """REVISE_DELIVERY + BRIEF routes to Constructor, then re-runs both gates."""
    runtime, run_id, _ = _make_delivery_run(tmp_path)
    constructor = FakeConstructor()
    writer = FakeWriter()
    factory = FakeReviewerFactory(rounds=[0, 0])
    reviser = FakeReviser()
    integrity = FakeIntegrity(
        dispositions=(
            IntegrityDisposition.REVISE_DELIVERY,
            IntegrityDisposition.PASS,
        ),
        repair_targets=("BRIEF", None),
        issues_seq=(("brief material missing for section",), ()),
    )
    pipeline = _build_pipeline(
        runtime,
        constructor=constructor,
        writer=writer,
        reviewer_factory=factory,
        reviser=reviser,
        integrity=integrity,
    )
    result = pipeline.run(run_id)
    assert isinstance(result, PublishedReportPipelineResult)
    # Constructor called twice: initial + repair with feedback.
    assert len(constructor.calls) == 2
    assert constructor.calls[1]["feedback"] == ("brief material missing for section",)
    # Reviser was NOT called for a Brief-level repair.
    assert reviser.calls == []


def test_integrity_revise_delivery_looping_raises(tmp_path: Path):
    """If integrity repair does not clear REVISE_DELIVERY, the pipeline raises."""
    runtime, run_id, _ = _make_delivery_run(tmp_path)
    constructor = FakeConstructor()
    writer = FakeWriter()
    factory = FakeReviewerFactory(rounds=[0, 0])
    reviser = FakeReviser()
    integrity = FakeIntegrity(
        dispositions=(
            IntegrityDisposition.REVISE_DELIVERY,
            IntegrityDisposition.REVISE_DELIVERY,
        ),
        repair_targets=("MANUSCRIPT", "MANUSCRIPT"),
        issues_seq=(("persistent issue",), ("persistent issue",)),
    )
    pipeline = _build_pipeline(
        runtime,
        constructor=constructor,
        writer=writer,
        reviewer_factory=factory,
        reviser=reviser,
        integrity=integrity,
    )
    with pytest.raises(ReportPipelineError, match="did not clear REVISE_DELIVERY"):
        pipeline.run(run_id)


def test_constructor_escalation_reopens_research(tmp_path: Path):
    """A ResearchEscalationRequired from the Constructor reopens research."""
    runtime, run_id, _ = _make_delivery_run(tmp_path)
    constructor = FakeConstructor(escalate=True)
    writer = FakeWriter()
    factory = FakeReviewerFactory(rounds=[0])
    reviser = FakeReviser()
    integrity = FakeIntegrity()
    pipeline = _build_pipeline(
        runtime,
        constructor=constructor,
        writer=writer,
        reviewer_factory=factory,
        reviser=reviser,
        integrity=integrity,
    )
    result = pipeline.run(run_id)
    assert isinstance(result, ReportResearchReopenedResult)
    assert "state insufficient" in result.rationale


def test_brief_must_match_current_delivery_basis(tmp_path: Path):
    """Invariant 2: Brief freshness binds DeliveryBasis; mismatch is rejected."""
    runtime, run_id, basis = _make_delivery_run(tmp_path)

    from my_search_harness.domain.model import PartialAuthorizationBasis, CompletionPassBasis
    from datetime import datetime

    # Build a Brief against a DIFFERENT delivery_basis than the view's.
    wrong_basis = PartialAuthorizationBasis(
        basis_revision=99,
        basis_contract_revision=1,
        authorized_at=datetime(2026, 1, 1),
        rationale="stale",
    )
    assert wrong_basis != basis

    constructor = _StubConstructor(brief_basis=wrong_basis)
    writer = FakeWriter()
    factory = FakeReviewerFactory(rounds=[0])
    reviser = FakeReviser()
    integrity = FakeIntegrity()
    pipeline = _build_pipeline(
        runtime,
        constructor=constructor,
        writer=writer,
        reviewer_factory=factory,
        reviser=reviser,
        integrity=integrity,
    )
    with pytest.raises(ReportPipelineError, match="delivery_basis does not match"):
        pipeline.run(run_id)


def test_brief_unknown_research_refs_rejected(tmp_path: Path):
    runtime, run_id, basis = _make_delivery_run(tmp_path)
    constructor = FakeConstructor()
    # Tamper: inject an unknown research_ref into the Brief the constructor builds.
    original_construct = constructor.construct

    def patched_construct(view, writing_guideline, evidence, *, previous_brief=None, feedback=()):
        brief = original_construct(view, writing_guideline, evidence, previous_brief=previous_brief, feedback=feedback)
        tampered_section = replace(
            brief.sections[0],
            research_refs=("paper_does_not_exist",),
        )
        return replace(brief, sections=(tampered_section,))

    constructor.construct = patched_construct
    writer = FakeWriter()
    factory = FakeReviewerFactory(rounds=[0])
    reviser = FakeReviser()
    integrity = FakeIntegrity()
    pipeline = _build_pipeline(
        runtime,
        constructor=constructor,
        writer=writer,
        reviewer_factory=factory,
        reviser=reviser,
        integrity=integrity,
    )
    with pytest.raises(ReportPipelineError, match="unknown research refs"):
        pipeline.run(run_id)


def test_integrity_pass_with_issues_rejected(tmp_path: Path):
    """Invariant 4: PASS with issues is a malformed review (validator catches it)."""
    runtime, run_id, _ = _make_delivery_run(tmp_path)
    constructor = FakeConstructor()
    writer = FakeWriter()
    factory = FakeReviewerFactory(rounds=[0])
    reviser = FakeReviser()
    integrity = FakeIntegrity(
        dispositions=(IntegrityDisposition.PASS,),
        issues_seq=(("should not be here",),),
    )
    pipeline = _build_pipeline(
        runtime,
        constructor=constructor,
        writer=writer,
        reviewer_factory=factory,
        reviser=reviser,
        integrity=integrity,
    )
    with pytest.raises(ReportPipelineError, match="issues must match"):
        pipeline.run(run_id)


def test_integrity_pass_must_not_carry_repair_target(tmp_path: Path):
    runtime, run_id, _ = _make_delivery_run(tmp_path)
    constructor = FakeConstructor()
    writer = FakeWriter()
    factory = FakeReviewerFactory(rounds=[0])
    reviser = FakeReviser()
    integrity = FakeIntegrity(
        dispositions=(IntegrityDisposition.PASS,),
        repair_targets=("MANUSCRIPT",),
    )
    pipeline = _build_pipeline(
        runtime,
        constructor=constructor,
        writer=writer,
        reviewer_factory=factory,
        reviser=reviser,
        integrity=integrity,
    )
    with pytest.raises(ReportPipelineError, match="PASS must not carry a repair_target"):
        pipeline.run(run_id)


def test_delivery_inspect_source_requires_delivery_lifecycle(tmp_path: Path):
    """The Delivery inspect_source façade requires DELIVERY (not RESEARCH)."""
    runtime = LocalV1Runtime(
        tmp_path, paper_search_provider=None, source_access_provider=None
    )
    researcher = runtime.researcher
    create = researcher.create_run(
        CreateRunRequest(
            mission="m",
            requirements=("r",),
            scope="s",
            deliverable_description="d",
            required_artifacts=frozenset(),
        )
    )
    # In RESEARCH: delivery.inspect_source must be refused.
    with pytest.raises(CapabilityUnavailableError):
        runtime.delivery.inspect_source(create.run_id, create.state_revision, "paper_x")


def test_delivery_inspect_source_tracks_revision(tmp_path: Path):
    """DeliveryEvidenceAccess.inspect_source advances state_revision on success."""
    # This test needs a real provider that returns an outline. Use a stub.
    runtime, run_id, _ = _make_delivery_run(tmp_path)
    # Rebuild runtime with a stub source-access provider so inspect_source works.
    from my_search_harness.runtime.source_access import (
        SourceOutline,
        SourceOutlineEntry,
        SourceContent,
    )
    from my_search_harness.domain.model import PaperSource, SourceLocator

    class _StubProvider:
        def validate_inspect(self, source: PaperSource) -> None:
            return None

        def validate_read(self, source, locator) -> None:
            return None

        def inspect_source(self, paper_ref, source):
            return SourceOutline(
                paper_ref=paper_ref,
                sections=(
                    SourceOutlineEntry(
                        title="Results",
                        locator=SourceLocator(kind="section", value="Results"),
                    ),
                ),
            )

        def read_source(self, paper_ref, source, locator):
            return SourceContent(paper_ref=paper_ref, content="body")

    runtime2 = LocalV1Runtime(
        tmp_path, paper_search_provider=None, source_access_provider=_StubProvider()
    )
    # Drive the second runtime to DELIVERY too.
    researcher = runtime2.researcher
    create = researcher.create_run(
        CreateRunRequest(
            mission="m", requirements=("r",), scope="s",
            deliverable_description="d", required_artifacts=frozenset(),
        )
    )
    run_id = create.run_id
    retain = researcher.retain_papers(
        run_id, create.state_revision, (PaperSearchHit(title="P"),)
    )
    paper_ref = retain.paper_refs[0]
    researcher.apply_research_mutation(
        run_id,
        retain.state_revision,
        ResearchMutationBatch(
            puts=(
                PutPaperAnalysis(
                    paper_ref=paper_ref,
                    analysis=PaperAnalysis(
                        summary="s", relevance_to_run="r",
                    ),
                ),
            ),
        ),
    )
    req = researcher.request_completion_check(
        run_id, _rev_via_view(runtime2, run_id), "ok"
    )
    submit = runtime2.completion_checker.submit_completion_check(
        run_id, req.state_revision, req.completion_check_ref,
        CompletionVerdict.PASS, ("ok",),
    )
    view = runtime2.delivery.view(run_id)
    access = DeliveryEvidenceAccess(
        runtime2.delivery, run_id, view.state_revision
    )
    result = access.inspect_source(paper_ref)
    assert result.state_revision > view.state_revision
    assert access.state_revision == result.state_revision


def test_delivery_inspect_source_tracks_revision_on_failure(tmp_path: Path):
    """On SourceAccessAttemptError, the access object still advances state_revision."""
    runtime, run_id, _ = _make_delivery_run(tmp_path)
    from my_search_harness.domain.model import PaperSource

    class _FailingProvider:
        def validate_inspect(self, source: PaperSource) -> None:
            return None

        def validate_read(self, source, locator) -> None:
            return None

        def inspect_source(self, paper_ref, source):
            raise RuntimeError("provider down")

        def read_source(self, paper_ref, source, locator):
            raise RuntimeError("provider down")

    runtime2 = LocalV1Runtime(
        tmp_path, paper_search_provider=None, source_access_provider=_FailingProvider()
    )
    researcher = runtime2.researcher
    create = researcher.create_run(
        CreateRunRequest(
            mission="m", requirements=("r",), scope="s",
            deliverable_description="d", required_artifacts=frozenset(),
        )
    )
    run_id = create.run_id
    retain = researcher.retain_papers(
        run_id, create.state_revision, (PaperSearchHit(title="P"),)
    )
    paper_ref = retain.paper_refs[0]
    researcher.apply_research_mutation(
        run_id,
        retain.state_revision,
        ResearchMutationBatch(
            puts=(
                PutPaperAnalysis(
                    paper_ref=paper_ref,
                    analysis=PaperAnalysis(
                        summary="s", relevance_to_run="r",
                    ),
                ),
            ),
        ),
    )
    req = researcher.request_completion_check(
        run_id, _rev_via_view(runtime2, run_id), "ok"
    )
    submit = runtime2.completion_checker.submit_completion_check(
        run_id, req.state_revision, req.completion_check_ref,
        CompletionVerdict.PASS, ("ok",),
    )
    view = runtime2.delivery.view(run_id)
    access = DeliveryEvidenceAccess(
        runtime2.delivery, run_id, view.state_revision
    )
    before = access.state_revision
    with pytest.raises(SourceAccessAttemptError):
        access.inspect_source(paper_ref)
    assert access.state_revision > before, "revision must advance even on failure"


# ---------------------------------------------------------------------------
# Small helpers for the stub constructor used in basis-mismatch test.
# ---------------------------------------------------------------------------


@dataclass
class _StubConstructor:
    brief_basis: DeliveryBasis

    def construct(self, view, writing_guideline, evidence, *, previous_brief=None, feedback=()):
        paper_ref = view.papers[0].ref
        req_ref = view.contract.requirements[0].ref
        return ReportBrief(
            delivery_basis=self.brief_basis,
            audience="a",
            report_goal="g",
            reader_takeaway="t",
            sections=(
                ReportBriefSection(
                    title="S",
                    requirement_refs=(req_ref,),
                    research_refs=(paper_ref,),
                ),
            ),
        )
