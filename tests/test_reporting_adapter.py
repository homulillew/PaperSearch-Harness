"""Production adapter and certified publication integration tests."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

from my_search_harness.domain.model import (
    ArtifactKind,
    CompletionVerdict,
    LifecycleMode,
)
from my_search_harness.runtime.commands import CreateRunRequest
from my_search_harness.runtime.local_runtime import LocalV1Runtime


REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = (
    REPO_ROOT / ".claude" / "skills" / "literature-research" / "scripts" / "harness.py"
)


def _load_harness():
    spec = importlib.util.spec_from_file_location("reporting_harness", HARNESS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime(workspace: Path) -> LocalV1Runtime:
    return LocalV1Runtime(
        workspace,
        paper_search_provider=None,
        source_access_provider=None,
    )


def _delivery_run(workspace: Path) -> tuple[str, int, str]:
    runtime = _runtime(workspace)
    created = runtime.researcher.create_run(
        CreateRunRequest(
            mission="test certified delivery",
            requirements=("explain the result",),
            scope="test scope",
            deliverable_description="certified report",
            required_artifacts=frozenset({ArtifactKind.REPORT}),
        )
    )
    requested = runtime.researcher.request_completion_check(
        created.run_id, created.state_revision, "ready"
    )
    passed = runtime.completion_checker.submit_completion_check(
        created.run_id,
        requested.state_revision,
        requested.completion_check_ref,
        CompletionVerdict.PASS,
        ("sufficient",),
    )
    view = runtime.delivery.view(created.run_id)
    return created.run_id, passed.state_revision, view.contract.requirements[0].ref


def _input(tmp_path: Path, name: str, payload: dict[str, object]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _invoke(harness, workspace: Path, *args: str) -> tuple[int, dict[str, object]]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = harness.main(
        ["--workspace", str(workspace), *args],
        stdout=stdout,
        stderr=stderr,
    )
    raw = stdout.getvalue() if code == 0 else stderr.getvalue()
    return code, json.loads(raw)


# ---------------------------------------------------------------------------
# Lean v0.6.1 payload helpers
# ---------------------------------------------------------------------------
#
# The Report Brief is the lean editorial design: audience, promise, frame,
# arc, focus — all non-empty strings. It carries no section list, no heading
# text, no outline depth, no semantic moves, no material. The Reader issue
# has no per-issue repair target or why_blocking; the repair target is a
# Phase 2 top-level decision. Brief repair feedback carries problem /
# optional location only (no resolution_condition, no downstream_effect).


def _brief(*, promise: str = "explain the result") -> dict[str, object]:
    return {
        "audience": "technical reader",
        "promise": promise,
        "frame": "premise and consequence form one causal model",
        "arc": "establish the premise, then derive the consequence",
        "focus": "the consequence follows from the premise",
    }


def _manuscript(
    markdown: str = "# Report\n\n## Result\n\nCertified content.",
) -> dict[str, object]:
    return {"markdown": markdown, "citations": []}


def _reader_issue(
    *,
    observation: str = "a key transition is missing",
    reader_effect: str = "the reader cannot connect premise and result",
    location: str | None = "Result",
) -> dict[str, object]:
    issue: dict[str, object] = {
        "observation": observation,
        "reader_effect": reader_effect,
    }
    if location is not None:
        issue["location"] = location
    return issue


def _brief_repair_feedback() -> dict[str, object]:
    return {
        "feedback": [
            {
                "problem": "the current comparison cannot be realized faithfully",
                "location": "Result",
            }
        ]
    }


def _put_brief_and_manuscript(
    harness,
    workspace: Path,
    tmp_path: Path,
    run_id: str,
    *,
    promise: str = "explain the result",
    markdown: str = "# Report\n\n## Result\n\nCertified content.",
) -> tuple[str, str]:
    brief_path = _input(tmp_path, f"brief-{promise}.json", _brief(promise=promise))
    code, envelope = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(brief_path),
    )
    assert code == 0, envelope
    b_digest = envelope["result"]["brief_digest"]
    manuscript_path = _input(tmp_path, f"manuscript-{promise}.json", _manuscript(markdown))
    code, envelope = _invoke(
        harness,
        workspace,
        "put-report-manuscript",
        "--run-id",
        run_id,
        "--input",
        str(manuscript_path),
    )
    assert code == 0, envelope
    return b_digest, envelope["result"]["manuscript_digest"]


def _blind_read(
    manuscript_digest: str,
    *,
    received_understanding: str = "understood",
    blocking_issues: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "received_understanding": received_understanding,
        "manuscript_digest": manuscript_digest,
        "blocking_issues": blocking_issues or [],
    }


def _reader_review(
    *,
    blind_read_digest: str,
    brief_digest: str,
    manuscript_digest: str,
    repair_target: str | None,
    rationale: str,
) -> dict[str, object]:
    return {
        "blind_read_digest": blind_read_digest,
        "brief_digest": brief_digest,
        "manuscript_digest": manuscript_digest,
        "repair_target": repair_target,
        "rationale": rationale,
    }


def _reader_pass(
    harness,
    workspace: Path,
    tmp_path: Path,
    run_id: str,
    brief_digest: str,
    manuscript_digest: str,
    *,
    blind_issues: list[dict[str, object]] | None = None,
    repair_target: str | None = None,
    rationale: str = "the report delivers the promised understanding",
) -> dict[str, object]:
    """Run Phase 1 (blind) then Phase 2 (attribution) and return the envelope.

    v0.6.1: a PASS is ``repair_target is None`` (rationale may be empty). A
    non-None target requires a non-empty rationale. Phase 2 no longer carries
    blocking_issues; the frozen Blind Read owns the blockers.
    """

    blind_path = _input(
        tmp_path,
        "blind.json",
        _blind_read(
            manuscript_digest,
            received_understanding="understood",
            blocking_issues=blind_issues,
        ),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "submit-blind-review",
        "--run-id",
        run_id,
        "--input",
        str(blind_path),
    )
    assert code == 0, envelope
    frozen_digest = envelope["result"]["blind_read_digest"]
    review_path = _input(
        tmp_path,
        "reader.json",
        _reader_review(
            blind_read_digest=frozen_digest,
            brief_digest=brief_digest,
            manuscript_digest=manuscript_digest,
            repair_target=repair_target,
            rationale=rationale,
        ),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "submit-reader-review",
        "--run-id",
        run_id,
        "--input",
        str(review_path),
    )
    assert code == 0, envelope
    return envelope


def _integrity_pass(
    harness, workspace: Path, tmp_path: Path, run_id: str
) -> dict[str, object]:
    path = _input(
        tmp_path,
        "integrity.json",
        {"disposition": "PASS", "issues": []},
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "submit-integrity-review",
        "--run-id",
        run_id,
        "--input",
        str(path),
    )
    assert code == 0, envelope
    return envelope


def _certify(
    harness,
    workspace: Path,
    tmp_path: Path,
    run_id: str,
) -> tuple[str, str]:
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id
    )
    envelope = _reader_pass(
        harness, workspace, tmp_path, run_id, b_digest, m_digest
    )
    assert envelope["result"]["stage"] == "READER_PASS"
    envelope = _integrity_pass(harness, workspace, tmp_path, run_id)
    assert envelope["result"]["stage"] == "INTEGRITY_PASS"
    return b_digest, m_digest


# ---------------------------------------------------------------------------
# Certified publication path
# ---------------------------------------------------------------------------


def test_harness_has_one_certified_publication_path(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, revision, _requirement_ref = _delivery_run(workspace)

    # The old direct publication commands are no longer part of the parser.
    for command in ("render-report", "publish-report"):
        code, envelope = _invoke(harness, workspace, command)
        assert code == 2
        assert envelope["error"]["type"] == "AdapterInputError"

    # No session/certification cannot publish.
    code, _ = _invoke(
        harness,
        workspace,
        "publish-certified-report",
        "--run-id",
        run_id,
        "--expected-revision",
        str(revision),
    )
    assert code == 2

    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id
    )
    session_path = workspace / "runs" / run_id / "delivery" / "report_session.json"
    session_before_preview = session_path.read_text(encoding="utf-8")
    code, preview = _invoke(
        harness,
        workspace,
        "render-reader-preview",
        "--run-id",
        run_id,
    )
    assert code == 0, preview
    assert (
        preview["result"]["content"] == "# Report\n\n## Result\n\nCertified content.\n"
    )
    assert preview["result"]["brief_digest"] == b_digest
    assert preview["result"]["manuscript_digest"] == m_digest
    assert session_path.read_text(encoding="utf-8") == session_before_preview
    # No Reader or Integrity PASS.
    code, _ = _invoke(
        harness,
        workspace,
        "render-certified-report",
        "--run-id",
        run_id,
    )
    assert code == 2

    _reader_pass(harness, workspace, tmp_path, run_id, b_digest, m_digest)
    # Reader-only is insufficient.
    code, _ = _invoke(
        harness,
        workspace,
        "render-certified-report",
        "--run-id",
        run_id,
    )
    assert code == 2

    _integrity_pass(harness, workspace, tmp_path, run_id)
    code, rendered = _invoke(
        harness,
        workspace,
        "render-certified-report",
        "--run-id",
        run_id,
    )
    assert code == 0, rendered
    assert rendered["result"]["content"] == preview["result"]["content"]
    code, published = _invoke(
        harness,
        workspace,
        "publish-certified-report",
        "--run-id",
        run_id,
        "--expected-revision",
        str(revision),
    )
    assert code == 0, published
    assert published["result"]["artifact_kind"] == "REPORT"

    captures = workspace / "scratch" / run_id / "captures" / "report"
    assert (captures / "report_brief.json").is_file()
    assert (captures / "manuscript_pre_reader.md").is_file()
    assert (captures / f"blind_review_{m_digest[:12]}.json").is_file()
    assert (captures / f"reader_review_{m_digest[:12]}.json").is_file()
    assert (captures / f"integrity_review_{m_digest[:12]}.json").is_file()
    session = workspace / "runs" / run_id / "delivery" / "report_session.json"
    assert session.is_file()
    session_value = json.loads(session.read_text(encoding="utf-8"))
    # v0.6.1 schema: schema_version is 6, and the lean Brief carries
    # only the five editorial fields — no sections, no semantic_moves, no
    # outline_depth, no material.
    assert session_value["schema_version"] == 6
    assert set(session_value["brief"]) == {
        "audience",
        "promise",
        "frame",
        "arc",
        "focus",
    }
    assert "sections" not in session_value["brief"]
    assert "semantic_moves" not in session_value["brief"]
    assert "outline_depth" not in session_value["brief"]
    assert "material" not in session_value["brief"]
    assert not (
        workspace / "scratch" / run_id / "report_delivery" / "session.json"
    ).exists()
    state = json.loads((workspace / "runs" / run_id / "state.json").read_text())
    assert "report_brief" not in state
    assert "reader_pass" not in state
    assert set(item.value for item in ArtifactKind) == {"REPORT"}


# ---------------------------------------------------------------------------
# Lean Report Brief adapter (ADR §16: Lean Brief)
# ---------------------------------------------------------------------------


def test_report_brief_adapter_accepts_lean_five_field_schema(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)

    brief_path = _input(tmp_path, "lean-brief.json", _brief())
    code, envelope = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(brief_path),
    )
    assert code == 0, envelope
    assert envelope["result"]["stage"] == "BRIEF_ACCEPTED"
    assert envelope["result"]["brief_digest"]
    assert envelope["result"]["pending_action"] == "NONE"


def test_report_brief_adapter_rejects_empty_editorial_fields(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)

    for field in ("audience", "promise", "frame"):
        brief = _brief()
        brief[field] = "   "
        path = _input(tmp_path, f"empty-{field}.json", brief)
        code, envelope = _invoke(
            harness,
            workspace,
            "put-report-brief",
            "--run-id",
            run_id,
            "--input",
            str(path),
        )
        assert code == 2
        assert field in envelope["error"]["message"]

    for field in ("arc", "focus"):
        brief = _brief()
        brief[field] = []
        path = _input(tmp_path, f"empty-{field}.json", brief)
        code, envelope = _invoke(
            harness,
            workspace,
            "put-report-brief",
            "--run-id",
            run_id,
            "--input",
            str(path),
        )
        assert code == 2
        assert field in envelope["error"]["message"]


def test_report_brief_adapter_rejects_v05_section_material_schema(tmp_path):
    """No compatibility migration: the old section/material schema is unknown.

    The lean five fields are present (so the required-fields check passes),
    but the legacy section/material/terminology fields are unknown inputs and
    are rejected — Python does not auto-convert the v0.5 schema.
    """
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)

    legacy = dict(_brief())
    legacy.update(
        {
            "report_title": "Report",
            "report_goal": "explain",
            "conceptual_model": "model",
            "reader_takeaway": "takeaway",
            "narrative_logic": "logic",
            "sections": [
                {
                    "title": "Result",
                    "purpose": "explain",
                    "semantic_moves": ["establish premise"],
                    "outline_depth": 0,
                    "requirement_refs": [],
                    "research_refs": [],
                    "material": [],
                    "evidence_boundary": "accepted state only",
                }
            ],
            "terminology": [],
            "intentional_omissions": [],
        }
    )
    path = _input(tmp_path, "legacy-brief.json", legacy)
    code, envelope = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(path),
    )
    assert code == 2
    assert "unknown input fields" in envelope["error"]["message"]


# ---------------------------------------------------------------------------
# Report Construction & Authoring Context (ADR §16: Authoring context)
# ---------------------------------------------------------------------------


def test_report_construction_input_is_narrow_and_deterministic(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)

    code, first = _invoke(
        harness, workspace, "report-construction-input", "--run-id", run_id
    )
    code2, second = _invoke(
        harness, workspace, "report-construction-input", "--run-id", run_id
    )
    assert code == code2 == 0
    assert first["result"] == second["result"]
    context = first["result"]["context"]
    assert "papers" not in context
    assert all(
        "representative_paper_refs" not in item for item in context["approach_families"]
    )
    assert all("sources" not in item for item in context["findings"])
    assert all("sources" not in item for item in context["open_problems"])
    # Constructor sees open gaps (a design input); Authoring does not.
    assert "open_gaps" in context
    assert first["result"]["repair"] is None


def test_report_authoring_context_excludes_paper_inventory_and_gaps(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)

    code, envelope = _invoke(
        harness, workspace, "report-authoring-context", "--run-id", run_id
    )
    assert code == 0, envelope
    context = envelope["result"]
    # Authoring sees the accepted high-level semantics...
    assert context["lifecycle"] == LifecycleMode.DELIVERY.value
    assert context["contract"]["mission"] == "test certified delivery"
    # Empty tuples are serialized as JSON arrays, so they come back as [].
    assert context["approach_families"] == []
    assert context["findings"] == []
    assert context["open_problems"] == []
    # ...but not the paper inventory, representative-paper refs, sources, or
    # open gaps (those are design inputs, not realization inputs).
    assert "papers" not in context
    assert "open_gaps" not in context
    assert "representative_paper_refs" not in context


# ---------------------------------------------------------------------------
# Outline freedom (ADR §16: Outline freedom)
# ---------------------------------------------------------------------------


def test_manuscript_outline_freedom_accepts_distinct_heading_structures(tmp_path):
    """The Lean Brief carries no heading contract, so Python does not match
    heading count, depth, or order. Two mechanically valid manuscripts with
    different titles and H2/H3 structures both enter the Reader."""
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)

    brief_path = _input(tmp_path, "brief.json", _brief())
    code, _ = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(brief_path),
    )
    assert code == 0

    flat = _input(
        tmp_path,
        "flat-manuscript.json",
        _manuscript("# Survey\n\n## Result\n\nContent."),
    )
    nested = _input(
        tmp_path,
        "nested-manuscript.json",
        _manuscript(
            "# Deep Report\n\n## Background\n\n### Prior\n\nText.\n\n## Result\n\n"
            "### Sub\n\nMore text.\n"
        ),
    )
    for path in (flat, nested):
        code, envelope = _invoke(
            harness,
            workspace,
            "put-report-manuscript",
            "--run-id",
            run_id,
            "--input",
            str(path),
        )
        assert code == 0, envelope


def test_manuscript_does_not_match_title_against_brief(tmp_path):
    """No H1/title invariant: a manuscript whose H1 differs from any Brief
    field is accepted. Authoring owns the title."""
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)

    brief_path = _input(tmp_path, "brief.json", _brief())
    code, _ = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(brief_path),
    )
    assert code == 0

    path = _input(
        tmp_path,
        "unmatched-title.json",
        _manuscript("# A Completely Different Title\n\n## Result\n\nContent."),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "put-report-manuscript",
        "--run-id",
        run_id,
        "--input",
        str(path),
    )
    assert code == 0, envelope


def test_staged_presentation_preflight_rejects_bad_tokens_and_math(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    brief_path = _input(tmp_path, "presentation-brief.json", _brief())
    code, _ = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(brief_path),
    )
    assert code == 0

    for index, (markdown, expected) in enumerate(
        (
            (
                "# Report\n\n## Result\n\n{{paper:unknown}}",
                "malformed paper navigation token",
            ),
            (
                "# Report\n\n## Result\n\n$$ x",
                "unmatched $$ math delimiters",
            ),
        )
    ):
        path = _input(tmp_path, f"bad-presentation-{index}.json", _manuscript(markdown))
        code, envelope = _invoke(
            harness,
            workspace,
            "put-report-manuscript",
            "--run-id",
            run_id,
            "--input",
            str(path),
        )
        assert code == 2
        assert expected in envelope["error"]["message"]


# ---------------------------------------------------------------------------
# Schema migration (ADR §5.1: older schema → fail-closed rebuild)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("old_version", [4, 5])
def test_schema_older_version_requires_brief_rebuild_and_put_brief_rebuilds(
    tmp_path, old_version
):
    # §5: every schema version below 6 must fail closed with the older-schema
    # error and rebuild to a schema-6 session via put-report-brief. Schema 5
    # is the direct predecessor of 6; a v5 session stores arc/focus as arrays
    # and carries why_blocking/blocking_issues/resolution_condition, so the
    # strict v6 deserializers cannot silently reuse it.
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    _put_brief_and_manuscript(harness, workspace, tmp_path, run_id)
    session_path = workspace / "runs" / run_id / "delivery" / "report_session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["schema_version"] = old_version
    session_path.write_text(json.dumps(session), encoding="utf-8")

    code, envelope = _invoke(
        harness,
        workspace,
        "render-reader-preview",
        "--run-id",
        run_id,
    )
    assert code == 2
    assert "older schema" in envelope["error"]["message"]
    assert "rebuild the Report Brief" in envelope["error"]["message"]

    rebuilt_path = _input(tmp_path, "rebuilt-brief.json", _brief())
    code, envelope = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(rebuilt_path),
    )
    assert code == 0, envelope
    rebuilt = json.loads(session_path.read_text(encoding="utf-8"))
    assert rebuilt["schema_version"] == 6
    assert rebuilt["manuscript"] is None


# ---------------------------------------------------------------------------
# Certification invalidation (ADR §16: Certification)
# ---------------------------------------------------------------------------


def test_brief_change_invalidates_certification_without_changing_research(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    runtime = _runtime(workspace)
    basis_before = runtime.delivery.view(run_id).delivery_basis
    state_path = workspace / "runs" / run_id / "state.json"
    state_before = state_path.read_bytes()

    _certify(harness, workspace, tmp_path, run_id)
    code, _ = _invoke(harness, workspace, "render-certified-report", "--run-id", run_id)
    assert code == 0

    changed_brief = _input(
        tmp_path, "changed-brief.json", _brief(promise="a different promise")
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(changed_brief),
    )
    assert code == 0, envelope
    assert envelope["result"]["reader_pass"] is None
    assert envelope["result"]["integrity_pass"] is None
    assert envelope["result"]["manuscript_digest"] is None

    code, _ = _invoke(
        harness,
        workspace,
        "render-certified-report",
        "--run-id",
        run_id,
    )
    assert code == 2
    assert state_path.read_bytes() == state_before
    assert _runtime(workspace).delivery.view(run_id).delivery_basis == basis_before


def test_integrity_input_without_reader_pass_is_rejected(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    _put_brief_and_manuscript(harness, workspace, tmp_path, run_id)
    path = _input(
        tmp_path,
        "integrity-without-reader.json",
        {"disposition": "PASS", "issues": []},
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "submit-integrity-review",
        "--run-id",
        run_id,
        "--input",
        str(path),
    )
    assert code == 2
    assert "Reader PASS" in envelope["error"]["message"]


def test_stale_manuscript_and_brief_invalidate_certification(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, revision, _ = _delivery_run(workspace)
    _certify(harness, workspace, tmp_path, run_id)
    code, _ = _invoke(harness, workspace, "render-certified-report", "--run-id", run_id)
    assert code == 0

    changed = _input(
        tmp_path,
        "changed-manuscript.json",
        _manuscript("# Report\n\n## Result\n\nChanged."),
    )
    code, _ = _invoke(
        harness,
        workspace,
        "put-report-manuscript",
        "--run-id",
        run_id,
        "--input",
        str(changed),
    )
    assert code == 0
    code, _ = _invoke(
        harness,
        workspace,
        "publish-certified-report",
        "--run-id",
        run_id,
        "--expected-revision",
        str(revision),
    )
    assert code == 2

    # Re-certify, then changing the Brief invalidates Manuscript and both gates.
    _certify(harness, workspace, tmp_path, run_id)
    changed_brief = _input(
        tmp_path, "changed-brief.json", _brief(promise="changed promise")
    )
    code, _ = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(changed_brief),
    )
    assert code == 0
    code, _ = _invoke(harness, workspace, "render-certified-report", "--run-id", run_id)
    assert code == 2


# ---------------------------------------------------------------------------
# v0.6 one-shot Reader decision (ADR §5.7, §7)
# ---------------------------------------------------------------------------


def test_reader_manuscript_blocker_requires_changed_manuscript_and_new_reader(tmp_path):
    """v0.6: a MANUSCRIPT repair target is a resource stop. The host must
    re-author and re-run the Reader; the same manuscript cannot clear the
    obligation, and a fresh blind read is required before Phase 2 again."""
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id
    )
    blocked = _reader_pass(
        harness,
        workspace,
        tmp_path,
        run_id,
        b_digest,
        m_digest,
        repair_target="MANUSCRIPT",
        rationale="a key transition is missing from the manuscript",
    )
    assert blocked["result"]["pending_action"] == "MANUSCRIPT_REPAIR_REQUIRED"

    pass_path = _input(
        tmp_path,
        "reader-pass-without-repair.json",
        _reader_review(
            blind_read_digest=blocked["result"]["blind_read_digest"],
            brief_digest=b_digest,
            manuscript_digest=m_digest,
            repair_target=None,
            rationale="the report now delivers the promised understanding",
        ),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "submit-reader-review",
        "--run-id",
        run_id,
        "--input",
        str(pass_path),
    )
    assert code == 2
    assert "MANUSCRIPT_REPAIR_REQUIRED" in envelope["error"]["message"]

    integrity_path = _input(
        tmp_path, "integrity-without-repair.json", {"disposition": "PASS", "issues": []}
    )
    code, _ = _invoke(
        harness,
        workspace,
        "submit-integrity-review",
        "--run-id",
        run_id,
        "--input",
        str(integrity_path),
    )
    assert code == 2

    same_path = _input(tmp_path, "same-manuscript.json", _manuscript())
    code, _ = _invoke(
        harness,
        workspace,
        "put-report-manuscript",
        "--run-id",
        run_id,
        "--input",
        str(same_path),
    )
    assert code == 2

    changed_path = _input(
        tmp_path,
        "repaired-manuscript.json",
        _manuscript("# Report\n\n## Result\n\nRepaired."),
    )
    code, accepted = _invoke(
        harness,
        workspace,
        "put-report-manuscript",
        "--run-id",
        run_id,
        "--input",
        str(changed_path),
    )
    assert code == 0, accepted
    assert accepted["result"]["pending_action"] == "NONE"
    assert accepted["result"]["blind_read_digest"] is None
    assert accepted["result"]["reader_pass"] is None
    assert accepted["result"]["integrity_pass"] is None

    # The old frozen blind read is gone; Phase 2 cannot run without a fresh
    # blind read on the new manuscript.
    code, envelope = _invoke(
        harness,
        workspace,
        "submit-reader-review",
        "--run-id",
        run_id,
        "--input",
        str(pass_path),
    )
    assert code == 2
    assert "submit-blind-review" in envelope["error"]["message"]


def test_authoring_brief_insufficient_round_trips_feedback_without_research_mutation(
    tmp_path,
):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    old_brief_digest, _ = _certify(harness, workspace, tmp_path, run_id)
    state_path = workspace / "runs" / run_id / "state.json"
    state_before = state_path.read_bytes()
    view_before = _runtime(workspace).delivery.view(run_id)
    feedback = _brief_repair_feedback()
    feedback_path = _input(tmp_path, "authoring-brief-insufficient.json", feedback)

    code, blocked = _invoke(
        harness,
        workspace,
        "submit-brief-insufficient",
        "--run-id",
        run_id,
        "--input",
        str(feedback_path),
    )
    assert code == 0, blocked
    status = blocked["result"]
    assert status["stage"] == "BRIEF_REBUILD_REQUIRED"
    assert status["pending_action"] == "BRIEF_REBUILD_REQUIRED"
    assert status["brief_digest"] == old_brief_digest
    assert status["manuscript_digest"] is None
    assert status["blind_read_digest"] is None
    assert status["reader_pass"] is None
    assert status["integrity_pass"] is None
    assert status["rendered"] is False
    assert state_path.read_bytes() == state_before

    code, construction = _invoke(
        harness,
        workspace,
        "report-construction-input",
        "--run-id",
        run_id,
    )
    assert code == 0, construction
    repair = construction["result"]["repair"]
    assert repair["previous_brief"]["promise"] == "explain the result"
    assert repair["feedback"] == feedback["feedback"]
    # v0.6.1: Brief repair feedback carries problem / optional location only
    # (no downstream_effect, no resolution_condition).
    assert "downstream_effect" not in repair["feedback"][0]
    assert "resolution_condition" not in repair["feedback"][0]
    assert set(repair["feedback"][0]) == {"problem", "location"}

    same_path = _input(tmp_path, "same-authoring-brief.json", _brief())
    code, _ = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(same_path),
    )
    assert code == 2

    changed_path = _input(
        tmp_path,
        "changed-authoring-brief.json",
        _brief(promise="a realizable comparison"),
    )
    code, accepted = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(changed_path),
    )
    assert code == 0, accepted
    assert accepted["result"]["pending_action"] == "NONE"
    assert accepted["result"]["brief_digest"] != old_brief_digest
    assert accepted["result"]["manuscript_digest"] is None
    assert accepted["result"]["blind_read_digest"] is None
    assert accepted["result"]["reader_pass"] is None
    assert accepted["result"]["integrity_pass"] is None

    session_path = workspace / "runs" / run_id / "delivery" / "report_session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    assert session["schema_version"] == 6
    assert session["brief_repair_feedback"] is None
    assert state_path.read_bytes() == state_before
    view_after = _runtime(workspace).delivery.view(run_id)
    assert view_after == view_before
    assert {item.value for item in ArtifactKind} == {"REPORT"}
    assert {item.value for item in LifecycleMode} == {
        "RESEARCH",
        "COMPLETION_CHECK",
        "DELIVERY",
        "CLOSED",
    }


def test_authoring_brief_insufficient_is_legal_before_initial_manuscript(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    brief_path = _input(tmp_path, "initial-brief.json", _brief())
    code, accepted = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(brief_path),
    )
    assert code == 0, accepted

    feedback_path = _input(
        tmp_path, "initial-brief-insufficient.json", _brief_repair_feedback()
    )
    code, blocked = _invoke(
        harness,
        workspace,
        "submit-brief-insufficient",
        "--run-id",
        run_id,
        "--input",
        str(feedback_path),
    )
    assert code == 0, blocked
    assert blocked["result"]["pending_action"] == "BRIEF_REBUILD_REQUIRED"
    assert blocked["result"]["brief_digest"] == accepted["result"]["brief_digest"]
    assert blocked["result"]["manuscript_digest"] is None


def test_authoring_brief_insufficient_requires_brief_and_can_supersede_revise(
    tmp_path,
):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    feedback_path = _input(
        tmp_path, "brief-insufficient-required.json", _brief_repair_feedback()
    )
    code, _ = _invoke(
        harness,
        workspace,
        "submit-brief-insufficient",
        "--run-id",
        run_id,
        "--input",
        str(feedback_path),
    )
    assert code == 2

    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id
    )
    blocked = _reader_pass(
        harness,
        workspace,
        tmp_path,
        run_id,
        b_digest,
        m_digest,
        repair_target="MANUSCRIPT",
        rationale="a key transition is missing",
    )
    assert blocked["result"]["pending_action"] == "MANUSCRIPT_REPAIR_REQUIRED"

    code, escalated = _invoke(
        harness,
        workspace,
        "submit-brief-insufficient",
        "--run-id",
        run_id,
        "--input",
        str(feedback_path),
    )
    assert code == 0, escalated
    assert escalated["result"]["pending_action"] == "BRIEF_REBUILD_REQUIRED"
    assert escalated["result"]["manuscript_digest"] is None
    assert escalated["result"]["reader_pass"] is None
    assert escalated["result"]["integrity_pass"] is None


def test_reader_brief_blocker_requires_changed_brief(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id
    )
    blocked = _reader_pass(
        harness,
        workspace,
        tmp_path,
        run_id,
        b_digest,
        m_digest,
        repair_target="BRIEF",
        rationale="the Brief's arc has a cognitive gap",
    )
    assert blocked["result"]["pending_action"] == "BRIEF_REBUILD_REQUIRED"

    code, construction = _invoke(
        harness,
        workspace,
        "report-construction-input",
        "--run-id",
        run_id,
    )
    assert code == 0, construction
    repair = construction["result"]["repair"]
    assert repair["previous_brief"]["promise"] == "explain the result"
    assert repair["feedback"]
    assert repair["feedback"][0]["problem"]
    # v0.6.1: no resolution_condition, no downstream_effect in the projected
    # repair feedback — problem / optional location only.
    assert "resolution_condition" not in repair["feedback"][0]
    assert "downstream_effect" not in repair["feedback"][0]

    same_path = _input(tmp_path, "same-brief.json", _brief())
    code, _ = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(same_path),
    )
    assert code == 2

    changed_path = _input(
        tmp_path,
        "repaired-brief.json",
        _brief(promise="a repaired explanation"),
    )
    code, accepted = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(changed_path),
    )
    assert code == 0, accepted
    assert accepted["result"]["pending_action"] == "NONE"
    assert accepted["result"]["manuscript_digest"] is None
    assert accepted["result"]["reader_pass"] is None
    assert accepted["result"]["integrity_pass"] is None


def test_manuscript_blocker_does_not_create_brief_repair_context(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id
    )
    _reader_pass(
        harness,
        workspace,
        tmp_path,
        run_id,
        b_digest,
        m_digest,
        repair_target="MANUSCRIPT",
        rationale="a key transition is missing",
    )
    code, construction = _invoke(
        harness,
        workspace,
        "report-construction-input",
        "--run-id",
        run_id,
    )
    assert code == 0, construction
    assert construction["result"]["repair"] is None


# ---------------------------------------------------------------------------
# Integrity repair routing (ADR §16: Certification)
# ---------------------------------------------------------------------------


def test_integrity_manuscript_repair_cannot_be_overwritten_by_pass(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id
    )
    _reader_pass(harness, workspace, tmp_path, run_id, b_digest, m_digest)
    revise_path = _input(
        tmp_path,
        "integrity-revise-manuscript.json",
        {
            "disposition": "REVISE_DELIVERY",
            "issues": ["repair the manuscript"],
            "revise_target": "MANUSCRIPT",
        },
    )
    code, blocked = _invoke(
        harness,
        workspace,
        "submit-integrity-review",
        "--run-id",
        run_id,
        "--input",
        str(revise_path),
    )
    assert code == 0, blocked
    assert blocked["result"]["pending_action"] == "MANUSCRIPT_REPAIR_REQUIRED"

    pass_path = _input(
        tmp_path,
        "integrity-pass-without-repair.json",
        {"disposition": "PASS", "issues": []},
    )
    code, _ = _invoke(
        harness,
        workspace,
        "submit-integrity-review",
        "--run-id",
        run_id,
        "--input",
        str(pass_path),
    )
    assert code == 2

    same_path = _input(tmp_path, "same-integrity-manuscript.json", _manuscript())
    code, _ = _invoke(
        harness,
        workspace,
        "put-report-manuscript",
        "--run-id",
        run_id,
        "--input",
        str(same_path),
    )
    assert code == 2

    changed_path = _input(
        tmp_path,
        "integrity-repaired-manuscript.json",
        _manuscript("# Report\n\n## Result\n\nIntegrity repaired."),
    )
    code, accepted = _invoke(
        harness,
        workspace,
        "put-report-manuscript",
        "--run-id",
        run_id,
        "--input",
        str(changed_path),
    )
    assert code == 0, accepted
    assert accepted["result"]["pending_action"] == "NONE"
    code, _ = _invoke(
        harness,
        workspace,
        "submit-integrity-review",
        "--run-id",
        run_id,
        "--input",
        str(pass_path),
    )
    assert code == 2  # New Blind + Reader PASS are required first.


def test_integrity_brief_repair_requires_changed_brief(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id
    )
    _reader_pass(harness, workspace, tmp_path, run_id, b_digest, m_digest)
    revise_path = _input(
        tmp_path,
        "integrity-revise-brief.json",
        {
            "disposition": "REVISE_DELIVERY",
            "issues": ["repair the brief"],
            "revise_target": "BRIEF",
        },
    )
    code, blocked = _invoke(
        harness,
        workspace,
        "submit-integrity-review",
        "--run-id",
        run_id,
        "--input",
        str(revise_path),
    )
    assert code == 0, blocked
    assert blocked["result"]["pending_action"] == "BRIEF_REBUILD_REQUIRED"

    same_path = _input(tmp_path, "same-integrity-brief.json", _brief())
    code, _ = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(same_path),
    )
    assert code == 2

    changed_path = _input(
        tmp_path,
        "integrity-repaired-brief.json",
        _brief(promise="an integrity-repaired explanation"),
    )
    code, accepted = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(changed_path),
    )
    assert code == 0, accepted
    assert accepted["result"]["pending_action"] == "NONE"
    assert accepted["result"]["manuscript_digest"] is None


def test_stale_delivery_basis_rejects_old_certification(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, revision, _ = _delivery_run(workspace)
    _certify(harness, workspace, tmp_path, run_id)

    runtime = _runtime(workspace)
    reopened = runtime.delivery.reopen_research(run_id, revision)
    requested = runtime.researcher.request_completion_check(
        run_id, reopened.state_revision, "new basis"
    )
    passed = runtime.completion_checker.submit_completion_check(
        run_id,
        requested.state_revision,
        requested.completion_check_ref,
        CompletionVerdict.PASS,
        ("new sufficient basis",),
    )
    code, envelope = _invoke(
        harness, workspace, "render-certified-report", "--run-id", run_id
    )
    assert code == 2
    assert "DeliveryBasis" in envelope["error"]["message"]
    assert passed.state_revision > revision


# ---------------------------------------------------------------------------
# Blind → Phase 2 staged lock (ADR §5.6, §16: Frozen Blind)
# ---------------------------------------------------------------------------


def test_blind_failures_are_frozen_before_phase2_attribution(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id
    )
    blind_issue = _reader_issue(
        observation="missing bridge between premise and result",
        reader_effect="the reader cannot connect premise and result",
        location="section 1",
    )
    second_blind_issue = _reader_issue(
        observation="unstable comparison coordinate",
        reader_effect="the reader cannot compare the approaches",
        location="section 2",
    )
    blind_path = _input(
        tmp_path,
        "blind-with-issue.json",
        _blind_read(
            m_digest,
            received_understanding="partial understanding",
            blocking_issues=[blind_issue, second_blind_issue],
        ),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "submit-blind-review",
        "--run-id",
        run_id,
        "--input",
        str(blind_path),
    )
    assert code == 0, envelope
    frozen_digest = envelope["result"]["blind_read_digest"]
    capture = json.loads(
        (
            workspace
            / "scratch"
            / run_id
            / "captures"
            / "report"
            / f"blind_review_{m_digest[:12]}.json"
        ).read_text()
    )
    # v0.6.1: a ReaderIssue has no per-issue repair_target and no
    # why_blocking / resolution_condition. Attribution is a Phase 2 top-level
    # decision; the frozen Blind Read owns the blockers.
    assert "repair_target" not in capture["blocking_issues"][0]
    assert "resolution_condition" not in capture["blocking_issues"][0]
    assert "why_blocking" not in capture["blocking_issues"][0]
    assert set(capture["blocking_issues"][0]) == {
        "observation",
        "reader_effect",
        "location",
    }

    # A different digest represents an attempted rewrite of the frozen read.
    bad_review = _input(
        tmp_path,
        "rewritten-reader.json",
        _reader_review(
            blind_read_digest="0" * 64,
            brief_digest=b_digest,
            manuscript_digest=m_digest,
            repair_target=None,
            rationale="the report delivers the promised understanding",
        ),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "submit-reader-review",
        "--run-id",
        run_id,
        "--input",
        str(bad_review),
    )
    assert code == 2
    assert "frozen Blind Read" in envelope["error"]["message"]

    # The right frozen digest is still insufficient: a frozen Blind Read with
    # blocking issues cannot become a Phase 2 PASS. The staged lock rejects
    # a PASS (repair_target is None) while the blind read has blockers.
    disappearing_review = _input(
        tmp_path,
        "disappearing-reader.json",
        _reader_review(
            blind_read_digest=frozen_digest,
            brief_digest=b_digest,
            manuscript_digest=m_digest,
            repair_target=None,
            rationale="the report delivers the promised understanding",
        ),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "submit-reader-review",
        "--run-id",
        run_id,
        "--input",
        str(disappearing_review),
    )
    assert code == 2
    assert "frozen Blind Read" in envelope["error"]["message"]

    # Attribution with a real repair target is accepted. v0.6: the repair
    # target is top-level, not per-issue.
    attributed = _input(
        tmp_path,
        "attributed-reader.json",
        _reader_review(
            blind_read_digest=frozen_digest,
            brief_digest=b_digest,
            manuscript_digest=m_digest,
            repair_target="MANUSCRIPT",
            rationale="the manuscript must add the missing bridge",
        ),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "submit-reader-review",
        "--run-id",
        run_id,
        "--input",
        str(attributed),
    )
    assert code == 0, envelope
    assert envelope["result"]["stage"] == "READER_BLOCKED"
    assert envelope["result"]["pending_action"] == "MANUSCRIPT_REPAIR_REQUIRED"


def test_blind_to_phase2_staged_lock_blocks_mutation_until_review(tmp_path):
    """§1 staged hole: once a Blind Read is frozen (blind_read is not None)
    and the Phase 2 review has not been submitted (reader_review is None),
    semantic mutation is blocked until ``submit-reader-review`` completes.

    Concretely: put manuscript → submit blind → put a NEW manuscript must be
    REJECTED, because the frozen Blind Read was bound to the old manuscript.
    Only ``submit-reader-review`` (and read-only inspection) may proceed.
    Implemented with existing session facts — no new persisted stage.
    """
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id
    )

    # Freeze a Blind Read against the first manuscript.
    blind_path = _input(
        tmp_path, "blind.json", _blind_read(m_digest, received_understanding="ok")
    )
    code, blind_env = _invoke(
        harness,
        workspace,
        "submit-blind-review",
        "--run-id",
        run_id,
        "--input",
        str(blind_path),
    )
    assert code == 0, blind_env

    # A new manuscript must be rejected while the Phase 2 review is pending.
    new_manuscript = _input(
        tmp_path,
        "new-manuscript.json",
        _manuscript("# Report\n\n## Result\n\nA different manuscript."),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "put-report-manuscript",
        "--run-id",
        run_id,
        "--input",
        str(new_manuscript),
    )
    assert code == 2
    assert "blind" in envelope["error"]["message"].lower()

    # The staged lock blocks the other mutating commands too. A new Brief is
    # rejected for the same reason.
    new_brief = _input(
        tmp_path, "new-brief.json", _brief(promise="a different promise")
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(new_brief),
    )
    assert code == 2
    assert "blind" in envelope["error"]["message"].lower()

    # submit-reader-review is the one mutation that IS allowed: it completes
    # the staged lock.
    review_path = _input(
        tmp_path,
        "reader.json",
        _reader_review(
            blind_read_digest=blind_env["result"]["blind_read_digest"],
            brief_digest=b_digest,
            manuscript_digest=m_digest,
            repair_target=None,
            rationale="the report delivers the promised understanding",
        ),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "submit-reader-review",
        "--run-id",
        run_id,
        "--input",
        str(review_path),
    )
    assert code == 0, envelope

    # After the review completes, mutation is allowed again: a new manuscript
    # is now accepted (it clears the stale blind read for a fresh re-read).
    code, envelope = _invoke(
        harness,
        workspace,
        "put-report-manuscript",
        "--run-id",
        run_id,
        "--input",
        str(new_manuscript),
    )
    assert code == 0, envelope


def test_reader_research_target_is_rejected(tmp_path):
    """The Reader's repair_target is MANUSCRIPT | BRIEF only. There is no
    RESEARCH target — a suspected research gap must go through Integrity."""
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id
    )
    blind_path = _input(
        tmp_path,
        "blind-research-suspicion.json",
        _blind_read(
            m_digest,
            received_understanding="partial understanding",
            blocking_issues=[
                _reader_issue(
                    observation="support may be insufficient",
                    reader_effect="the claim cannot be trusted",
                )
            ],
        ),
    )
    code, frozen = _invoke(
        harness,
        workspace,
        "submit-blind-review",
        "--run-id",
        run_id,
        "--input",
        str(blind_path),
    )
    assert code == 0, frozen
    review_path = _input(
        tmp_path,
        "reader-research-target.json",
        _reader_review(
            blind_read_digest=frozen["result"]["blind_read_digest"],
            brief_digest=b_digest,
            manuscript_digest=m_digest,
            repair_target="POSSIBLE_RESEARCH_ISSUE",
            rationale="the support may be insufficient",
        ),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "submit-reader-review",
        "--run-id",
        run_id,
        "--input",
        str(review_path),
    )
    assert code == 2
    assert "repair_target is invalid" in envelope["error"]["message"]
    assert _runtime(workspace).delivery.view(run_id).lifecycle is LifecycleMode.DELIVERY


def test_reader_pass_requires_rationale_when_target_set(tmp_path):
    """v0.6.1: a non-None repair_target requires a non-empty rationale. Phase 2
    no longer carries blocking_issues (the frozen Blind Read owns the
    blockers), so a non-PASS review needs only target + rationale. PASS
    (target is None) may carry an empty rationale."""
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, _ = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id
    )
    blind_path = _input(
        tmp_path,
        "blind.json",
        _blind_read(m_digest),
    )
    code, blind_env = _invoke(
        harness,
        workspace,
        "submit-blind-review",
        "--run-id",
        run_id,
        "--input",
        str(blind_path),
    )
    assert code == 0, blind_env
    frozen_digest = blind_env["result"]["blind_read_digest"]

    # Target with empty rationale is rejected.
    no_rationale = _input(
        tmp_path,
        "no-rationale.json",
        _reader_review(
            blind_read_digest=frozen_digest,
            brief_digest=b_digest,
            manuscript_digest=m_digest,
            repair_target="MANUSCRIPT",
            rationale="   ",
        ),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "submit-reader-review",
        "--run-id",
        run_id,
        "--input",
        str(no_rationale),
    )
    assert code == 2
    assert "rationale" in envelope["error"]["message"]

    # A non-PASS review with a non-empty rationale and NO blocking_issues
    # is accepted: Phase 2 attributes only; the frozen Blind owns blockers.
    target_only = _input(
        tmp_path,
        "target-only.json",
        _reader_review(
            blind_read_digest=frozen_digest,
            brief_digest=b_digest,
            manuscript_digest=m_digest,
            repair_target="MANUSCRIPT",
            rationale="a transition is missing",
        ),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "submit-reader-review",
        "--run-id",
        run_id,
        "--input",
        str(target_only),
    )
    assert code == 0, envelope
    assert envelope["result"]["pending_action"] == "MANUSCRIPT_REPAIR_REQUIRED"


def test_confirmed_research_insufficiency_requires_explicit_reopen(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, revision, _ = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id
    )
    _reader_pass(harness, workspace, tmp_path, run_id, b_digest, m_digest)
    confirmation = _input(
        tmp_path,
        "confirmed-research-gap.json",
        {
            "disposition": "REOPEN_RESEARCH",
            "issues": ["accepted state is insufficient"],
        },
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "submit-integrity-review",
        "--run-id",
        run_id,
        "--input",
        str(confirmation),
    )
    assert code == 0, envelope
    assert envelope["result"]["stage"] == "RESEARCH_REOPEN_CONFIRMED"
    assert envelope["result"]["pending_action"] == "RESEARCH_REOPEN_REQUIRED"
    assert _runtime(workspace).delivery.view(run_id).lifecycle is LifecycleMode.DELIVERY

    pass_path = _input(
        tmp_path,
        "integrity-pass-over-reopen.json",
        {"disposition": "PASS", "issues": []},
    )
    code, _ = _invoke(
        harness,
        workspace,
        "submit-integrity-review",
        "--run-id",
        run_id,
        "--input",
        str(pass_path),
    )
    assert code == 2
    code, _ = _invoke(harness, workspace, "render-certified-report", "--run-id", run_id)
    assert code == 2
    code, _ = _invoke(
        harness,
        workspace,
        "publish-certified-report",
        "--run-id",
        run_id,
        "--expected-revision",
        str(revision),
    )
    assert code == 2

    code, reopened = _invoke(
        harness,
        workspace,
        "reopen-research",
        "--run-id",
        run_id,
        "--expected-revision",
        str(revision),
    )
    assert code == 0, reopened
    assert (
        _runtime(workspace).researcher.view(run_id).lifecycle is LifecycleMode.RESEARCH
    )
