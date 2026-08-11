"""Deep Reading Control Loop — candidate-closure regression E2E.

This is the end-to-end acceptance test for the Deep Reading Control Loop. It
reconstructs the bad_case shape (many retained candidates, only a few
analyzed) and asserts the loop now forces every material candidate to close:

  - A retained paper cannot silently sit as ACTIVE+analysis=None at Completion.
  - The completion hard gate blocks request-completion while the Reading
    Frontier is non-empty, naming the unresolved candidates.
  - Closing each candidate (analyze OR retire-with-reason) is the only way
    through the gate.
  - A reassessed landscape changes the next action: after integrating a paper,
    the next uncertainty (and next read/retire choice) is chosen from the
    updated State, not from a fixed plan.

The scenario uses six retained papers (A-F) standing in for a real corpus:

  A, B  -> analyzed early; A becomes the route-A representative.
  C     -> the "disappearing" candidate from the bad_case: retained, never
           analyzed, never retired. The gate must catch C.
  D     -> analyzed after a landscape update (the reassessment changes the
           next action).
  E     -> retired with a defensible reason (superseded by A).
  F     -> retired with a defensible reason (out of scope on re-read).

No network, no DeepXiv, no source reads. Drives scripts/harness.py as a
subprocess (style of test_filesystem_boundary.py).

Run:

    python -m pytest tests/test_deep_reading_regression.py --basetemp=./.pytest_tmp
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
SCRIPTS_DIR = SKILL_DIR / "scripts"
HARNESS = SCRIPTS_DIR / "harness.py"

_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def _run_harness(workspace: Path, *args: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(HARNESS), "--workspace", str(workspace), *args],
        capture_output=True,
        text=True,
        env=_ENV,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _create_run(workspace: Path) -> str:
    scratch = workspace / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    contract_path = scratch / "research-contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "mission": "Map route A and its frontier",
                "requirements": ["Cover route A", "Cover recent frontier"],
                "scope": "primary literature",
                "deliverable_description": "survey",
                "required_artifacts": ["REPORT"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    code, out, err = _run_harness(workspace, "create-run", "--input", str(contract_path))
    assert code == 0, f"create-run failed: {err}\n{out}"
    return json.loads(out)["result"]["run_id"]


def _view(workspace: Path, run_id: str) -> dict:
    code, out, err = _run_harness(workspace, "view", "--run-id", run_id)
    assert code == 0, f"view failed: {err}\n{out}"
    return json.loads(out)["result"]


def _inspect_paper(workspace: Path, run_id: str, rev: int, paper_ref: str) -> dict:
    """Inspect a single paper ref; return its ``value`` dict."""

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


def _retain(workspace: Path, run_id: str, rev: int, titles: list[str]) -> tuple[dict, list[str]]:
    """Retain synthetic hits; return (result_dict, paper_refs)."""

    scratch = workspace / "scratch" / run_id / "inputs"
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / "retain.json"
    path.write_text(
        json.dumps({"hits": [{"title": t} for t in titles]}, ensure_ascii=False),
        encoding="utf-8",
    )
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
    result = json.loads(out)["result"]
    return result, list(result["paper_refs"])


def _put_analysis(workspace: Path, run_id: str, rev: int, paper_ref: str) -> int:
    scratch = workspace / "scratch" / run_id / "inputs"
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / f"analysis_{paper_ref}.json"
    path.write_text(
        json.dumps(
            {
                "paper_ref": paper_ref,
                "summary": f"analysis of {paper_ref}",
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
    return json.loads(out)["result"]["state_revision"]


def _set_status(
    workspace: Path, run_id: str, rev: int, paper_ref: str, status: str, reason: str
) -> tuple[int, dict]:
    code, out, err = _run_harness(
        workspace,
        "set-paper-status",
        "--run-id",
        run_id,
        "--expected-revision",
        str(rev),
        "--paper-ref",
        paper_ref,
        "--status",
        status,
        "--reason",
        reason,
    )
    envelope = json.loads(out) if code == 0 else json.loads(err)
    body = envelope["result"] if code == 0 else envelope["error"]
    return code, body


def _put_approach_family(
    workspace: Path, run_id: str, rev: int, refs: list[str], name: str = "routeA"
) -> tuple[int, dict]:
    scratch = workspace / "scratch" / run_id / "inputs"
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / f"approach_{name}.json"
    path.write_text(
        json.dumps(
            {
                "name": name,
                "core_idea": "route A mechanism",
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


def _request_completion(
    workspace: Path, run_id: str, rev: int
) -> tuple[int, dict]:
    code, out, err = _run_harness(
        workspace,
        "request-completion",
        "--run-id",
        run_id,
        "--expected-revision",
        str(rev),
        "--rationale",
        "contract satisfied",
    )
    envelope = json.loads(out) if code == 0 else json.loads(err)
    body = envelope["result"] if code == 0 else envelope["error"]
    return code, body


def _completion_view(workspace: Path, run_id: str) -> dict:
    code, out, err = _run_harness(workspace, "completion-view", "--run-id", run_id)
    assert code == 0, f"completion-view failed: {err}\n{out}"
    return json.loads(out)["result"]


# ===========================================================================
# The candidate-closure E2E
# ===========================================================================


def test_candidate_cannot_silently_disappear(tmp_path):
    """The bad_case regression: a retained-but-unanalyzed candidate (C) cannot
    silently disappear. The completion hard gate catches it, and the only way
    through is to close C (analyze or retire-with-reason).
    """

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]

    # Retain six candidates (A-F).
    result, refs = _retain(workspace, run_id, rev, ["A", "B", "C", "D", "E", "F"])
    rev = result["state_revision"]
    a, b, c, d, e, f = refs

    # Analyze A and B early; A becomes the route-A representative.
    rev = _put_analysis(workspace, run_id, rev, a)
    rev = _put_analysis(workspace, run_id, rev, b)
    code, body = _put_approach_family(workspace, run_id, rev, [a])
    assert code == 0, body
    rev = body["state_revision"]

    # C, D, E, F are still ACTIVE+analysis=None — the Reading Frontier.
    # E and F can be retired (they are not yet referenced by the landscape).
    code, body = _set_status(
        workspace, run_id, rev, e, "RETIRED", reason="superseded by A for route A"
    )
    assert code == 0, body
    rev = body["state_revision"]
    code, body = _set_status(
        workspace, run_id, rev, f, "RETIRED", reason="out of scope on re-read: not route A"
    )
    assert code == 0, body
    rev = body["state_revision"]

    # C and D remain unresolved. request-completion MUST be rejected, and the
    # rejection MUST name C and D — they have not silently disappeared.
    code, body = _request_completion(workspace, run_id, rev)
    assert code == 2, body
    assert body["type"] == "CommandRejectedError"
    msg = body["message"]
    assert c in msg and d in msg, (
        f"unresolved candidates must be named in the gate rejection; got: {msg}"
    )

    # Close D by analysis (the reassessment chose to read D next).
    rev = _put_analysis(workspace, run_id, rev, d)
    # C is still unresolved — the gate must still reject, naming C alone now.
    code, body = _request_completion(workspace, run_id, rev)
    assert code == 2, body
    assert c in body["message"]
    assert d not in body["message"], "D was analyzed; it must no longer block"

    # Close C by retirement with a defensible reason (the reassessment chose
    # not to read C — it duplicates A's coverage of route A).
    code, body = _set_status(
        workspace, run_id, rev, c, "RETIRED", reason="duplicates A's coverage of route A"
    )
    assert code == 0, body
    rev = body["state_revision"]

    # Now the Frontier is empty: every retained paper is ACTIVE+analyzed or
    # RETIRED+reason. The gate lets request-completion through.
    code, body = _request_completion(workspace, run_id, rev)
    assert code == 0, body
    assert body["completion_check_ref"]


def test_completion_view_names_every_retained_paper(tmp_path):
    """After closure, completion-view shows every retained paper's disposition.
    The fresh checker sees the whole corpus — not only A the representative —
    so C, E, and F's retirements are visible for judgment.
    """

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]

    result, refs = _retain(workspace, run_id, rev, ["A", "B", "C", "D", "E", "F"])
    rev = result["state_revision"]
    a, b, c, d, e, f = refs

    rev = _put_analysis(workspace, run_id, rev, a)
    rev = _put_analysis(workspace, run_id, rev, b)
    rev = _put_analysis(workspace, run_id, rev, d)
    code, body = _put_approach_family(workspace, run_id, rev, [a])
    assert code == 0, body
    rev = body["state_revision"]
    code, body = _set_status(
        workspace, run_id, rev, e, "RETIRED", reason="superseded by A"
    )
    assert code == 0, body
    rev = body["state_revision"]
    code, body = _set_status(
        workspace, run_id, rev, f, "RETIRED", reason="out of scope"
    )
    assert code == 0, body
    rev = body["state_revision"]
    code, body = _set_status(
        workspace, run_id, rev, c, "RETIRED", reason="duplicates A"
    )
    assert code == 0, body
    rev = body["state_revision"]

    code, body = _request_completion(workspace, run_id, rev)
    assert code == 0, body

    view = _completion_view(workspace, run_id)
    papers = {p["ref"]: p for p in view["papers"]}
    # All six retained papers are visible to the checker.
    assert set(papers) == set(refs), (
        f"completion-view must expose every retained paper; missing: "
        f"{set(refs) - set(papers)}"
    )
    # The three analyzed ACTIVE papers.
    for ref in (a, b, d):
        assert papers[ref]["research_status"] == "ACTIVE"
        assert papers[ref]["has_analysis"] is True
        assert papers[ref]["retirement_reason"] is None
    # The three retired papers carry defensible reasons.
    for ref in (c, e, f):
        assert papers[ref]["research_status"] == "RETIRED"
        assert papers[ref]["has_analysis"] is False
        assert papers[ref]["retirement_reason"] is not None
    # Only A is the representative; the others are still visible as candidates.
    assert view["representative_paper_refs"] == [a]


def test_state_update_changes_next_action(tmp_path):
    """Loop-behavior acceptance: after integrating a paper, the reassessed
    landscape changes what the next action must be. Concretely, after A
    becomes the route-A representative, retiring E (which A supersedes) becomes
    a defensible closure that was NOT defensible before A was integrated.

    This is the structural signature of the Deep Reading Control Loop: the
    next action is chosen from the reassessed State, not from a fixed plan.
    """

    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_id = _create_run(workspace)
    rev = _view(workspace, run_id)["state_revision"]

    result, refs = _retain(workspace, run_id, rev, ["A", "E"])
    rev = result["state_revision"]
    a, e = refs

    # Before A is integrated, E is just another unresolved candidate. Retiring
    # E with "superseded by A" is not yet a defensible closure: A has no
    # PaperAnalysis and is not a representative, so nothing supersedes E.
    # (The Harness does not judge defensible-ness — that is the checker's job —
    # but it DOES require a non-empty reason, which we provide. The point is
    # semantic: the Researcher would not write this reason yet.)
    code, body = _set_status(
        workspace, run_id, rev, e, "RETIRED", reason="superseded by A"
    )
    assert code == 0, body  # structurally allowed; semantically premature.
    rev = body["state_revision"]

    # Re-activate E (the Researcher reconsidered — the retirement was premature).
    code, body = _set_status(workspace, run_id, rev, e, "ACTIVE", reason="revisit")
    # ACTIVE clears the reason regardless of the --reason value.
    assert code == 0, body
    rev = body["state_revision"]
    e_paper = _inspect_paper(workspace, run_id, rev, e)
    assert e_paper["research_status"] == "ACTIVE"
    assert e_paper["retirement_reason"] is None

    # Now integrate A: analyze it and make it the route-A representative.
    # This is the State update that changes the next action.
    rev = _put_analysis(workspace, run_id, rev, a)
    code, body = _put_approach_family(workspace, run_id, rev, [a])
    assert code == 0, body
    rev = body["state_revision"]

    # After the landscape update, retiring E with "superseded by A" is now a
    # defensible closure: A is an analyzed representative of route A, so E
    # (a duplicate of that route) can honestly be retired. The reassessed
    # landscape made this the right next action.
    code, body = _set_status(
        workspace, run_id, rev, e, "RETIRED", reason="superseded by A for route A"
    )
    assert code == 0, body
    rev = body["state_revision"]

    # And now the corpus is closed: A analyzed + representative, E retired.
    code, body = _request_completion(workspace, run_id, rev)
    assert code == 0, body
