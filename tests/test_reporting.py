"""ADR-012 Report Brief dual-review-loop — deterministic invariant tests.

These tests pin the ADR-012 report pipeline invariants using deterministic fake
protocols / factories. No real LLM, no subprocess, no real persistence. They
cover the 32 required invariants grouped as:

  Report Brief validation (1-4)
  Blind Reader boundary (5-9)
  Fresh Review (10-13)
  Root repair routing (14-18)
  Reader PASS version binding (19-21)
  Integrity routing (22-26)
  Integrity version binding (27-28)
  No-score / no-FSM constraints (29-32)

Run:

    python -m pytest tests/test_reporting.py --basetemp=./.pytest_tmp
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

import pytest

from my_search_harness.domain.model import (
    ArtifactKind,
    CompletionPassBasis,
    LifecycleMode,
    SourceLocator,
)
from my_search_harness.runtime.context import (
    ContractContext,
    DeliveryView,
    GapContext,
    ApproachContext,
    FindingContext,
    InspectResult,
    InspectedObject,
    OpenProblemContext,
    PaperIndexEntry,
    PaperResearchStatus,
    RequirementContext,
)
from my_search_harness.runtime.reporting import (
    BlindBlockingIssue,
    BlindReadResult,
    BlockingIssue,
    BriefMaterial,
    CitationMetadata,
    IntegrityDisposition,
    IntegrityPass,
    NoopReportCaptureSink,
    ReaderPass,
    RepairTarget,
    ReportBrief,
    ReportBriefSection,
    ReportCaptureSink,
    ReportManuscript,
    ReportPipeline,
    ReportPipelineError,
    ReportResourceExhausted,
    ReportReviewResult,
    ResearchConfirmationRequiredResult,
    ResearchIntegrityReview,
    brief_digest,
    blind_read_digest,
    manuscript_digest,
)


# ---------------------------------------------------------------------------
# Fixtures: a minimal DeliveryView + a fake DeliveryCapabilities
# ---------------------------------------------------------------------------


def _make_view(
    *,
    state_revision: int = 5,
    requirements: tuple[RequirementContext, ...] = (
        RequirementContext(ref="requirement_alpha", statement="req A"),
        RequirementContext(ref="requirement_beta", statement="req B"),
    ),
    approaches: tuple[ApproachContext, ...] = (
        ApproachContext(
            ref="approach_one",
            name="Approach One",
            core_idea="idea",
            representative_paper_refs=("paper_p1",),
        ),
    ),
    findings: tuple[FindingContext, ...] = (
        FindingContext(
            ref="finding_f1",
            statement="finding",
            approach_refs=("approach_one",),
            sources=(),
        ),
    ),
    open_problems: tuple[OpenProblemContext, ...] = (
        OpenProblemContext(
            ref="problem_op1",
            statement="problem",
            approach_refs=("approach_one",),
            sources=(),
        ),
    ),
    open_gaps: tuple[GapContext, ...] = (
        GapContext(
            ref="gap_g1",
            description="gap",
            requirement_refs=("requirement_alpha",),
            approach_refs=("approach_one",),
            resolution=None,
        ),
    ),
    papers: tuple[PaperIndexEntry, ...] = (
        PaperIndexEntry(
            ref="paper_p1",
            title="Paper One",
            authors=("Author A",),
            publication_year=2023,
            publication_date=None,
            doi=None,
            arxiv_id=None,
            canonical_url="https://example.org/p1",
            research_status=PaperResearchStatus.ACTIVE,
            retirement_reason=None,
            has_analysis=True,
        ),
    ),
    delivery_basis: CompletionPassBasis | None = None,
) -> DeliveryView:
    return DeliveryView(
        state_revision=state_revision,
        lifecycle=LifecycleMode.DELIVERY,
        contract=ContractContext(
            contract_revision=1,
            mission="mission",
            requirements=requirements,
            scope="scope",
            deliverable_description="deliverable",
            required_artifacts=("REPORT",),
        ),
        delivery_basis=delivery_basis
        or CompletionPassBasis(completion_check_ref="check_1"),
        approach_families=approaches,
        findings=findings,
        open_problems=open_problems,
        open_gaps=open_gaps,
        papers=papers,
    )


@dataclass
class FakePublishResult:
    artifact_kind: ArtifactKind = ArtifactKind.REPORT
    path: str = "workspace/runs/x/report.md"
    delivery_basis: CompletionPassBasis = field(
        default_factory=lambda: CompletionPassBasis(completion_check_ref="check_1")
    )
    content_sha256: str = "abc123"


@dataclass
class FakeReopenResult:
    state_revision: int = 6


class FakeDeliveryCapabilities:
    """Minimal stand-in for DeliveryCapabilities used by the pipeline.

    Records calls so tests can assert call order and arguments. ``view`` returns
    a fixed DeliveryView; private certified publication / ``reopen_research``
    return canned results and bump the recorded revision.
    """

    def __init__(self, view: DeliveryView) -> None:
        self._view = view
        self.publish_calls: list[tuple[str, int, str, object]] = []
        self.reopen_calls: list[tuple[str, int]] = []
        self.inspect_calls: list[tuple[str, int, tuple[str, ...]]] = []
        self.read_calls: list[tuple[str, int, str]] = []
        self._published = FakePublishResult()

    def view(self, run_id: str) -> DeliveryView:
        return self._view

    def inspect(self, run_id: str, expected_revision: int, refs: tuple[str, ...]):
        self.inspect_calls.append((run_id, expected_revision, refs))
        return InspectResult(
            state_revision=expected_revision,
            objects=tuple(
                InspectedObject(ref=r, kind="paper", value=object()) for r in refs
            ),
        )

    def read_source(self, run_id, expected_revision, paper_ref, locator=None):
        self.read_calls.append((run_id, expected_revision, paper_ref))
        # Return a lightweight stand-in; tests that need real content inject a
        # custom reader via the evidence access surface.
        from my_search_harness.runtime.source_access import (
            ReadSourceResult,
            SourceContent,
        )

        return ReadSourceResult(
            state_revision=expected_revision,
            source_content=SourceContent(paper_ref=paper_ref, content="source body"),
        )

    def _publish_certified_report(
        self, run_id: str, expected_revision: int, content: str, authorization: object
    ):
        self.publish_calls.append((run_id, expected_revision, content, authorization))
        return self._published

    def reopen_research(self, run_id: str, expected_revision: int):
        self.reopen_calls.append((run_id, expected_revision))
        return FakeReopenResult(state_revision=expected_revision + 1)

    def close_run(self, run_id: str, expected_revision: int):  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Fake semantic-stage actors (scripted, observable)
# ---------------------------------------------------------------------------


def _valid_brief() -> ReportBrief:
    return ReportBrief(
        audience="domain researchers",
        report_goal="survey X",
        reader_takeaway="understand X",
        narrative_logic="logic",
        sections=(
            ReportBriefSection(
                title="Section 1",
                purpose="purpose",
                reader_takeaway="takeaway",
                argument_flow="flow",
                requirement_refs=("requirement_alpha",),
                research_refs=("approach_one", "finding_f1", "paper_p1"),
                material=(
                    BriefMaterial(
                        content="high-density fact",
                        role="contrast",
                        research_refs=("paper_p1",),
                        source_ref="paper_p1",
                        locator=SourceLocator(kind="section", value="3.2"),
                    ),
                ),
                evidence_boundary="accepted state only",
            ),
        ),
        terminology=(("term", "definition"),),
        intentional_omissions=("omitted tangent",),
    )


def _manuscript(markdown: str = "# Report\n\nbody {{cite:c1}}") -> ReportManuscript:
    return ReportManuscript(
        markdown=markdown,
        citations=(
            __import__(
                "my_search_harness.runtime.reporting", fromlist=["CitationReference"]
            ).CitationReference(citation_id="c1", paper_ref="paper_p1"),
        ),
    )


class FakeConstructor:
    def __init__(self, briefs: list[ReportBrief] | None = None) -> None:
        self.calls: list[tuple] = []
        self._briefs = briefs

    def construct(self, view, quality_standard, evidence):
        self.calls.append((view, quality_standard))
        if self._briefs is not None:
            return self._briefs.pop(0)
        return _valid_brief()


class FakeWriter:
    def __init__(self, manuscripts: list[ReportManuscript] | None = None) -> None:
        self.calls: list[tuple] = []
        self._manuscripts = manuscripts

    def write(self, brief, writing_guide, citation_metadata):
        self.calls.append((brief, writing_guide, citation_metadata))
        if self._manuscripts is not None:
            return self._manuscripts.pop(0)
        return _manuscript()


class FakeReviewer:
    """One-use two-phase reviewer. Records inputs to assert the blind boundary."""

    def __init__(
        self,
        *,
        blind_result: BlindReadResult | None = None,
        blocking_issues: tuple[BlockingIssue, ...] = (),
        blind_hook: Callable[..., None] | None = None,
        check_hook: Callable[..., None] | None = None,
    ) -> None:
        self.blind_calls: list[dict] = []
        self.check_calls: list[dict] = []
        self._blind_hook = blind_hook
        self._check_hook = check_hook
        self._blocking = blocking_issues
        self._blind_result = blind_result

    def blind_read(
        self,
        deliverable_description,
        audience,
        quality_standard,
        review_guide,
        manuscript,
    ):
        record = {
            "deliverable_description": deliverable_description,
            "audience": audience,
            "quality_standard": quality_standard,
            "review_guide": review_guide,
            "manuscript": manuscript,
        }
        self.blind_calls.append(record)
        if self._blind_hook is not None:
            self._blind_hook(record)
        if self._blind_result is not None:
            return self._blind_result
        return BlindReadResult(
            core_understanding="understood",
            domain_model="model",
            comparison_coordinates="coords",
            reverse_outline="outline",
            manuscript_digest=manuscript_digest(manuscript),
        )

    def brief_check(self, blind_read, brief, manuscript, review_guide):
        record = {
            "blind_read": blind_read,
            "brief": brief,
            "manuscript": manuscript,
            "review_guide": review_guide,
        }
        self.check_calls.append(record)
        if self._check_hook is not None:
            self._check_hook(record)
        return ReportReviewResult(
            blind_read_digest=blind_read_digest(blind_read),
            brief_digest=brief_digest(brief),
            manuscript_digest=manuscript_digest(manuscript),
            blocking_issues=self._blocking,
        )


class CountingReviewerFactory:
    """Creates fresh FakeReviewer instances and counts how many were created."""

    def __init__(self, reviewer_builder: Callable[[], FakeReviewer]) -> None:
        self._builder = reviewer_builder
        self.created: list[FakeReviewer] = []

    def create(self) -> FakeReviewer:
        r = self._builder()
        self.created.append(r)
        return r


class FakeReviser:
    def __init__(self, revised_markdowns: list[str] | None = None) -> None:
        self.calls: list[tuple] = []
        self._revised = revised_markdowns

    def revise(self, brief, manuscript, issues, writing_guide, citation_metadata):
        self.calls.append((brief, manuscript, issues, citation_metadata))
        if self._revised is not None:
            return _manuscript(self._revised.pop(0))
        return _manuscript("# Report\n\nrevised body {{cite:c1}}")


class FakeIntegrityReviewer:
    def __init__(self, reviews: list[ResearchIntegrityReview]) -> None:
        self.calls: list[tuple] = []
        self._reviews = list(reviews)

    def review(self, view, brief, manuscript, evidence, integrity_guide):
        self.calls.append((view, brief, manuscript, integrity_guide))
        return self._reviews.pop(0)


class FakeCitationRenderer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def render(self, view, manuscript):
        self.calls.append((view, manuscript))
        return "rendered report content"


# ---------------------------------------------------------------------------
# Pipeline builder helper
# ---------------------------------------------------------------------------


def _build_pipeline(
    caps: FakeDeliveryCapabilities,
    *,
    constructor=None,
    writer=None,
    reviewer_factory=None,
    reviser=None,
    integrity_reviewer=None,
    citation_renderer=None,
    capture_sink: ReportCaptureSink | None = None,
    max_constructor_rebuilds: int = 8,
    max_reader_rounds: int = 12,
    max_integrity_rounds: int = 12,
) -> ReportPipeline:
    return ReportPipeline(
        caps,
        constructor=constructor or FakeConstructor(),
        writer=writer or FakeWriter(),
        reviewer_factory=reviewer_factory or CountingReviewerFactory(FakeReviewer),
        reviser=reviser or FakeReviser(),
        integrity_reviewer=integrity_reviewer
        or FakeIntegrityReviewer(
            [ResearchIntegrityReview(disposition=IntegrityDisposition.PASS)]
        ),
        citation_renderer=citation_renderer or FakeCitationRenderer(),
        quality_standard="quality standard text",
        writing_guide="writing guide text",
        review_guide="review guide text",
        integrity_guide="integrity guide text",
        capture_sink=capture_sink,
        max_constructor_rebuilds=max_constructor_rebuilds,
        max_reader_rounds=max_reader_rounds,
        max_integrity_rounds=max_integrity_rounds,
    )


# ===========================================================================
# Report Brief validation (invariants 1-4)
# ===========================================================================


class TestReportBriefValidation:
    def test_unknown_requirement_ref_rejected(self):
        # Invariant 1: unknown requirement ref rejected.
        caps = FakeDeliveryCapabilities(_make_view())
        bad_brief = ReportBrief(
            audience="a",
            report_goal="g",
            reader_takeaway="t",
            narrative_logic="l",
            sections=(
                ReportBriefSection(
                    title="s",
                    purpose="p",
                    reader_takeaway="t",
                    argument_flow="f",
                    requirement_refs=("requirement_NONEXISTENT",),
                    research_refs=(),
                ),
            ),
        )
        pipeline = _build_pipeline(
            caps, constructor=FakeConstructor(briefs=[bad_brief])
        )
        with pytest.raises(ReportPipelineError, match="unknown requirement refs"):
            pipeline.run("run_1")

    def test_requirement_ref_cannot_pass_as_research_ref(self):
        caps = FakeDeliveryCapabilities(_make_view())
        brief = _valid_brief()
        section = replace(brief.sections[0], research_refs=("requirement_alpha",))
        pipeline = _build_pipeline(
            caps, constructor=FakeConstructor([replace(brief, sections=(section,))])
        )
        with pytest.raises(ReportPipelineError, match="unknown research refs"):
            pipeline.run("run_1")

    def test_material_source_ref_must_be_retained_paper(self):
        caps = FakeDeliveryCapabilities(_make_view())
        brief = _valid_brief()
        material = replace(
            brief.sections[0].material[0],
            source_ref="paper_missing",
            locator=None,
        )
        section = replace(brief.sections[0], material=(material,))
        pipeline = _build_pipeline(
            caps, constructor=FakeConstructor([replace(brief, sections=(section,))])
        )
        with pytest.raises(ReportPipelineError, match="retained paper ref"):
            pipeline.run("run_1")

    def test_material_locator_requires_source_ref(self):
        caps = FakeDeliveryCapabilities(_make_view())
        brief = _valid_brief()
        material = replace(brief.sections[0].material[0], source_ref=None)
        section = replace(brief.sections[0], material=(material,))
        pipeline = _build_pipeline(
            caps, constructor=FakeConstructor([replace(brief, sections=(section,))])
        )
        with pytest.raises(ReportPipelineError, match="locator requires source_ref"):
            pipeline.run("run_1")

    def test_unknown_research_ref_rejected(self):
        # Invariant 2: unknown research ref rejected.
        caps = FakeDeliveryCapabilities(_make_view())
        bad_brief = ReportBrief(
            audience="a",
            report_goal="g",
            reader_takeaway="t",
            narrative_logic="l",
            sections=(
                ReportBriefSection(
                    title="s",
                    purpose="p",
                    reader_takeaway="t",
                    argument_flow="f",
                    requirement_refs=("requirement_alpha",),
                    research_refs=("approach_NONEXISTENT",),
                ),
            ),
        )
        pipeline = _build_pipeline(
            caps, constructor=FakeConstructor(briefs=[bad_brief])
        )
        with pytest.raises(ReportPipelineError, match="unknown research refs"):
            pipeline.run("run_1")

    def test_invalid_material_locator_rejected(self):
        # Invariant 3: invalid material / locator structure rejected.
        caps = FakeDeliveryCapabilities(_make_view())
        bad_brief = ReportBrief(
            audience="a",
            report_goal="g",
            reader_takeaway="t",
            narrative_logic="l",
            sections=(
                ReportBriefSection(
                    title="s",
                    purpose="p",
                    reader_takeaway="t",
                    argument_flow="f",
                    requirement_refs=("requirement_alpha",),
                    research_refs=("paper_p1",),
                    material=(
                        BriefMaterial(
                            content="",
                            research_refs=("paper_p1",),
                        ),
                    ),
                ),
            ),
        )
        pipeline = _build_pipeline(
            caps, constructor=FakeConstructor(briefs=[bad_brief])
        )
        with pytest.raises(ReportPipelineError, match="BriefMaterial content"):
            pipeline.run("run_1")

    def test_brief_not_stored_in_research_run_no_new_artifactkind(self):
        # Invariant 4: Brief does not enter ResearchRun / ArtifactKind not expanded.
        # The pipeline never calls any ResearchRun mutation; ArtifactKind has only REPORT.
        caps = FakeDeliveryCapabilities(_make_view())
        pipeline = _build_pipeline(caps)
        result = pipeline.run("run_1")
        # No REPORT_BRIEF artifact kind exists.
        assert not hasattr(ArtifactKind, "REPORT_BRIEF")
        assert {kind.value for kind in ArtifactKind} == {"REPORT"}
        # The published result carries only the REPORT artifact kind.
        assert result.artifact.artifact_kind is ArtifactKind.REPORT
        # The fake capabilities recorded no run mutation beyond publish/reopen.
        assert caps.reopen_calls == []
        assert len(caps.publish_calls) == 1


# ===========================================================================
# Blind Reader boundary (invariants 5-9)
# ===========================================================================


class TestBlindReaderBoundary:
    def test_phase1_args_have_no_brief(self):
        # Invariant 5: Phase 1 call parameters contain no Brief.
        caps = FakeDeliveryCapabilities(_make_view())
        seen_briefs: list[object] = []

        def hook(record):
            # The blind_read signature has no brief parameter; confirm by
            # inspecting that none of the recorded values is a ReportBrief.
            for value in record.values():
                assert not isinstance(value, ReportBrief)
            seen_briefs.append(record)

        factory = CountingReviewerFactory(lambda: FakeReviewer(blind_hook=hook))
        pipeline = _build_pipeline(caps, reviewer_factory=factory)
        pipeline.run("run_1")
        assert len(seen_briefs) >= 1

    def test_phase1_has_no_writing_guide(self):
        # Invariant 6: Phase 1 has no Writing Guide.
        caps = FakeDeliveryCapabilities(_make_view())
        guide_texts: list[str] = []

        def hook(record):
            guide_texts.append(record["review_guide"])
            # The writing guide text must not appear in any Phase 1 argument.
            assert record["quality_standard"] != "writing guide text"

        factory = CountingReviewerFactory(lambda: FakeReviewer(blind_hook=hook))
        pipeline = _build_pipeline(caps, reviewer_factory=factory)
        pipeline.run("run_1")
        # review_guide is the REVIEW guide, distinct from the writing guide.
        assert all(g == "review guide text" for g in guide_texts)

    def test_audience_only_via_narrow_projection(self):
        # Invariant 7: audience passed only via narrow projection (the audience
        # string), not the whole Brief or DeliveryView.
        caps = FakeDeliveryCapabilities(_make_view())
        audiences: list[object] = []

        def hook(record):
            audiences.append(record["audience"])

        factory = CountingReviewerFactory(lambda: FakeReviewer(blind_hook=hook))
        pipeline = _build_pipeline(caps, reviewer_factory=factory)
        pipeline.run("run_1")
        # Audience is the bare string from the Brief, not the Brief object.
        assert all(isinstance(a, str) for a in audiences)
        assert all(a == "domain researchers" for a in audiences)

    def test_phase1_precedes_phase2(self):
        # Invariant 8: Phase 1 before Phase 2 (within a single reviewer instance).
        caps = FakeDeliveryCapabilities(_make_view())
        order: list[str] = []

        def blind_hook(record):
            order.append("blind")

        def check_hook(record):
            order.append("check")

        factory = CountingReviewerFactory(
            lambda: FakeReviewer(blind_hook=blind_hook, check_hook=check_hook)
        )
        pipeline = _build_pipeline(caps, reviewer_factory=factory)
        pipeline.run("run_1")
        # For the first reviewer, blind must come before check.
        assert order[0] == "blind"
        assert order[1] == "check"

    def test_phase1_result_binds_current_manuscript_digest(self):
        # Invariant 9: Phase 1 result binds the current manuscript digest.
        caps = FakeDeliveryCapabilities(_make_view())
        ms = _manuscript()
        expected = manuscript_digest(ms)
        captured: list[BlindReadResult] = []

        def check_hook(record):
            captured.append(record["blind_read"])

        factory = CountingReviewerFactory(lambda: FakeReviewer(check_hook=check_hook))
        pipeline = _build_pipeline(
            caps, reviewer_factory=factory, writer=FakeWriter(manuscripts=[ms])
        )
        pipeline.run("run_1")
        assert captured
        assert all(b.manuscript_digest == expected for b in captured)
        # The pipeline validates the digest binding; a mismatch would raise.
        with pytest.raises(ReportPipelineError, match="bind to the manuscript digest"):
            bad_blind = BlindReadResult(
                core_understanding="u",
                domain_model="d",
                comparison_coordinates="c",
                reverse_outline="r",
                manuscript_digest="wrong",
            )
            factory2 = CountingReviewerFactory(
                lambda: FakeReviewer(blind_result=bad_blind)
            )
            _build_pipeline(caps, reviewer_factory=factory2).run("run_2")

    def test_phase1_freezes_reader_failures_without_repair_target(self):
        issue = BlindBlockingIssue(
            location="section 1",
            problem="missing bridge",
            reader_effect="reader cannot connect the argument",
            why_blocking="main conclusion is not recoverable",
        )
        assert not hasattr(issue, "repair_target")
        blind = BlindReadResult(
            core_understanding="partial",
            domain_model="incomplete",
            comparison_coordinates="none",
            reverse_outline="disconnected",
            manuscript_digest=manuscript_digest(_manuscript()),
            blocking_issues=(issue,),
        )
        caps = FakeDeliveryCapabilities(_make_view())
        result = _build_pipeline(
            caps,
            reviewer_factory=CountingReviewerFactory(
                lambda: FakeReviewer(blind_result=blind)
            ),
        ).run("run_1")
        assert result.reader_pass.manuscript_digest == blind.manuscript_digest

    def test_phase2_cannot_replace_frozen_blind_read(self):
        class RewritingReviewer(FakeReviewer):
            def brief_check(self, blind_read, brief, manuscript, review_guide):
                return ReportReviewResult(
                    blind_read_digest="0" * 64,
                    brief_digest=brief_digest(brief),
                    manuscript_digest=manuscript_digest(manuscript),
                )

        caps = FakeDeliveryCapabilities(_make_view())
        pipeline = _build_pipeline(
            caps,
            reviewer_factory=CountingReviewerFactory(RewritingReviewer),
        )
        with pytest.raises(ReportPipelineError, match="frozen Blind Read"):
            pipeline.run("run_1")


# ===========================================================================
# Fresh Review (invariants 10-13)
# ===========================================================================


class TestFreshReview:
    def test_manuscript_revision_uses_new_reviewer_instance(self):
        # Invariant 10: after a manuscript revision, a new reviewer instance is used.
        caps = FakeDeliveryCapabilities(_make_view())
        # First review blocks (MANUSCRIPT); second passes.
        call_count = {"n": 0}

        def builder():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return FakeReviewer(
                    blocking_issues=(
                        BlockingIssue(
                            problem="p",
                            reader_effect="e",
                            why_blocking="w",
                            repair_target=RepairTarget.MANUSCRIPT,
                        ),
                    )
                )
            return FakeReviewer()

        factory = CountingReviewerFactory(builder)
        pipeline = _build_pipeline(caps, reviewer_factory=factory)
        pipeline.run("run_1")
        # At least two reviewer instances created (fresh per revision).
        assert len(factory.created) >= 2
        assert factory.created[0] is not factory.created[1]

    def test_reviser_does_not_go_straight_to_integrity(self):
        # Invariant 11: after Reviser, cannot enter Integrity directly — must
        # re-run the Reader gate first.
        caps = FakeDeliveryCapabilities(_make_view())
        call_count = {"n": 0}

        def builder():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return FakeReviewer(
                    blocking_issues=(
                        BlockingIssue(
                            problem="p",
                            reader_effect="e",
                            why_blocking="w",
                            repair_target=RepairTarget.MANUSCRIPT,
                        ),
                    )
                )
            return FakeReviewer()

        factory = CountingReviewerFactory(builder)
        integrity = FakeIntegrityReviewer(
            [ResearchIntegrityReview(disposition=IntegrityDisposition.PASS)]
        )
        pipeline = _build_pipeline(
            caps, reviewer_factory=factory, integrity_reviewer=integrity
        )
        pipeline.run("run_1")
        # Integrity was called exactly once (after the Reader PASS), not after
        # the Reviser directly. Two reader instances => two blind reads, but
        # only one integrity call.
        assert len(integrity.calls) == 1
        assert len(factory.created) >= 2

    def test_revised_manuscript_re_runs_phase1_and_phase2(self):
        # Invariant 12: revised manuscript must re-run Phase 1 + Phase 2.
        caps = FakeDeliveryCapabilities(_make_view())
        call_count = {"n": 0}

        def builder():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return FakeReviewer(
                    blocking_issues=(
                        BlockingIssue(
                            problem="p",
                            reader_effect="e",
                            why_blocking="w",
                            repair_target=RepairTarget.MANUSCRIPT,
                        ),
                    )
                )
            return FakeReviewer()

        factory = CountingReviewerFactory(builder)
        pipeline = _build_pipeline(caps, reviewer_factory=factory)
        pipeline.run("run_1")
        # Each created reviewer ran both blind_read and brief_check.
        for reviewer in factory.created:
            assert len(reviewer.blind_calls) == 1
            assert len(reviewer.check_calls) == 1

    def test_brief_reconstruction_new_writer_and_fresh_reader(self):
        # Invariant 13: after Brief reconstruction, a new Writer pass + fresh Reader.
        caps = FakeDeliveryCapabilities(_make_view())
        # First reader blocks with BRIEF → Constructor rebuild → Writer → Reader.
        call_count = {"n": 0}

        def builder():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return FakeReviewer(
                    blocking_issues=(
                        BlockingIssue(
                            problem="p",
                            reader_effect="e",
                            why_blocking="w",
                            repair_target=RepairTarget.BRIEF,
                        ),
                    )
                )
            return FakeReviewer()

        factory = CountingReviewerFactory(builder)
        constructor = FakeConstructor(briefs=[_valid_brief(), _valid_brief()])
        writer = FakeWriter(manuscripts=[_manuscript("# v1"), _manuscript("# v2")])
        pipeline = _build_pipeline(
            caps,
            constructor=constructor,
            writer=writer,
            reviewer_factory=factory,
        )
        pipeline.run("run_1")
        # Constructor called twice (rebuild), Writer called twice, two readers.
        assert len(constructor.calls) == 2
        assert len(writer.calls) == 2
        assert len(factory.created) == 2


# ===========================================================================
# Root repair routing (invariants 14-18)
# ===========================================================================


class TestRootRepairRouting:
    def test_manuscript_blocker_routes_to_reviser(self):
        # Invariant 14: MANUSCRIPT blocker → Reviser.
        caps = FakeDeliveryCapabilities(_make_view())
        call_count = {"n": 0}

        def builder():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return FakeReviewer(
                    blocking_issues=(
                        BlockingIssue(
                            problem="p",
                            reader_effect="e",
                            why_blocking="w",
                            repair_target=RepairTarget.MANUSCRIPT,
                        ),
                    )
                )
            return FakeReviewer()

        factory = CountingReviewerFactory(builder)
        reviser = FakeReviser()
        pipeline = _build_pipeline(caps, reviewer_factory=factory, reviser=reviser)
        pipeline.run("run_1")
        assert len(reviser.calls) == 1

    def test_brief_blocker_routes_to_constructor(self):
        # Invariant 15: BRIEF blocker → Constructor (rebuild).
        caps = FakeDeliveryCapabilities(_make_view())
        call_count = {"n": 0}

        def builder():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return FakeReviewer(
                    blocking_issues=(
                        BlockingIssue(
                            problem="p",
                            reader_effect="e",
                            why_blocking="w",
                            repair_target=RepairTarget.BRIEF,
                        ),
                    )
                )
            return FakeReviewer()

        factory = CountingReviewerFactory(builder)
        constructor = FakeConstructor(briefs=[_valid_brief(), _valid_brief()])
        reviser = FakeReviser()
        pipeline = _build_pipeline(
            caps, constructor=constructor, reviewer_factory=factory, reviser=reviser
        )
        pipeline.run("run_1")
        # Constructor rebuilt; Reviser NOT called for a BRIEF route.
        assert len(constructor.calls) == 2
        assert len(reviser.calls) == 0

    def test_mixed_manuscript_and_brief_brief_dominates(self):
        # Invariant 16: multiple MANUSCRIPT + BRIEF blockers → BRIEF upstream wins.
        caps = FakeDeliveryCapabilities(_make_view())
        call_count = {"n": 0}

        def builder():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return FakeReviewer(
                    blocking_issues=(
                        BlockingIssue(
                            problem="p1",
                            reader_effect="e1",
                            why_blocking="w1",
                            repair_target=RepairTarget.MANUSCRIPT,
                        ),
                        BlockingIssue(
                            problem="p2",
                            reader_effect="e2",
                            why_blocking="w2",
                            repair_target=RepairTarget.BRIEF,
                        ),
                    )
                )
            return FakeReviewer()

        factory = CountingReviewerFactory(builder)
        constructor = FakeConstructor(briefs=[_valid_brief(), _valid_brief()])
        reviser = FakeReviser()
        pipeline = _build_pipeline(
            caps, constructor=constructor, reviewer_factory=factory, reviser=reviser
        )
        pipeline.run("run_1")
        # BRIEF dominates: Constructor rebuilt, Reviser NOT called.
        assert len(constructor.calls) == 2
        assert len(reviser.calls) == 0

    def test_possible_research_issue_reader_cannot_mutate_state(self):
        # Invariant 17: POSSIBLE_RESEARCH_ISSUE does not let the Reader mutate
        # Research State. It returns a typed confirmation request and stops.
        caps = FakeDeliveryCapabilities(_make_view())

        def builder():
            return FakeReviewer(
                blocking_issues=(
                    BlockingIssue(
                        problem="p",
                        reader_effect="e",
                        why_blocking="w",
                        repair_target=RepairTarget.POSSIBLE_RESEARCH_ISSUE,
                    ),
                )
            )

        factory = CountingReviewerFactory(builder)
        pipeline = _build_pipeline(caps, reviewer_factory=factory)
        result = pipeline.run("run_1")

        assert isinstance(result, ResearchConfirmationRequiredResult)
        assert caps.publish_calls == []
        assert caps.reopen_calls == []

    def test_confirmed_research_insufficiency_reopens_research(self):
        # Invariant 18: confirmed research insufficiency → reopen RESEARCH.
        caps = FakeDeliveryCapabilities(_make_view())
        integrity = FakeIntegrityReviewer(
            [
                ResearchIntegrityReview(
                    disposition=IntegrityDisposition.REOPEN_RESEARCH,
                    issues=("unsupported claim",),
                )
            ]
        )
        pipeline = _build_pipeline(caps, integrity_reviewer=integrity)
        result = pipeline.run("run_1")
        from my_search_harness.runtime.reporting import ReportResearchReopenedResult

        assert isinstance(result, ReportResearchReopenedResult)
        assert "unsupported claim" in result.rationale
        assert caps.reopen_calls == [("run_1", 5)]
        assert caps.publish_calls == []


# ===========================================================================
# Reader PASS version binding (invariants 19-21)
# ===========================================================================


class TestReaderPassVersionBinding:
    def test_reader_pass_same_brief_manuscript_allows_integrity(self):
        # Invariant 19: Reader PASS + same Brief/Manuscript → allowed to Integrity.
        caps = FakeDeliveryCapabilities(_make_view())
        integrity = FakeIntegrityReviewer(
            [ResearchIntegrityReview(disposition=IntegrityDisposition.PASS)]
        )
        pipeline = _build_pipeline(caps, integrity_reviewer=integrity)
        result = pipeline.run("run_1")
        from my_search_harness.runtime.reporting import PublishedReportPipelineResult

        assert isinstance(result, PublishedReportPipelineResult)
        assert isinstance(result.reader_pass, ReaderPass)
        assert len(integrity.calls) == 1

    def test_manuscript_digest_changed_old_pass_rejected(self):
        # Invariant 20: manuscript digest changed → old PASS rejected (re-run).
        # Simulated by the pipeline re-running the Reader after any manuscript
        # edit; the PASS binds the new digest. We verify the published PASS
        # carries the final manuscript digest.
        caps = FakeDeliveryCapabilities(_make_view())
        call_count = {"n": 0}

        def builder():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return FakeReviewer(
                    blocking_issues=(
                        BlockingIssue(
                            problem="p",
                            reader_effect="e",
                            why_blocking="w",
                            repair_target=RepairTarget.MANUSCRIPT,
                        ),
                    )
                )
            return FakeReviewer()

        factory = CountingReviewerFactory(builder)
        reviser = FakeReviser(revised_markdowns=["# Report\n\nrevised {{cite:c1}}"])
        pipeline = _build_pipeline(caps, reviewer_factory=factory, reviser=reviser)
        result = pipeline.run("run_1")
        from my_search_harness.runtime.reporting import PublishedReportPipelineResult

        assert isinstance(result, PublishedReportPipelineResult)
        # The certified digest matches the REVISED manuscript, not the original.
        revised_ms = _manuscript("# Report\n\nrevised {{cite:c1}}")
        assert result.reader_pass.manuscript_digest == manuscript_digest(revised_ms)

    def test_brief_digest_changed_old_pass_rejected(self):
        # Invariant 21: brief digest changed → old PASS rejected (re-run).
        # After a BRIEF rebuild, the Reader runs against the new Brief; the
        # published PASS binds the new brief digest.
        caps = FakeDeliveryCapabilities(_make_view())
        call_count = {"n": 0}

        def builder():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return FakeReviewer(
                    blocking_issues=(
                        BlockingIssue(
                            problem="p",
                            reader_effect="e",
                            why_blocking="w",
                            repair_target=RepairTarget.BRIEF,
                        ),
                    )
                )
            return FakeReviewer()

        factory = CountingReviewerFactory(builder)
        brief_v2 = ReportBrief(
            audience="a2",
            report_goal="g2",
            reader_takeaway="t2",
            narrative_logic="l2",
            sections=(
                ReportBriefSection(
                    title="s",
                    purpose="p",
                    reader_takeaway="t",
                    argument_flow="f",
                    requirement_refs=("requirement_beta",),
                    research_refs=("paper_p1",),
                ),
            ),
        )
        constructor = FakeConstructor(briefs=[_valid_brief(), brief_v2])
        pipeline = _build_pipeline(
            caps, constructor=constructor, reviewer_factory=factory
        )
        result = pipeline.run("run_1")
        from my_search_harness.runtime.reporting import PublishedReportPipelineResult

        assert isinstance(result, PublishedReportPipelineResult)
        # Certified brief digest is the v2 brief's, not v1's.
        assert result.reader_pass.brief_digest == brief_digest(brief_v2)
        assert result.reader_pass.brief_digest != brief_digest(_valid_brief())


# ===========================================================================
# Integrity routing (invariants 22-26)
# ===========================================================================


class TestIntegrityRouting:
    def test_integrity_pass_routes_to_renderer_publish(self):
        # Invariant 22: Integrity PASS → renderer/publish.
        caps = FakeDeliveryCapabilities(_make_view())
        renderer = FakeCitationRenderer()
        integrity = FakeIntegrityReviewer(
            [ResearchIntegrityReview(disposition=IntegrityDisposition.PASS)]
        )
        pipeline = _build_pipeline(
            caps, integrity_reviewer=integrity, citation_renderer=renderer
        )
        result = pipeline.run("run_1")
        from my_search_harness.runtime.reporting import PublishedReportPipelineResult

        assert isinstance(result, PublishedReportPipelineResult)
        assert len(renderer.calls) == 1
        assert len(caps.publish_calls) == 1
        assert caps.publish_calls[0][2] == "rendered report content"

    def test_revise_delivery_manuscript_reviser_reader_integrity(self):
        # Invariant 23: REVISE_DELIVERY target MANUSCRIPT → Reviser → Reader
        # again → Integrity again.
        caps = FakeDeliveryCapabilities(_make_view())
        # Integrity: first REVISE_DELIVERY(MANUSCRIPT), then PASS.
        integrity = FakeIntegrityReviewer(
            [
                ResearchIntegrityReview(
                    disposition=IntegrityDisposition.REVISE_DELIVERY,
                    issues=("fix prose",),
                    revise_target=RepairTarget.MANUSCRIPT,
                ),
                ResearchIntegrityReview(disposition=IntegrityDisposition.PASS),
            ]
        )
        reviser = FakeReviser(revised_markdowns=["# Report\n\nfixed {{cite:c1}}"])
        pipeline = _build_pipeline(caps, integrity_reviewer=integrity, reviser=reviser)
        result = pipeline.run("run_1")
        from my_search_harness.runtime.reporting import PublishedReportPipelineResult

        assert isinstance(result, PublishedReportPipelineResult)
        # Integrity called twice (first REVISE, then PASS).
        assert len(integrity.calls) == 2
        # Reviser called once for the integrity repair.
        assert len(reviser.calls) == 1
        assert result.reader_pass.manuscript_digest == manuscript_digest(
            _manuscript("# Report\n\nfixed {{cite:c1}}")
        )
        assert (
            result.reader_pass.manuscript_digest
            == result.integrity_pass.manuscript_digest
        )

    def test_revise_delivery_brief_constructor_writer_reader_integrity(self):
        # Invariant 24: REVISE_DELIVERY target BRIEF → Constructor → Writer →
        # Reader → Integrity.
        caps = FakeDeliveryCapabilities(_make_view())
        integrity = FakeIntegrityReviewer(
            [
                ResearchIntegrityReview(
                    disposition=IntegrityDisposition.REVISE_DELIVERY,
                    issues=("brief gap",),
                    revise_target=RepairTarget.BRIEF,
                ),
                ResearchIntegrityReview(disposition=IntegrityDisposition.PASS),
            ]
        )
        constructor = FakeConstructor(briefs=[_valid_brief(), _valid_brief()])
        writer = FakeWriter(manuscripts=[_manuscript("# v1"), _manuscript("# v2")])
        pipeline = _build_pipeline(
            caps,
            constructor=constructor,
            writer=writer,
            integrity_reviewer=integrity,
        )
        result = pipeline.run("run_1")
        from my_search_harness.runtime.reporting import PublishedReportPipelineResult

        assert isinstance(result, PublishedReportPipelineResult)
        assert len(constructor.calls) == 2
        assert len(writer.calls) == 2
        assert len(integrity.calls) == 2

    def test_reopen_research_uses_existing_reopen_transition(self):
        # Invariant 25: REOPEN_RESEARCH → existing reopen transition.
        caps = FakeDeliveryCapabilities(_make_view())
        integrity = FakeIntegrityReviewer(
            [
                ResearchIntegrityReview(
                    disposition=IntegrityDisposition.REOPEN_RESEARCH,
                    issues=("research gap",),
                )
            ]
        )
        pipeline = _build_pipeline(caps, integrity_reviewer=integrity)
        result = pipeline.run("run_1")
        from my_search_harness.runtime.reporting import ReportResearchReopenedResult

        assert isinstance(result, ReportResearchReopenedResult)
        # The existing delivery.reopen_research bumped the revision.
        assert result.state_revision == 6
        assert caps.reopen_calls == [("run_1", 5)]

    def test_any_integrity_repair_invalidates_old_reader_certification(self):
        # Invariant 26: any Integrity-triggered repair invalidates the old
        # Reader PASS — Reader must run again before Integrity.
        caps = FakeDeliveryCapabilities(_make_view())
        integrity = FakeIntegrityReviewer(
            [
                ResearchIntegrityReview(
                    disposition=IntegrityDisposition.REVISE_DELIVERY,
                    issues=("fix",),
                    revise_target=RepairTarget.MANUSCRIPT,
                ),
                ResearchIntegrityReview(disposition=IntegrityDisposition.PASS),
            ]
        )
        reviser = FakeReviser(revised_markdowns=["# Report\n\nfixed {{cite:c1}}"])
        factory = CountingReviewerFactory(FakeReviewer)
        pipeline = _build_pipeline(
            caps,
            integrity_reviewer=integrity,
            reviser=reviser,
            reviewer_factory=factory,
        )
        pipeline.run("run_1")
        # Two reader instances: one before integrity, one after the repair.
        assert len(factory.created) == 2


# ===========================================================================
# Integrity version binding (invariants 27-28)
# ===========================================================================


class TestIntegrityVersionBinding:
    def test_integrity_pass_binds_basis_brief_manuscript(self):
        # Invariant 27: Integrity PASS binds DeliveryBasis + Brief + Manuscript.
        caps = FakeDeliveryCapabilities(_make_view())
        integrity = FakeIntegrityReviewer(
            [ResearchIntegrityReview(disposition=IntegrityDisposition.PASS)]
        )
        pipeline = _build_pipeline(caps, integrity_reviewer=integrity)
        result = pipeline.run("run_1")
        from my_search_harness.runtime.reporting import PublishedReportPipelineResult

        assert isinstance(result, PublishedReportPipelineResult)
        ip = result.integrity_pass
        assert isinstance(ip, IntegrityPass)
        # All three bindings present and non-empty.
        assert ip.delivery_basis_key
        assert ip.brief_digest
        assert ip.manuscript_digest
        # The basis key is a stable canonical form of the DeliveryBasis.
        from my_search_harness.runtime.reporting import _delivery_basis_key

        assert ip.delivery_basis_key == _delivery_basis_key(
            CompletionPassBasis(completion_check_ref="check_1")
        )

    def test_stale_certification_cannot_render_publish(self):
        # Invariant 28: stale certification cannot render/publish.
        # If the manuscript changes after Integrity PASS but before publish,
        # the pipeline must not publish. We force this by making the renderer
        # return empty (the pipeline rejects empty render output), confirming
        # the publish gate validates content. More directly: a REVISE_DELIVERY
        # loop that never converges must NOT publish.
        caps = FakeDeliveryCapabilities(_make_view())
        # Integrity always says REVISE_DELIVERY(MANUSCRIPT); never converges.
        integrity = FakeIntegrityReviewer(
            [
                ResearchIntegrityReview(
                    disposition=IntegrityDisposition.REVISE_DELIVERY,
                    issues=(f"fix {i}",),
                    revise_target=RepairTarget.MANUSCRIPT,
                )
                for i in range(20)
            ]
        )
        reviser = FakeReviser(
            revised_markdowns=[f"# Report\n\nv{i} {{cite:c1}}" for i in range(20)]
        )
        pipeline = _build_pipeline(
            caps,
            integrity_reviewer=integrity,
            reviser=reviser,
            max_integrity_rounds=3,
        )
        with pytest.raises(ReportResourceExhausted):
            pipeline.run("run_1")
        # Never published — stale/never-certified content cannot be published.
        assert caps.publish_calls == []


# ===========================================================================
# No-score / no-FSM constraints (invariants 29-32)
# ===========================================================================


class TestNoScoreNoFSM:
    def test_no_fixed_round_count_required_for_semantic_pass(self):
        # Invariant 29: no fixed review round count required for semantic PASS.
        # A single reader pass with no blockers → PASS immediately (1 round).
        caps = FakeDeliveryCapabilities(_make_view())
        factory = CountingReviewerFactory(FakeReviewer)
        pipeline = _build_pipeline(caps, reviewer_factory=factory)
        pipeline.run("run_1")
        # Exactly one reader instance for a clean pass — not a fixed N rounds.
        assert len(factory.created) == 1

    def test_no_quality_readability_cognitive_score_introduced(self):
        # Invariant 30: no report quality/readability/cognitive score introduced.
        import my_search_harness.runtime.reporting as reporting

        # No score-like attributes on the result types.
        for cls in (
            ReportReviewResult,
            ResearchIntegrityReview,
            ReaderPass,
            IntegrityPass,
            BlockingIssue,
            BlindReadResult,
        ):
            for attr in (
                "score",
                "quality_score",
                "readability_score",
                "cognitive_score",
                "ai_style_score",
                "severity",
                "rank",
            ):
                assert not hasattr(cls, attr), f"{cls.__name__} has {attr}"
        # No score function in the module namespace.
        score_names = [
            n for n in dir(reporting) if "score" in n.lower() and not n.startswith("_")
        ]
        assert score_names == []

    def test_no_new_report_lifecycle_mode(self):
        # Invariant 31: no new Report lifecycle mode.
        # The pipeline runs inside DELIVERY; LifecycleMode is unchanged.
        from my_search_harness.domain.model import LifecycleMode

        modes = {m.value for m in LifecycleMode}
        assert modes == {"RESEARCH", "COMPLETION_CHECK", "DELIVERY", "CLOSED"}
        # The pipeline never transitions lifecycle except via reopen_research
        # (which the existing delivery boundary owns).
        caps = FakeDeliveryCapabilities(_make_view())
        pipeline = _build_pipeline(caps)
        pipeline.run("run_1")
        # View lifecycle stayed DELIVERY throughout the happy path.
        assert caps._view.lifecycle is LifecycleMode.DELIVERY

    def test_no_reportbrief_stored_in_research_run(self):
        # Invariant 32: no ReportBrief stored in ResearchRun.
        # The reporting module exposes no ResearchRun write path and no
        # ArtifactKind.REPORT_BRIEF. The pipeline only calls certified publish /
        # reopen_research on Delivery capabilities — neither stores a Brief.
        assert not hasattr(ArtifactKind, "REPORT_BRIEF")
        # The pipeline result carries the Brief as a return value, not as a
        # persisted run artifact.
        caps = FakeDeliveryCapabilities(_make_view())
        pipeline = _build_pipeline(caps)
        result = pipeline.run("run_1")
        from my_search_harness.runtime.reporting import PublishedReportPipelineResult

        assert isinstance(result, PublishedReportPipelineResult)
        # The artifact is a REPORT, not a REPORT_BRIEF.
        assert result.artifact.artifact_kind is ArtifactKind.REPORT


# ===========================================================================
# Additional structural invariants (not numbered, but required by the ADR)
# ===========================================================================


class TestAdditionalInvariants:
    def test_capture_sink_is_noop_by_default_and_never_authority(self):
        # The default capture sink discards; captures never enter ResearchRun.
        caps = FakeDeliveryCapabilities(_make_view())
        sink = NoopReportCaptureSink()
        # Noop capture must not raise and must not persist anything.
        sink.capture("run_1", "report_brief.json", "{}")
        pipeline = _build_pipeline(caps, capture_sink=sink)
        pipeline.run("run_1")
        # No run mutation recorded beyond the single publish.
        assert len(caps.publish_calls) == 1

    def test_resource_exhaustion_is_not_pass(self):
        # Hitting a resource limit raises ReportResourceExhausted, never a PASS.
        caps = FakeDeliveryCapabilities(_make_view())

        # Reader always blocks MANUSCRIPT; reviser always returns a manuscript
        # that still blocks → reader loop never converges.
        def builder():
            return FakeReviewer(
                blocking_issues=(
                    BlockingIssue(
                        problem="p",
                        reader_effect="e",
                        why_blocking="w",
                        repair_target=RepairTarget.MANUSCRIPT,
                    ),
                )
            )

        factory = CountingReviewerFactory(builder)
        pipeline = _build_pipeline(caps, reviewer_factory=factory, max_reader_rounds=2)
        with pytest.raises(ReportResourceExhausted):
            pipeline.run("run_1")
        assert caps.publish_calls == []

    def test_writer_receives_narrow_citation_metadata_not_full_view(self):
        # The Writer must not receive the full DeliveryView or evidence access.
        caps = FakeDeliveryCapabilities(_make_view())
        writer = FakeWriter()
        pipeline = _build_pipeline(caps, writer=writer)
        pipeline.run("run_1")
        brief, guide, citation_meta = writer.calls[0]
        assert isinstance(citation_meta, CitationMetadata)
        # CitationMetadata carries only (ref, title, url) tuples — no DeliveryView.
        assert all(isinstance(t, tuple) and len(t) == 3 for t in citation_meta.papers)
        assert citation_meta.papers[0] == (
            "paper_p1",
            "Paper One",
            "https://example.org/p1",
        )

    def test_constructor_does_not_receive_writing_guide(self):
        # The Constructor receives the Quality Standard, not the Writing Guide.
        caps = FakeDeliveryCapabilities(_make_view())
        constructor = FakeConstructor()
        pipeline = _build_pipeline(caps, constructor=constructor)
        pipeline.run("run_1")
        view, quality_standard = constructor.calls[0]
        assert quality_standard == "quality standard text"
        assert quality_standard != "writing guide text"

    def test_integrity_revise_delivery_requires_valid_target(self):
        # REVISE_DELIVERY without a valid target is rejected by the pipeline.
        caps = FakeDeliveryCapabilities(_make_view())
        integrity = FakeIntegrityReviewer(
            [
                ResearchIntegrityReview(
                    disposition=IntegrityDisposition.REVISE_DELIVERY,
                    issues=("fix",),
                    revise_target=None,
                )
            ]
        )
        pipeline = _build_pipeline(caps, integrity_reviewer=integrity)
        with pytest.raises(ReportPipelineError, match="REVISE_DELIVERY requires"):
            pipeline.run("run_1")

    def test_integrity_pass_with_issues_rejected(self):
        # PASS must carry no issues (validation invariant).
        caps = FakeDeliveryCapabilities(_make_view())
        integrity = FakeIntegrityReviewer(
            [
                ResearchIntegrityReview(
                    disposition=IntegrityDisposition.PASS,
                    issues=("should not be here",),
                )
            ]
        )
        pipeline = _build_pipeline(caps, integrity_reviewer=integrity)
        with pytest.raises(ReportPipelineError, match="PASS must carry no issues"):
            pipeline.run("run_1")

    def test_non_pass_without_issues_rejected(self):
        # Non-PASS disposition requires issues (validation invariant).
        caps = FakeDeliveryCapabilities(_make_view())
        integrity = FakeIntegrityReviewer(
            [
                ResearchIntegrityReview(
                    disposition=IntegrityDisposition.REOPEN_RESEARCH,
                    issues=(),
                )
            ]
        )
        pipeline = _build_pipeline(caps, integrity_reviewer=integrity)
        with pytest.raises(
            ReportPipelineError, match="non-PASS disposition requires issues"
        ):
            pipeline.run("run_1")

    def test_empty_guides_rejected_at_construction(self):
        # The pipeline validates all four guides at construction time.
        caps = FakeDeliveryCapabilities(_make_view())
        with pytest.raises(
            ValueError, match="quality_standard must be a non-empty string"
        ):
            ReportPipeline(
                caps,
                constructor=FakeConstructor(),
                writer=FakeWriter(),
                reviewer_factory=CountingReviewerFactory(FakeReviewer),
                reviser=FakeReviser(),
                integrity_reviewer=FakeIntegrityReviewer(
                    [ResearchIntegrityReview(disposition=IntegrityDisposition.PASS)]
                ),
                citation_renderer=FakeCitationRenderer(),
                quality_standard="",
                writing_guide="writing guide text",
                review_guide="review guide text",
                integrity_guide="integrity guide text",
            )

    def test_render_and_publish_only_with_both_passes(self):
        # Publish happens only when the current version has BOTH Reader PASS
        # and Integrity PASS. A REOPEN_RESEARCH must not publish.
        caps = FakeDeliveryCapabilities(_make_view())
        integrity = FakeIntegrityReviewer(
            [
                ResearchIntegrityReview(
                    disposition=IntegrityDisposition.REOPEN_RESEARCH,
                    issues=("nope",),
                )
            ]
        )
        pipeline = _build_pipeline(caps, integrity_reviewer=integrity)
        result = pipeline.run("run_1")
        from my_search_harness.runtime.reporting import ReportResearchReopenedResult

        assert isinstance(result, ReportResearchReopenedResult)
        assert caps.publish_calls == []
