"""Semantic report orchestration over the frozen Delivery boundary.

This module implements the ADR-012 report pipeline:

    Research State
        ↓ Completion PASS / DeliveryBasis
    Report Constructor → Report Brief
        ↓
    Writer → Manuscript
        ↓
    Report Reviewer (fresh instance, two-phase cold reading)
        ├─ Blocking Issues → earliest repair layer (MANUSCRIPT / BRIEF / RESEARCH)
        └─ no Blocking Issues → Reader PASS (brief_digest + manuscript_digest)
        ↓
    Research Integrity Reviewer
        ├─ PASS → Citation Renderer → Publish
        ├─ REVISE_DELIVERY → earliest faulty layer → Reader again → Integrity again
        └─ REOPEN_RESEARCH → RESEARCH
    ...

The pipeline is an Action loop, not a Report FSM. Python enforces legal call
order, stable-ref validation, freshness/digests, the blind context boundary,
fresh reviewer instances, repair routing, and the render/publish gate. It never
judges article quality, scores cognition, or decides which classification is
better — those remain Agent (semantic) responsibilities.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypeAlias

from my_search_harness.domain.model import DeliveryBasis, SourceLocator

from .capabilities import DeliveryCapabilities
from .context import DeliveryView, InspectResult
from .delivery import PublishReportResult, _ReportPublicationAuthorization
from .source_access import ReadSourceResult, SourceAccessAttemptError


class ReportPipelineError(RuntimeError):
    """A report-stage boundary rejected semantic runner output."""


class ReportWritingGuideLoadError(RuntimeError):
    """The configured report writing guideline cannot be loaded."""


class ReportCaptureError(RuntimeError):
    """A runtime-local Delivery capture could not be persisted safely."""


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
# Stable, unchanged delivery value objects
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True, kw_only=True)
class CitationReference:
    citation_id: str
    paper_ref: str
    locator: SourceLocator | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportManuscript:
    markdown: str
    citations: tuple[CitationReference, ...] = ()


# ---------------------------------------------------------------------------
# Report Brief — the single report-semantic middle layer
# ---------------------------------------------------------------------------
# Report Brief replaces NarrativePlan. It is a Delivery work product, not a
# Research Domain entity: it is NOT stored in ResearchRun, has no stable
# identity, no independent lifecycle, and no ArtifactKind. Its freshness binds
# the DeliveryBasis (not a generic state_revision). Sections carry stable-ref
# validation; material is a lightweight value object (no MaterialRole enum).


@dataclass(slots=True, frozen=True, kw_only=True)
class BriefMaterial:
    """High-density distilled fact/condition/number/limit with its evidence refs.

    A Delivery-specific distillation, not new Research truth and not a cache of
    raw SourceContent. ``role`` is an optional natural-language hint
    ("mechanism contrast", "limitation", ...); there is intentionally no
    MaterialRole enum.
    """

    content: str
    role: str | None = None
    research_refs: tuple[str, ...] = ()
    source_ref: str | None = None
    locator: SourceLocator | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportBriefSection:
    title: str
    purpose: str
    reader_takeaway: str
    argument_flow: str
    requirement_refs: tuple[str, ...] = ()
    research_refs: tuple[str, ...] = ()
    material: tuple[BriefMaterial, ...] = ()
    evidence_boundary: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportBrief:
    audience: str
    report_goal: str
    reader_takeaway: str
    narrative_logic: str
    sections: tuple[ReportBriefSection, ...]
    terminology: tuple[tuple[str, str], ...] = ()
    intentional_omissions: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Reviewer result types
# ---------------------------------------------------------------------------


class RepairTarget(StrEnum):
    """The earliest layer qualified to fix a blocking issue.

    Precedence (most-upstream fault wins) is enforced by the pipeline, not by
    the Reviewer. POSSIBLE_RESEARCH_ISSUE is a discovery/escalation signal,
    never a confirmed Research fault — the Reader has no research authority.
    """

    MANUSCRIPT = "MANUSCRIPT"
    BRIEF = "BRIEF"
    POSSIBLE_RESEARCH_ISSUE = "POSSIBLE_RESEARCH_ISSUE"


@dataclass(slots=True, frozen=True, kw_only=True)
class BlockingIssue:
    """A root-cause reader/argument failure that blocks the report promise.

    One issue per cognitive root cause (symptoms with the same root are
    consolidated). No score, no severity rank — the pipeline routes by target
    layer and precedence, never by "which problem is worse".
    """

    problem: str
    reader_effect: str
    why_blocking: str
    repair_target: RepairTarget
    location: str | None = None
    brief_ref: str | None = None
    suggested_repair_direction: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class BlindBlockingIssue:
    """A Phase 1 reader failure with no repair-layer authority."""

    problem: str
    reader_effect: str
    why_blocking: str
    location: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class BlindReadResult:
    """Frozen Phase 1 reconstruction of what a first reader actually understood.

    Bound to the manuscript digest it read. Produced before the Brief is
    exposed to the same review; Phase 2 must not rewrite or reinterpret it.
    """

    core_understanding: str
    domain_model: str
    comparison_coordinates: str
    reverse_outline: str
    manuscript_digest: str
    blocking_issues: tuple[BlindBlockingIssue, ...] = ()


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportReviewResult:
    """Phase 2 attribution for one frozen Blind Read.

    ``blocking_issues == ()`` is the only semantic PASS condition.
    """

    blind_read_digest: str
    brief_digest: str
    manuscript_digest: str
    blocking_issues: tuple[BlockingIssue, ...] = ()


# ---------------------------------------------------------------------------
# Integrity review (disposition kept stable; REVISE_DELIVERY now carries a target)
# ---------------------------------------------------------------------------


class IntegrityDisposition(StrEnum):
    PASS = "PASS"
    REVISE_DELIVERY = "REVISE_DELIVERY"
    REOPEN_RESEARCH = "REOPEN_RESEARCH"


@dataclass(slots=True, frozen=True, kw_only=True)
class ResearchIntegrityReview:
    disposition: IntegrityDisposition
    issues: tuple[str, ...] = ()
    # Required for REVISE_DELIVERY: the earliest faulty Delivery layer.
    # Ignored for PASS / REOPEN_RESEARCH (must be None).
    revise_target: RepairTarget | None = None


# ---------------------------------------------------------------------------
# Version-bound certifications (lightweight metadata, NOT Domain entities)
# ---------------------------------------------------------------------------


def _jsonable(value: object) -> object:
    """Convert a frozen dataclass value tree into a JSON-serializable form.

    DeliveryBasis carries a ``datetime`` (PartialAuthorizationBasis), so a plain
    ``asdict`` + ``json.dumps`` would raise. This walker renders datetimes as
    ISO strings and tuples/sets as sorted lists, yielding a stable canonical
    form used for digesting and for the Integrity PASS basis key.
    """

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (frozenset, set)):
        return sorted((_jsonable(item) for item in value), key=repr)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def _canonical(value: object) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, ensure_ascii=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _delivery_basis_key(basis: DeliveryBasis) -> str:
    """A stable string identity for a DeliveryBasis value.

    DeliveryBasis is a frozen union of frozen dataclasses, so its field tuple
    is a stable identity. Used for Integrity PASS binding and Brief freshness.
    """

    return _canonical(basis)


def brief_digest(brief: ReportBrief) -> str:
    """A canonical digest of the Report Brief's semantic content."""

    return _digest(_canonical(brief))


def delivery_basis_key(basis: DeliveryBasis) -> str:
    """Public runtime identity used by persisted ephemeral Delivery sessions."""

    return _delivery_basis_key(basis)


def manuscript_digest(manuscript: ReportManuscript) -> str:
    """A canonical digest of the manuscript markdown + citation declarations."""

    payload = {
        "markdown": manuscript.markdown,
        "citations": [asdict(c) for c in manuscript.citations],
    }
    return _digest(_canonical(payload))


def blind_read_digest(blind_read: BlindReadResult) -> str:
    """Digest the complete frozen Phase 1 result, including reader failures."""

    return _digest(_canonical(blind_read))


@dataclass(slots=True, frozen=True, kw_only=True)
class ReaderPass:
    """Reader Gate PASS certifies a specific (brief, manuscript) version pair."""

    brief_digest: str
    manuscript_digest: str


@dataclass(slots=True, frozen=True, kw_only=True)
class IntegrityPass:
    """Integrity PASS certifies (delivery_basis, brief, manuscript) together."""

    delivery_basis_key: str
    brief_digest: str
    manuscript_digest: str


# ---------------------------------------------------------------------------
# Delivery evidence access (unchanged mechanics)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Citation metadata projection for the Writer (narrowest read-only surface)
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True, kw_only=True)
class CitationMetadata:
    """Narrow read-only navigation/citation metadata for the Writer.

    The Writer must NOT receive the full DeliveryView or broad evidence access.
    This projection carries only the per-paper metadata needed to write
    canonical citations and first-use navigation links.
    """

    papers: tuple[tuple[str, str, str | None], ...]
    """Each entry: (paper_ref, title, canonical_url_or_None)."""


def citation_metadata_for(view: DeliveryView) -> CitationMetadata:
    """Project the narrowest citation/navigation metadata from a DeliveryView."""

    return CitationMetadata(
        papers=tuple(
            (paper.ref, paper.title, paper.canonical_url) for paper in view.papers
        )
    )


# ---------------------------------------------------------------------------
# Capture sink — observability without authority
# ---------------------------------------------------------------------------


class ReportCaptureSink(Protocol):
    """Optional observer for Delivery work products.

    Captures are NOT Research truth, never enter ResearchRun, never gain
    stable identity, and never affect lifecycle. The runtime only emits; the
    host/Skill decides file paths. ``NoopReportCaptureSink`` is the default.
    """

    def capture(self, run_id: str, name: str, payload: str) -> None: ...


class NoopReportCaptureSink:
    """Default capture sink: observes nothing, touches nothing."""

    def capture(self, run_id: str, name: str, payload: str) -> None:  # noqa: D401
        """Discard the capture."""


class LocalReportCaptureSink:
    """Persist non-authoritative report captures below workspace scratch."""

    def __init__(self, scratch_root: str | Path) -> None:
        self._scratch_root = Path(scratch_root)

    def capture(self, run_id: str, name: str, payload: str) -> None:
        if (
            not isinstance(run_id, str)
            or not run_id
            or "/" in run_id
            or "\\" in run_id
            or run_id in {".", ".."}
        ):
            raise ReportCaptureError("capture run_id is invalid")
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or name in {".", ".."}
        ):
            raise ReportCaptureError("capture name must be a plain file name")
        if not isinstance(payload, str):
            raise ReportCaptureError("capture payload must be text")
        directory = self._scratch_root / run_id / "captures" / "report"
        path = directory / name
        temporary = path.with_name(f"{path.name}.tmp")
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(payload)
                if not payload.endswith("\n"):
                    stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ReportCaptureError(f"cannot persist report capture {name}") from exc


# ---------------------------------------------------------------------------
# Semantic-stage Protocols
# ---------------------------------------------------------------------------


class ReportConstructor(Protocol):
    """Construct the Report Brief: select, expand, organize, omit.

    Reads Research Contract, Delivery View, Delivery Basis, and the Report
    Quality Standard. Does NOT read the Report Writing Guide (language is not
    its job). May targeted inspect/read source to recover explanatory detail,
    but must not produce new consensus, stronger generalization, new approach
    relationships, or contract-facing research judgments — those require
    reopening RESEARCH via ``ResearchEscalationRequired``.
    """

    def construct(
        self,
        view: DeliveryView,
        quality_standard: str,
        evidence: DeliveryEvidenceAccess,
    ) -> ReportBrief: ...


class ReportWriter(Protocol):
    """Implement the Brief as a natural, professional manuscript.

    Reads the Report Brief, the Report Writing Guide, and the narrow citation
    metadata. Does NOT receive the full DeliveryView or broad evidence access.
    If the Brief is insufficient, raise ``BriefInsufficient`` to return to the
    Constructor rather than re-doing research or re-designing the report.
    """

    def write(
        self,
        brief: ReportBrief,
        writing_guide: str,
        citation_metadata: CitationMetadata,
    ) -> ReportManuscript: ...


class ReportReviewer(Protocol):
    """One-use two-phase cold reader for a single manuscript version.

    Phase 1 (Blind Read) receives only deliverable/audience/quality standard/
    review guide/manuscript — never the Brief or Writing Guide. Phase 2 (Brief
    Check) receives the frozen Blind Read result plus the Brief. The runtime
    guarantees Phase 1 is frozen before Phase 2 sees the Brief.
    """

    def blind_read(
        self,
        deliverable_description: str,
        audience: str,
        quality_standard: str,
        review_guide: str,
        manuscript: ReportManuscript,
    ) -> BlindReadResult: ...

    def brief_check(
        self,
        blind_read: BlindReadResult,
        brief: ReportBrief,
        manuscript: ReportManuscript,
        review_guide: str,
    ) -> ReportReviewResult: ...


class ReportReviewerFactory(Protocol):
    """Create a reviewer with no authority carried over from earlier reviews.

    A fresh instance is required for every manuscript version so the reviewer
    cannot confirm "I see you fixed what I asked" instead of re-reading cold.
    """

    def create(self) -> ReportReviewer: ...


class ReportReviser(Protocol):
    """Fix explicit Manuscript blockers; no broad research/source authority.

    Reads the Brief, current manuscript, reader/integrity issues, the Writing
    Guide, and narrow citation metadata. If an issue belongs to the Brief, it
    must be routed back to the Constructor; if it needs new research judgment,
    it must be escalated — the Reviser does neither itself.
    """

    def revise(
        self,
        brief: ReportBrief,
        manuscript: ReportManuscript,
        issues: tuple[BlockingIssue, ...],
        writing_guide: str,
        citation_metadata: CitationMetadata,
    ) -> ReportManuscript: ...


class ResearchIntegrityReviewer(Protocol):
    """Independent of the Reader Gate. Checks research fidelity, not prose.

    Reads DeliveryView, Report Brief, Manuscript, Delivery Evidence Access,
    and the Research Integrity Guide. Does NOT use the Writing Guide as a
    style rubric. REVISE_DELIVERY must carry the earliest faulty Delivery
    target (MANUSCRIPT or BRIEF); REOPEN_RESEARCH returns to RESEARCH.
    """

    def review(
        self,
        view: DeliveryView,
        brief: ReportBrief,
        manuscript: ReportManuscript,
        evidence: DeliveryEvidenceAccess,
        integrity_guide: str,
    ) -> ResearchIntegrityReview: ...


class ReportCitationRenderer(Protocol):
    """Deterministic implementation is supplied by the citation boundary."""

    def render(self, view: DeliveryView, manuscript: ReportManuscript) -> str: ...


# ---------------------------------------------------------------------------
# Exceptions for Brief-insufficient / research-escalation signals
# ---------------------------------------------------------------------------


class BriefInsufficient(ReportPipelineError):
    """Writer/Reviewer found the Brief cannot carry the report promise.

    Routes back to the Report Constructor (BRIEF), not to research by itself.
    """

    def __init__(self, rationale: str) -> None:
        if not isinstance(rationale, str) or not rationale:
            raise ValueError("brief-insufficient rationale must be non-empty")
        super().__init__(rationale)
        self.rationale = rationale


class ReportResourceExhausted(ReportPipelineError):
    """A hard resource limit terminated the loop. This is NOT a PASS."""


# ---------------------------------------------------------------------------
# Pipeline result types
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True, kw_only=True)
class PublishedReportPipelineResult:
    report_brief: ReportBrief
    reader_pass: ReaderPass
    integrity_pass: IntegrityPass
    artifact: PublishReportResult


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportResearchReopenedResult:
    state_revision: int
    rationale: str


@dataclass(slots=True, frozen=True, kw_only=True)
class ResearchConfirmationRequiredResult:
    """Reader suspicion awaiting a separate actor with Research Authority."""

    rationale: str
    issues: tuple[BlockingIssue, ...]
    brief_digest: str
    manuscript_digest: str


ReportPipelineResult: TypeAlias = (
    PublishedReportPipelineResult
    | ReportResearchReopenedResult
    | ResearchConfirmationRequiredResult
)


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

# A resource guard, not a semantic stop. Hitting it raises
# ReportResourceExhausted — never interpreted as PASS. Defaults are generous;
# the host may lower them for bounded environments.
_DEFAULT_MAX_CONSTRUCTOR_REBUILDS = 8
_DEFAULT_MAX_READER_ROUNDS = 12
_DEFAULT_MAX_INTEGRITY_ROUNDS = 12


class ReportPipeline:
    """Run the ADR-012 semantic stages with deterministic invariants.

    It is an Action pipeline, not a Report FSM. No new lifecycle mode is
    introduced; everything here runs inside the existing DELIVERY mode.
    """

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
        quality_standard: str,
        writing_guide: str,
        review_guide: str,
        integrity_guide: str,
        capture_sink: ReportCaptureSink | None = None,
        max_constructor_rebuilds: int = _DEFAULT_MAX_CONSTRUCTOR_REBUILDS,
        max_reader_rounds: int = _DEFAULT_MAX_READER_ROUNDS,
        max_integrity_rounds: int = _DEFAULT_MAX_INTEGRITY_ROUNDS,
    ) -> None:
        self._validate_guides(
            quality_standard, writing_guide, review_guide, integrity_guide
        )
        for name, value in (
            ("max_constructor_rebuilds", max_constructor_rebuilds),
            ("max_reader_rounds", max_reader_rounds),
            ("max_integrity_rounds", max_integrity_rounds),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive int")
        self._delivery = delivery
        self._constructor = constructor
        self._writer = writer
        self._reviewer_factory = reviewer_factory
        self._reviser = reviser
        self._integrity_reviewer = integrity_reviewer
        self._citation_renderer = citation_renderer
        self._quality_standard = quality_standard
        self._writing_guide = writing_guide
        self._review_guide = review_guide
        self._integrity_guide = integrity_guide
        self._capture = capture_sink or NoopReportCaptureSink()
        self._max_constructor_rebuilds = max_constructor_rebuilds
        self._max_reader_rounds = max_reader_rounds
        self._max_integrity_rounds = max_integrity_rounds

    def run(self, run_id: str) -> ReportPipelineResult:
        self._current_run_id = run_id
        view = self._delivery.view(run_id)
        evidence = DeliveryEvidenceAccess(self._delivery, run_id, view.state_revision)
        try:
            return self._run_delivery(run_id, view, evidence)
        except _ResearchConfirmationSignal as exc:
            return exc.result
        except ResearchEscalationRequired as exc:
            return self._reopen(run_id, evidence.state_revision, exc.rationale)

    # -- the main delivery loop -------------------------------------------------

    def _run_delivery(
        self,
        run_id: str,
        view: DeliveryView,
        evidence: DeliveryEvidenceAccess,
    ) -> ReportPipelineResult:
        basis_key = _delivery_basis_key(view.delivery_basis)
        rejected_brief_digest: str | None = None

        for _ in range(self._max_constructor_rebuilds):
            brief = self._construct_brief(view, evidence)
            b_digest = brief_digest(brief)
            if rejected_brief_digest == b_digest:
                raise ReportPipelineError(
                    "Brief repair must produce a new Brief version"
                )
            self._capture.capture(run_id, "report_brief.json", _brief_json(brief))

            try:
                manuscript, reader_pass = self._writer_then_reader(
                    run_id, view, brief, b_digest
                )
            except BriefInsufficient:
                # Reader (or integrity re-review) routed a blocker to the Brief:
                # rebuild the Brief via a fresh Constructor pass. The most
                # upstream fault wins, so a BRIEF route always returns here
                # rather than revising the manuscript or reopening research.
                rejected_brief_digest = b_digest
                continue
            # Reader PASS obtained for the current Brief/Manuscript pair.
            integrity_outcome = self._integrity_loop(
                run_id,
                view,
                evidence,
                brief,
                b_digest,
                manuscript,
                reader_pass,
                basis_key,
            )
            if isinstance(integrity_outcome, _BriefRebuild):
                rejected_brief_digest = b_digest
                continue  # Integrity routed to BRIEF → new Constructor pass.
            if isinstance(integrity_outcome, _ResearchReopen):
                raise ResearchEscalationRequired(integrity_outcome.rationale)
            # _Published: both gates passed and the report was published.
            return PublishedReportPipelineResult(
                report_brief=brief,
                reader_pass=integrity_outcome.reader_pass,
                integrity_pass=integrity_outcome.integrity_pass,
                artifact=integrity_outcome.artifact,
            )
        raise ReportResourceExhausted(
            "report pipeline exceeded the constructor-rebuild resource budget; "
            "this is a resource stop, not a PASS"
        )

    # -- construction & writing -------------------------------------------------

    def _construct_brief(
        self,
        view: DeliveryView,
        evidence: DeliveryEvidenceAccess,
    ) -> ReportBrief:
        brief = self._constructor.construct(view, self._quality_standard, evidence)
        self._validate_brief(view, brief)
        return brief

    def _writer_then_reader(
        self,
        run_id: str,
        view: DeliveryView,
        brief: ReportBrief,
        b_digest: str,
    ) -> tuple[ReportManuscript, ReaderPass]:
        """Write a manuscript, then run the Reader gate to PASS.

        Raises ``ResearchEscalationRequired`` (RESEARCH route) or
        ``BriefInsufficient`` (BRIEF route) so the outer loop can react; the
        MANUSCRIPT route is handled internally by ``_review_until_reader_pass``.
        The Writer is invoked exactly once here; downstream re-reads after a
        revision or an integrity repair never re-write the manuscript.
        """

        manuscript = self._write_manuscript(brief)
        self._capture.capture(run_id, "manuscript_pre_reader.md", manuscript.markdown)
        return self._review_until_reader_pass(run_id, view, brief, b_digest, manuscript)

    def _review_until_reader_pass(
        self,
        run_id: str,
        view: DeliveryView,
        brief: ReportBrief,
        b_digest: str,
        manuscript: ReportManuscript,
    ) -> tuple[ReportManuscript, ReaderPass]:
        """Run the Reader gate on ``manuscript`` to PASS.

        Reviews the given manuscript with a fresh reviewer. On a MANUSCRIPT
        blocker, revises and re-reviews the revision with another fresh
        reviewer until PASS — each revision gets its own cold read. BRIEF
        blockers raise ``BriefInsufficient``; RESEARCH blockers raise
        ``ResearchEscalationRequired``. The manuscript is never re-written
        here; only the Reviser touches it.
        """

        current = manuscript
        for _ in range(self._max_reader_rounds):
            result = self._fresh_review(run_id, view, brief, current)
            if not result.blocking_issues:
                return current, ReaderPass(
                    brief_digest=b_digest,
                    manuscript_digest=result.manuscript_digest,
                )
            target = self._route_issues(result.blocking_issues)
            if target is RepairTarget.BRIEF:
                raise BriefInsufficient(
                    "report reviewer routed a blocking issue to the Brief"
                )
            if target is RepairTarget.POSSIBLE_RESEARCH_ISSUE:
                raise _ResearchConfirmationSignal(
                    ResearchConfirmationRequiredResult(
                        rationale=(
                            "report reviewer flagged a possible research issue; "
                            "a stage with Research Authority must confirm it"
                        ),
                        issues=result.blocking_issues,
                        brief_digest=b_digest,
                        manuscript_digest=result.manuscript_digest,
                    )
                )
            # MANUSCRIPT → Reviser. A fresh reviewer re-reads the revision.
            rejected_manuscript_digest = manuscript_digest(current)
            current = self._reviser.revise(
                brief=brief,
                manuscript=current,
                issues=result.blocking_issues,
                writing_guide=self._writing_guide,
                citation_metadata=self._current_citation_metadata(),
            )
            self._validate_manuscript(current)
            if manuscript_digest(current) == rejected_manuscript_digest:
                raise ReportPipelineError(
                    "Manuscript repair must produce a new Manuscript version"
                )
            self._capture.capture(
                run_id, "manuscript_post_revision.md", current.markdown
            )
        raise ReportResourceExhausted(
            "reader gate exceeded the revision resource budget; "
            "this is a resource stop, not a PASS"
        )

    def _write_manuscript(self, brief: ReportBrief) -> ReportManuscript:
        # The Writer receives only the Brief + Writing Guide + narrow citation
        # metadata. It does NOT get the full DeliveryView or evidence access.
        citation_meta = self._current_citation_metadata()
        manuscript = self._writer.write(brief, self._writing_guide, citation_meta)
        self._validate_manuscript(manuscript)
        return manuscript

    def _current_citation_metadata(self) -> CitationMetadata:
        # Citation metadata is a narrow projection of the current DeliveryView.
        # Re-resolved per write so a reconstructed Brief sees consistent metadata.
        return citation_metadata_for(self._delivery.view(self._current_run_id))

    # -- reader gate (two-phase, fresh instance, version-bound) -----------------

    def _fresh_review(
        self,
        run_id: str,
        view: DeliveryView,
        brief: ReportBrief,
        manuscript: ReportManuscript,
    ) -> ReportReviewResult:
        reviewer = self._reviewer_factory.create()
        m_digest = manuscript_digest(manuscript)
        # Phase 1 — Blind Read. NO Brief, NO Writing Guide. Audience is a narrow
        # projection of the Brief (the audience string only).
        blind = reviewer.blind_read(
            deliverable_description=view.contract.deliverable_description,
            audience=brief.audience,
            quality_standard=self._quality_standard,
            review_guide=self._review_guide,
            manuscript=manuscript,
        )
        self._validate_blind_read(blind, m_digest)
        self._capture.capture(
            run_id,
            f"blind_review_{m_digest[:12]}.json",
            _blind_read_json(blind),
        )
        # Phase 2 — Brief Check. Only after Phase 1 is frozen.
        result = reviewer.brief_check(
            blind_read=blind,
            brief=brief,
            manuscript=manuscript,
            review_guide=self._review_guide,
        )
        b_digest = brief_digest(brief)
        self._validate_review_result(result, blind, b_digest, m_digest)
        self._capture.capture(
            run_id,
            f"reader_review_{m_digest[:12]}.json",
            _review_result_json(result),
        )
        return result

    # -- integrity gate --------------------------------------------------------

    def _integrity_loop(
        self,
        run_id: str,
        view: DeliveryView,
        evidence: DeliveryEvidenceAccess,
        brief: ReportBrief,
        b_digest: str,
        manuscript: ReportManuscript,
        reader_pass: ReaderPass,
        basis_key: str,
    ) -> _BriefRebuild | _ResearchReopen | _Published:
        """Run Integrity, route REVISE_DELIVERY, re-run Reader after any repair."""

        current_brief = brief
        current_b_digest = b_digest
        current_ms = manuscript
        current_reader_pass = reader_pass
        for _ in range(self._max_integrity_rounds):
            review = self._integrity_reviewer.review(
                view, current_brief, current_ms, evidence, self._integrity_guide
            )
            self._validate_integrity_review(review)
            m_digest = manuscript_digest(current_ms)
            self._capture.capture(
                run_id,
                f"integrity_review_{m_digest[:12]}.json",
                _integrity_review_json(review),
            )
            if review.disposition is IntegrityDisposition.REOPEN_RESEARCH:
                rationale = (
                    "; ".join(review.issues) or "integrity review reopened research"
                )
                return _ResearchReopen(rationale=rationale)
            if review.disposition is IntegrityDisposition.PASS:
                integrity_pass = IntegrityPass(
                    delivery_basis_key=basis_key,
                    brief_digest=current_b_digest,
                    manuscript_digest=m_digest,
                )
                rendered = self._render_and_publish(
                    run_id,
                    view,
                    current_ms,
                    evidence,
                    current_reader_pass,
                    integrity_pass,
                )
                return _Published(
                    reader_pass=current_reader_pass,
                    integrity_pass=integrity_pass,
                    artifact=rendered,
                )
            # REVISE_DELIVERY — route to the earliest faulty Delivery layer.
            target = self._require_integrity_revise_target(review)
            if target is RepairTarget.BRIEF:
                return _BriefRebuild()
            # MANUSCRIPT → Reviser → Reader again → loop back to Integrity.
            current_ms = self._revise_for_integrity(
                run_id, view, current_brief, current_ms, review
            )
            # Integrity-triggered manuscript edit invalidates the old Reader
            # PASS; re-run the Reader gate (review only — never re-write) on
            # the Reviser's output before looping back to Integrity.
            current_ms, reader_pass = self._review_until_reader_pass(
                run_id, view, current_brief, current_b_digest, current_ms
            )
            current_b_digest = reader_pass.brief_digest
            current_reader_pass = reader_pass
            # Loop back to Integrity with the new (b_digest, m_digest).
        raise ReportResourceExhausted(
            "integrity gate exceeded the revision resource budget; "
            "this is a resource stop, not a PASS"
        )

    def _revise_for_integrity(
        self,
        run_id: str,
        view: DeliveryView,
        brief: ReportBrief,
        manuscript: ReportManuscript,
        integrity: ResearchIntegrityReview,
    ) -> ReportManuscript:
        issues = tuple(
            BlockingIssue(
                problem=text,
                reader_effect=text,
                why_blocking=text,
                repair_target=RepairTarget.MANUSCRIPT,
            )
            for text in integrity.issues
        )
        revised = self._reviser.revise(
            brief=brief,
            manuscript=manuscript,
            issues=issues,
            writing_guide=self._writing_guide,
            citation_metadata=self._current_citation_metadata(),
        )
        self._validate_manuscript(revised)
        if manuscript_digest(revised) == manuscript_digest(manuscript):
            raise ReportPipelineError(
                "Integrity Manuscript repair must produce a new Manuscript version"
            )
        self._capture.capture(run_id, "manuscript_post_revision.md", revised.markdown)
        return revised

    # -- routing & render/publish ----------------------------------------------

    @staticmethod
    def _route_issues(issues: tuple[BlockingIssue, ...]) -> RepairTarget:
        """Compute the single repair target by precedence.

        Most-upstream fault wins: POSSIBLE_RESEARCH_ISSUE > BRIEF > MANUSCRIPT.
        Python only routes; it does not compute "which problem is worse".
        """

        targets = {issue.repair_target for issue in issues}
        if RepairTarget.POSSIBLE_RESEARCH_ISSUE in targets:
            return RepairTarget.POSSIBLE_RESEARCH_ISSUE
        if RepairTarget.BRIEF in targets:
            return RepairTarget.BRIEF
        if RepairTarget.MANUSCRIPT in targets:
            return RepairTarget.MANUSCRIPT
        raise ReportPipelineError("blocking issues carry no repair target")

    @staticmethod
    def _require_integrity_revise_target(
        integrity: ResearchIntegrityReview,
    ) -> RepairTarget:
        target = integrity.revise_target
        if target is None or target is RepairTarget.POSSIBLE_RESEARCH_ISSUE:
            raise ReportPipelineError(
                "REVISE_DELIVERY requires a MANUSCRIPT or BRIEF target"
            )
        return target

    def _render_and_publish(
        self,
        run_id: str,
        view: DeliveryView,
        manuscript: ReportManuscript,
        evidence: DeliveryEvidenceAccess,
        reader_pass: ReaderPass,
        integrity_pass: IntegrityPass,
    ) -> PublishReportResult:
        current_m_digest = manuscript_digest(manuscript)
        current_basis_key = _delivery_basis_key(view.delivery_basis)
        if (
            reader_pass.brief_digest != integrity_pass.brief_digest
            or reader_pass.manuscript_digest != current_m_digest
            or integrity_pass.manuscript_digest != current_m_digest
            or integrity_pass.delivery_basis_key != current_basis_key
        ):
            raise ReportPipelineError(
                "formal REPORT publication requires matching current Reader and "
                "Integrity certifications"
            )
        rendered = self._citation_renderer.render(view, manuscript)
        if not isinstance(rendered, str) or not rendered.strip():
            raise ReportPipelineError(
                "citation renderer must return non-empty report content"
            )
        # Publish against the latest evidence revision: source access during
        # Integrity may have advanced state_revision past the initial view.
        return self._delivery._publish_certified_report(
            run_id,
            evidence.state_revision,
            rendered,
            _ReportPublicationAuthorization(
                delivery_basis=view.delivery_basis,
                brief_digest=reader_pass.brief_digest,
                manuscript_digest=reader_pass.manuscript_digest,
                reader_brief_digest=reader_pass.brief_digest,
                reader_manuscript_digest=reader_pass.manuscript_digest,
                integrity_delivery_basis=view.delivery_basis,
                integrity_brief_digest=integrity_pass.brief_digest,
                integrity_manuscript_digest=integrity_pass.manuscript_digest,
            ),
        )

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

    # -- deterministic validation -----------------------------------------------

    @staticmethod
    def _validate_guides(
        quality_standard: str,
        writing_guide: str,
        review_guide: str,
        integrity_guide: str,
    ) -> None:
        for name, value in (
            ("quality_standard", quality_standard),
            ("writing_guide", writing_guide),
            ("review_guide", review_guide),
            ("integrity_guide", integrity_guide),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")

    @staticmethod
    def _research_refs(view: DeliveryView) -> set[str]:
        return {
            *(approach.ref for approach in view.approach_families),
            *(finding.ref for finding in view.findings),
            *(problem.ref for problem in view.open_problems),
            *(gap.ref for gap in view.open_gaps),
            *(paper.ref for paper in view.papers),
        }

    @staticmethod
    def _validate_brief(view: DeliveryView, brief: object) -> None:
        if not isinstance(brief, ReportBrief):
            raise ReportPipelineError("constructor must return ReportBrief")
        if (
            not brief.audience
            or not brief.report_goal
            or not brief.reader_takeaway
            or not brief.narrative_logic
            or not brief.sections
        ):
            raise ReportPipelineError(
                "ReportBrief requires audience, report_goal, reader_takeaway, "
                "narrative_logic, and sections"
            )
        research_refs = ReportPipeline._research_refs(view)
        requirement_refs = {r.ref for r in view.contract.requirements}
        paper_refs = {paper.ref for paper in view.papers}
        for section in brief.sections:
            if not isinstance(section, ReportBriefSection):
                raise ReportPipelineError("ReportBrief contains an invalid section")
            if (
                not section.title
                or not section.purpose
                or not section.reader_takeaway
                or not section.argument_flow
            ):
                raise ReportPipelineError(
                    "ReportBriefSection requires title, purpose, "
                    "reader_takeaway, and argument_flow"
                )
            for ref_list, label in (
                (section.requirement_refs, "requirement_refs"),
                (section.research_refs, "research_refs"),
            ):
                if not isinstance(ref_list, tuple) or not all(
                    isinstance(ref, str) and ref for ref in ref_list
                ):
                    raise ReportPipelineError(
                        f"ReportBriefSection {label} must be a tuple of non-empty strings"
                    )
            missing_req = set(section.requirement_refs) - requirement_refs
            if missing_req:
                raise ReportPipelineError(
                    f"ReportBrief has unknown requirement refs: {sorted(missing_req)!r}"
                )
            missing_research = set(section.research_refs) - research_refs
            if missing_research:
                raise ReportPipelineError(
                    f"ReportBrief has unknown research refs: {sorted(missing_research)!r}"
                )
            if not isinstance(section.material, tuple) or not all(
                isinstance(m, BriefMaterial) for m in section.material
            ):
                raise ReportPipelineError(
                    "ReportBriefSection material must be a tuple of BriefMaterial"
                )
            for material in section.material:
                if (
                    not isinstance(material.content, str)
                    or not material.content.strip()
                ):
                    raise ReportPipelineError("BriefMaterial content must be non-empty")
                if material.role is not None and (
                    not isinstance(material.role, str) or not material.role.strip()
                ):
                    raise ReportPipelineError(
                        "BriefMaterial role must be a non-empty string"
                    )
                if not isinstance(material.research_refs, tuple) or not all(
                    isinstance(ref, str) and ref for ref in material.research_refs
                ):
                    raise ReportPipelineError(
                        "BriefMaterial research_refs must be a tuple of non-empty strings"
                    )
                missing_material = set(material.research_refs) - research_refs
                if missing_material:
                    raise ReportPipelineError(
                        f"BriefMaterial has unknown research refs: "
                        f"{sorted(missing_material)!r}"
                    )
                if material.source_ref is not None and (
                    not isinstance(material.source_ref, str)
                    or material.source_ref not in paper_refs
                ):
                    raise ReportPipelineError(
                        "BriefMaterial source_ref must be a retained paper ref"
                    )
                if material.locator is not None and material.source_ref is None:
                    raise ReportPipelineError(
                        "BriefMaterial locator requires source_ref"
                    )
                if material.locator is not None and (
                    not isinstance(material.locator, SourceLocator)
                    or not isinstance(material.locator.kind, str)
                    or not material.locator.kind.strip()
                    or not isinstance(material.locator.value, str)
                    or not material.locator.value.strip()
                ):
                    raise ReportPipelineError("BriefMaterial locator is invalid")
        if not isinstance(brief.terminology, tuple) or not all(
            isinstance(term, tuple)
            and len(term) == 2
            and all(isinstance(value, str) and value for value in term)
            for term in brief.terminology
        ):
            raise ReportPipelineError("ReportBrief terminology is invalid")
        if not isinstance(brief.intentional_omissions, tuple) or not all(
            isinstance(omit, str) and omit for omit in brief.intentional_omissions
        ):
            raise ReportPipelineError("ReportBrief intentional_omissions is invalid")

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
    def _validate_blind_read(blind: object, expected_manuscript_digest: str) -> None:
        if not isinstance(blind, BlindReadResult):
            raise ReportPipelineError("reviewer blind_read must return BlindReadResult")
        for field_name in (
            "core_understanding",
            "domain_model",
            "comparison_coordinates",
            "reverse_outline",
        ):
            value = getattr(blind, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ReportPipelineError(
                    f"BlindReadResult.{field_name} must be a non-empty string"
                )
        if blind.manuscript_digest != expected_manuscript_digest:
            raise ReportPipelineError(
                "BlindReadResult must bind to the manuscript digest it read"
            )
        if not isinstance(blind.blocking_issues, tuple) or not all(
            isinstance(issue, BlindBlockingIssue) for issue in blind.blocking_issues
        ):
            raise ReportPipelineError(
                "BlindReadResult blocking_issues must contain BlindBlockingIssue"
            )
        for issue in blind.blocking_issues:
            for field_name in ("problem", "reader_effect", "why_blocking"):
                value = getattr(issue, field_name)
                if not isinstance(value, str) or not value.strip():
                    raise ReportPipelineError(
                        f"BlindBlockingIssue.{field_name} must be a non-empty string"
                    )
            if issue.location is not None and (
                not isinstance(issue.location, str) or not issue.location.strip()
            ):
                raise ReportPipelineError(
                    "BlindBlockingIssue.location must be a non-empty string or None"
                )

    @staticmethod
    def _validate_review_result(
        result: object,
        frozen_blind_read: BlindReadResult,
        expected_brief_digest: str,
        expected_manuscript_digest: str,
    ) -> None:
        if not isinstance(result, ReportReviewResult):
            raise ReportPipelineError(
                "reviewer brief_check must return ReportReviewResult"
            )
        expected_blind_read_digest = blind_read_digest(frozen_blind_read)
        if result.blind_read_digest != expected_blind_read_digest:
            raise ReportPipelineError(
                "ReportReviewResult attempts to replace the frozen Blind Read"
            )
        if result.brief_digest != expected_brief_digest:
            raise ReportPipelineError("ReportReviewResult binds the wrong brief digest")
        if result.manuscript_digest != expected_manuscript_digest:
            raise ReportPipelineError(
                "ReportReviewResult binds the wrong manuscript digest"
            )
        if not isinstance(result.blocking_issues, tuple) or not all(
            isinstance(issue, BlockingIssue) for issue in result.blocking_issues
        ):
            raise ReportPipelineError(
                "blocking_issues must be a tuple of BlockingIssue"
            )
        if frozen_blind_read.blocking_issues and not result.blocking_issues:
            raise ReportPipelineError(
                "Phase 2 cannot PASS while the frozen Blind Read has blocking issues"
            )
        for issue in result.blocking_issues:
            for field_name in ("problem", "reader_effect", "why_blocking"):
                value = getattr(issue, field_name)
                if not isinstance(value, str) or not value.strip():
                    raise ReportPipelineError(
                        f"BlockingIssue.{field_name} must be a non-empty string"
                    )
            if not isinstance(issue.repair_target, RepairTarget):
                raise ReportPipelineError("BlockingIssue repair_target is invalid")

    @staticmethod
    def _validate_integrity_review(review: object) -> None:
        if not isinstance(review, ResearchIntegrityReview):
            raise ReportPipelineError(
                "integrity reviewer must return ResearchIntegrityReview"
            )
        if not isinstance(review.disposition, IntegrityDisposition):
            raise ReportPipelineError("integrity disposition is invalid")
        if not isinstance(review.issues, tuple) or not all(
            isinstance(issue, str) and bool(issue) for issue in review.issues
        ):
            raise ReportPipelineError(
                "integrity issues must be a tuple of non-empty strings"
            )
        if review.disposition is IntegrityDisposition.PASS and review.issues:
            raise ReportPipelineError("PASS must carry no issues")
        if review.disposition is not IntegrityDisposition.PASS and not review.issues:
            raise ReportPipelineError("non-PASS disposition requires issues")
        if review.disposition is IntegrityDisposition.REVISE_DELIVERY:
            if review.revise_target not in (
                RepairTarget.MANUSCRIPT,
                RepairTarget.BRIEF,
            ):
                raise ReportPipelineError(
                    "REVISE_DELIVERY requires a MANUSCRIPT or BRIEF target"
                )
        elif review.revise_target is not None:
            raise ReportPipelineError(
                "revise_target must be None unless disposition is REVISE_DELIVERY"
            )


# ---------------------------------------------------------------------------
# Internal loop-control sentinels (not part of the public surface)
# ---------------------------------------------------------------------------


class _ResearchConfirmationSignal(ReportPipelineError):
    """Stop Delivery without granting a Reader authority to reopen Research."""

    def __init__(self, result: ResearchConfirmationRequiredResult) -> None:
        super().__init__(result.rationale)
        self.result = result


@dataclass(slots=True, frozen=True, kw_only=True)
class _BriefRebuild:
    """Integrity routed to BRIEF: outer loop must rebuild the Brief."""


@dataclass(slots=True, frozen=True, kw_only=True)
class _ResearchReopen:
    rationale: str


@dataclass(slots=True, frozen=True, kw_only=True)
class _Published:
    reader_pass: ReaderPass
    integrity_pass: IntegrityPass
    artifact: PublishReportResult


# ---------------------------------------------------------------------------
# Capture serialization helpers (plain JSON; no Domain authority)
# ---------------------------------------------------------------------------


def _brief_json(brief: ReportBrief) -> str:
    return json.dumps(_jsonable(brief), sort_keys=True, ensure_ascii=False, indent=2)


def _blind_read_json(blind: BlindReadResult) -> str:
    return json.dumps(_jsonable(blind), sort_keys=True, ensure_ascii=False, indent=2)


def _review_result_json(result: ReportReviewResult) -> str:
    return json.dumps(_jsonable(result), sort_keys=True, ensure_ascii=False, indent=2)


def _integrity_review_json(review: ResearchIntegrityReview) -> str:
    return json.dumps(_jsonable(review), sort_keys=True, ensure_ascii=False, indent=2)


def integrity_review_digest(basis_key: str, b_digest: str, m_digest: str) -> str:
    """Canonical digest bound by an Integrity PASS."""

    return _digest(f"{basis_key}|{b_digest}|{m_digest}")


def validate_report_brief(view: DeliveryView, brief: object) -> None:
    """Apply the deterministic Report Brief boundary outside the actor loop."""

    ReportPipeline._validate_brief(view, brief)


def validate_report_manuscript(manuscript: object) -> None:
    """Apply the deterministic Manuscript boundary outside the actor loop."""

    ReportPipeline._validate_manuscript(manuscript)


def validate_blind_read(blind: object, expected_manuscript_digest: str) -> None:
    """Validate one frozen Phase 1 result."""

    ReportPipeline._validate_blind_read(blind, expected_manuscript_digest)


def validate_report_review(
    result: object,
    frozen_blind_read: BlindReadResult,
    expected_brief_digest: str,
    expected_manuscript_digest: str,
) -> None:
    """Validate Phase 2 attribution against all frozen inputs."""

    ReportPipeline._validate_review_result(
        result,
        frozen_blind_read,
        expected_brief_digest,
        expected_manuscript_digest,
    )


def validate_integrity_review(review: object) -> None:
    """Apply the deterministic Integrity result boundary."""

    ReportPipeline._validate_integrity_review(review)
