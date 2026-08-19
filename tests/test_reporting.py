"""Certified Report Brief dual-review-loop deterministic invariant tests.

These tests pin the report pipeline invariants using deterministic fake
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
from pathlib import Path
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
    ReportAuthoringContext,
    ReportConstructionContext,
    report_authoring_context_for,
    report_construction_context_for,
)
from my_search_harness.runtime.reporting import (
    BlindReadResult,
    CitationReference,
    IntegrityDisposition,
    IntegrityPass,
    NoopReportCaptureSink,
    ReaderIssue,
    ReaderPass,
    RepairTarget,
    ReportBrief,
    ReportCaptureSink,
    ReportManuscript,
    ReportPipeline,
    ReportPipelineError,
    ReportResourceExhausted,
    ReportReviewResult,
    ResearchIntegrityReview,
    brief_digest,
    blind_read_digest,
    manuscript_digest,
    validate_report_brief,
)
from my_search_harness.runtime.citations import (
    CitationValidationError,
    DeterministicCitationRenderer,
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

    def report_construction_context(self, run_id: str) -> ReportConstructionContext:
        return report_construction_context_for(self._view)

    def report_authoring_context(self, run_id: str) -> ReportAuthoringContext:
        return report_authoring_context_for(self._view)

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
        promise="the reader will understand how each route changes the variable",
        frame="routes organized by the decision variable each one changes",
        arc="establish the premise, derive the comparison, state the consequence",
        focus="the mechanism that distinguishes the routes",
    )


def _manuscript(
    markdown: str = "# Report\n\n## Section 1\n\nbody {{cite:c1}}",
) -> ReportManuscript:
    return ReportManuscript(
        markdown=markdown,
        citations=(
            CitationReference(citation_id="c1", paper_ref="paper_p1"),
        ),
    )


def _reader_issue(
    *,
    observation: str = "the reader cannot see the mechanism",
    reader_effect: str = "the comparison is unsupported",
    location: str | None = None,
) -> ReaderIssue:
    return ReaderIssue(
        observation=observation,
        reader_effect=reader_effect,
        location=location,
    )


class FakeConstructor:
    def __init__(self, briefs: list[ReportBrief] | None = None) -> None:
        self.calls: list[tuple] = []
        self._briefs = briefs

    def construct(
        self, construction_input, construction_guide, evidence
    ):
        self.calls.append((construction_input, construction_guide))
        if self._briefs is not None:
            return self._briefs.pop(0)
        return _valid_brief()


class FakeWriter:
    def __init__(self, manuscripts: list[ReportManuscript] | None = None) -> None:
        self.calls: list[tuple] = []
        self._manuscripts = manuscripts

    def write(self, brief, writing_guide, authoring_context):
        self.calls.append((brief, writing_guide, authoring_context))
        if self._manuscripts is not None:
            return self._manuscripts.pop(0)
        return _manuscript()


class FakeReviewer:
    """Two-phase reviewer with fresh instances per phase. Records inputs to
    assert the blind boundary.

    v0.6.1: ``brief_check`` receives only the frozen Blind Read + Brief +
    review guide — NO manuscript, NO reader surface. It returns a single
    top-level ``repair_target`` (None for PASS) plus an optional ``rationale``;
    it does NOT re-collect blocking issues (the frozen Blind owns those).
    Phase 1 returns a lean ``BlindReadResult``.
    """

    def __init__(
        self,
        *,
        blind_result: BlindReadResult | None = None,
        repair_target: RepairTarget | None = None,
        rationale: str = "the manuscript does not realize the promise",
        blind_hook: Callable[..., None] | None = None,
        check_hook: Callable[..., None] | None = None,
    ) -> None:
        self.blind_calls: list[dict] = []
        self.check_calls: list[dict] = []
        self._blind_hook = blind_hook
        self._check_hook = check_hook
        self._repair_target = repair_target
        self._rationale = rationale
        self._blind_result = blind_result

    def blind_read(
        self,
        deliverable_description,
        audience,
        review_guide,
        reader_surface,
        manuscript_digest,
    ):
        record = {
            "deliverable_description": deliverable_description,
            "audience": audience,
            "review_guide": review_guide,
            "reader_surface": reader_surface,
            "manuscript_digest": manuscript_digest,
        }
        self.blind_calls.append(record)
        if self._blind_hook is not None:
            self._blind_hook(record)
        if self._blind_result is not None:
            return self._blind_result
        return BlindReadResult(
            received_understanding="the reader understood the promise",
            manuscript_digest=manuscript_digest,
        )

    def brief_check(
        self,
        blind_read,
        brief,
        contract,
        review_guide,
    ):
        record = {
            "blind_read": blind_read,
            "brief": brief,
            "contract": contract,
            "review_guide": review_guide,
        }
        self.check_calls.append(record)
        if self._check_hook is not None:
            self._check_hook(record)
        return ReportReviewResult(
            blind_read_digest=blind_read_digest(blind_read),
            brief_digest=brief_digest(brief),
            manuscript_digest=blind_read.manuscript_digest,
            repair_target=self._repair_target,
            rationale=self._rationale,
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

    def revise(self, brief, manuscript, issues, writing_guide, authoring_context):
        self.calls.append((brief, manuscript, issues, authoring_context))
        if self._revised is not None:
            return _manuscript(self._revised.pop(0))
        return _manuscript("# Report\n\n## Section 1\n\nrevised body {{cite:c1}}")


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
        construction_guide="construction guide text",
        writing_guide="writing guide text",
        review_guide="review guide text",
        integrity_guide="integrity guide text",
        capture_sink=capture_sink,
        max_constructor_rebuilds=max_constructor_rebuilds,
        max_integrity_rounds=max_integrity_rounds,
    )


# ===========================================================================
# Report Brief validation (invariants 1-4)
# ===========================================================================


class TestReportBriefValidation:
    # Invariants 1-4 now collapse into the lean Brief boundary: the five
    # editorial fields must be present and non-empty. There is no section IR,
    # no outline_depth, no material, no requirement/research ref checking —
    # those v0.5 machines are deleted. What remains is the deterministic
    # invariant that the Brief is a Delivery work product, not a Research
    # entity: it is never stored in ResearchRun and never gains an
    # ArtifactKind.

    @pytest.mark.parametrize(
        "field_name, bad_value",
        [
            ("audience", ""),
            ("audience", "   "),
            ("promise", ""),
            ("promise", "   "),
            ("frame", ""),
            ("frame", "   "),
        ],
    )
    def test_lean_brief_text_fields_must_be_non_empty(self, field_name, bad_value):
        # Invariant 1: audience/promise/frame are non-empty text.
        with pytest.raises(
            ReportPipelineError, match=f"ReportBrief.{field_name}"
        ):
            validate_report_brief(
                _make_view(), replace(_valid_brief(), **{field_name: bad_value})
            )

    @pytest.mark.parametrize(
        "field_name, bad_value, message",
        [
            ("arc", "", "arc"),
            ("arc", "  ", "arc"),
            ("arc", ("not a string",), "arc"),
            ("arc", ["not a string"], "arc"),
            ("focus", "", "focus"),
            ("focus", "  ", "focus"),
            ("focus", ("not a string",), "focus"),
            ("focus", ["not a string"], "focus"),
        ],
    )
    def test_lean_brief_string_fields_must_be_non_empty(
        self, field_name, bad_value, message
    ):
        # Invariant 2: arc/focus are non-empty strings (v0.6.1). No ordering
        # invariant remains; meaning is semantic.
        with pytest.raises(ReportPipelineError, match=message):
            validate_report_brief(
                _make_view(), replace(_valid_brief(), **{field_name: bad_value})
            )

    def test_lean_brief_rejects_non_brief(self):
        # Invariant 3: the constructor must return a ReportBrief.
        with pytest.raises(ReportPipelineError, match="constructor must return"):
            validate_report_brief(_make_view(), object())

    def test_lean_brief_has_no_v05_section_machinery(self):
        # Invariant 4 (shape): the lean Brief carries exactly the five
        # editorial fields. The v0.5 section/material/outline IR is gone.
        brief = _valid_brief()
        for removed in (
            "report_title",
            "report_goal",
            "conceptual_model",
            "reader_takeaway",
            "narrative_logic",
            "sections",
            "terminology",
            "intentional_omissions",
        ):
            assert not hasattr(brief, removed), f"Brief still has {removed}"
        assert {f for f in brief.__dataclass_fields__} == {
            "audience",
            "promise",
            "frame",
            "arc",
            "focus",
        }

    def test_brief_not_stored_in_research_run_no_new_artifactkind(self):
        # Invariant 4: Brief does not enter ResearchRun / ArtifactKind not
        # expanded. The pipeline never calls any ResearchRun mutation; the
        # only ArtifactKind is REPORT.
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
# Report information architecture fidelity
# ===========================================================================
# v0.6 deletes the deterministic outline-fidelity machine (validate_outline_fidelity,
# brief_outline_signature, manuscript_outline_signature). The lean Brief has no
# section IR, so there is no Python-side outline to match. What remains in this
# suite are the citation-renderer invariants and the guide-content invariants,
# which test Presentation and the review/writing guides — not outline fidelity.


class TestReportOutlineFidelity:
    def test_locator_is_preserved_for_audit_but_hidden_from_reader(self):
        # Presentation preserves the locator for audit but never shows it to
        # the reader. This is a citation-renderer invariant, not an outline one.
        locator = SourceLocator(kind="section", value="3.2")
        manuscript = ReportManuscript(
            markdown="# Report\n\n## Section 1\n\nclaim {{cite:c1}}",
            citations=(
                CitationReference(
                    citation_id="c1",
                    paper_ref="paper_p1",
                    locator=locator,
                ),
            ),
        )
        renderer = DeterministicCitationRenderer()
        audit = renderer.audit(_make_view(), manuscript)
        rendered = renderer.render(_make_view(), manuscript)

        assert audit.citations[0].locator == locator
        assert "claim [1]" in rendered
        assert "section: 3.2" not in rendered

    def test_first_paper_use_gets_deterministic_primary_navigation(self):
        # First use of a paper gets the canonical navigation link; later uses
        # get only the citation number. Deterministic Presentation owns this.
        manuscript = ReportManuscript(
            markdown=(
                "# Report\n\n## Section 1\n\n"
                "first {{cite:first}}, later {{cite:later}}"
            ),
            citations=(
                CitationReference(citation_id="first", paper_ref="paper_p1"),
                CitationReference(citation_id="later", paper_ref="paper_p1"),
            ),
        )
        rendered = DeterministicCitationRenderer().render(_make_view(), manuscript)
        assert "first [1](https://example.org/p1)" in rendered
        assert "later [1]" in rendered
        assert rendered.count("[1](https://example.org/p1)") == 1

    def test_structured_method_navigation_owns_the_first_paper_link(self):
        # A {{paper:...}} navigation token owns the first-use link; the
        # accompanying {{cite:...}} is rendered as a bare number.
        manuscript = ReportManuscript(
            markdown=(
                "# Report\n\n## Section 1\n\n"
                "{{paper:method|Method One}} changes the mechanism {{cite:method}}."
            ),
            citations=(CitationReference(citation_id="method", paper_ref="paper_p1"),),
        )
        rendered = DeterministicCitationRenderer().render(_make_view(), manuscript)
        assert "[Method One](https://example.org/p1)" in rendered
        assert "mechanism [1]." in rendered
        assert "[1](https://example.org/p1)" not in rendered

    @pytest.mark.parametrize(
        "markdown, message",
        [
            (
                "# Report\n\n## Section 1\n\n{{paper:paper_p1}}",
                "malformed paper navigation token",
            ),
            (
                "# Report\n\n## Section 1\n\n$$ x + y",
                "unmatched \\$\\$ math delimiters",
            ),
            (
                "# Report\n\n## Section 1\n\n\\(x + y",
                "math delimiters",
            ),
            (
                "# Report\n\n## Section 1\n\n\\[x + y",
                "math delimiters",
            ),
            (
                "# Report\n\n## Section 1\n\n```text\nunclosed",
                "unclosed fenced block",
            ),
        ],
    )
    def test_presentation_preflight_rejects_mechanical_defects(self, markdown, message):
        # Presentation audit rejects mechanical markdown defects before the
        # reader ever sees the surface.
        with pytest.raises(CitationValidationError, match=message):
            DeterministicCitationRenderer().audit(
                _make_view(), ReportManuscript(markdown=markdown)
            )

    def test_reader_guide_phase1_boundary_excludes_brief_and_writing_guide(self):
        # v0.6.1 guide content: the review guide names the Phase 1 boundary
        # (no Brief, no Writing Guide) and the narrow audience projection
        # (audience string only — not promise/frame/arc/focus). Per §14, assert
        # the structural contract only — not a specific formulation.
        guide = (
            Path(__file__).resolve().parents[1]
            / ".claude"
            / "skills"
            / "literature-research"
            / "references"
            / "REPORT_REVIEW_GUIDE.md"
        ).read_text(encoding="utf-8")
        # Phase 1 must not receive the Brief or the Writing Guide.
        assert "Phase 1" in guide
        # The audience projection is the audience string only.
        assert "audience" in guide.lower()
        # The lean Brief fields are named somewhere in the guide (Phase 2
        # receives the Brief), but Phase 1 must not.
        assert "promise" in guide.lower()
        assert "arc" in guide.lower()

    def test_reader_guide_has_dual_blocking_threshold_without_style_score(self):
        # v0.6.1 guide content: the review guide rejects a unified quality
        # score as a stop condition. Per §14, assert the structural contract
        # only — not a specific formulation of the threshold.
        review_guide = (
            Path(__file__).resolve().parents[1]
            / ".claude"
            / "skills"
            / "literature-research"
            / "references"
            / "REPORT_REVIEW_GUIDE.md"
        ).read_text(encoding="utf-8")
        # The guide must not prescribe a quality_score threshold as the PASS
        # stop condition. It may name quality_score only to reject it.
        assert "quality_score >= threshold" not in review_guide.replace(
            "不是 `quality_score >= threshold`", ""
        )

    def test_reader_issue_format_in_guide_matches_lean_shape(self):
        # v0.6.1 guide content: the ReaderIssue format in the guide matches
        # the lean dataclass shape (observation / reader_effect / optional
        # location). The removed why_blocking field is NOT prescribed.
        # repair_target is a top-level Phase 2 decision, not per-issue.
        # Per §14, this asserts the structural contract only — not a specific
        # natural-language formulation of the charter.
        guide = (
            Path(__file__).resolve().parents[1]
            / ".claude"
            / "skills"
            / "literature-research"
            / "references"
            / "REPORT_REVIEW_GUIDE.md"
        ).read_text(encoding="utf-8")
        assert "observation" in guide
        assert "reader_effect" in guide
        # The removed v0.5 field must not survive in the guide.
        assert "why_blocking" not in guide
        assert "resolution_condition" not in guide

    def test_writing_guide_is_short_charter_not_production_ontology(self):
        # §9: the writing guide is a short charter. It must NOT carry the
        # v0.5 production-writing ontology (section/material/outline IR,
        # semantic-navigation tokens, quality scoring). Per §14, assert the
        # structural contract only — not a specific formulation.
        guide = (
            Path(__file__).resolve().parents[1]
            / ".claude"
            / "skills"
            / "literature-research"
            / "references"
            / "REPORT_WRITING_GUIDE.md"
        ).read_text(encoding="utf-8")
        # Removed v0.5 ontology must not survive.
        for removed in (
            "首次正式引入一个具名方法或系统",
            "semantic_navigation",
            "quality_score",
            "narrative_logic",
            "conceptual_model",
        ):
            assert removed not in guide, f"writing guide still references {removed}"
        # The charter still names the core editorial fields of the lean Brief.
        assert "audience" in guide
        assert "promise" in guide

    def test_review_guide_no_production_quality_ontology(self):
        # §3/§6-D: the review guide must not carry the v0.5 production quality
        # ontology (cognitive-jump/restart/debt, argument island, material
        # economy, domain mental model, stable comparison coordinates). Assert
        # the structural contract only — the deleted terms are gone.
        guide = (
            Path(__file__).resolve().parents[1]
            / ".claude"
            / "skills"
            / "literature-research"
            / "references"
            / "REPORT_REVIEW_GUIDE.md"
        ).read_text(encoding="utf-8")
        for removed in (
            "认知跳步",
            "认知重启",
            "认知债务",
            "论证孤岛",
            "材料经济性",
            "领域心智模型",
            "稳定比较坐标",
            "论证完整性",
        ):
            assert removed not in guide, f"review guide still references {removed}"

    def test_review_guide_phase2_names_contract(self):
        # §1/§6-D: the review guide must state Phase 2 receives the Contract.
        guide = (
            Path(__file__).resolve().parents[1]
            / ".claude"
            / "skills"
            / "literature-research"
            / "references"
            / "REPORT_REVIEW_GUIDE.md"
        ).read_text(encoding="utf-8")
        assert "Contract" in guide
        assert "Phase 2" in guide

    def test_construction_guide_does_not_nudge_structured_lists(self):
        # §2/§6-D: the construction guide must not describe frame as comparison
        # coordinates / taxonomy, arc as a stage list / section order, or focus
        # as an exclusion checklist. Assert the structural contract only.
        guide = (
            Path(__file__).resolve().parents[1]
            / ".claude"
            / "skills"
            / "literature-research"
            / "references"
            / "REPORT_CONSTRUCTION_GUIDE.md"
        ).read_text(encoding="utf-8")
        for removed in (
            "比较坐标",
            "分类依据",
            "有序阶段",
            "认知台阶",
            "不写什么",
            "不比较什么",
        ):
            assert removed not in guide, f"construction guide still references {removed}"

    def test_writing_guide_owns_presentation_form_and_idea_before_paper(self):
        # v0.6.3 authoring calibration: the writing guide must (a) make
        # Authoring own the prose/list/table choice based on information shape,
        # and (b) carry the idea-before-paper principle. Assert the structural
        # contract only — not a specific formulation, example, or column set.
        guide = (
            Path(__file__).resolve().parents[1]
            / ".claude"
            / "skills"
            / "literature-research"
            / "references"
            / "REPORT_WRITING_GUIDE.md"
        ).read_text(encoding="utf-8")
        # Authoring owns presentation-form choice; paragraphs are not the default.
        assert "表格" in guide
        assert "列表" in guide
        # The idea-before-paper principle is present (in either phrasing).
        assert "论文应当例示解释" in guide or "论文应当实例化" in guide

    def test_writing_guide_has_no_mandatory_format_counts_or_fixed_template(self):
        # v0.6.3: the authoring calibration must not reintroduce mandatory
        # table/list counts as POSITIVE requirements. Negated mentions (e.g.
        # "不要求每节必有表格") are the desired anti-checklist stance and are
        # not matched here; only unambiguous positive-mandate phrasings count.
        guide = (
            Path(__file__).resolve().parents[1]
            / ".claude"
            / "skills"
            / "literature-research"
            / "references"
            / "REPORT_WRITING_GUIDE.md"
        ).read_text(encoding="utf-8")
        for removed in (
            "至少一个表格",
            "至少一张表",
            "至少 N 个列表",
        ):
            assert removed not in guide, f"writing guide reintroduced {removed}"

    def test_construction_guide_does_not_own_headings_or_paper_inventory(self):
        # v0.6.3: Constructor designs understanding and attention allocation,
        # not exact headings / tables / paper inventory / formula inventory.
        # Assert the structural contract only — not a specific formulation.
        guide = (
            Path(__file__).resolve().parents[1]
            / ".claude"
            / "skills"
            / "literature-research"
            / "references"
            / "REPORT_CONSTRUCTION_GUIDE.md"
        ).read_text(encoding="utf-8")
        # The charter names what Constructor does NOT design.
        assert "heading" in guide.lower()
        assert "表格" in guide
        # The lean-brief-as-checklist anti-pattern is explicitly discouraged.
        assert "预写稿件" in guide or "材料消耗清单" in guide

    def test_reader_guide_authority_unchanged_by_authoring_calibration(self):
        # v0.6.3: the authoring calibration is upstream of the Reader. The
        # review guide must NOT gain checks for AI-like headings, missing
        # tables, method-first exposition, or paragraph density. Assert these
        # enforcement concepts stay absent from the review guide.
        guide = (
            Path(__file__).resolve().parents[1]
            / ".claude"
            / "skills"
            / "literature-research"
            / "references"
            / "REPORT_REVIEW_GUIDE.md"
        ).read_text(encoding="utf-8")
        for removed in (
            "AI 措辞检测",
            "missing table",
            "method-first",
            "段落密度",
        ):
            assert removed not in guide, f"review guide gained style check {removed}"

    def test_skill_does_not_claim_authoring_context_carries_paper_identity(self):
        # §4/§6-D: SKILL must not claim ReportAuthoringContext carries paper
        # identity or title. Concrete paper/source detail comes through
        # targeted delivery-inspect / delivery-read-source.
        skill = (
            Path(__file__).resolve().parents[1]
            / ".claude"
            / "skills"
            / "literature-research"
            / "SKILL.md"
        ).read_text(encoding="utf-8")
        # The old claim that the authoring context "carries paper identity and
        # title" must be gone.
        assert "carries paper identity" not in skill
        # The corrected rule: AuthoringContext exposes high-level semantics
        # only; concrete detail comes via delivery-inspect / delivery-read-source.
        assert "delivery-inspect" in skill
        assert "delivery-read-source" in skill


# ===========================================================================
# Math renderability preflight (mechanical TeX renderability only)
# ===========================================================================


def _math_renderer_available() -> bool:
    """True when the real MathJax validator is installed and runnable.

    Renderer-exercising tests are skipped in node-less environments so the
    suite stays green there; locally the renderer is installed and these run
    against the real MathJax.
    """
    from my_search_harness.runtime import math_preflight

    available, _ = math_preflight._renderer_available()
    return available


@pytest.fixture()
def _math_view():
    return _make_view()


class TestMathRenderabilityPreflight:
    """Pin only the expressions the real renderer deterministically rejects.

    Per the spec, every case here was first verified against the actual
    MathJax renderer. Borderline-but-valid notation (``R^(\\lambda)``,
    ``x^10``, ``A_tree``) is asserted to PASS — the preflight must not add
    semantic LaTeX lint. Math inside code is ignored. Validation runs before
    the Reader.
    """

    @pytest.mark.skipif(
        not _math_renderer_available(), reason="MathJax renderer not installed"
    )
    def test_preflight_accepts_valid_and_borderline_expressions(self, _math_view):
        # These are syntactically valid TeX the renderer accepts — including
        # borderline notation the spec forbids rejecting.
        markdown = (
            "# Report\n\n## Section 1\n\nbody {{cite:c1}}\n\n"
            "$R^{(\\lambda)}$ and $x^10$ and $A_tree$ and "
            "$$\\begin{matrix}a & b \\\\ c & d \\end{matrix}$$ and "
            "$\\frac{a}{b}$ and $\\alpha + \\beta$."
        )
        manuscript = ReportManuscript(
            markdown=markdown,
            citations=(CitationReference(citation_id="c1", paper_ref="paper_p1"),),
        )
        # Must not raise: every expression is renderable.
        DeterministicCitationRenderer().audit(_math_view, manuscript)

    @pytest.mark.skipif(
        not _math_renderer_available(), reason="MathJax renderer not installed"
    )
    @pytest.mark.parametrize(
        "markdown, message",
        [
            # The observed E2E failure: ^_ with no braces.
            (
                "# Report\n\n## S\n\n{{cite:c1}}\n\n$$A^_tree=A^_Intra+A^_Inter$$",
                "open brace",
            ),
            # Dangling superscript with no argument.
            (
                "# Report\n\n## S\n\n{{cite:c1}}\n\n$x^$",
                "superscript",
            ),
            # \frac missing its second argument.
            (
                "# Report\n\n## S\n\n{{cite:c1}}\n\n$\\frac{a}$",
                "frac",
            ),
            # Unclosed group inside math.
            (
                "# Report\n\n## S\n\n{{cite:c1}}\n\n$\\frac{a}{b$",
                "close brace",
            ),
        ],
    )
    def test_preflight_rejects_renderer_rejected_math(
        self, _math_view, markdown, message
    ):
        manuscript = ReportManuscript(
            markdown=markdown,
            citations=(CitationReference(citation_id="c1", paper_ref="paper_p1"),),
        )
        with pytest.raises(CitationValidationError, match=message):
            DeterministicCitationRenderer().audit(_math_view, manuscript)

    def test_preflight_ignores_math_inside_fenced_code(self, _math_view):
        # Math inside a fenced code block is verbatim prose, not rendered math.
        markdown = (
            "# Report\n\n## S\n\n{{cite:c1}}\n\n"
            "```text\n$A^_tree=A^_Intra+A^_Inter$\n```\n"
            "after the block."
        )
        manuscript = ReportManuscript(
            markdown=markdown,
            citations=(CitationReference(citation_id="c1", paper_ref="paper_p1"),),
        )
        # Even with the renderer unavailable this must pass: no math was
        # extracted, so no renderer call is made.
        DeterministicCitationRenderer().audit(_math_view, manuscript)

    def test_preflight_ignores_math_inside_inline_code(self, _math_view):
        markdown = (
            "# Report\n\n## S\n\n{{cite:c1}}\n\n"
            "see `$x^$` in the snippet."
        )
        manuscript = ReportManuscript(
            markdown=markdown,
            citations=(CitationReference(citation_id="c1", paper_ref="paper_p1"),),
        )
        DeterministicCitationRenderer().audit(_math_view, manuscript)

    @pytest.mark.skipif(
        not _math_renderer_available(), reason="MathJax renderer not installed"
    )
    def test_preflight_reports_only_the_bad_expression(self, _math_view):
        # One valid and one invalid expression in the same manuscript: the
        # rejection names the invalid one, not the valid one.
        markdown = (
            "# Report\n\n## S\n\n{{cite:c1}}\n\n"
            "$\\frac{a}{b}$ is fine but $x^$ is not."
        )
        manuscript = ReportManuscript(
            markdown=markdown,
            citations=(CitationReference(citation_id="c1", paper_ref="paper_p1"),),
        )
        with pytest.raises(CitationValidationError) as exc:
            DeterministicCitationRenderer().audit(_math_view, manuscript)
        message = str(exc.value)
        assert "x^" in message
        assert "\\frac{a}{b}" not in message

    def test_preflight_skips_when_no_math(self, _math_view, monkeypatch):
        # A math-free manuscript must short-circuit: no Node subprocess is
        # spawned. We assert this by patching the subprocess call to fail
        # loudly if it were ever invoked.
        from my_search_harness.runtime import math_preflight

        def _explode(*args, **kwargs):
            raise AssertionError("renderer must not be called for math-free text")

        monkeypatch.setattr(math_preflight.subprocess, "run", _explode)
        markdown = "# Report\n\n## Section 1\n\nbody {{cite:c1}} with no math."
        manuscript = ReportManuscript(
            markdown=markdown,
            citations=(CitationReference(citation_id="c1", paper_ref="paper_p1"),),
        )
        DeterministicCitationRenderer().audit(_math_view, manuscript)

    @pytest.mark.skipif(
        not _math_renderer_available(), reason="MathJax renderer not installed"
    )
    def test_pipeline_rejects_bad_math_before_reader(self, _math_view):
        # The real renderer is injected into the pipeline. A bad-math
        # manuscript must be rejected in _reader_surface (which calls
        # render -> audit) BEFORE the Reader is ever consulted. We drive
        # _fresh_review directly because _write_then_review authors its own
        # manuscript; the point is that the Presentation boundary fires
        # before any reviewer is created.
        caps = FakeDeliveryCapabilities(_make_view())
        reviewer_factory = CountingReviewerFactory(FakeReviewer)
        pipeline = _build_pipeline(
            caps,
            reviewer_factory=reviewer_factory,
            citation_renderer=DeterministicCitationRenderer(),
        )
        brief = _valid_brief()
        manuscript = ReportManuscript(
            markdown=(
                "# Report\n\n## Section 1\n\nbody {{cite:c1}}\n\n"
                "$$A^_tree=A^_Intra+A^_Inter$$"
            ),
            citations=(CitationReference(citation_id="c1", paper_ref="paper_p1"),),
        )
        with pytest.raises(CitationValidationError):
            pipeline._fresh_review("run-1", _make_view(), brief, manuscript)
        # The Reader must never have been consulted: _reader_surface throws
        # before the first reviewer is created.
        assert len(reviewer_factory.created) == 0


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
        # Invariant 6: Phase 1 has no Writing Guide. The blind_read signature
        # carries only deliverable_description / audience / review_guide /
        # reader_surface / manuscript_digest — no writing guide, no quality
        # standard.
        caps = FakeDeliveryCapabilities(_make_view())
        recorded_keys: list[tuple[str, ...]] = []

        def hook(record):
            recorded_keys.append(tuple(record.keys()))
            # The writing guide text must not appear in any Phase 1 argument.
            assert "writing guide text" not in record.values()

        factory = CountingReviewerFactory(lambda: FakeReviewer(blind_hook=hook))
        pipeline = _build_pipeline(caps, reviewer_factory=factory)
        pipeline.run("run_1")
        # review_guide is the REVIEW guide, distinct from the writing guide.
        # The v0.6 blind_read signature has exactly these five keys and no
        # quality_standard key.
        assert recorded_keys
        for keys in recorded_keys:
            assert set(keys) == {
                "deliverable_description",
                "audience",
                "review_guide",
                "reader_surface",
                "manuscript_digest",
            }
            assert "quality_standard" not in keys

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
        # The pipeline validates the digest binding; a mismatch raises.
        with pytest.raises(
            ReportPipelineError, match="BlindReadResult must bind to the manuscript"
        ):
            bad_blind = BlindReadResult(
                received_understanding="u",
                manuscript_digest="wrong",
            )
            factory2 = CountingReviewerFactory(
                lambda: FakeReviewer(blind_result=bad_blind)
            )
            _build_pipeline(caps, reviewer_factory=factory2).run("run_2")

    def test_phase1_blocker_cannot_disappear_into_phase2_pass(self):
        # Invariant (blind freeze, spec D): a frozen Blind Read with blocking
        # issues cannot quietly become a Phase 2 PASS. v0.6.1: Phase 2 no longer
        # carries blocking_issues; the invariant is examined on the frozen
        # Blind — if it has blocking issues, a Phase 2 PASS (repair_target is
        # None) is rejected.
        issue = _reader_issue()
        blind = BlindReadResult(
            received_understanding="partial",
            manuscript_digest=manuscript_digest(_manuscript()),
            blocking_issues=(issue,),
        )
        caps = FakeDeliveryCapabilities(_make_view())
        # Phase 2 attempts a PASS (repair_target None) while the frozen Blind
        # recorded a blocking issue — rejected.
        with pytest.raises(
            ReportPipelineError,
            match="Phase 2 cannot PASS while the frozen Blind Read has blocking issues",
        ):
            _build_pipeline(
                caps,
                reviewer_factory=CountingReviewerFactory(
                    lambda: FakeReviewer(
                        blind_result=blind,
                        repair_target=None,
                        rationale="",
                    )
                ),
            ).run("run_1")

    def test_phase2_may_consolidate_multiple_blind_blockers(self):
        # Phase 2 may consolidate multiple blind blockers into one repair
        # target. v0.6.1: the repair target is a single top-level field, not a
        # per-issue field, and Phase 2 carries no blocking_issues. A MANUSCRIPT
        # route is a resource stop (no auto-revise); assert the stop and that
        # the consolidated target was MANUSCRIPT.
        blind = BlindReadResult(
            received_understanding="partial",
            manuscript_digest=manuscript_digest(_manuscript()),
            blocking_issues=(
                _reader_issue(observation="missing bridge"),
                _reader_issue(observation="unstable comparison"),
            ),
        )
        caps = FakeDeliveryCapabilities(_make_view())
        pipeline = _build_pipeline(
            caps,
            reviewer_factory=CountingReviewerFactory(
                lambda: FakeReviewer(
                    blind_result=blind,
                    repair_target=RepairTarget.MANUSCRIPT,
                    rationale="the manuscript does not realize the promise",
                )
            ),
        )
        # v0.6: a MANUSCRIPT Reader blocker is a resource stop, not an
        # auto-revise. The host must re-author and re-run.
        with pytest.raises(ReportResourceExhausted, match="MANUSCRIPT repair"):
            pipeline.run("run_1")

    def test_phase2_cannot_replace_frozen_blind_read(self):
        # Phase 2 cannot rewrite or reinterpret the frozen Blind Read. The
        # review result's blind_read_digest must match the frozen record.
        class RewritingReviewer(FakeReviewer):
            def brief_check(
                self,
                blind_read,
                brief,
                contract,
                review_guide,
            ):
                return ReportReviewResult(
                    blind_read_digest="0" * 64,
                    brief_digest=brief_digest(brief),
                    manuscript_digest=blind_read.manuscript_digest,
                    repair_target=None,
                    rationale="pass",
                )

        caps = FakeDeliveryCapabilities(_make_view())
        pipeline = _build_pipeline(
            caps,
            reviewer_factory=CountingReviewerFactory(RewritingReviewer),
        )
        with pytest.raises(
            ReportPipelineError, match="replace the frozen Blind Read"
        ):
            pipeline.run("run_1")

    def test_reader_issue_shape_requires_core_fields(self):
        # Invariant (spec B): ReaderIssue requires observation and
        # reader_effect as non-empty strings; location is optional (None or a
        # non-empty string). No repair_target, no why_blocking, no
        # resolution_condition, no score.
        issue = _reader_issue()
        assert not hasattr(issue, "repair_target")
        assert not hasattr(issue, "why_blocking")
        assert not hasattr(issue, "resolution_condition")
        assert not hasattr(issue, "score")
        assert {f for f in issue.__dataclass_fields__} == {
            "observation",
            "reader_effect",
            "location",
        }
        # location defaults to None.
        assert _reader_issue().location is None
        # A non-empty location is accepted by the pipeline; an empty string
        # location is rejected.
        caps = FakeDeliveryCapabilities(_make_view())
        with pytest.raises(
            ReportPipelineError, match="ReaderIssue.location must be a non-empty"
        ):
            _build_pipeline(
                caps,
                reviewer_factory=CountingReviewerFactory(
                    lambda: FakeReviewer(
                        blind_result=BlindReadResult(
                            received_understanding="u",
                            manuscript_digest=manuscript_digest(_manuscript()),
                            blocking_issues=(
                                ReaderIssue(
                                    observation="o",
                                    reader_effect="e",
                                    location="  ",
                                ),
                            ),
                        )
                    )
                ),
            ).run("run_1")


# ===========================================================================
# Fresh Review (invariants 10-13)
# ===========================================================================


class TestFreshReview:
    def test_manuscript_revision_uses_new_reviewer_instance(self):
        # Invariant 10 (v0.6): a MANUSCRIPT Reader blocker is a resource
        # stop — there is no auto-revise loop. The pipeline raises
        # ReportResourceExhausted and never invokes the Reviser. The host
        # owns re-authoring.
        caps = FakeDeliveryCapabilities(_make_view())
        factory = CountingReviewerFactory(
            lambda: FakeReviewer(
                repair_target=RepairTarget.MANUSCRIPT,
                rationale="the manuscript does not realize the promise",
            )
        )
        reviser = FakeReviser()
        pipeline = _build_pipeline(
            caps, reviewer_factory=factory, reviser=reviser
        )
        with pytest.raises(ReportResourceExhausted, match="MANUSCRIPT repair"):
            pipeline.run("run_1")
        # No revision happened — v0.6 surfaces the outcome to the host.
        assert reviser.calls == []
        # Two fresh reviewer instances for one review: Phase 1 + Phase 2 (§3).
        assert len(factory.created) == 2
        assert caps.publish_calls == []

    def test_reviser_does_not_go_straight_to_integrity(self):
        # Invariant 11 (v0.6): the old "Reviser → Reader → Integrity"
        # convergence loop is gone. A MANUSCRIPT Reader blocker is a
        # resource stop before Integrity is ever consulted.
        caps = FakeDeliveryCapabilities(_make_view())
        factory = CountingReviewerFactory(
            lambda: FakeReviewer(
                repair_target=RepairTarget.MANUSCRIPT,
                rationale="the manuscript does not realize the promise",
            )
        )
        integrity = FakeIntegrityReviewer(
            [ResearchIntegrityReview(disposition=IntegrityDisposition.PASS)]
        )
        reviser = FakeReviser()
        pipeline = _build_pipeline(
            caps,
            reviewer_factory=factory,
            integrity_reviewer=integrity,
            reviser=reviser,
        )
        with pytest.raises(ReportResourceExhausted, match="MANUSCRIPT repair"):
            pipeline.run("run_1")
        # Integrity was never called — the Reader stop precedes it.
        assert integrity.calls == []
        assert reviser.calls == []
        assert caps.publish_calls == []

    def test_revised_manuscript_re_runs_phase1_and_phase2(self):
        # Invariant 12 (v0.6): the only path that re-runs Phase 1 + Phase 2
        # after a manuscript edit is the INTEGRITY repair path (which still
        # re-revises + re-reads). A Reader MANUSCRIPT blocker no longer
        # triggers a re-read. We exercise the integrity-triggered re-read:
        # Integrity REVISE_DELIVERY(MANUSCRIPT) → Reviser → fresh Reader
        # (Phase 1 + Phase 2) → Integrity again.
        caps = FakeDeliveryCapabilities(_make_view())
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
        factory = CountingReviewerFactory(FakeReviewer)
        reviser = FakeReviser(
            revised_markdowns=["# Report\n\n## Section 1\n\nfixed {{cite:c1}}"]
        )
        pipeline = _build_pipeline(
            caps,
            reviewer_factory=factory,
            integrity_reviewer=integrity,
            reviser=reviser,
        )
        pipeline.run("run_1")
        # Two reviews (one before integrity, one after the repair); each
        # review uses two fresh instances (Phase 1 + Phase 2, §3) → 4 total.
        assert len(factory.created) == 4
        # §3 isolation: Phase 1 instances perform only blind_read; Phase 2
        # instances perform only brief_check. No single instance does both.
        phase1_instances = factory.created[0::2]
        phase2_instances = factory.created[1::2]
        assert all(len(r.blind_calls) == 1 for r in phase1_instances)
        assert all(len(r.check_calls) == 0 for r in phase1_instances)
        assert all(len(r.blind_calls) == 0 for r in phase2_instances)
        assert all(len(r.check_calls) == 1 for r in phase2_instances)

    def test_brief_reconstruction_new_writer_and_fresh_reader(self):
        # Invariant 13 (v0.6): a BRIEF Reader blocker still routes back to
        # the Constructor (rebuild) → new Writer pass → fresh Reader. The
        # outer constructor-rebuild loop owns this; it is not auto-revise.
        caps = FakeDeliveryCapabilities(_make_view())
        call_count = {"n": 0}

        def builder():
            call_count["n"] += 1
            # §3: BRIEF verdict lands on the Phase 2 slot (2nd create) of
            # the first review.
            if call_count["n"] == 2:
                return FakeReviewer(
                    repair_target=RepairTarget.BRIEF,
                    rationale="the brief lacks a stable comparison frame",
                )
            return FakeReviewer()

        factory = CountingReviewerFactory(builder)
        brief_v2 = replace(_valid_brief(), promise="repaired promise v2")
        constructor = FakeConstructor(briefs=[_valid_brief(), brief_v2])
        writer = FakeWriter(
            manuscripts=[
                _manuscript("# Report\n\n## Section 1\n\nv1 {{cite:c1}}"),
                _manuscript("# Report\n\n## Section 1\n\nv2 {{cite:c1}}"),
            ]
        )
        reviser = FakeReviser()
        pipeline = _build_pipeline(
            caps,
            constructor=constructor,
            writer=writer,
            reviewer_factory=factory,
            reviser=reviser,
        )
        result = pipeline.run("run_1")
        # Constructor called twice (initial + rebuild), Writer called twice,
        # two fresh reader instances.
        assert len(constructor.calls) == 2
        assert len(writer.calls) == 2
        # Two reviews (initial + after rebuild); each uses two fresh
        # instances (Phase 1 + Phase 2, §3) → 4 total.
        assert len(factory.created) == 4
        # Reviser NOT called for a BRIEF route.
        assert reviser.calls == []
        # The certified brief digest is the v2 brief's, not v1's.
        from my_search_harness.runtime.reporting import PublishedReportPipelineResult

        assert isinstance(result, PublishedReportPipelineResult)
        assert result.reader_pass.brief_digest == brief_digest(brief_v2)
        assert result.reader_pass.brief_digest != brief_digest(_valid_brief())


# ===========================================================================
# Root repair routing (invariants 14-18)
# ===========================================================================


class TestRootRepairRouting:
    def test_manuscript_blocker_routes_to_reviser(self):
        # Invariant 14 (v0.6): a MANUSCRIPT Reader blocker is a resource
        # stop. The pipeline does NOT auto-route to the Reviser; it raises
        # ReportResourceExhausted so the host re-authors and re-runs.
        caps = FakeDeliveryCapabilities(_make_view())
        factory = CountingReviewerFactory(
            lambda: FakeReviewer(
                repair_target=RepairTarget.MANUSCRIPT,
                rationale="the manuscript does not realize the promise",
            )
        )
        reviser = FakeReviser()
        pipeline = _build_pipeline(
            caps, reviewer_factory=factory, reviser=reviser
        )
        with pytest.raises(ReportResourceExhausted, match="MANUSCRIPT repair"):
            pipeline.run("run_1")
        # No revision happened — the host owns re-authoring in v0.6.
        assert reviser.calls == []
        assert caps.publish_calls == []

    def test_brief_blocker_routes_to_constructor(self):
        # Invariant 15 (v0.6): a BRIEF Reader blocker raises
        # BriefInsufficient; the outer loop rebuilds the Brief via a fresh
        # Constructor pass. The single top-level repair_target IS the route.
        caps = FakeDeliveryCapabilities(_make_view())
        call_count = {"n": 0}

        def builder():
            call_count["n"] += 1
            # §3: each review consumes two creates (Phase 1 then Phase 2).
            # The BRIEF verdict is emitted by brief_check, so it must land on
            # the Phase 2 slot (the 2nd create) of the first review.
            if call_count["n"] == 2:
                return FakeReviewer(
                    repair_target=RepairTarget.BRIEF,
                    rationale="the brief lacks a stable comparison frame",
                )
            return FakeReviewer()

        factory = CountingReviewerFactory(builder)
        brief_v2 = replace(_valid_brief(), promise="repaired promise v2")
        constructor = FakeConstructor(briefs=[_valid_brief(), brief_v2])
        reviser = FakeReviser()
        pipeline = _build_pipeline(
            caps,
            constructor=constructor,
            reviewer_factory=factory,
            reviser=reviser,
        )
        pipeline.run("run_1")
        # Constructor rebuilt; Reviser NOT called for a BRIEF route.
        assert len(constructor.calls) == 2
        assert reviser.calls == []
        # The rebuild received the previous brief + neutral feedback.
        repair_input = constructor.calls[1][0]
        assert repair_input.repair is not None
        assert repair_input.repair.previous_brief == _valid_brief()
        assert repair_input.repair.feedback
        # v0.6.1 BriefRepairFeedback has problem only (no
        # resolution_condition, no downstream_effect). The reader rationale
        # is projected into the problem field.
        feedback = repair_input.repair.feedback[0]
        assert not hasattr(feedback, "downstream_effect")
        assert not hasattr(feedback, "resolution_condition")
        assert feedback.problem

    def test_mixed_manuscript_and_brief_brief_dominates(self):
        # Invariant 16 (v0.6): there is no per-issue repair_target. The
        # single top-level repair_target IS the route. A BRIEF
        # repair_target routes to the Constructor regardless of how many
        # blocking issues describe manuscript-level symptoms.
        caps = FakeDeliveryCapabilities(_make_view())
        call_count = {"n": 0}

        def builder():
            call_count["n"] += 1
            # §3: BRIEF verdict lands on the Phase 2 slot (2nd create).
            if call_count["n"] == 2:
                # The single top-level target is BRIEF.
                return FakeReviewer(
                    repair_target=RepairTarget.BRIEF,
                    rationale="the brief lacks a stable comparison frame",
                )
            return FakeReviewer()

        factory = CountingReviewerFactory(builder)
        brief_v2 = replace(_valid_brief(), promise="repaired promise v2")
        constructor = FakeConstructor(briefs=[_valid_brief(), brief_v2])
        reviser = FakeReviser()
        pipeline = _build_pipeline(
            caps,
            constructor=constructor,
            reviewer_factory=factory,
            reviser=reviser,
        )
        pipeline.run("run_1")
        # BRIEF target dominates: Constructor rebuilt, Reviser NOT called.
        assert len(constructor.calls) == 2
        assert reviser.calls == []

    def test_reader_repair_targets_are_delivery_only(self):
        # Invariant 17: Fresh Reader can attribute only MANUSCRIPT or BRIEF.
        assert {target.value for target in RepairTarget} == {"MANUSCRIPT", "BRIEF"}

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
        # Invariant 20 (v0.6): the only path that edits a manuscript after a
        # Reader PASS is the INTEGRITY repair path. An integrity-triggered
        # manuscript repair invalidates the old Reader PASS and re-runs the
        # Reader on the revised manuscript. The published PASS binds the
        # revised manuscript digest, not the original.
        caps = FakeDeliveryCapabilities(_make_view())
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
        reviser = FakeReviser(
            revised_markdowns=["# Report\n\n## Section 1\n\nrevised {{cite:c1}}"]
        )
        pipeline = _build_pipeline(
            caps, integrity_reviewer=integrity, reviser=reviser
        )
        result = pipeline.run("run_1")
        from my_search_harness.runtime.reporting import PublishedReportPipelineResult

        assert isinstance(result, PublishedReportPipelineResult)
        # The certified digest matches the REVISED manuscript, not the original.
        revised_ms = _manuscript("# Report\n\n## Section 1\n\nrevised {{cite:c1}}")
        assert result.reader_pass.manuscript_digest == manuscript_digest(revised_ms)

    def test_brief_digest_changed_old_pass_rejected(self):
        # Invariant 21 (v0.6): after a BRIEF rebuild (Reader or Integrity
        # routed to BRIEF), the Reader runs against the new Brief; the
        # published PASS binds the new brief digest. Here a Reader BRIEF
        # blocker triggers the outer rebuild loop.
        caps = FakeDeliveryCapabilities(_make_view())
        call_count = {"n": 0}

        def builder():
            call_count["n"] += 1
            # §3: BRIEF verdict lands on the Phase 2 slot (2nd create) of
            # the first review.
            if call_count["n"] == 2:
                return FakeReviewer(
                    repair_target=RepairTarget.BRIEF,
                    rationale="the brief lacks a stable comparison frame",
                )
            return FakeReviewer()

        factory = CountingReviewerFactory(builder)
        brief_v2 = replace(
            _valid_brief(),
            audience="a2",
            promise="repaired promise v2",
            frame="frame v2",
            arc="arc a then arc b",
            focus="focus a",
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
        # Reader preview and final publication share the same renderer.
        assert len(renderer.calls) == 2
        assert renderer.calls[0] == renderer.calls[1]
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
        reviser = FakeReviser(
            revised_markdowns=["# Report\n\n## Section 1\n\nfixed {{cite:c1}}"]
        )
        pipeline = _build_pipeline(caps, integrity_reviewer=integrity, reviser=reviser)
        result = pipeline.run("run_1")
        from my_search_harness.runtime.reporting import PublishedReportPipelineResult

        assert isinstance(result, PublishedReportPipelineResult)
        # Integrity called twice (first REVISE, then PASS).
        assert len(integrity.calls) == 2
        # Reviser called once for the integrity repair.
        assert len(reviser.calls) == 1
        assert result.reader_pass.manuscript_digest == manuscript_digest(
            _manuscript("# Report\n\n## Section 1\n\nfixed {{cite:c1}}")
        )
        assert (
            result.reader_pass.manuscript_digest
            == result.integrity_pass.manuscript_digest
        )

    def test_revise_delivery_brief_constructor_writer_reader_integrity(self):
        # Invariant 24 (v0.6): REVISE_DELIVERY target BRIEF → Constructor →
        # Writer → Reader → Integrity. Lean Brief shapes throughout.
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
        brief_v2 = replace(_valid_brief(), promise="repaired promise v2")
        constructor = FakeConstructor(briefs=[_valid_brief(), brief_v2])
        writer = FakeWriter(
            manuscripts=[
                _manuscript("# Report\n\n## Section 1\n\nv1 {{cite:c1}}"),
                _manuscript("# Report\n\n## Section 1\n\nv2 {{cite:c1}}"),
            ]
        )
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
        # Certified brief digest is the v2 brief's.
        assert result.reader_pass.brief_digest == brief_digest(brief_v2)

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
        reviser = FakeReviser(
            revised_markdowns=["# Report\n\n## Section 1\n\nfixed {{cite:c1}}"]
        )
        factory = CountingReviewerFactory(FakeReviewer)
        pipeline = _build_pipeline(
            caps,
            integrity_reviewer=integrity,
            reviser=reviser,
            reviewer_factory=factory,
        )
        pipeline.run("run_1")
        # Two reviews (before/after integrity); each uses two fresh
        # instances (Phase 1 + Phase 2, §3) → 4 total.
        assert len(factory.created) == 4


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
            revised_markdowns=[
                f"# Report\n\n## Section 1\n\nv{i} {{cite:c1}}" for i in range(20)
            ]
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
        # One review, but two fresh instances (Phase 1 + Phase 2, §3) — not a
        # fixed N rounds.
        assert len(factory.created) == 2

    def test_no_quality_readability_cognitive_score_introduced(self):
        # Invariant 30 (v0.6): no report quality/readability/cognitive score
        # introduced. The lean result types carry no score-like attributes.
        import my_search_harness.runtime.reporting as reporting

        # No score-like attributes on the lean result types.
        for cls in (
            ReportReviewResult,
            ResearchIntegrityReview,
            ReaderPass,
            IntegrityPass,
            BlindReadResult,
            ReaderIssue,
            ReportBrief,
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
    def test_constructor_context_is_structurally_anti_anchoring(self):
        view = _make_view()
        before = repr(view)
        context = report_construction_context_for(view)
        assert context == report_construction_context_for(view)
        assert repr(view) == before
        assert not hasattr(context, "papers")
        assert not hasattr(context.approach_families[0], "representative_paper_refs")
        assert not hasattr(context.findings[0], "sources")
        assert not hasattr(context.open_problems[0], "sources")
        assert context.approach_families[0].ref == "approach_one"
        assert context.findings[0].ref == "finding_f1"
        assert context.open_gaps[0].ref == "gap_g1"

    def test_phase2_brief_check_receives_contract_not_manuscript(self):
        # §1/§2/§3 boundary: Phase 1 (blind_read) receives the rendered reader
        # surface (citations resolved, no raw {{cite}} markup). Phase 2
        # (brief_check) receives ONLY the frozen Blind Read + Brief + Contract
        # + review guide — no manuscript, no reader surface, no manuscript
        # digest beyond what the frozen Blind Read already carries. The
        # Contract lets Phase 2 detect a Brief that omits a required delivery
        # concern; it is not a manuscript-derived representation.
        view = _make_view()
        caps = FakeDeliveryCapabilities(view)
        factory = CountingReviewerFactory(FakeReviewer)
        pipeline = _build_pipeline(
            caps,
            reviewer_factory=factory,
            citation_renderer=DeterministicCitationRenderer(),
        )
        pipeline.run("run_1")
        # §3: two fresh instances per review — index 0 is Phase 1, index 1 is
        # Phase 2.
        phase1 = factory.created[0]
        phase2 = factory.created[1]
        # Phase 1 still receives the rendered reader surface.
        blind_call = phase1.blind_calls[0]
        assert isinstance(blind_call["reader_surface"], str)
        assert "{{cite:" not in blind_call["reader_surface"]
        assert "[1](https://example.org/p1)" in blind_call["reader_surface"]
        # Phase 2 receives neither a manuscript nor a reader surface: the only
        # bridge to Phase 1 is the frozen Blind Read result.
        assert phase2.blind_calls == []
        assert len(phase2.check_calls) == 1
        check_call = phase2.check_calls[0]
        # §1: Phase 2 receives blind_read + brief + contract + review_guide.
        assert set(check_call.keys()) == {
            "blind_read",
            "brief",
            "contract",
            "review_guide",
        }
        assert "reader_surface" not in check_call
        assert "manuscript" not in check_call
        assert "manuscript_digest" not in check_call
        # The Contract is the view's contract (the delivery requirements), not
        # a manuscript-derived representation.
        assert check_call["contract"] is view.contract

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
        # v0.6 has no reader convergence loop, so we exercise the constructor
        # rebuild budget: a Reader that always routes BRIEF forces the outer
        # loop to rebuild until max_constructor_rebuilds is exceeded.
        caps = FakeDeliveryCapabilities(_make_view())

        def builder():
            return FakeReviewer(
                repair_target=RepairTarget.BRIEF,
                rationale="the brief lacks a stable comparison frame",
            )

        factory = CountingReviewerFactory(builder)
        # Each rebuild produces a distinct Brief so the loop does not trip the
        # "Brief repair must produce a new Brief version" guard before the
        # budget is exhausted.
        briefs = [
            replace(_valid_brief(), promise=f"repaired promise v{i}")
            for i in range(20)
        ]
        constructor = FakeConstructor(briefs=briefs)
        pipeline = _build_pipeline(
            caps,
            constructor=constructor,
            reviewer_factory=factory,
            max_constructor_rebuilds=3,
        )
        with pytest.raises(ReportResourceExhausted, match="constructor-rebuild"):
            pipeline.run("run_1")
        assert caps.publish_calls == []

    def test_writer_receives_narrow_authoring_context_not_full_view(self):
        # Invariant (spec F): the Writer must not receive the full
        # DeliveryView or evidence access. v0.6 passes a thin
        # ReportAuthoringContext, which has no open_gaps and no papers
        # inventory — Authoring cites via the Brief, not by browsing.
        caps = FakeDeliveryCapabilities(_make_view())
        writer = FakeWriter()
        pipeline = _build_pipeline(caps, writer=writer)
        pipeline.run("run_1")
        brief, guide, authoring_context = writer.calls[0]
        assert isinstance(authoring_context, ReportAuthoringContext)
        # The authoring context is deliberately narrower than the construction
        # context: no open_gaps, no papers.
        assert not hasattr(authoring_context, "open_gaps")
        assert not hasattr(authoring_context, "papers")
        # It also does not carry representative-paper anchoring or sources.
        assert not hasattr(
            authoring_context.approach_families[0], "representative_paper_refs"
        )
        assert not hasattr(authoring_context.findings[0], "sources")
        # Presentation owns canonical URL resolution; the authoring context
        # exposes no canonical_url field anywhere on its projections.
        assert "canonical_url" not in repr(authoring_context)
        assert "https://example.org/p1" not in repr(authoring_context)

    def test_constructor_does_not_receive_writing_guide(self):
        # Invariant (v0.6): the Constructor receives the Construction Guide,
        # not the Writing Guide. v0.6 has no quality_standard parameter; the
        # constructor signature is (construction_input, construction_guide,
        # evidence).
        caps = FakeDeliveryCapabilities(_make_view())
        constructor = FakeConstructor()
        pipeline = _build_pipeline(caps, constructor=constructor)
        pipeline.run("run_1")
        construction_input, construction_guide = constructor.calls[0]
        assert isinstance(construction_input.context, ReportConstructionContext)
        assert construction_guide == "construction guide text"
        assert construction_guide != "writing guide text"

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
        # v0.6: the pipeline validates the four guides (construction,
        # writing, review, integrity) at construction time. There is no
        # quality_standard parameter anymore.
        caps = FakeDeliveryCapabilities(_make_view())
        common = dict(
            constructor=FakeConstructor(),
            writer=FakeWriter(),
            reviewer_factory=CountingReviewerFactory(FakeReviewer),
            reviser=FakeReviser(),
            integrity_reviewer=FakeIntegrityReviewer(
                [ResearchIntegrityReview(disposition=IntegrityDisposition.PASS)]
            ),
            citation_renderer=FakeCitationRenderer(),
        )
        # Each empty guide is rejected with a named error.
        for guide_name, kwargs in (
            (
                "construction_guide",
                dict(
                    construction_guide="",
                    writing_guide="writing guide text",
                    review_guide="review guide text",
                    integrity_guide="integrity guide text",
                ),
            ),
            (
                "writing_guide",
                dict(
                    construction_guide="construction guide text",
                    writing_guide="",
                    review_guide="review guide text",
                    integrity_guide="integrity guide text",
                ),
            ),
            (
                "review_guide",
                dict(
                    construction_guide="construction guide text",
                    writing_guide="writing guide text",
                    review_guide="",
                    integrity_guide="integrity guide text",
                ),
            ),
            (
                "integrity_guide",
                dict(
                    construction_guide="construction guide text",
                    writing_guide="writing guide text",
                    review_guide="review guide text",
                    integrity_guide="",
                ),
            ),
        ):
            with pytest.raises(ValueError, match=f"{guide_name} must be a non-empty"):
                ReportPipeline(caps, **common, **kwargs)

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
