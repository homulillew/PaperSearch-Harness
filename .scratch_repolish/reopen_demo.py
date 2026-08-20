"""Manual demonstration of the reopen-delivery basis/artifact distinction.

CASE 1: CLOSED COMPLETE, valid basis, old report artifact removed
        -> reopen-delivery SUCCEEDS (basis validated, not the old artifact).
CASE 2: same reopened run, no new report published yet
        -> close-run FAILS (valid current REPORT artifact still required).
CASE 3: new certified report authored, certified, and published
        -> close-run SUCCEEDS (artifact requirement satisfied at closure).

Uses a fresh temp workspace so the demonstration is independent of the
committed workspace run's Windows CRLF byte mismatch.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import importlib.util  # noqa: E402

HARNESS_PATH = (
    REPO_ROOT / ".claude" / "skills" / "literature-research" / "scripts" / "harness.py"
)
RUNTIME_SRC = (
    REPO_ROOT / ".claude" / "skills" / "literature-research" / "runtime" / "src"
)
sys.path.insert(0, str(RUNTIME_SRC))

from my_search_harness.domain.model import ArtifactKind, CompletionVerdict  # noqa: E402
from my_search_harness.runtime.commands import CreateRunRequest  # noqa: E402
from my_search_harness.runtime.local_runtime import LocalV1Runtime  # noqa: E402


def _load_harness():
    spec = importlib.util.spec_from_file_location("demo_harness", HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _invoke(harness, workspace, *args):
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = harness.main(
        ["--workspace", str(workspace), *args], stdout=stdout, stderr=stderr
    )
    raw = stdout.getvalue() if code == 0 else stderr.getvalue()
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        envelope = {"raw": raw}
    return code, envelope


def _input(tmp, name, payload):
    path = tmp / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _brief(promise="explain the result"):
    return {
        "audience": "technical reader",
        "promise": promise,
        "frame": "premise and consequence form one causal model",
        "arc": "establish the premise, then derive the consequence",
        "focus": "the consequence follows from the premise",
    }


def _manuscript(markdown="# Report\n\n## Result\n\nCertified content."):
    return {"markdown": markdown, "citations": []}


def _put_brief_and_manuscript(harness, workspace, tmp, run_id, *, promise, markdown):
    brief_path = _input(tmp, f"brief-{promise}.json", _brief(promise=promise))
    code, env = _invoke(harness, workspace, "put-report-brief", "--run-id", run_id, "--input", str(brief_path))
    assert code == 0, env
    b_digest = env["result"]["brief_digest"]
    m_path = _input(tmp, f"ms-{promise}.json", _manuscript(markdown))
    code, env = _invoke(harness, workspace, "put-report-manuscript", "--run-id", run_id, "--input", str(m_path))
    assert code == 0, env
    return b_digest, env["result"]["manuscript_digest"]


def _reader_pass(harness, workspace, tmp, run_id, b_digest, m_digest):
    blind = _input(tmp, "blind.json", {
        "received_understanding": "understood",
        "manuscript_digest": m_digest,
        "blocking_issues": [],
    })
    code, env = _invoke(harness, workspace, "submit-blind-review", "--run-id", run_id, "--input", str(blind))
    assert code == 0, env
    frozen = env["result"]["blind_read_digest"]
    review = _input(tmp, "reader.json", {
        "blind_read_digest": frozen,
        "brief_digest": b_digest,
        "manuscript_digest": m_digest,
        "repair_target": None,
        "rationale": "the report delivers the promised understanding",
    })
    code, env = _invoke(harness, workspace, "submit-reader-review", "--run-id", run_id, "--input", str(review))
    assert code == 0, env


def _integrity_pass(harness, workspace, tmp, run_id):
    path = _input(tmp, "integrity.json", {"disposition": "PASS", "issues": []})
    code, env = _invoke(harness, workspace, "submit-integrity-review", "--run-id", run_id, "--input", str(path))
    assert code == 0, env


def _publish(harness, workspace, tmp, run_id, delivery_revision):
    b_digest, m_digest = _put_brief_and_manuscript(
        harness, workspace, tmp, run_id, promise="explain the result", markdown="# Report\n\n## Result\n\nCertified content."
    )
    _reader_pass(harness, workspace, tmp, run_id, b_digest, m_digest)
    _integrity_pass(harness, workspace, tmp, run_id)
    code, _ = _invoke(harness, workspace, "render-certified-report", "--run-id", run_id)
    assert code == 0
    code, env = _invoke(
        harness, workspace, "publish-certified-report", "--run-id", run_id, "--expected-revision", str(delivery_revision)
    )
    assert code == 0, env


def main():
    harness = _load_harness()
    tmp = Path(tempfile.mkdtemp(prefix="reopen_demo_"))
    workspace = tmp / "workspace"

    # Build a CLOSED COMPLETE run through the normal certified path.
    runtime = LocalV1Runtime(workspace, paper_search_provider=None, source_access_provider=None)
    created = runtime.researcher.create_run(
        CreateRunRequest(
            mission="reopen delivery demo",
            requirements=("explain the result",),
            scope="test scope",
            deliverable_description="certified report",
            required_artifacts=frozenset({ArtifactKind.REPORT}),
        )
    )
    requested = runtime.researcher.request_completion_check(created.run_id, created.state_revision, "ready")
    passed = runtime.completion_checker.submit_completion_check(
        created.run_id, requested.state_revision, requested.completion_check_ref, CompletionVerdict.PASS, ("sufficient",)
    )
    delivery_revision = passed.state_revision
    _publish(harness, workspace, tmp, created.run_id, delivery_revision)
    code, closed = _invoke(harness, workspace, "close-run", "--run-id", created.run_id, "--expected-revision", str(delivery_revision))
    assert code == 0, closed
    closed_revision = closed["result"]["state_revision"]
    print(f"[setup] Closed COMPLETE run at revision {closed_revision}")

    # CASE 1: remove the old report artifact, then reopen-delivery.
    artifacts_dir = workspace / "runs" / created.run_id / "artifacts"
    (artifacts_dir / "report.md").unlink()
    (artifacts_dir / "report.meta.json").unlink()
    print("[case1] Removed old report.md + report.meta.json")

    code, reopened = _invoke(harness, workspace, "reopen-delivery", "--run-id", created.run_id, "--expected-revision", str(closed_revision))
    assert code == 0, reopened
    reopened_revision = reopened["result"]["state_revision"]
    print(f"[case1] reopen-delivery SUCCEEDED -> revision {reopened_revision} (DELIVERY, outcome=None)")

    # CASE 2: no new report published yet -> close-run must fail.
    code, env = _invoke(harness, workspace, "close-run", "--run-id", created.run_id, "--expected-revision", str(reopened_revision))
    assert code == 2, f"[case2] close-run unexpectedly succeeded: {env}"
    print(f"[case2] close-run FAILED as expected: {env['error']['type']} - {env['error']['message']}")

    # CASE 3: author + certify + publish a new report, then close-run succeeds.
    _publish(harness, workspace, tmp, created.run_id, reopened_revision)
    code, closed2 = _invoke(harness, workspace, "close-run", "--run-id", created.run_id, "--expected-revision", str(reopened_revision))
    assert code == 0, closed2
    print(f"[case3] close-run SUCCEEDED after new report published -> outcome={closed2['result']['outcome']}")

    print("\nALL THREE CASES PASSED.")


if __name__ == "__main__":
    main()
