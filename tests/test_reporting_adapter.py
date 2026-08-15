"""Production adapter and certified publication integration tests."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

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


def _brief(requirement_ref: str, *, goal: str = "explain") -> dict[str, object]:
    return {
        "audience": "technical reader",
        "report_goal": goal,
        "reader_takeaway": "understand the result",
        "narrative_logic": "premise to conclusion",
        "sections": [
            {
                "title": "Result",
                "purpose": "explain",
                "reader_takeaway": "result understood",
                "argument_flow": "premise then consequence",
                "outline_depth": 0,
                "requirement_refs": [requirement_ref],
                "research_refs": [],
                "material": [],
            }
        ],
        "terminology": [],
        "intentional_omissions": [],
    }


def _brief_with_depths(
    requirement_ref: str,
    depths: tuple[int, ...],
) -> dict[str, object]:
    brief = _brief(requirement_ref)
    base = brief["sections"][0]
    assert isinstance(base, dict)
    brief["sections"] = [
        {**base, "title": f"Section {index}", "outline_depth": depth}
        for index, depth in enumerate(depths, start=1)
    ]
    return brief


def _manuscript(
    markdown: str = "# Report\n\n## Result\n\nCertified content.",
) -> dict[str, object]:
    return {"markdown": markdown, "citations": []}


def _reader_issue(target: str) -> dict[str, object]:
    return {
        "problem": f"{target.lower()} repair needed",
        "reader_effect": "reader cannot rely on the report",
        "why_blocking": "the report promise is not met",
        "repair_target": target,
    }


def _put_brief_and_manuscript(
    harness,
    workspace: Path,
    tmp_path: Path,
    run_id: str,
    requirement_ref: str,
    *,
    goal: str = "explain",
    markdown: str = "# Report\n\n## Result\n\nCertified content.",
) -> tuple[str, str]:
    brief_path = _input(
        tmp_path, f"brief-{goal}.json", _brief(requirement_ref, goal=goal)
    )
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
    manuscript_path = _input(tmp_path, f"manuscript-{goal}.json", _manuscript(markdown))
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


def _reader_pass(
    harness,
    workspace: Path,
    tmp_path: Path,
    run_id: str,
    brief_digest: str,
    manuscript_digest: str,
    *,
    blind_issues: list[dict[str, object]] | None = None,
    reader_issues: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    blind_path = _input(
        tmp_path,
        "blind.json",
        {
            "core_understanding": "understood",
            "domain_model": "model",
            "comparison_coordinates": "coordinates",
            "reverse_outline": "outline",
            "manuscript_digest": manuscript_digest,
            "blocking_issues": blind_issues or [],
        },
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
        {
            "blind_read_digest": frozen_digest,
            "brief_digest": brief_digest,
            "manuscript_digest": manuscript_digest,
            "blocking_issues": reader_issues or [],
        },
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
    requirement_ref: str,
) -> tuple[str, str]:
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id, requirement_ref
    )
    envelope = _reader_pass(harness, workspace, tmp_path, run_id, b_digest, m_digest)
    assert envelope["result"]["stage"] == "READER_PASS"
    envelope = _integrity_pass(harness, workspace, tmp_path, run_id)
    assert envelope["result"]["stage"] == "INTEGRITY_PASS"
    return b_digest, m_digest


def test_harness_has_one_certified_publication_path(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, revision, requirement_ref = _delivery_run(workspace)

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
        harness, workspace, tmp_path, run_id, requirement_ref
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
    assert preview["result"]["content"] == "# Report\n\n## Result\n\nCertified content.\n"
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
    assert session_value["schema_version"] == 3
    assert session_value["brief"]["sections"][0]["outline_depth"] == 0
    assert not (
        workspace / "scratch" / run_id / "report_delivery" / "session.json"
    ).exists()
    state = json.loads((workspace / "runs" / run_id / "state.json").read_text())
    assert "report_brief" not in state
    assert "reader_pass" not in state
    assert set(item.value for item in ArtifactKind) == {"REPORT"}


def test_report_brief_adapter_requires_valid_outline_depth(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, requirement_ref = _delivery_run(workspace)

    missing = _brief(requirement_ref)
    del missing["sections"][0]["outline_depth"]
    missing_path = _input(tmp_path, "brief-missing-depth.json", missing)
    code, envelope = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(missing_path),
    )
    assert code == 2
    assert "outline_depth" in envelope["error"]["message"]

    boolean = _brief(requirement_ref)
    boolean["sections"][0]["outline_depth"] = True
    boolean_path = _input(tmp_path, "brief-bool-depth.json", boolean)
    code, envelope = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(boolean_path),
    )
    assert code == 2
    assert "integer" in envelope["error"]["message"]


def test_staged_manuscript_boundary_rejects_outline_mismatch(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, requirement_ref = _delivery_run(workspace)
    brief_path = _input(tmp_path, "brief.json", _brief(requirement_ref))
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

    manuscript_path = _input(
        tmp_path,
        "flat-manuscript.json",
        _manuscript("# Report\n\n**Result**\n\nContent."),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "put-report-manuscript",
        "--run-id",
        run_id,
        "--input",
        str(manuscript_path),
    )
    assert code == 2
    assert "visible outline" in envelope["error"]["message"]


def test_schema_v2_requires_brief_rebuild_and_put_brief_rebuilds(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, requirement_ref = _delivery_run(workspace)
    _put_brief_and_manuscript(
        harness,
        workspace,
        tmp_path,
        run_id,
        requirement_ref,
    )
    session_path = workspace / "runs" / run_id / "delivery" / "report_session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["schema_version"] = 2
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

    rebuilt_path = _input(tmp_path, "rebuilt-brief.json", _brief(requirement_ref))
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
    assert rebuilt["schema_version"] == 3
    assert rebuilt["manuscript"] is None


def test_outline_change_invalidates_certification_without_changing_research(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, requirement_ref = _delivery_run(workspace)
    runtime = _runtime(workspace)
    basis_before = runtime.delivery.view(run_id).delivery_basis
    state_path = workspace / "runs" / run_id / "state.json"
    state_before = state_path.read_bytes()

    nested_path = _input(
        tmp_path,
        "nested-brief.json",
        _brief_with_depths(requirement_ref, (0, 1)),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(nested_path),
    )
    assert code == 0, envelope
    old_brief_digest = envelope["result"]["brief_digest"]
    manuscript_path = _input(
        tmp_path,
        "nested-manuscript.json",
        _manuscript("# Report\n\n## Section 1\n\n### Section 2\n\nContent."),
    )
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
    manuscript_digest = envelope["result"]["manuscript_digest"]
    _reader_pass(
        harness,
        workspace,
        tmp_path,
        run_id,
        old_brief_digest,
        manuscript_digest,
    )
    _integrity_pass(harness, workspace, tmp_path, run_id)

    flat_path = _input(
        tmp_path,
        "flat-brief.json",
        _brief_with_depths(requirement_ref, (0, 0)),
    )
    code, envelope = _invoke(
        harness,
        workspace,
        "put-report-brief",
        "--run-id",
        run_id,
        "--input",
        str(flat_path),
    )
    assert code == 0, envelope
    assert envelope["result"]["brief_digest"] != old_brief_digest
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
    run_id, _, requirement_ref = _delivery_run(workspace)
    _put_brief_and_manuscript(harness, workspace, tmp_path, run_id, requirement_ref)
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
    run_id, revision, requirement_ref = _delivery_run(workspace)
    _certify(harness, workspace, tmp_path, run_id, requirement_ref)
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
    _certify(harness, workspace, tmp_path, run_id, requirement_ref)
    changed_brief = _input(
        tmp_path, "changed-brief.json", _brief(requirement_ref, goal="changed goal")
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


def test_reader_manuscript_blocker_requires_changed_manuscript_and_new_reader(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, requirement_ref = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id, requirement_ref
    )
    blocked = _reader_pass(
        harness,
        workspace,
        tmp_path,
        run_id,
        b_digest,
        m_digest,
        reader_issues=[_reader_issue("MANUSCRIPT")],
    )
    assert blocked["result"]["pending_action"] == "MANUSCRIPT_REPAIR_REQUIRED"

    pass_path = _input(
        tmp_path,
        "reader-pass-without-repair.json",
        {
            "blind_read_digest": blocked["result"]["blind_read_digest"],
            "brief_digest": b_digest,
            "manuscript_digest": m_digest,
            "blocking_issues": [],
        },
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


def test_reader_brief_blocker_requires_changed_brief(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, requirement_ref = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id, requirement_ref
    )
    blocked = _reader_pass(
        harness,
        workspace,
        tmp_path,
        run_id,
        b_digest,
        m_digest,
        reader_issues=[_reader_issue("BRIEF")],
    )
    assert blocked["result"]["pending_action"] == "BRIEF_REBUILD_REQUIRED"

    same_path = _input(tmp_path, "same-brief.json", _brief(requirement_ref))
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
        _brief(requirement_ref, goal="repaired explanation"),
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


def test_integrity_manuscript_repair_cannot_be_overwritten_by_pass(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, requirement_ref = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id, requirement_ref
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
        tmp_path, "integrity-pass-without-repair.json", {"disposition": "PASS", "issues": []}
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
    run_id, _, requirement_ref = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id, requirement_ref
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

    same_path = _input(tmp_path, "same-integrity-brief.json", _brief(requirement_ref))
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
        _brief(requirement_ref, goal="integrity repaired explanation"),
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
    run_id, revision, requirement_ref = _delivery_run(workspace)
    _certify(harness, workspace, tmp_path, run_id, requirement_ref)

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


def test_blind_failures_are_frozen_before_phase2_attribution(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, _, requirement_ref = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id, requirement_ref
    )
    blind_issue = {
        "location": "section 1",
        "problem": "missing bridge",
        "reader_effect": "reader cannot connect premise and result",
        "why_blocking": "the main conclusion cannot be reconstructed",
    }
    second_blind_issue = {
        "location": "section 2",
        "problem": "unstable comparison",
        "reader_effect": "reader cannot compare the approaches",
        "why_blocking": "the promised synthesis cannot be reconstructed",
    }
    blind_path = _input(
        tmp_path,
        "blind-with-issue.json",
        {
            "core_understanding": "partial",
            "domain_model": "incomplete",
            "comparison_coordinates": "none",
            "reverse_outline": "one disconnected section",
            "manuscript_digest": m_digest,
            "blocking_issues": [blind_issue, second_blind_issue],
        },
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
    assert "repair_target" not in capture["blocking_issues"][0]

    # A different digest represents an attempted rewrite of the frozen read.
    bad_review = _input(
        tmp_path,
        "rewritten-reader.json",
        {
            "blind_read_digest": "0" * 64,
            "brief_digest": b_digest,
            "manuscript_digest": m_digest,
            "blocking_issues": [],
        },
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

    # The right frozen digest is still insufficient: Blind FAIL cannot become
    # a Phase 2 PASS. Python does not require one-to-one issue mapping.
    disappearing_review = _input(
        tmp_path,
        "disappearing-reader.json",
        {
            "blind_read_digest": frozen_digest,
            "brief_digest": b_digest,
            "manuscript_digest": m_digest,
            "blocking_issues": [],
        },
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

    attributed = _input(
        tmp_path,
        "attributed-reader.json",
        {
            "blind_read_digest": frozen_digest,
            "brief_digest": b_digest,
            "manuscript_digest": m_digest,
            "blocking_issues": [{**blind_issue, "repair_target": "MANUSCRIPT"}],
        },
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


def test_possible_research_issue_does_not_reopen_research(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, revision, requirement_ref = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id, requirement_ref
    )
    issue = {
        "problem": "support may be insufficient",
        "reader_effect": "claim cannot be trusted",
        "why_blocking": "possible state-level gap",
    }
    envelope = _reader_pass(
        harness,
        workspace,
        tmp_path,
        run_id,
        b_digest,
        m_digest,
        blind_issues=[issue],
        reader_issues=[{**issue, "repair_target": "POSSIBLE_RESEARCH_ISSUE"}],
    )
    assert envelope["result"]["rationale"]
    assert _runtime(workspace).delivery.view(run_id).lifecycle is LifecycleMode.DELIVERY
    session = json.loads(
        (
            workspace / "runs" / run_id / "delivery" / "report_session.json"
        ).read_text()
    )
    assert session["pending_action"] == "RESEARCH_CONFIRMATION_REQUIRED"

    pass_path = _input(
        tmp_path,
        "reader-pass-over-suspicion.json",
        {
            "blind_read_digest": session["blind_read_digest"],
            "brief_digest": b_digest,
            "manuscript_digest": m_digest,
            "blocking_issues": [],
        },
    )
    code, _ = _invoke(
        harness,
        workspace,
        "submit-reader-review",
        "--run-id",
        run_id,
        "--input",
        str(pass_path),
    )
    assert code == 2

    integrity_path = _input(
        tmp_path, "integrity-over-suspicion.json", {"disposition": "PASS", "issues": []}
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

    same_path = _input(tmp_path, "same-suspect-manuscript.json", _manuscript())
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
        "confirmed-delivery-repair.json",
        _manuscript(
            "# Report\n\n## Result\n\nConfirmed Delivery-layer repair."
        ),
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


def test_confirmed_research_insufficiency_requires_explicit_reopen(tmp_path):
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    run_id, revision, requirement_ref = _delivery_run(workspace)
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp_path, run_id, requirement_ref
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
        tmp_path, "integrity-pass-over-reopen.json", {"disposition": "PASS", "issues": []}
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
