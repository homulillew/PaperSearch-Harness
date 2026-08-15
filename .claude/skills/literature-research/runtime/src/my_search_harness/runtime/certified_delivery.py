"""Persisted staged ADR-012 delivery with version-bound publication gates.

Claude Code supplies semantic work products across CLI calls.  This module
owns their deterministic order, freshness and certification in workspace
scratch.  Nothing here is ResearchRun state or a published artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from my_search_harness.domain.model import SourceLocator
from my_search_harness.domain.validation import validate_ref

from .capabilities import DeliveryCapabilities
from .citations import DeterministicCitationRenderer
from .delivery import PublishReportResult, _ReportPublicationAuthorization
from .reporting import (
    BlindReadResult,
    BriefMaterial,
    CitationReference,
    IntegrityDisposition,
    IntegrityPass,
    LocalReportCaptureSink,
    ReaderPass,
    RepairTarget,
    ReportBrief,
    ReportBriefSection,
    ReportManuscript,
    ReportReviewResult,
    ResearchConfirmationRequiredResult,
    ResearchIntegrityReview,
    blind_read_digest,
    brief_digest,
    delivery_basis_key,
    manuscript_digest,
    validate_blind_read,
    validate_integrity_review,
    validate_report_brief,
    validate_report_manuscript,
    validate_report_review,
)


class CertifiedDeliveryError(RuntimeError):
    """A staged Delivery command violated ordering or certification."""


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


@dataclass(slots=True, frozen=True, kw_only=True)
class CertifiedReportRenderResult:
    content: str
    content_sha256: str
    brief_digest: str
    manuscript_digest: str


class CertifiedReportDelivery:
    """Production orchestrator for Claude-driven staged report delivery."""

    _SCHEMA_VERSION = 1

    def __init__(
        self,
        workspace_root: str | Path,
        delivery: DeliveryCapabilities,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._delivery = delivery
        self._captures = LocalReportCaptureSink(self._workspace_root / "scratch")
        self._renderer = DeterministicCitationRenderer()

    def put_brief(self, run_id: str, brief: ReportBrief) -> CertifiedDeliveryStatus:
        view = self._delivery.view(run_id)
        validate_report_brief(view, brief)
        b_digest = brief_digest(brief)
        session = self._empty_session(delivery_basis_key(view.delivery_basis))
        session["brief"] = _jsonable(brief)
        session["brief_digest"] = b_digest
        self._save(run_id, session)
        self._captures.capture(run_id, "report_brief.json", _pretty(brief))
        return self._status(run_id, "BRIEF_ACCEPTED", session)

    def put_manuscript(
        self, run_id: str, manuscript: ReportManuscript
    ) -> CertifiedDeliveryStatus:
        session = self._load_current(run_id)
        self._require_brief(session)
        validate_report_manuscript(manuscript)
        name = (
            "manuscript_post_revision.md"
            if session.get("manuscript") is not None
            else "manuscript_pre_reader.md"
        )
        session["manuscript"] = _jsonable(manuscript)
        session["manuscript_digest"] = manuscript_digest(manuscript)
        self._clear_from(session, "blind_read")
        self._save(run_id, session)
        self._captures.capture(run_id, name, manuscript.markdown)
        return self._status(run_id, "MANUSCRIPT_ACCEPTED", session)

    def submit_blind_read(
        self, run_id: str, blind: BlindReadResult
    ) -> CertifiedDeliveryStatus:
        session = self._load_current(run_id)
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
    ) -> CertifiedDeliveryStatus | ResearchConfirmationRequiredResult:
        session = self._load_current(run_id)
        brief = self._require_brief(session)
        manuscript = self._require_manuscript(session)
        frozen_digest = self._required_string(session, "blind_read_digest")
        b_digest = brief_digest(brief)
        m_digest = manuscript_digest(manuscript)
        validate_report_review(review, frozen_digest, b_digest, m_digest)
        session["reader_review"] = _jsonable(review)
        session["reader_pass"] = (
            _jsonable(
                ReaderPass(
                    brief_digest=b_digest,
                    manuscript_digest=m_digest,
                )
            )
            if not review.blocking_issues
            else None
        )
        self._clear_from(session, "integrity_review")
        self._save(run_id, session)
        self._captures.capture(
            run_id, f"reader_review_{m_digest[:12]}.json", _pretty(review)
        )
        if any(
            issue.repair_target is RepairTarget.POSSIBLE_RESEARCH_ISSUE
            for issue in review.blocking_issues
        ):
            return ResearchConfirmationRequiredResult(
                rationale=(
                    "Reader reported a possible Research-layer issue; an actor "
                    "with Research Authority must confirm before reopen-research"
                ),
                issues=review.blocking_issues,
                brief_digest=b_digest,
                manuscript_digest=m_digest,
            )
        stage = "READER_PASS" if not review.blocking_issues else "READER_BLOCKED"
        return self._status(run_id, stage, session)

    def submit_integrity_review(
        self, run_id: str, review: ResearchIntegrityReview
    ) -> CertifiedDeliveryStatus:
        session = self._load_current(run_id)
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
            stage = "INTEGRITY_PASS"
        else:
            session["integrity_pass"] = None
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
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != self._SCHEMA_VERSION
        ):
            raise CertifiedDeliveryError("report delivery session is invalid")
        return value

    def _save(self, run_id: str, session: dict[str, object]) -> None:
        self._write_atomic(
            self._session_path(run_id),
            json.dumps(session, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def _session_directory(self, run_id: str) -> Path:
        validate_ref(run_id, "run", "run_id")
        return self._workspace_root / "scratch" / run_id / "report_delivery"

    def _session_path(self, run_id: str) -> Path:
        return self._session_directory(run_id) / "session.json"

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
            "brief": None,
            "brief_digest": None,
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
    if not isinstance(item, str) or not item:
        raise CertifiedDeliveryError(f"stored {name} is invalid")
    return item


def _optional_mapping_string(value: Mapping[str, object], name: str) -> str | None:
    item = value.get(name)
    if item is None:
        return None
    return _mapping_string(value, name)


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
        isinstance(item, str) and item for item in value
    ):
        raise CertifiedDeliveryError(f"stored {name} is invalid")
    return tuple(value)


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
                    role=raw.get("role") if isinstance(raw.get("role"), str) else None,
                    research_refs=_strings_from(
                        raw.get("research_refs", []), "material research_refs"
                    ),
                    source_ref=(
                        raw.get("source_ref")
                        if isinstance(raw.get("source_ref"), str)
                        else None
                    ),
                    locator=_locator_from(raw.get("locator")),
                )
            )
        sections.append(
            ReportBriefSection(
                title=_mapping_string(raw_section, "title"),
                purpose=_mapping_string(raw_section, "purpose"),
                reader_takeaway=_mapping_string(raw_section, "reader_takeaway"),
                argument_flow=_mapping_string(raw_section, "argument_flow"),
                requirement_refs=_strings_from(
                    raw_section.get("requirement_refs", []), "requirement_refs"
                ),
                research_refs=_strings_from(
                    raw_section.get("research_refs", []), "research_refs"
                ),
                material=tuple(material),
                evidence_boundary=(
                    raw_section.get("evidence_boundary")
                    if isinstance(raw_section.get("evidence_boundary"), str)
                    else None
                ),
            )
        )
    raw_terms = value.get("terminology", [])
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
        audience=_mapping_string(value, "audience"),
        report_goal=_mapping_string(value, "report_goal"),
        reader_takeaway=_mapping_string(value, "reader_takeaway"),
        narrative_logic=_mapping_string(value, "narrative_logic"),
        sections=tuple(sections),
        terminology=tuple(terminology),
        intentional_omissions=_strings_from(
            value.get("intentional_omissions", []), "intentional_omissions"
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
