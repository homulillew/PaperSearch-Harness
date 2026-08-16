"""Persisted certified delivery with version-bound publication gates.

Claude Code supplies semantic work products across CLI calls.  This module
owns their deterministic order, freshness and certification in workspace
Runtime persistence.  Nothing here is ResearchRun state or a published artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum, StrEnum
from pathlib import Path
from typing import Mapping

from my_search_harness.domain.model import SourceLocator
from my_search_harness.domain.validation import validate_ref

from .capabilities import DeliveryCapabilities
from .citations import DeterministicCitationRenderer
from .delivery import PublishReportResult, _ReportPublicationAuthorization
from .reporting import (
    BlindBlockingIssue,
    BlindReadResult,
    BriefRepairContext,
    BriefRepairFeedback,
    BriefMaterial,
    CitationReference,
    CognitiveFrictionObservation,
    IntegrityDisposition,
    IntegrityPass,
    LocalReportCaptureSink,
    ReaderPass,
    RepairTarget,
    ReportBrief,
    ReportBriefSection,
    ReportConstructionInput,
    ReportManuscript,
    ReportReviewResult,
    ResearchIntegrityReview,
    blind_read_digest,
    brief_digest,
    delivery_basis_key,
    manuscript_digest,
    reader_repair_target,
    validate_blind_read,
    validate_brief_repair_feedback,
    validate_integrity_review,
    validate_report_brief,
    validate_report_manuscript,
    validate_report_review,
    validate_outline_fidelity,
)


class CertifiedDeliveryError(RuntimeError):
    """A staged Delivery command violated ordering or certification."""


class _PendingAction(StrEnum):
    """Runtime-only repair obligation; never part of ResearchRun state."""

    NONE = "NONE"
    MANUSCRIPT_REPAIR_REQUIRED = "MANUSCRIPT_REPAIR_REQUIRED"
    BRIEF_REBUILD_REQUIRED = "BRIEF_REBUILD_REQUIRED"
    RESEARCH_REOPEN_REQUIRED = "RESEARCH_REOPEN_REQUIRED"


@dataclass(slots=True, frozen=True, kw_only=True)
class CertifiedDeliveryStatus:
    run_id: str
    stage: str
    delivery_basis_key: str
    brief_digest: str | None
    manuscript_digest: str | None
    blind_read_digest: str | None
    reader_pass: ReaderPass | None
    integrity_pass: IntegrityPass | None
    rendered: bool
    pending_action: str


@dataclass(slots=True, frozen=True, kw_only=True)
class CertifiedReportRenderResult:
    content: str
    content_sha256: str
    brief_digest: str
    manuscript_digest: str


@dataclass(slots=True, frozen=True, kw_only=True)
class ReaderPreviewResult:
    """Read-only citation projection bound to the source work-product versions."""

    content: str
    brief_digest: str
    manuscript_digest: str


class CertifiedReportDelivery:
    """Production orchestrator for Claude-driven staged report delivery."""

    _SCHEMA_VERSION = 4
    _OLDER_SCHEMA_MESSAGE = (
        "report delivery session uses an older schema and must rebuild "
        "the Report Brief"
    )

    def __init__(
        self,
        workspace_root: str | Path,
        delivery: DeliveryCapabilities,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._delivery = delivery
        self._captures = LocalReportCaptureSink(self._workspace_root / "scratch")
        self._renderer = DeterministicCitationRenderer()

    def construction_input(self, run_id: str) -> ReportConstructionInput:
        """Build the authoritative staged input for a Constructor invocation."""

        context = self._delivery.report_construction_context(run_id)
        basis_key = delivery_basis_key(context.delivery_basis)
        session = self._load_if_exists(run_id, basis_key)
        if session is None:
            return ReportConstructionInput(context=context)
        pending = self._pending_action(session)
        if pending is not _PendingAction.BRIEF_REBUILD_REQUIRED:
            return ReportConstructionInput(context=context)
        previous = self._require_brief(session)
        feedback = self._brief_repair_feedback(session)
        return ReportConstructionInput(
            context=context,
            repair=BriefRepairContext(
                previous_brief=previous,
                feedback=feedback,
            ),
        )

    def put_brief(self, run_id: str, brief: ReportBrief) -> CertifiedDeliveryStatus:
        view = self._delivery.view(run_id)
        validate_report_brief(view, brief)
        b_digest = brief_digest(brief)
        basis_key = delivery_basis_key(view.delivery_basis)
        existing = self._load_if_exists(run_id, basis_key)
        if existing is not None:
            self._require_action_allowed(existing, "put-report-brief")
            pending = self._pending_action(existing)
            if (
                pending
                in {
                    _PendingAction.BRIEF_REBUILD_REQUIRED,
                }
                and existing.get("brief_digest") == b_digest
            ):
                raise CertifiedDeliveryError(
                    "pending repair requires a new Report Brief version"
                )
        session = self._empty_session(basis_key)
        session["brief"] = _jsonable(brief)
        session["brief_digest"] = b_digest
        self._save(run_id, session)
        self._captures.capture(run_id, "report_brief.json", _pretty(brief))
        return self._status(run_id, "BRIEF_ACCEPTED", session)

    def submit_brief_insufficient(
        self,
        run_id: str,
        feedback: tuple[BriefRepairFeedback, ...],
    ) -> CertifiedDeliveryStatus:
        """Route Authoring's neutral Brief insufficiency back to Constructor."""

        validate_brief_repair_feedback(feedback)
        session = self._load_current(run_id)
        self._require_action_allowed(session, "submit-brief-insufficient")
        brief = self._require_brief(session)
        session["brief_repair_feedback"] = _jsonable(feedback)
        session["manuscript"] = None
        session["manuscript_digest"] = None
        self._clear_from(session, "blind_read")
        session["pending_action"] = _PendingAction.BRIEF_REBUILD_REQUIRED.value
        self._save(run_id, session)
        self._captures.capture(
            run_id,
            f"brief_repair_feedback_{brief_digest(brief)[:12]}.json",
            _pretty(feedback),
        )
        return self._status(run_id, "BRIEF_REBUILD_REQUIRED", session)

    def put_manuscript(
        self, run_id: str, manuscript: ReportManuscript
    ) -> CertifiedDeliveryStatus:
        session = self._load_current(run_id)
        self._require_action_allowed(session, "put-report-manuscript")
        brief = self._require_brief(session)
        validate_report_manuscript(manuscript)
        validate_outline_fidelity(brief, manuscript)
        self._renderer.audit(self._delivery.view(run_id), manuscript)
        new_digest = manuscript_digest(manuscript)
        pending = self._pending_action(session)
        if (
            pending is _PendingAction.MANUSCRIPT_REPAIR_REQUIRED
            and session.get("manuscript_digest") == new_digest
        ):
            raise CertifiedDeliveryError(
                "pending repair requires a new Report Manuscript version"
            )
        name = (
            "manuscript_post_revision.md"
            if session.get("manuscript") is not None
            else "manuscript_pre_reader.md"
        )
        session["manuscript"] = _jsonable(manuscript)
        session["manuscript_digest"] = new_digest
        self._clear_from(session, "blind_read")
        session["pending_action"] = _PendingAction.NONE.value
        self._save(run_id, session)
        self._captures.capture(run_id, name, manuscript.markdown)
        return self._status(run_id, "MANUSCRIPT_ACCEPTED", session)

    def render_reader_preview(self, run_id: str) -> ReaderPreviewResult:
        """Render reader-facing citations without persistence or certification."""

        session = self._load_current(run_id)
        self._require_action_allowed(session, "render-reader-preview")
        brief = self._require_brief(session)
        manuscript = self._require_manuscript(session)
        validate_outline_fidelity(brief, manuscript)
        content = self._renderer.render(self._delivery.view(run_id), manuscript)
        if not content.strip():
            raise CertifiedDeliveryError("citation renderer returned empty preview")
        return ReaderPreviewResult(
            content=content,
            brief_digest=brief_digest(brief),
            manuscript_digest=manuscript_digest(manuscript),
        )

    def submit_blind_read(
        self, run_id: str, blind: BlindReadResult
    ) -> CertifiedDeliveryStatus:
        session = self._load_current(run_id)
        self._require_action_allowed(session, "submit-blind-review")
        manuscript = self._require_manuscript(session)
        m_digest = manuscript_digest(manuscript)
        validate_blind_read(blind, m_digest)
        frozen_digest = blind_read_digest(blind)
        session["blind_read"] = _jsonable(blind)
        session["blind_read_digest"] = frozen_digest
        self._clear_from(session, "reader_review")
        self._save(run_id, session)
        self._captures.capture(
            run_id, f"blind_review_{m_digest[:12]}.json", _pretty(blind)
        )
        return self._status(run_id, "BLIND_READ_FROZEN", session)

    def submit_reader_review(
        self, run_id: str, review: ReportReviewResult
    ) -> CertifiedDeliveryStatus:
        session = self._load_current(run_id)
        self._require_action_allowed(session, "submit-reader-review")
        brief = self._require_brief(session)
        manuscript = self._require_manuscript(session)
        frozen_blind = self._require_blind_read(session)
        frozen_digest = self._required_string(session, "blind_read_digest")
        b_digest = brief_digest(brief)
        m_digest = manuscript_digest(manuscript)
        validate_blind_read(frozen_blind, m_digest)
        if blind_read_digest(frozen_blind) != frozen_digest:
            raise CertifiedDeliveryError("stored Blind Read digest is stale")
        if frozen_blind.blocking_issues and not review.blocking_issues:
            raise CertifiedDeliveryError(
                "Phase 2 cannot PASS while the frozen Blind Read has blocking issues"
            )
        validate_report_review(review, frozen_blind, b_digest, m_digest)
        session["reader_review"] = _jsonable(review)
        if review.blocking_issues:
            session["reader_pass"] = None
            pending = self._pending_for_reader(review)
        else:
            session["reader_pass"] = _jsonable(
                ReaderPass(
                    brief_digest=b_digest,
                    manuscript_digest=m_digest,
                )
            )
            pending = _PendingAction.NONE
        self._clear_from(session, "integrity_review")
        session["pending_action"] = pending.value
        self._save(run_id, session)
        self._captures.capture(
            run_id, f"reader_review_{m_digest[:12]}.json", _pretty(review)
        )
        stage = "READER_PASS" if not review.blocking_issues else "READER_BLOCKED"
        return self._status(run_id, stage, session)

    def submit_integrity_review(
        self, run_id: str, review: ResearchIntegrityReview
    ) -> CertifiedDeliveryStatus:
        session = self._load_current(run_id)
        self._require_action_allowed(session, "submit-integrity-review")
        brief = self._require_brief(session)
        manuscript = self._require_manuscript(session)
        reader_pass = self._reader_pass(session)
        b_digest = brief_digest(brief)
        m_digest = manuscript_digest(manuscript)
        if (
            reader_pass.brief_digest != b_digest
            or reader_pass.manuscript_digest != m_digest
        ):
            raise CertifiedDeliveryError(
                "Integrity review requires a matching current Reader PASS"
            )
        validate_integrity_review(review)
        session["integrity_review"] = _jsonable(review)
        if review.disposition is IntegrityDisposition.PASS:
            session["integrity_pass"] = _jsonable(
                IntegrityPass(
                    delivery_basis_key=self._required_string(
                        session, "delivery_basis_key"
                    ),
                    brief_digest=b_digest,
                    manuscript_digest=m_digest,
                )
            )
            session["pending_action"] = _PendingAction.NONE.value
            stage = "INTEGRITY_PASS"
        else:
            session["integrity_pass"] = None
            session["reader_pass"] = None
            session["pending_action"] = self._pending_for_integrity(review).value
            stage = (
                "RESEARCH_REOPEN_CONFIRMED"
                if review.disposition is IntegrityDisposition.REOPEN_RESEARCH
                else "INTEGRITY_REVISION_REQUIRED"
            )
        session["rendered"] = None
        self._save(run_id, session)
        self._captures.capture(
            run_id, f"integrity_review_{m_digest[:12]}.json", _pretty(review)
        )
        return self._status(run_id, stage, session)

    def render_certified(self, run_id: str) -> CertifiedReportRenderResult:
        session = self._load_current(run_id)
        self._require_action_allowed(session, "render-certified-report")
        brief, manuscript, _, _ = self._require_certified(run_id, session)
        content = self._renderer.render(self._delivery.view(run_id), manuscript)
        if not content.strip():
            raise CertifiedDeliveryError("citation renderer returned empty content")
        content_sha256 = _text_digest(content)
        rendered_path = self._session_directory(run_id) / "rendered_report.md"
        self._write_atomic(rendered_path, content)
        session["rendered"] = {
            "content_sha256": content_sha256,
            "brief_digest": brief_digest(brief),
            "manuscript_digest": manuscript_digest(manuscript),
        }
        self._save(run_id, session)
        return CertifiedReportRenderResult(
            content=content,
            content_sha256=content_sha256,
            brief_digest=brief_digest(brief),
            manuscript_digest=manuscript_digest(manuscript),
        )

    def publish_certified(
        self, run_id: str, expected_revision: int
    ) -> PublishReportResult:
        session = self._load_current(run_id)
        self._require_action_allowed(session, "publish-certified-report")
        brief, manuscript, reader_pass, integrity_pass = self._require_certified(
            run_id, session
        )
        rendered = session.get("rendered")
        if not isinstance(rendered, Mapping):
            raise CertifiedDeliveryError(
                "publish-certified-report requires render-certified-report first"
            )
        rendered_path = self._session_directory(run_id) / "rendered_report.md"
        try:
            content = rendered_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise CertifiedDeliveryError(
                "certified rendered report is unavailable"
            ) from exc
        b_digest = brief_digest(brief)
        m_digest = manuscript_digest(manuscript)
        if (
            rendered.get("content_sha256") != _text_digest(content)
            or rendered.get("brief_digest") != b_digest
            or rendered.get("manuscript_digest") != m_digest
        ):
            raise CertifiedDeliveryError(
                "rendered report does not match the current certified versions"
            )
        view = self._delivery.view(run_id)
        return self._delivery._publish_certified_report(
            run_id,
            expected_revision,
            content,
            _ReportPublicationAuthorization(
                delivery_basis=view.delivery_basis,
                brief_digest=b_digest,
                manuscript_digest=m_digest,
                reader_brief_digest=reader_pass.brief_digest,
                reader_manuscript_digest=reader_pass.manuscript_digest,
                integrity_delivery_basis=view.delivery_basis,
                integrity_brief_digest=integrity_pass.brief_digest,
                integrity_manuscript_digest=integrity_pass.manuscript_digest,
            ),
        )

    def _require_certified(
        self, run_id: str, session: dict[str, object]
    ) -> tuple[ReportBrief, ReportManuscript, ReaderPass, IntegrityPass]:
        brief = self._require_brief(session)
        manuscript = self._require_manuscript(session)
        reader_pass = self._reader_pass(session)
        integrity_pass = self._integrity_pass(session)
        b_digest = brief_digest(brief)
        m_digest = manuscript_digest(manuscript)
        basis_key = delivery_basis_key(self._delivery.view(run_id).delivery_basis)
        if session.get("brief_digest") != b_digest:
            raise CertifiedDeliveryError("stored Brief digest is stale")
        if session.get("manuscript_digest") != m_digest:
            raise CertifiedDeliveryError("stored Manuscript digest is stale")
        if (
            reader_pass.brief_digest != b_digest
            or reader_pass.manuscript_digest != m_digest
        ):
            raise CertifiedDeliveryError("Reader PASS is stale")
        if (
            integrity_pass.delivery_basis_key != basis_key
            or integrity_pass.brief_digest != b_digest
            or integrity_pass.manuscript_digest != m_digest
        ):
            raise CertifiedDeliveryError("Integrity PASS is stale")
        return brief, manuscript, reader_pass, integrity_pass

    def _load_current(self, run_id: str) -> dict[str, object]:
        session = self._load(run_id)
        current_key = delivery_basis_key(self._delivery.view(run_id).delivery_basis)
        if session.get("delivery_basis_key") != current_key:
            raise CertifiedDeliveryError(
                "Delivery session is stale for the current DeliveryBasis"
            )
        return session

    def _load_if_exists(
        self, run_id: str, current_basis_key: str
    ) -> dict[str, object] | None:
        if not self._session_path(run_id).exists():
            return None
        try:
            session = self._load(run_id)
        except CertifiedDeliveryError as exc:
            if str(exc) == self._OLDER_SCHEMA_MESSAGE:
                return None
            raise
        if session.get("delivery_basis_key") != current_basis_key:
            return None
        return session

    def _load(self, run_id: str) -> dict[str, object]:
        path = self._session_path(run_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CertifiedDeliveryError(
                "report delivery session does not exist"
            ) from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CertifiedDeliveryError(
                "report delivery session is unreadable"
            ) from exc
        schema_version = (
            value.get("schema_version") if isinstance(value, dict) else None
        )
        if (
            isinstance(schema_version, int)
            and not isinstance(schema_version, bool)
            and schema_version < self._SCHEMA_VERSION
        ):
            raise CertifiedDeliveryError(self._OLDER_SCHEMA_MESSAGE)
        if not isinstance(value, dict) or schema_version != self._SCHEMA_VERSION:
            raise CertifiedDeliveryError("report delivery session is invalid")
        return value

    def _save(self, run_id: str, session: dict[str, object]) -> None:
        self._write_atomic(
            self._session_path(run_id),
            json.dumps(session, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def _session_directory(self, run_id: str) -> Path:
        validate_ref(run_id, "run", "run_id")
        return self._workspace_root / "runs" / run_id / "delivery"

    def _session_path(self, run_id: str) -> Path:
        return self._session_directory(run_id) / "report_session.json"

    @staticmethod
    def _write_atomic(path: Path, payload: str) -> None:
        temporary = path.with_name(f"{path.name}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise CertifiedDeliveryError(
                "cannot persist report delivery session"
            ) from exc

    @staticmethod
    def _empty_session(basis_key: str) -> dict[str, object]:
        return {
            "schema_version": CertifiedReportDelivery._SCHEMA_VERSION,
            "delivery_basis_key": basis_key,
            "pending_action": _PendingAction.NONE.value,
            "brief": None,
            "brief_digest": None,
            "brief_repair_feedback": None,
            "manuscript": None,
            "manuscript_digest": None,
            "blind_read": None,
            "blind_read_digest": None,
            "reader_review": None,
            "reader_pass": None,
            "integrity_review": None,
            "integrity_pass": None,
            "rendered": None,
        }

    @staticmethod
    def _clear_from(session: dict[str, object], field: str) -> None:
        order = (
            "blind_read",
            "reader_review",
            "integrity_review",
        )
        start = order.index(field)
        for item in order[start:]:
            session[item] = None
            if item == "blind_read":
                session["blind_read_digest"] = None
            if item == "reader_review":
                session["reader_pass"] = None
            if item == "integrity_review":
                session["integrity_pass"] = None
        session["rendered"] = None

    @staticmethod
    def _pending_action(session: Mapping[str, object]) -> _PendingAction:
        raw = session.get("pending_action")
        if not isinstance(raw, str):
            raise CertifiedDeliveryError(
                "report delivery session has invalid pending_action"
            )
        try:
            return _PendingAction(raw)
        except ValueError as exc:
            raise CertifiedDeliveryError(
                "report delivery session has invalid pending_action"
            ) from exc

    @classmethod
    def _require_action_allowed(
        cls, session: Mapping[str, object], command: str
    ) -> None:
        pending = cls._pending_action(session)
        allowed = {
            _PendingAction.NONE: None,
            _PendingAction.MANUSCRIPT_REPAIR_REQUIRED: {
                "put-report-manuscript",
                "submit-brief-insufficient",
            },
            _PendingAction.BRIEF_REBUILD_REQUIRED: {"put-report-brief"},
            _PendingAction.RESEARCH_REOPEN_REQUIRED: set(),
        }[pending]
        if allowed is None or command in allowed:
            return
        raise CertifiedDeliveryError(
            f"{command} is blocked by pending action {pending.value}"
        )

    @staticmethod
    def _pending_for_reader(review: ReportReviewResult) -> _PendingAction:
        target = reader_repair_target(review.blocking_issues)
        if target is RepairTarget.BRIEF:
            return _PendingAction.BRIEF_REBUILD_REQUIRED
        if target is RepairTarget.MANUSCRIPT:
            return _PendingAction.MANUSCRIPT_REPAIR_REQUIRED
        raise CertifiedDeliveryError("Reader blockers carry no repair target")

    @staticmethod
    def _brief_repair_feedback(
        session: Mapping[str, object],
    ) -> tuple[BriefRepairFeedback, ...]:
        """Adapt stored downstream review into neutral Constructor feedback."""

        submitted = session.get("brief_repair_feedback")
        if submitted is not None:
            if not isinstance(submitted, list) or not submitted:
                raise CertifiedDeliveryError("stored Brief repair feedback is invalid")
            submitted_feedback = tuple(
                _brief_repair_feedback_from(raw) for raw in submitted
            )
            validate_brief_repair_feedback(submitted_feedback)
            return submitted_feedback

        reader = session.get("reader_review")
        if isinstance(reader, Mapping):
            raw_issues = reader.get("blocking_issues")
            if not isinstance(raw_issues, list):
                raise CertifiedDeliveryError(
                    "stored Reader review blocking_issues are invalid"
                )
            reader_feedback: list[BriefRepairFeedback] = []
            for raw in raw_issues:
                if not isinstance(raw, Mapping):
                    raise CertifiedDeliveryError("stored Reader blocker is invalid")
                if raw.get("repair_target") != RepairTarget.BRIEF.value:
                    continue
                reader_feedback.append(
                    BriefRepairFeedback(
                        problem=_mapping_string(raw, "problem"),
                        downstream_effect=_mapping_string(raw, "reader_effect"),
                        resolution_condition=_mapping_string(
                            raw, "resolution_condition"
                        ),
                        location=_optional_stored_string(raw, "location"),
                    )
                )
            if reader_feedback:
                return tuple(reader_feedback)

        integrity = session.get("integrity_review")
        if isinstance(integrity, Mapping):
            if integrity.get("revise_target") != RepairTarget.BRIEF.value:
                raise CertifiedDeliveryError(
                    "stored Integrity review does not require Brief repair"
                )
            raw_issues = integrity.get("issues")
            issues = _strings_from(raw_issues, "Integrity issues")
            if issues:
                return tuple(
                    BriefRepairFeedback(
                        problem=issue,
                        downstream_effect=(
                            "The current Report Brief cannot receive Research "
                            "Integrity certification"
                        ),
                        resolution_condition=(
                            "The rebuilt Report Brief must represent the accepted "
                            "research semantics within its evidence boundary"
                        ),
                    )
                    for issue in issues
                )
        raise CertifiedDeliveryError(
            "Brief rebuild is pending but semantic repair feedback is unavailable"
        )

    @staticmethod
    def _pending_for_integrity(review: ResearchIntegrityReview) -> _PendingAction:
        if review.disposition is IntegrityDisposition.REOPEN_RESEARCH:
            return _PendingAction.RESEARCH_REOPEN_REQUIRED
        if review.revise_target is RepairTarget.BRIEF:
            return _PendingAction.BRIEF_REBUILD_REQUIRED
        if review.revise_target is RepairTarget.MANUSCRIPT:
            return _PendingAction.MANUSCRIPT_REPAIR_REQUIRED
        raise CertifiedDeliveryError("Integrity repair carries no repair target")

    @staticmethod
    def _required_string(session: Mapping[str, object], name: str) -> str:
        value = session.get(name)
        if not isinstance(value, str) or not value:
            raise CertifiedDeliveryError(f"report delivery session lacks {name}")
        return value

    @staticmethod
    def _require_brief(session: Mapping[str, object]) -> ReportBrief:
        value = session.get("brief")
        if not isinstance(value, Mapping):
            raise CertifiedDeliveryError("put-report-brief is required first")
        return _brief_from(value)

    @staticmethod
    def _require_manuscript(session: Mapping[str, object]) -> ReportManuscript:
        value = session.get("manuscript")
        if not isinstance(value, Mapping):
            raise CertifiedDeliveryError("put-report-manuscript is required first")
        return _manuscript_from(value)

    @staticmethod
    def _require_blind_read(session: Mapping[str, object]) -> BlindReadResult:
        value = session.get("blind_read")
        if not isinstance(value, Mapping):
            raise CertifiedDeliveryError("submit-blind-review is required first")
        return _blind_read_from(value)

    @staticmethod
    def _reader_pass(session: Mapping[str, object]) -> ReaderPass:
        value = session.get("reader_pass")
        if not isinstance(value, Mapping):
            raise CertifiedDeliveryError("matching Reader PASS is required")
        return ReaderPass(
            brief_digest=_mapping_string(value, "brief_digest"),
            manuscript_digest=_mapping_string(value, "manuscript_digest"),
        )

    @staticmethod
    def _integrity_pass(session: Mapping[str, object]) -> IntegrityPass:
        value = session.get("integrity_pass")
        if not isinstance(value, Mapping):
            raise CertifiedDeliveryError("matching Integrity PASS is required")
        return IntegrityPass(
            delivery_basis_key=_mapping_string(value, "delivery_basis_key"),
            brief_digest=_mapping_string(value, "brief_digest"),
            manuscript_digest=_mapping_string(value, "manuscript_digest"),
        )

    @staticmethod
    def _status(
        run_id: str, stage: str, session: Mapping[str, object]
    ) -> CertifiedDeliveryStatus:
        reader = session.get("reader_pass")
        integrity = session.get("integrity_pass")
        return CertifiedDeliveryStatus(
            run_id=run_id,
            stage=stage,
            delivery_basis_key=CertifiedReportDelivery._required_string(
                session, "delivery_basis_key"
            ),
            brief_digest=_optional_mapping_string(session, "brief_digest"),
            manuscript_digest=_optional_mapping_string(session, "manuscript_digest"),
            blind_read_digest=_optional_mapping_string(session, "blind_read_digest"),
            reader_pass=(
                ReaderPass(
                    brief_digest=_mapping_string(reader, "brief_digest"),
                    manuscript_digest=_mapping_string(reader, "manuscript_digest"),
                )
                if isinstance(reader, Mapping)
                else None
            ),
            integrity_pass=(
                IntegrityPass(
                    delivery_basis_key=_mapping_string(integrity, "delivery_basis_key"),
                    brief_digest=_mapping_string(integrity, "brief_digest"),
                    manuscript_digest=_mapping_string(integrity, "manuscript_digest"),
                )
                if isinstance(integrity, Mapping)
                else None
            ),
            rendered=isinstance(session.get("rendered"), Mapping),
            pending_action=CertifiedReportDelivery._pending_action(session).value,
        )


def _jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _pretty(value: object) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True)


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mapping_string(value: Mapping[str, object], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item.strip():
        raise CertifiedDeliveryError(f"stored {name} is invalid")
    return item


def _mapping_non_negative_int(value: Mapping[str, object], name: str) -> int:
    item = value.get(name)
    if not isinstance(item, int) or isinstance(item, bool) or item < 0:
        raise CertifiedDeliveryError(f"stored {name} is invalid")
    return item


def _optional_mapping_string(value: Mapping[str, object], name: str) -> str | None:
    item = value.get(name)
    if item is None:
        return None
    return _mapping_string(value, name)


def _optional_stored_string(value: Mapping[str, object], name: str) -> str | None:
    """Read an explicitly stored nullable string and fail closed on bad types."""

    if name not in value:
        raise CertifiedDeliveryError(f"stored {name} is missing")
    item = value[name]
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise CertifiedDeliveryError(f"stored {name} is invalid")
    return item


def _brief_repair_feedback_from(value: object) -> BriefRepairFeedback:
    if not isinstance(value, Mapping):
        raise CertifiedDeliveryError("stored BriefRepairFeedback is invalid")
    return BriefRepairFeedback(
        problem=_mapping_string(value, "problem"),
        downstream_effect=_mapping_string(value, "downstream_effect"),
        resolution_condition=_mapping_string(value, "resolution_condition"),
        location=_optional_stored_string(value, "location"),
    )


def _locator_from(value: object) -> SourceLocator | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CertifiedDeliveryError("stored locator is invalid")
    return SourceLocator(
        kind=_mapping_string(value, "kind"),
        value=_mapping_string(value, "value"),
    )


def _strings_from(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise CertifiedDeliveryError(f"stored {name} is invalid")
    return tuple(value)


def _nonempty_strings_from(value: object, name: str) -> tuple[str, ...]:
    result = _strings_from(value, name)
    if not result:
        raise CertifiedDeliveryError(f"stored {name} is invalid")
    return result


def _brief_from(value: Mapping[str, object]) -> ReportBrief:
    raw_sections = value.get("sections")
    if not isinstance(raw_sections, list):
        raise CertifiedDeliveryError("stored Report Brief sections are invalid")
    sections: list[ReportBriefSection] = []
    for raw_section in raw_sections:
        if not isinstance(raw_section, Mapping):
            raise CertifiedDeliveryError("stored Report Brief section is invalid")
        raw_material = raw_section.get("material", [])
        if not isinstance(raw_material, list):
            raise CertifiedDeliveryError("stored Brief material is invalid")
        material: list[BriefMaterial] = []
        for raw in raw_material:
            if not isinstance(raw, Mapping):
                raise CertifiedDeliveryError("stored BriefMaterial is invalid")
            material.append(
                BriefMaterial(
                    content=_mapping_string(raw, "content"),
                    role=_optional_stored_string(raw, "role"),
                    reader_visible_obligation=_optional_stored_string(
                        raw, "reader_visible_obligation"
                    ),
                    research_refs=_strings_from(
                        raw.get("research_refs"), "material research_refs"
                    ),
                    source_ref=_optional_stored_string(raw, "source_ref"),
                    locator=_locator_from(raw.get("locator")),
                )
            )
        sections.append(
            ReportBriefSection(
                title=_mapping_string(raw_section, "title"),
                purpose=_mapping_string(raw_section, "purpose"),
                reader_takeaway=_mapping_string(raw_section, "reader_takeaway"),
                semantic_moves=_nonempty_strings_from(
                    raw_section.get("semantic_moves"), "semantic_moves"
                ),
                outline_depth=_mapping_non_negative_int(raw_section, "outline_depth"),
                requirement_refs=_strings_from(
                    raw_section.get("requirement_refs"), "requirement_refs"
                ),
                research_refs=_strings_from(
                    raw_section.get("research_refs"), "research_refs"
                ),
                material=tuple(material),
                evidence_boundary=_mapping_string(raw_section, "evidence_boundary"),
            )
        )
    raw_terms = value.get("terminology")
    if not isinstance(raw_terms, list):
        raise CertifiedDeliveryError("stored terminology is invalid")
    terminology: list[tuple[str, str]] = []
    for item in raw_terms:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(term, str) and term for term in item)
        ):
            raise CertifiedDeliveryError("stored terminology entry is invalid")
        terminology.append((item[0], item[1]))
    return ReportBrief(
        report_title=_mapping_string(value, "report_title"),
        audience=_mapping_string(value, "audience"),
        report_goal=_mapping_string(value, "report_goal"),
        conceptual_model=_mapping_string(value, "conceptual_model"),
        reader_takeaway=_mapping_string(value, "reader_takeaway"),
        narrative_logic=_mapping_string(value, "narrative_logic"),
        sections=tuple(sections),
        terminology=tuple(terminology),
        intentional_omissions=_strings_from(
            value.get("intentional_omissions"), "intentional_omissions"
        ),
    )


def _manuscript_from(value: Mapping[str, object]) -> ReportManuscript:
    raw_citations = value.get("citations", [])
    if not isinstance(raw_citations, list):
        raise CertifiedDeliveryError("stored citations are invalid")
    citations: list[CitationReference] = []
    for raw in raw_citations:
        if not isinstance(raw, Mapping):
            raise CertifiedDeliveryError("stored citation is invalid")
        citations.append(
            CitationReference(
                citation_id=_mapping_string(raw, "citation_id"),
                paper_ref=_mapping_string(raw, "paper_ref"),
                locator=_locator_from(raw.get("locator")),
            )
        )
    return ReportManuscript(
        markdown=_mapping_string(value, "markdown"),
        citations=tuple(citations),
    )


def _blind_read_from(value: Mapping[str, object]) -> BlindReadResult:
    raw_issues = value.get("blocking_issues", [])
    if not isinstance(raw_issues, list):
        raise CertifiedDeliveryError("stored Blind Read blocking_issues are invalid")
    issues: list[BlindBlockingIssue] = []
    for raw in raw_issues:
        if not isinstance(raw, Mapping):
            raise CertifiedDeliveryError("stored BlindBlockingIssue is invalid")
        issues.append(
            BlindBlockingIssue(
                problem=_mapping_string(raw, "problem"),
                reader_effect=_mapping_string(raw, "reader_effect"),
                why_blocking=_mapping_string(raw, "why_blocking"),
                location=_optional_stored_string(raw, "location"),
            )
        )
    raw_friction = value.get("cognitive_friction")
    if not isinstance(raw_friction, list):
        raise CertifiedDeliveryError("stored cognitive_friction is invalid")
    friction: list[CognitiveFrictionObservation] = []
    for raw in raw_friction:
        if not isinstance(raw, Mapping):
            raise CertifiedDeliveryError(
                "stored CognitiveFrictionObservation is invalid"
            )
        friction.append(
            CognitiveFrictionObservation(
                location=_mapping_string(raw, "location"),
                observation=_mapping_string(raw, "observation"),
                reader_cost=_mapping_string(raw, "reader_cost"),
            )
        )
    return BlindReadResult(
        core_understanding=_mapping_string(value, "core_understanding"),
        domain_model=_mapping_string(value, "domain_model"),
        comparison_coordinates=_mapping_string(value, "comparison_coordinates"),
        reverse_outline=_mapping_string(value, "reverse_outline"),
        material_economy=_mapping_string(value, "material_economy"),
        professional_finish=_mapping_string(value, "professional_finish"),
        manuscript_digest=_mapping_string(value, "manuscript_digest"),
        cognitive_friction=tuple(friction),
        blocking_issues=tuple(issues),
    )
