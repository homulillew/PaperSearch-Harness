"""Deep Reading Control Loop — deterministic invariant tests.

These tests pin the four-state Paper model and the command-level invariants
that close the deep-reading loop:

  SearchHit (ephemeral)
    -> ACTIVE + analysis=None   (durable unresolved candidate)
    -> ACTIVE + analysis         (integrated)
    -> RETIRED + retirement_reason   (explicitly closed)

The invariants are enforced at command/mutation time, NOT in ``validate_run``
(which fires on every load and must not reject old/historical runs). These
tests cover:

- retirement: RETIRED requires a reason; ACTIVE clears it; reference safety.
- backward compat: an old paper JSON without ``retirement_reason`` still loads.
- completion hard gate: ACTIVE+unanalyzed blocks ``request-completion``.
- landscape evidence eligibility: a representative / source must be
  ACTIVE+analyzed.
- CompletionView: every retained paper is visible with its closure summary.

The command-level tests drive ``scripts/harness.py`` as a subprocess with
``--workspace`` pointed at a temp dir (style of ``test_filesystem_boundary.py``).
The codec round-trip tests use the in-process runtime (style of
``test_credentials.py``). No network, no DeepXiv, no source reads.

Run:

    python -m pytest tests/test_deep_reading.py --basetemp=./.pytest_tmp
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "literature-research"
)
RUNTIME_SRC = SKILL_DIR / "runtime" / "src"
SCRIPTS_DIR = SKILL_DIR / "scripts"
HARNESS = SCRIPTS_DIR / "harness.py"
REPO_ROOT = Path(__file__).resolve().parents[1]

# UTF-8 on Windows so the harness emits clean JSON.
_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

if str(RUNTIME_SRC) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SRC))

from my_search_harness.runtime.codec import (  # noqa: E402
    _paper_from_dict,
    _paper_to_dict,
    run_from_dict,
    run_to_dict,
)
from my_search_harness.domain.model import (  # noqa: E402
    Paper,
    PaperAnalysis,
    PaperResearchStatus,
    PaperSource,
)


# --- subprocess helpers ----------------------------------------------------


def _run_harness(workspace: Path, *args: str) -> tuple[int, str, str]:
    """Run harness.py as a subprocess; return (exit_code, stdout, stderr)."""

    proc = subprocess.run(
        [sys.executable, str(HARNESS), "--workspace", str(workspace), *args],
        capture_output=True,
        text=True,
        env=_ENV,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _create_run(workspace: Path, contract: dict | None = None) -> str:
    """Create a run via the harness and return its run_id."""

    scratch = workspace / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    contract_path = scratch / "research-contract.json"
    contract_path.write_text(
        json.dumps(contract or _DEFAULT_CONTRACT, ensure_ascii=False),
        encoding="utf-8",
    )
    code, out, err = _run_harness(workspace, "create-run", "--input", str(contract_path))
    assert code == 0, f"create-run failed: {err}\n{out}"
    payload = json.loads(out)
    assert payload["ok"] is True, payload
    return payload["result"]["run_id"]


def _view(workspace: Path, run_id: str) -> dict:
    code, out, err = _run_harness(workspace, "view", "--run-id", run_id)
    assert code == 0, f"view failed: {err}\n{out}"
    return json.loads(out)["result"]


def _inspect_paper(workspace: Path, run_id: str, rev: int, paper_ref: str) -> dict:
    """Inspect a single paper ref; return its ``value`` dict (research_status,
    retirement_reason, analysis, source)."""

    code, out, err = _run_harness(
        workspace,
        "inspect",
        "--run-id",
        run_id,
        "--expected-revision",
        str(rev),
        "--refs",
        paper_ref,
    )
    assert code == 0, f"inspect failed: {err}\n{out}"
    objs = json.loads(out)["result"]["objects"]
    assert objs and objs[0]["kind"] == "paper", objs
    return objs[0]["value"]


def _retain(workspace: Path, run_id: str, rev: int, hits: list[dict]) -> dict:
    """Retain papers from synthetic hit objects (no search needed).

    Returns the RetainPapersResult dict: {state_revision, paper_refs}.
    """

    scratch = workspace / "scratch" / run_id / "inputs"
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / "retain.json"
    path.write_text(json.dumps({"hits": hits}, ensure_ascii=False), encoding="utf-8")
    code, out, err = _run_harness(
        workspace,
        "retain-papers",
        "--run-id",
        run_id,
        "--expected-revision",
        str(rev),
        "--input",
        str(path),
    )
    assert code == 0, f"retain-papers failed: {err}\n{out}"
    return json.loads(out)["result"]


def _put_analysis(
    workspace: Path, run_id: str, rev: int, paper_ref: str, summary: str = "analysis"
) -> dict:
    scratch = workspace / "scratch" / run_id / "inputs"
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / f"analysis_{paper_ref}.json"
    path.write_text(
        json.dumps(
            {
                "paper_ref": paper_ref,
                "summary": summary,
                "relevance_to_run": "relevant",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    code, out, err = _run_harness(
        workspace,
        "put-paper-analysis",
        "--run-id",
        run_id,
        "--expected-revision",
        str(rev),
        "--input",
        str(path),
    )
    assert code == 0, f"put-paper-analysis failed: {err}\n{out}"
    return json.loads(out)["result"]


def _set_status(
    workspace: Path,
    run_id: str,
    rev: int,
    paper_ref: str,
    status: str,
    *,
    reason: str | None = None,
) -> tuple[int, dict]:
    """Set paper research status.

    Returns (exit_code, body) where body is the ``result`` dict on success
    (carrying ``state_revision``) or the ``error`` dict on failure (carrying
    ``type`` and ``message``).
    """

    args = [
        "set-paper-status",
        "--run-id",
        run_id,
        "--expected-revision",
        str(rev),
        "--paper-ref",
        paper_ref,
        "--status",
        status,
    ]
    if reason is not None:
        args += ["--reason", reason]
    code, out, err = _run_harness(workspace, *args)
    envelope = json.loads(out) if code == 0 else json.loads(err)
    body = envelope["result"] if code == 0 else envelope["error"]
    return code, body


def _request_completion(
    workspace: Path, run_id: str, rev: int, rationale: str = "ready"
) -> tuple[int, dict]:
    code, out, err = _run_harness(
        workspace,
        "request-completion",
        "--run-id",
        run_id,
        "--expected-revision",
        str(rev),
        "--rationale",
        rationale,
    )
    envelope = json.loads(out) if code == 0 else json.loads(err)
    body = envelope["result"] if code == 0 else envelope["error"]
    return code, body


def _completion_view(workspace: Path, run_id: str) -> dict:
    code, out, err = _run_harness(workspace, "completion-view", "--run-id", run_id)
    assert code == 0, f"completion-view failed: {err}\n{out}"
    return json.loads(out)["result"]


_DEFAULT_CONTRACT = {
    "mission": "Test mission",
    "requirements": ["Cover route A"],
    "scope": "Test scope",
    "deliverable_description": "Test deliverable",
    "required_artifacts": ["REPORT"],
}


def _hit(title: str) -> dict:
    """A minimal synthetic search hit for retain-papers."""

    return {"title": title}


# ===========================================================================
# 1. Retirement: RETIRED requires a reason; ACTIVE clears it
# ===========================================================================


def test_retire_without_reason_is_rejected(tmp_path):
    """RETIRED must carry a non-empty retirement_reason; the gate is at the command."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(workspace, run_id, rev, [_hit("P1")])
    paper_ref = retained["paper_refs"][0]
    rev = retained["state_revision"]

    code, payload = _set_status(
        workspace, run_id, rev, paper_ref, "RETIRED", reason=None
    )
    assert code == 2, payload
    assert payload["type"] == "CommandRejectedError"
    assert "retirement_reason" in payload["message"]


def test_retire_with_empty_reason_is_rejected(tmp_path):
    """A whitespace-only reason is not a defensible retirement."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(workspace, run_id, rev, [_hit("P1")])
    paper_ref = retained["paper_refs"][0]
    rev = retained["state_revision"]

    code, payload = _set_status(
        workspace, run_id, rev, paper_ref, "RETIRED", reason="   "
    )
    assert code == 2, payload
    assert payload["type"] == "CommandRejectedError"


def test_retire_with_reason_succeeds_and_persists(tmp_path):
    """A non-empty reason retires the paper; the reason is visible in state."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(workspace, run_id, rev, [_hit("P1")])
    paper_ref = retained["paper_refs"][0]
    rev = retained["state_revision"]

    code, payload = _set_status(
        workspace,
        run_id,
        rev,
        paper_ref,
        "RETIRED",
        reason="Superseded by a stronger representative for this route.",
    )
    assert code == 0, payload
    new_rev = payload["state_revision"]

    # The reason is persisted on the paper and visible through inspect.
    state = _view(workspace, run_id)
    assert state["state_revision"] == new_rev
    paper = _inspect_paper(workspace, run_id, new_rev, paper_ref)
    assert paper["research_status"] == "RETIRED"
    assert (
        paper["retirement_reason"]
        == "Superseded by a stronger representative for this route."
    )


def test_active_clears_retirement_reason(tmp_path):
    """Re-activating a retired paper clears its retirement_reason to None."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(workspace, run_id, rev, [_hit("P1")])
    paper_ref = retained["paper_refs"][0]
    rev = retained["state_revision"]

    code, payload = _set_status(
        workspace, run_id, rev, paper_ref, "RETIRED", reason="out of scope on re-read"
    )
    assert code == 0, payload
    rev = payload["state_revision"]

    code, payload = _set_status(workspace, run_id, rev, paper_ref, "ACTIVE")
    assert code == 0, payload
    rev = payload["state_revision"]
    paper = _inspect_paper(workspace, run_id, rev, paper_ref)
    assert paper["research_status"] == "ACTIVE"
    assert paper["retirement_reason"] is None


# ===========================================================================
# 2. Backward compatibility: old paper JSON without retirement_reason loads
# ===========================================================================


def test_old_paper_json_without_retirement_reason_loads():
    """An old persisted paper (no retirement_reason key) must still decode.

    validate_run fires on every load and must not reject historical runs.
    The codec reads retirement_reason as optional, so an old paper that never
    had the field loads with reason=None. This is exercised at the Paper codec
    level (the exact code path run_from_dict uses for each paper entry).
    """

    paper = Paper(source=PaperSource(title="Legacy Paper"))
    encoded = _paper_to_dict(paper)
    # Simulate an old run: the field was absent before this feature.
    del encoded["retirement_reason"]
    decoded = _paper_from_dict(encoded, "papers[legacy]")
    assert decoded.retirement_reason is None
    assert decoded.research_status is PaperResearchStatus.ACTIVE
    assert decoded.analysis is None


def test_old_retired_paper_without_reason_still_loads():
    """An old run that pre-dates retirement_reason cannot be retroactively
    rejected for a missing reason on a RETIRED paper — the weak invariant
    (reason is not None ⟹ RETIRED) is vacuously true when reason is None.
    Strong rules (RETIRED requires a reason) live at command time only.
    """

    paper = Paper(
        source=PaperSource(title="Legacy Retired"),
        research_status=PaperResearchStatus.RETIRED,
    )
    encoded = _paper_to_dict(paper)
    # Old shape: no retirement_reason key, paper was RETIRED.
    del encoded["retirement_reason"]
    decoded = _paper_from_dict(encoded, "papers[legacy_retired]")
    assert decoded.research_status is PaperResearchStatus.RETIRED
    assert decoded.retirement_reason is None
    # Round-trip: re-serialize and the field appears (None) without breaking.
    re_encoded = _paper_to_dict(decoded)
    assert re_encoded["retirement_reason"] is None


def test_bad_case_persisted_run_still_loads():
    """The real bad_case run (CLOSED, 59 papers, pre-feature) must still load
    after all Deep Reading Control Loop changes. This is the non-regression
    target: validate_run must not reject historical runs.
    """

    state_path = (
        REPO_ROOT
        / "workspace"
        / "runs"
        / "run_2ca834ac-48de-4ce8-a325-bc70a7aa760f"
        / "state.json"
    )
    if not state_path.exists():
        pytest.skip("bad_case persisted run not present in this checkout")
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    run = run_from_dict(payload)
    # The bad_case had 59 retained papers, all ACTIVE, 10 analyzed.
    assert len(run.papers) == 59
    analyzed = sum(1 for p in run.papers.values() if p.analysis is not None)
    assert analyzed == 10
    # All load as ACTIVE with retirement_reason None (old shape).
    assert all(
        p.research_status is PaperResearchStatus.ACTIVE for p in run.papers.values()
    )
    assert all(p.retirement_reason is None for p in run.papers.values())


# ===========================================================================
# 3. Completion hard gate: ACTIVE+unanalyzed blocks request-completion
# ===========================================================================


def test_completion_gate_blocks_unanalyzed_active(tmp_path):
    """request-completion is rejected while any ACTIVE paper lacks an analysis.

    This is the core invariant that would have caught the bad_case (49
    unanalyzed ACTIVE papers). The gate is deterministic state consistency,
    not a paper count.
    """

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(workspace, run_id, rev, [_hit("P1"), _hit("P2")])
    refs = retained["paper_refs"]
    rev = retained["state_revision"]

    # Analyze P1 but leave P2 as ACTIVE+analysis=None.
    out = _put_analysis(workspace, run_id, rev, refs[0])
    rev = out["state_revision"]

    code, payload = _request_completion(workspace, run_id, rev)
    assert code == 2, payload
    assert payload["type"] == "CommandRejectedError"
    msg = payload["message"]
    assert "ACTIVE" in msg and "PaperAnalysis" in msg
    # The unresolved candidate is named.
    assert refs[1] in msg


def test_completion_gate_passes_when_all_active_analyzed(tmp_path):
    """With every ACTIVE paper analyzed, request-completion proceeds."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(workspace, run_id, rev, [_hit("P1"), _hit("P2")])
    refs = retained["paper_refs"]
    rev = retained["state_revision"]

    out = _put_analysis(workspace, run_id, rev, refs[0])
    rev = out["state_revision"]
    out = _put_analysis(workspace, run_id, rev, refs[1])
    rev = out["state_revision"]

    code, payload = _request_completion(workspace, run_id, rev)
    assert code == 0, payload
    assert payload["completion_check_ref"]


def test_completion_gate_passes_with_retired_unanalyzed(tmp_path):
    """A RETIRED paper without an analysis is closed, not unresolved — the
    gate only blocks ACTIVE+unanalyzed. Retiring is a valid closure."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(workspace, run_id, rev, [_hit("P1"), _hit("P2")])
    refs = retained["paper_refs"]
    rev = retained["state_revision"]

    # Analyze P1; retire P2 with a reason (do NOT analyze it).
    out = _put_analysis(workspace, run_id, rev, refs[0])
    rev = out["state_revision"]
    code, payload = _set_status(
        workspace, run_id, rev, refs[1], "RETIRED", reason="duplicate of P1"
    )
    assert code == 0, payload
    rev = payload["state_revision"]

    code, payload = _request_completion(workspace, run_id, rev)
    assert code == 0, payload


# ===========================================================================
# 4. Landscape evidence eligibility: ACTIVE+analyzed required
# ===========================================================================


def _put_approach_family(
    workspace: Path, run_id: str, rev: int, refs: list[str], name: str = "A"
) -> tuple[int, dict]:
    scratch = workspace / "scratch" / run_id / "inputs"
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / f"approach_{name}.json"
    path.write_text(
        json.dumps(
            {
                "name": name,
                "core_idea": "idea",
                "representative_paper_refs": refs,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    code, out, err = _run_harness(
        workspace,
        "put-approach-family",
        "--run-id",
        run_id,
        "--expected-revision",
        str(rev),
        "--input",
        str(path),
    )
    envelope = json.loads(out) if code == 0 else json.loads(err)
    body = envelope["result"] if code == 0 else envelope["error"]
    return code, body


def test_unanalyzed_paper_cannot_be_representative(tmp_path):
    """put-approach-family rejects a representative that is ACTIVE but unanalyzed."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(workspace, run_id, rev, [_hit("P1")])
    ref = retained["paper_refs"][0]
    rev = retained["state_revision"]

    # P1 is ACTIVE+analysis=None — cannot be a representative.
    code, payload = _put_approach_family(workspace, run_id, rev, [ref])
    assert code == 2, payload
    assert payload["type"] == "CommandRejectedError"
    assert "representative" in payload["message"].lower()


def test_retired_paper_cannot_be_representative(tmp_path):
    """A RETIRED paper (even if once analyzed) cannot be a new representative."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(workspace, run_id, rev, [_hit("P1")])
    ref = retained["paper_refs"][0]
    rev = retained["state_revision"]

    out = _put_analysis(workspace, run_id, rev, ref)
    rev = out["state_revision"]
    code, payload = _set_status(
        workspace, run_id, rev, ref, "RETIRED", reason="superseded"
    )
    assert code == 0, payload
    rev = payload["state_revision"]

    code, payload = _put_approach_family(workspace, run_id, rev, [ref])
    assert code == 2, payload
    assert payload["type"] == "CommandRejectedError"


def test_analyzed_active_paper_can_be_representative(tmp_path):
    """The happy path: ACTIVE+analyzed is eligible to be a representative."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(workspace, run_id, rev, [_hit("P1")])
    ref = retained["paper_refs"][0]
    rev = retained["state_revision"]

    out = _put_analysis(workspace, run_id, rev, ref)
    rev = out["state_revision"]

    code, payload = _put_approach_family(workspace, run_id, rev, [ref])
    assert code == 0, payload


def _put_finding(
    workspace: Path, run_id: str, rev: int, sources: list[dict]
) -> tuple[int, dict]:
    scratch = workspace / "scratch" / run_id / "inputs"
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / "finding.json"
    path.write_text(
        json.dumps(
            {"statement": "a finding", "sources": sources},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    code, out, err = _run_harness(
        workspace,
        "put-finding",
        "--run-id",
        run_id,
        "--expected-revision",
        str(rev),
        "--input",
        str(path),
    )
    envelope = json.loads(out) if code == 0 else json.loads(err)
    body = envelope["result"] if code == 0 else envelope["error"]
    return code, body


def test_unanalyzed_paper_cannot_be_finding_source(tmp_path):
    """put-finding rejects a source pointing at an unanalyzed ACTIVE paper."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(workspace, run_id, rev, [_hit("P1")])
    ref = retained["paper_refs"][0]
    rev = retained["state_revision"]

    code, payload = _put_finding(
        workspace,
        run_id,
        rev,
        [{"paper_ref": ref, "relation": "SUPPORTS"}],
    )
    assert code == 2, payload
    assert payload["type"] == "CommandRejectedError"
    assert "source" in payload["message"].lower() or "ACTIVE" in payload["message"]


def test_analyzed_active_paper_can_be_finding_source(tmp_path):
    """The happy path: ACTIVE+analyzed is eligible as a finding source."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(workspace, run_id, rev, [_hit("P1")])
    ref = retained["paper_refs"][0]
    rev = retained["state_revision"]

    out = _put_analysis(workspace, run_id, rev, ref)
    rev = out["state_revision"]

    code, payload = _put_finding(
        workspace,
        run_id,
        rev,
        [{"paper_ref": ref, "relation": "SUPPORTS"}],
    )
    assert code == 0, payload


# ===========================================================================
# 5. Reference safety: a paper cited by the landscape cannot be retired
# ===========================================================================


def test_referenced_paper_cannot_be_retired(tmp_path):
    """A paper still cited as a representative cannot be retired until the
    referencing object is updated or retired first."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(workspace, run_id, rev, [_hit("P1")])
    ref = retained["paper_refs"][0]
    rev = retained["state_revision"]

    out = _put_analysis(workspace, run_id, rev, ref)
    rev = out["state_revision"]
    code, payload = _put_approach_family(workspace, run_id, rev, [ref])
    assert code == 0, payload
    rev = payload["state_revision"]

    # P1 is now a representative — retiring it must be rejected.
    code, payload = _set_status(
        workspace, run_id, rev, ref, "RETIRED", reason="try to retire"
    )
    assert code == 2, payload
    assert payload["type"] == "CommandRejectedError"
    assert "referenced" in payload["message"].lower()


def test_paper_can_be_retired_after_reference_removed(tmp_path):
    """After the referencing approach is updated to drop the paper, the paper
    can be retired (the semantic decision was made by the Researcher)."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(workspace, run_id, rev, [_hit("P1"), _hit("P2")])
    refs = retained["paper_refs"]
    rev = retained["state_revision"]

    # Analyze both, make P1 the representative.
    out = _put_analysis(workspace, run_id, rev, refs[0])
    rev = out["state_revision"]
    out = _put_analysis(workspace, run_id, rev, refs[1])
    rev = out["state_revision"]
    code, payload = _put_approach_family(workspace, run_id, rev, [refs[0]])
    assert code == 0, payload
    rev = payload["state_revision"]
    approach_ref = payload["entity_ref"]

    # Retiring P1 is blocked.
    code, payload = _set_status(
        workspace, run_id, rev, refs[0], "RETIRED", reason="try"
    )
    assert code == 2, payload

    # Rewrite the approach to use P2 instead (update by approach_ref).
    scratch = workspace / "scratch" / run_id / "inputs"
    path = scratch / "approach_update.json"
    path.write_text(
        json.dumps(
            {
                "name": "A",
                "core_idea": "idea",
                "representative_paper_refs": [refs[1]],
                "approach_ref": approach_ref,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    code, out, err = _run_harness(
        workspace,
        "put-approach-family",
        "--run-id",
        run_id,
        "--expected-revision",
        str(rev),
        "--input",
        str(path),
    )
    assert code == 0, f"approach update failed: {err}\n{out}"
    rev = json.loads(out)["result"]["state_revision"]

    # Now P1 can be retired.
    code, payload = _set_status(
        workspace, run_id, rev, refs[0], "RETIRED", reason="replaced by P2"
    )
    assert code == 0, payload


# ===========================================================================
# 6. CompletionView: every retained paper visible with closure summary
# ===========================================================================


def test_completion_view_exposes_all_retained_papers(tmp_path):
    """completion-view carries a papers tuple covering every retained paper —
    not only representative ones — with research_status, has_analysis, and
    retirement_reason. This is the visibility fix for the bad_case, where 49
    retained-but-unanalyzed candidates were invisible to the checker.
    """

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(
        workspace, run_id, rev, [_hit("P1"), _hit("P2"), _hit("P3")]
    )
    refs = retained["paper_refs"]
    rev = retained["state_revision"]

    # P1: analyzed + representative. P2: analyzed, not representative.
    # P3: retired with a reason (not analyzed).
    out = _put_analysis(workspace, run_id, rev, refs[0])
    rev = out["state_revision"]
    out = _put_analysis(workspace, run_id, rev, refs[1])
    rev = out["state_revision"]
    code, payload = _set_status(
        workspace, run_id, rev, refs[2], "RETIRED", reason="out of scope on re-read"
    )
    assert code == 0, payload
    rev = payload["state_revision"]
    code, payload = _put_approach_family(workspace, run_id, rev, [refs[0]])
    assert code == 0, payload
    rev = payload["state_revision"]

    code, payload = _request_completion(workspace, run_id, rev)
    assert code == 0, payload

    view = _completion_view(workspace, run_id)
    papers = {p["ref"]: p for p in view["papers"]}
    # All three retained papers are visible — not only the representative.
    assert set(papers) == set(refs)
    assert papers[refs[0]]["research_status"] == "ACTIVE"
    assert papers[refs[0]]["has_analysis"] is True
    assert papers[refs[0]]["retirement_reason"] is None
    assert papers[refs[1]]["research_status"] == "ACTIVE"
    assert papers[refs[1]]["has_analysis"] is True
    assert papers[refs[2]]["research_status"] == "RETIRED"
    assert papers[refs[2]]["has_analysis"] is False
    assert papers[refs[2]]["retirement_reason"] == "out of scope on re-read"
    # The representative set is still reported separately.
    assert refs[0] in view["representative_paper_refs"]


def test_completion_view_paper_carries_retirement_reason(tmp_path):
    """The retirement_reason field is present on every PaperIndexEntry in the
    completion view, None for ACTIVE and the string for RETIRED."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]
    retained = _retain(workspace, run_id, rev, [_hit("P1"), _hit("P2")])
    refs = retained["paper_refs"]
    rev = retained["state_revision"]

    out = _put_analysis(workspace, run_id, rev, refs[0])
    rev = out["state_revision"]
    code, payload = _set_status(
        workspace, run_id, rev, refs[1], "RETIRED", reason="superseded by P1"
    )
    assert code == 0, payload
    rev = payload["state_revision"]

    code, payload = _request_completion(workspace, run_id, rev)
    assert code == 0, payload

    view = _completion_view(workspace, run_id)
    papers = {p["ref"]: p for p in view["papers"]}
    assert papers[refs[0]]["retirement_reason"] is None
    assert papers[refs[1]]["retirement_reason"] == "superseded by P1"
