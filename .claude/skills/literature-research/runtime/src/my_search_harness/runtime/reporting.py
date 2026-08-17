"""Semantic report orchestration over the certified Delivery boundary.

This module defines the shared report contracts and a compatibility/reference
in-process pipeline:

    Research State
        ↓ Completion PASS / DeliveryBasis
    Report Construction Context → Report Constructor → Report Brief
        ↓
    Authoring WRITE → Manuscript
        ↓
    deterministic Presentation → Reader Surface
        ↓
    Fresh Reader (fresh instance, two-phase cold reading)
        ├─ Blocking Issues → earliest repair layer (MANUSCRIPT / BRIEF)
        └─ no Blocking Issues → Reader PASS (brief_digest + manuscript_digest)
        ↓
    Research Integrity Reviewer
        ├─ PASS → Citation Renderer → Publish
        ├─ REVISE_DELIVERY → earliest faulty layer → Reader again → Integrity again
        └─ REOPEN_RESEARCH → RESEARCH
    ...

The production authority is ``CertifiedReportDelivery`` and its staged command
surface. The in-process ``ReportPipeline`` remains useful for compatibility,
tests, and embedding; it shares the same value objects and deterministic
validators. Neither path judges article quality, scores cognition, or decides
which classification is better — those remain Agent (semantic)
responsibilities.
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
from .context import (
    DeliveryView,
    InspectResult,
    ReportAuthoringContext,
    ReportConstructionContext,
)
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
# Report Brief — the lean editorial design middle layer (v0.6)
# ---------------------------------------------------------------------------
# The Report Brief is a Delivery work product, not a Research Domain entity:
# it is NOT stored in ResearchRun, has no stable identity, no independent
# lifecycle, and no ArtifactKind. v0.6 shrinks it to five editorial fields.
# The Constructor owns only these; Authoring owns the manuscript realization.
# There is no section/material IR, no outline_depth, no semantic_moves —
# Claude owns semantic judgment; Python owns only the deterministic invariant
# that the five fields are present and non-empty.


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportBrief:
    """Lean editorial design: who it is for and what it must deliver.

    Five fields, no semantic IR. ``audience`` and ``promise`` name the reader
    and the claim the report must land. ``frame`` is the conceptual vantage
    point. ``arc`` is the ordered narrative spine; ``focus`` is the ordered
    set of load-bearing claims the report must establish. Both are tuples of
    non-empty strings — ordering is the only deterministic invariant; the
    meaning of each entry is the Constructor's semantic judgment.
    """

    audience: str
    promise: str
    frame: str
    arc: tuple[str, ...]
    focus: tuple[str, ...]


# ---------------------------------------------------------------------------
# Reviewer result types (v0.6)
# ---------------------------------------------------------------------------


class RepairTarget(StrEnum):
    """The earliest Delivery layer qualified to fix a Reader blocker."""

    MANUSCRIPT = "MANUSCRIPT"
    BRIEF = "BRIEF"


@dataclass(slots=True, frozen=True, kw_only=True)
class ReaderIssue:
    """A reader-observed failure with no repair-layer authority.

    Phase 1 (Blind Read) emits these without attribution. Phase 2 (Brief
    Check) may carry the same shape to describe the consolidated reader
    failure that drives a single repair target. No score, no severity, no
    per-issue ``repair_target`` — attribution is a Phase 2 top-level decision.
    """

    observation: str
    reader_effect: str
    why_blocking: str
    location: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class BlindReadResult:
    """Frozen Phase 1 record of what a first reader actually understood.

    Bound to the manuscript digest it read. Produced before the Brief is
    exposed to the same review; Phase 2 must not rewrite or reinterpret it.
    v0.6 keeps only the received understanding, the digest binding, and the
    blind blocking issues — no reverse outline, no material economy, no
    professional-finish score.
    """

    received_understanding: str
    manuscript_digest: str
    blocking_issues: tuple[ReaderIssue, ...] = ()


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportReviewResult:
    """Phase 2 attribution for one frozen Blind Read.

    ``repair_target is None`` with no blocking issues is the only semantic
    PASS condition. A non-None target requires a non-empty ``rationale``.
    Attribution is a single top-level decision, not a per-issue field.
    """

    blind_read_digest: str
    brief_digest: str
    manuscript_digest: str
    repair_target: RepairTarget | None
    rationale: str
    blocking_issues: tuple[ReaderIssue, ...] = ()


# ---------------------------------------------------------------------------
# Integrity review (disposition kept stable; REVISE_DELIVERY carries a target)
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
# Constructor operational input and neutral Brief-repair projection
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True, kw_only=True)
class BriefRepairFeedback:
    """Neutral condition a rebuilt Brief must satisfy.

    A deterministic projection of stored downstream review, not Research
    truth, review history, or another report-semantic IR. v0.6 drops the
    ``downstream_effect`` field — the resolution condition alone carries the
    operational requirement.
    """

    problem: str
    resolution_condition: str
    location: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class BriefRepairContext:
    previous_brief: ReportBrief
    feedback: tuple[BriefRepairFeedback, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class ReportConstructionInput:
    context: ReportConstructionContext
    repair: BriefRepairContext | None = None


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
    """A canonical digest of the Report Brief's editorial content."""

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
    """Construct the lean Report Brief: audience, promise, frame, arc, focus.

    Reads the narrow Report Construction Context and the Report Construction
    Guide. A rebuild also receives the previous Brief plus neutral repair
    conditions. Does NOT read the Report Writing Guide (language is not its
    job). May targeted inspect/read source to recover explanatory detail,
    but must not produce new consensus, stronger generalization, new approach
    relationships, or contract-facing research judgments — those require
    reopening RESEARCH via ``ResearchEscalationRequired``.
    """

    def construct(
        self,
        construction_input: ReportConstructionInput,
        construction_guide: str,
        evidence: DeliveryEvidenceAccess,
    ) -> ReportBrief: ...


class ReportWriter(Protocol):
    """Authoring WRITE action: implement the Brief as a finished manuscript.

    Reads the Report Brief, the Report Writing Guide, and the thin Report
    Authoring Context. Does NOT receive the full DeliveryView or broad
    evidence access. If the Brief is insufficient, raise ``BriefInsufficient``
    to return to the Constructor rather than re-doing research or re-designing
    the report. This and ``ReportReviser`` are compatibility action facets of
    one Authoring authority, not independent semantic roles.
    """

    def write(
        self,
        brief: ReportBrief,
        writing_guide: str,
        authoring_context: ReportAuthoringContext,
    ) -> ReportManuscript: ...


class ReportReviewer(Protocol):
    """One-use two-phase cold reader for a single manuscript version.

    Phase 1 (Blind Read) receives only deliverable/audience/review guide/
    manuscript — never the Brief or Writing Guide. Phase 2 (Brief Check)
    receives the frozen Blind Read result plus the Brief. The runtime
    guarantees Phase 1 is frozen before Phase 2 sees the Brief.
    """

    def blind_read(
        self,
        deliverable_description: str,
        audience: str,
        review_guide: str,
        reader_surface: str,
        manuscript_digest: str,
    ) -> BlindReadResult: ...

    def brief_check(
        self,
        blind_read: BlindReadResult,
        brief: ReportBrief,
        reader_surface: str,
        manuscript_digest: str,
        review_guide: str,
    ) -> ReportReviewResult: ...


class ReportReviewerFactory(Protocol):
    """Create a reviewer with no authority carried over from earlier reviews.

    A fresh instance is required for every manuscript version so the reviewer
    cannot confirm "I see you fixed what I asked" instead of re-reading cold.
    """

    def create(self) -> ReportReviewer: ...


class ReportReviser(Protocol):
    """Authoring REVISE action: fix explicit Manuscript blockers.

    Reads the Brief, current manuscript, reader/integrity issues, the Writing
    Guide, and the thin Report Authoring Context. If an issue belongs to the
    Brief, it must be routed back to the Constructor; if it needs new research
    judgment, it must be escalated — Authoring does neither itself. This is the
    same authority as ``ReportWriter`` with a repair-shaped input.
    """

    def revise(
        self,
        brief: ReportBrief,
        manuscript: ReportManuscript,
        issues: tuple[ReaderIssue, ...],
        writing_guide: str,
        authoring_context: ReportAuthoringContext,
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
    """Authoring or Fresh Reader found the Brief cannot carry the promise.

    Routes back to the Report Constructor (BRIEF), not to research by itself.
    """

    def __init__(
        self,
        rationale: str,
        *,
        feedback: tuple[BriefRepairFeedback, ...] = (),
    ) -> None:
        if not isinstance(rationale, str) or not rationale:
            raise ValueError("brief-insufficient rationale must be non-empty")
        super().__init__(rationale)
        self.rationale = rationale
        selected_feedback = feedback or (
            BriefRepairFeedback(
                problem=rationale,
                resolution_condition=(
                    "The rebuilt Report Brief must make the intended cognitive "
                    "design and evidence boundary sufficient for Authoring"
                ),
            ),
        )
        validate_brief_repair_feedback(selected_feedback)
        self.feedback = selected_feedback


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


ReportPipelineResult: TypeAlias = (
    PublishedReportPipelineResult | ReportResearchReopenedResult
)


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

# A resource guard, not a semantic stop. Hitting it raises
# ReportResourceExhausted — never interpreted as PASS. Defaults are generous;
# the host may lower them for bounded environments.
_DEFAULT_MAX_CONSTRUCTOR_REBUILDS = 8
_DEFAULT_MAX_INTEGRITY_ROUNDS = 12


class ReportPipeline:
    """Compatibility/reference runner for the report semantic stages.

    It is an Action pipeline, not a Report FSM. No new lifecycle mode is
    introduced; everything here runs inside the existing DELIVERY mode. The
    persisted staged ``CertifiedReportDelivery`` path remains production
    authority. v0.6 removes the automatic Reader convergence loop: Authoring
    WRITE is followed by exactly one Reader decision; a MANUSCRIPT or BRIEF
    route returns to the host rather than auto-revising until PASS. The host
    (Claude) owns re-authoring.
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
        construction_guide: str,
        writing_guide: str,
        review_guide: str,
        integrity_guide: str,
        capture_sink: ReportCaptureSink | None = None,
        max_constructor_rebuilds: int = _DEFAULT_MAX_CONSTRUCTOR_REBUILDS,
        max_integrity_rounds: int = _DEFAULT_MAX_INTEGRITY_ROUNDS,
    ) -> None:
        self._validate_guides(
            construction_guide,
            writing_guide,
            review_guide,
            integrity_guide,
        )
        for name, value in (
            ("max_constructor_rebuilds", max_constructor_rebuilds),
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
        self._construction_guide = construction_guide
        self._writing_guide = writing_guide
        self._review_guide = review_guide
        self._integrity_guide = integrity_guide
        self._capture = capture_sink or NoopReportCaptureSink()
        self._max_constructor_rebuilds = max_constructor_rebuilds
        self._max_integrity_rounds = max_integrity_rounds

    def run(self, run_id: str) -> ReportPipelineResult:
        self._current_run_id = run_id
        view = self._delivery.view(run_id)
        evidence = DeliveryEvidenceAccess(self._delivery, run_id, view.state_revision)
        try:
            return self._run_delivery(run_id, view, evidence)
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
        construction_context = self._delivery.report_construction_context(run_id)
        repair_context: BriefRepairContext | None = None

        for _ in range(self._max_constructor_rebuilds):
            brief = self._construct_brief(
                view,
                construction_context,
                repair_context,
                evidence,
            )
            b_digest = brief_digest(brief)
            if rejected_brief_digest == b_digest:
                raise ReportPipelineError(
                    "Brief repair must produce a new Brief version"
                )
            self._capture.capture(run_id, "report_brief.json", _brief_json(brief))

            try:
                manuscript, reader_pass = self._write_then_review(
                    run_id, view, brief, b_digest
                )
            except BriefInsufficient as exc:
                # Reader (or integrity re-review) routed a blocker to the Brief:
                # rebuild the Brief via a fresh Constructor pass. The most
                # upstream fault wins, so a BRIEF route always returns here
                # rather than revising the manuscript or reopening research.
                rejected_brief_digest = b_digest
                repair_context = BriefRepairContext(
                    previous_brief=brief,
                    feedback=exc.feedback,
                )
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
                repair_context = BriefRepairContext(
                    previous_brief=brief,
                    feedback=integrity_outcome.feedback,
                )
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
        construction_context: ReportConstructionContext,
        repair_context: BriefRepairContext | None,
        evidence: DeliveryEvidenceAccess,
    ) -> ReportBrief:
        brief = self._constructor.construct(
            ReportConstructionInput(
                context=construction_context,
                repair=repair_context,
            ),
            self._construction_guide,
            evidence,
        )
        self._validate_brief(view, brief)
        return brief

    def _write_then_review(
        self,
        run_id: str,
        view: DeliveryView,
        brief: ReportBrief,
        b_digest: str,
    ) -> tuple[ReportManuscript, ReaderPass]:
        """Write a manuscript, then run the Reader gate exactly once.

        v0.6 removes the automatic revise→re-read convergence loop. The Reader
        returns a single decision: PASS, or a repair target (MANUSCRIPT/BRIEF).
        A BRIEF route raises ``BriefInsufficient`` for the outer loop. A
        MANUSCRIPT route is returned to the host — this compatibility runner
        does not auto-revise. Authoring WRITE is invoked exactly once here.
        """

        manuscript = self._write_manuscript(brief)
        self._capture.capture(run_id, "manuscript_pre_reader.md", manuscript.markdown)
        result = self._fresh_review(run_id, view, brief, manuscript)
        if result.repair_target is None:
            if result.blocking_issues:
                raise ReportPipelineError(
                    "Reader PASS requires no blocking issues"
                )
            return manuscript, ReaderPass(
                brief_digest=b_digest,
                manuscript_digest=result.manuscript_digest,
            )
        if result.repair_target is RepairTarget.BRIEF:
            raise BriefInsufficient(
                result.rationale or "report reviewer routed a blocking issue to the Brief",
                feedback=self._repair_feedback_from_reader(result),
            )
        # MANUSCRIPT route: no auto-convergence in v0.6. Surface the outcome
        # as a resource stop so the host re-authoring is the only path forward.
        raise ReportResourceExhausted(
            "Reader routed a MANUSCRIPT repair; v0.6 has no automatic "
            "convergence — re-author and re-run"
        )

    def _write_manuscript(self, brief: ReportBrief) -> ReportManuscript:
        # Authoring receives only the Brief + Writing Guide + thin Authoring
        # Context. It does NOT get the full DeliveryView or evidence access.
        authoring_context = self._current_authoring_context()
        manuscript = self._writer.write(brief, self._writing_guide, authoring_context)
        self._validate_manuscript(manuscript)
        return manuscript

    def _current_authoring_context(self) -> ReportAuthoringContext:
        # Thin projection of the current DeliveryView for Authoring. Re-resolved
        # per write so a reconstructed Brief sees consistent context.
        return self._delivery.report_authoring_context(self._current_run_id)

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
        reader_surface = self._reader_surface(view, manuscript)
        # Phase 1 — Blind Read. NO Brief, NO Writing Guide. Audience is a narrow
        # projection of the Brief (the audience string only).
        blind = reviewer.blind_read(
            deliverable_description=view.contract.deliverable_description,
            audience=brief.audience,
            review_guide=self._review_guide,
            reader_surface=reader_surface,
            manuscript_digest=m_digest,
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
            reader_surface=reader_surface,
            manuscript_digest=m_digest,
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
                return _BriefRebuild(
                    feedback=tuple(
                        BriefRepairFeedback(
                            problem=issue,
                            resolution_condition=(
                                "The rebuilt Report Brief must represent the accepted "
                                "research semantics within its evidence boundary"
                            ),
                        )
                        for issue in review.issues
                    )
                )
            # MANUSCRIPT → Authoring REVISE → Reader again → Integrity.
            current_ms = self._revise_for_integrity(
                run_id, view, current_brief, current_ms, review
            )
            # Integrity-triggered manuscript edit invalidates the old Reader
            # PASS; re-run the Reader gate (review only — never re-write) on
            # Authoring's revised output before looping back to Integrity.
            current_ms, reader_pass = self._review_after_integrity_repair(
                run_id, view, current_brief, current_b_digest, current_ms
            )
            current_b_digest = reader_pass.brief_digest
            current_reader_pass = reader_pass
            # Loop back to Integrity with the new (b_digest, m_digest).
        raise ReportResourceExhausted(
            "integrity gate exceeded the revision resource budget; "
            "this is a resource stop, not a PASS"
        )

    def _review_after_integrity_repair(
        self,
        run_id: str,
        view: DeliveryView,
        brief: ReportBrief,
        b_digest: str,
        manuscript: ReportManuscript,
    ) -> tuple[ReportManuscript, ReaderPass]:
        """Re-run the Reader gate once on an integrity-repaired manuscript.

        v0.6 has no automatic convergence: a single fresh review either PASSes
        or surfaces a repair target. A MANUSCRIPT route here is a resource
        stop (the host must re-author); a BRIEF route raises
        ``BriefInsufficient`` for the outer loop.
        """

        result = self._fresh_review(run_id, view, brief, manuscript)
        if result.repair_target is None:
            if result.blocking_issues:
                raise ReportPipelineError(
                    "Reader PASS requires no blocking issues"
                )
            return manuscript, ReaderPass(
                brief_digest=b_digest,
                manuscript_digest=result.manuscript_digest,
            )
        if result.repair_target is RepairTarget.BRIEF:
            raise BriefInsufficient(
                result.rationale or "report reviewer routed a blocking issue to the Brief",
                feedback=self._repair_feedback_from_reader(result),
            )
        raise ReportResourceExhausted(
            "Reader routed a MANUSCRIPT repair after integrity; v0.6 has no "
            "automatic convergence — re-author and re-run"
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
            ReaderIssue(
                observation=text,
                reader_effect=text,
                why_blocking=(
                    "The revised manuscript must faithfully express the accepted "
                    "research semantics within the current Report Brief"
                ),
            )
            for text in integrity.issues
        )
        revised = self._reviser.revise(
            brief=brief,
            manuscript=manuscript,
            issues=issues,
            writing_guide=self._writing_guide,
            authoring_context=self._current_authoring_context(),
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
    def _require_integrity_revise_target(
        integrity: ResearchIntegrityReview,
    ) -> RepairTarget:
        target = integrity.revise_target
        if target is None:
            raise ReportPipelineError(
                "REVISE_DELIVERY requires a MANUSCRIPT or BRIEF target"
            )
        return target

    def _reader_surface(
        self,
        view: DeliveryView,
        manuscript: ReportManuscript,
    ) -> str:
        """Run the shared deterministic Presentation boundary for Reader input."""

        rendered = self._citation_renderer.render(view, manuscript)
        if not isinstance(rendered, str) or not rendered.strip():
            raise ReportPipelineError(
                "presentation renderer must return a non-empty Reader Surface"
            )
        return rendered

    @staticmethod
    def _repair_feedback_from_reader(
        review: ReportReviewResult,
    ) -> tuple[BriefRepairFeedback, ...]:
        """Project a BRIEF-routed Reader review into neutral repair feedback."""

        if not review.rationale or not review.rationale.strip():
            raise ReportPipelineError("Brief repair requires a non-empty rationale")
        feedback = (
            BriefRepairFeedback(
                problem=review.rationale,
                resolution_condition=review.rationale,
            ),
        )
        return feedback

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
        construction_guide: str,
        writing_guide: str,
        review_guide: str,
        integrity_guide: str,
    ) -> None:
        for name, value in (
            ("construction_guide", construction_guide),
            ("writing_guide", writing_guide),
            ("review_guide", review_guide),
            ("integrity_guide", integrity_guide),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")

    @staticmethod
    def _validate_brief(view: DeliveryView, brief: object) -> None:
        if not isinstance(brief, ReportBrief):
            raise ReportPipelineError("constructor must return ReportBrief")
        for field_name in ("audience", "promise", "frame"):
            value = getattr(brief, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ReportPipelineError(
                    f"ReportBrief.{field_name} must be non-empty text"
                )
        for field_name in ("arc", "focus"):
            value = getattr(brief, field_name)
            if (
                not isinstance(value, tuple)
                or not value
                or not all(
                    isinstance(item, str) and item.strip() for item in value
                )
            ):
                raise ReportPipelineError(
                    f"ReportBrief.{field_name} must be a non-empty tuple of "
                    "non-empty strings"
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
    def _validate_blind_read(blind: object, expected_manuscript_digest: str) -> None:
        if not isinstance(blind, BlindReadResult):
            raise ReportPipelineError("reviewer blind_read must return BlindReadResult")
        value = blind.received_understanding
        if not isinstance(value, str) or not value.strip():
            raise ReportPipelineError(
                "BlindReadResult.received_understanding must be a non-empty string"
            )
        if blind.manuscript_digest != expected_manuscript_digest:
            raise ReportPipelineError(
                "BlindReadResult must bind to the manuscript digest it read"
            )
        if not isinstance(blind.blocking_issues, tuple) or not all(
            isinstance(issue, ReaderIssue) for issue in blind.blocking_issues
        ):
            raise ReportPipelineError(
                "BlindReadResult blocking_issues must contain ReaderIssue"
            )
        for issue in blind.blocking_issues:
            ReportPipeline._validate_reader_issue(issue)

    @staticmethod
    def _validate_reader_issue(issue: object) -> None:
        if not isinstance(issue, ReaderIssue):
            raise ReportPipelineError("ReaderIssue is invalid")
        for field_name in ("observation", "reader_effect", "why_blocking"):
            value = getattr(issue, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ReportPipelineError(
                    f"ReaderIssue.{field_name} must be a non-empty string"
                )
        if issue.location is not None and (
            not isinstance(issue.location, str) or not issue.location.strip()
        ):
            raise ReportPipelineError(
                "ReaderIssue.location must be a non-empty string or None"
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
            isinstance(issue, ReaderIssue) for issue in result.blocking_issues
        ):
            raise ReportPipelineError(
                "blocking_issues must be a tuple of ReaderIssue"
            )
        for issue in result.blocking_issues:
            ReportPipeline._validate_reader_issue(issue)
        # PASS condition: no repair target and no blocking issues.
        if result.repair_target is None:
            if result.blocking_issues:
                raise ReportPipelineError(
                    "Reader PASS requires no blocking issues"
                )
            if not isinstance(result.rationale, str) or not result.rationale.strip():
                raise ReportPipelineError(
                    "ReportReviewResult.rationale must be non-empty text"
                )
            return
        # A repair target requires a non-empty rationale and at least one
        # blocking issue describing the failure.
        if not isinstance(result.repair_target, RepairTarget):
            raise ReportPipelineError("ReportReviewResult repair_target is invalid")
        if (
            result.repair_target is not RepairTarget.MANUSCRIPT
            and result.repair_target is not RepairTarget.BRIEF
        ):
            raise ReportPipelineError("ReportReviewResult repair_target is invalid")
        if not isinstance(result.rationale, str) or not result.rationale.strip():
            raise ReportPipelineError(
                "ReportReviewResult.rationale must be non-empty text"
            )
        if not result.blocking_issues:
            raise ReportPipelineError(
                "a Reader repair target requires at least one blocking issue"
            )
        # Phase 2 cannot paper over a frozen blind FAIL: if the blind read
        # recorded blocking issues, the review must also carry issues.
        if frozen_blind_read.blocking_issues and not result.blocking_issues:
            raise ReportPipelineError(
                "Phase 2 cannot PASS while the frozen Blind Read has blocking issues"
            )

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


@dataclass(slots=True, frozen=True, kw_only=True)
class _BriefRebuild:
    """Integrity routed to BRIEF: outer loop must rebuild the Brief."""

    feedback: tuple[BriefRepairFeedback, ...]


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


def validate_brief_repair_feedback(feedback: object) -> None:
    """Validate neutral operational feedback for one Brief reconstruction."""

    if (
        not isinstance(feedback, tuple)
        or not feedback
        or not all(isinstance(item, BriefRepairFeedback) for item in feedback)
    ):
        raise ReportPipelineError(
            "Brief repair feedback must be a non-empty tuple of BriefRepairFeedback"
        )
    for item in feedback:
        for field_name in (
            "problem",
            "resolution_condition",
        ):
            value = getattr(item, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ReportPipelineError(
                    f"BriefRepairFeedback.{field_name} must be non-empty text"
                )
        if item.location is not None and (
            not isinstance(item.location, str) or not item.location.strip()
        ):
            raise ReportPipelineError(
                "BriefRepairFeedback.location must be non-empty text or None"
            )


def validate_integrity_review(review: object) -> None:
    """Apply the deterministic Integrity result boundary."""

    ReportPipeline._validate_integrity_review(review)
