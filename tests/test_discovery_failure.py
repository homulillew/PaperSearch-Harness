"""External Discovery Failure Closure — DeepXiv reason propagation tests.

These tests pin the diagnosability of a failed DeepXiv paper-search attempt and the
authority boundary that produces the safe reason:

```text
DeepXiv adapter
→ converts a provider failure into a sanitized PaperSearchProviderError
   (fixed, schema-path message; raw response object/body is never interpolated)

PaperSearchService
→ propagates that already-sanitized reason into PaperSearchAttemptError / audit / CLI

CLI
→ additionally redacts DEEPXIV_TOKEN defensively
```

They drive the in-process runtime with a fake ``PaperSearchProvider`` (no subprocess,
no network) so every failure kind can be exercised deterministically. A separate
adapter-level test feeds a malformed response object straight into
``DeepXivPaperSearchProvider._map_response`` (no token, no SDK) to pin that the
adapter itself emits the fixed schema-path reason and never interpolates raw
response values.

Coverage (9 cases):

  1.  INVALID_RESPONSE reason survives into PaperSearchAttemptError.
  2.  failure audit event contains the sanitized reason.
  3.  AUTHENTICATION / RATE_LIMIT / UNAVAILABLE propagate their safe message.
  4.  the runtime does not introduce a raw provider payload into audit/error state
      (the reason is the adapter's already-sanitized message; no raw body is stored).
  5.  a failed search attempt still consumes one paper_search_attempt and
      advances the revision exactly as before.
  6.  a structurally invalid provider page surfaces INVALID_RESPONSE + reason.
  7.  an unrecognized (non-PaperSearchProviderError) failure surfaces OTHER
      with no raw exception text as the reason.
  8.  CLI error JSON exposes failure_kind + safe reason (in-process main()).
  9.  adapter: a malformed provider response yields a fixed schema-path safe reason
      and the raw response value is not interpolated into the message.

Run:

    python -m pytest tests/test_discovery_failure.py --basetemp=./.pytest_tmp
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SKILL_DIR = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "literature-research"
)
RUNTIME_SRC = SKILL_DIR / "runtime" / "src"
SCRIPTS_DIR = SKILL_DIR / "scripts"
HARNESS = SCRIPTS_DIR / "harness.py"

if str(RUNTIME_SRC) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SRC))

from my_search_harness.domain.model import ArtifactKind  # noqa: E402
from my_search_harness.runtime.audit import LocalAuditLog  # noqa: E402
from my_search_harness.runtime.commands import CreateRunRequest  # noqa: E402
from my_search_harness.runtime.deepxiv import DeepXivPaperSearchProvider  # noqa: E402
from my_search_harness.runtime.local_runtime import LocalV1Runtime  # noqa: E402
from my_search_harness.runtime.paper_search import (  # noqa: E402
    PaperSearchAttemptError,
    PaperSearchHit,
    PaperSearchPage,
    PaperSearchProviderError,
    PaperSearchProvider,
    ProviderFailureKind,
)


# --- fake providers -------------------------------------------------------


class _InvalidResponseProvider:
    """Provider that raises INVALID_RESPONSE with a field-specific reason."""

    def __init__(self, message: str = "top-level status must be success") -> None:
        self._message = message

    def search(self, query, *, limit, offset=0, date_from=None, date_to=None):
        raise PaperSearchProviderError(
            ProviderFailureKind.INVALID_RESPONSE,
            f"invalid paper search provider response: {self._message}",
        )


class _KindedProvider:
    """Provider that raises a chosen failure kind with a fixed safe message."""

    def __init__(self, kind: ProviderFailureKind, message: str) -> None:
        self._kind = kind
        self._message = message

    def search(self, query, *, limit, offset=0, date_from=None, date_to=None):
        raise PaperSearchProviderError(self._kind, self._message)


class _RawExceptionProvider:
    """Provider that raises an unrecognized exception (not a ProviderError)."""

    def search(self, query, *, limit, offset=0, date_from=None, date_to=None):
        raise RuntimeError("provider internal: connection reset by peer 10.0.0.1")


class _MalformedPageProvider:
    """Provider that returns a structurally invalid page object."""

    def search(self, query, *, limit, offset=0, date_from=None, date_to=None):
        # total_count is a string, not an int -> structurally invalid.
        return PaperSearchPage(total_count="not-an-int", hits=())  # type: ignore[arg-type]


# --- in-process runtime helpers -------------------------------------------


def _make_runtime(workspace: Path, provider: PaperSearchProvider) -> LocalV1Runtime:
    audit = LocalAuditLog(workspace / "runs")
    return LocalV1Runtime(
        workspace,
        paper_search_provider=provider,
        source_access_provider=None,
        audit_sink=audit,
    )


def _create_run(runtime: LocalV1Runtime) -> str:
    result = runtime.researcher.create_run(
        CreateRunRequest(
            mission="Test mission",
            requirements=("Cover route A",),
            scope="Test scope",
            deliverable_description="survey",
            required_artifacts=frozenset({ArtifactKind.REPORT}),
        )
    )
    return result.run_id


def _search(runtime: LocalV1Runtime, run_id: str, rev: int):
    return runtime.researcher.search_papers(
        run_id, rev, "kv cache", limit=10, offset=0
    )


def _audit_events(workspace: Path, run_id: str):
    return LocalAuditLog(workspace / "runs").read(run_id)


# --- tests ----------------------------------------------------------------


def test_invalid_response_reason_survives_into_attempt_error(tmp_path):
    """INVALID_RESPONSE reason propagates into PaperSearchAttemptError."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime = _make_runtime(workspace, _InvalidResponseProvider())
    run_id = _create_run(runtime)
    rev = runtime.researcher.view(run_id).state_revision

    with pytest.raises(PaperSearchAttemptError) as exc_info:
        _search(runtime, run_id, rev)

    err = exc_info.value
    assert err.failure_kind is ProviderFailureKind.INVALID_RESPONSE
    assert err.reason == (
        "invalid paper search provider response: top-level status must be success"
    )
    assert err.state_revision == rev + 1


def test_failure_audit_event_contains_sanitized_reason(tmp_path):
    """The paper_search_attempt FAILURE audit event carries the safe reason."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime = _make_runtime(workspace, _InvalidResponseProvider())
    run_id = _create_run(runtime)
    rev = runtime.researcher.view(run_id).state_revision

    with pytest.raises(PaperSearchAttemptError):
        _search(runtime, run_id, rev)

    events = _audit_events(workspace, run_id)
    attempt_events = [e for e in events if e.action == "paper_search_attempt"]
    assert len(attempt_events) == 1
    event = attempt_events[0]
    assert event.outcome == "FAILURE"
    assert event.provider_outcome == "INVALID_RESPONSE"
    assert event.reason == (
        "invalid paper search provider response: top-level status must be success"
    )
    # The query is recorded in details; the reason is a separate field.
    assert event.details["query"] == "kv cache"


@pytest.mark.parametrize(
    "kind, message",
    [
        (ProviderFailureKind.AUTHENTICATION, "paper search provider rejected authentication"),
        (ProviderFailureKind.RATE_LIMIT, "paper search provider rate limit was reached"),
        (ProviderFailureKind.UNAVAILABLE, "paper search provider is unavailable"),
    ],
)
def test_existing_failure_kinds_propagate_safe_message(tmp_path, kind, message):
    """AUTHENTICATION / RATE_LIMIT / UNAVAILABLE keep their existing safe message."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime = _make_runtime(workspace, _KindedProvider(kind, message))
    run_id = _create_run(runtime)
    rev = runtime.researcher.view(run_id).state_revision

    with pytest.raises(PaperSearchAttemptError) as exc_info:
        _search(runtime, run_id, rev)

    err = exc_info.value
    assert err.failure_kind is kind
    assert err.reason == message

    event = _audit_events(workspace, run_id)[-1]
    assert event.action == "paper_search_attempt"
    assert event.outcome == "FAILURE"
    assert event.provider_outcome == kind.value
    assert event.reason == message


def test_runtime_does_not_introduce_raw_payload_into_audit_or_error(tmp_path):
    """The runtime propagates the adapter's already-sanitized reason and does not
    introduce a raw provider payload into audit, error, or state.

    The authority boundary is: the DeepXiv adapter converts a provider failure into
    a sanitized ``PaperSearchProviderError`` (fixed, schema-path message), and
    ``PaperSearchService`` propagates that already-sanitized reason verbatim. This
    test pins that the service layer does not synthesize or store any payload beyond
    the adapter's safe reason — it does not (and cannot) prove that an arbitrary
    raw response would be sanitized, because the adapter never lets raw text reach
    this layer. Adapter-level raw-response safety is pinned by
    ``test_adapter_malformed_response_yields_safe_schema_path_reason`` below.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # A provider that raises the adapter's exact sanitized INVALID_RESPONSE reason.
    runtime = _make_runtime(workspace, _InvalidResponseProvider())
    run_id = _create_run(runtime)
    rev = runtime.researcher.view(run_id).state_revision

    with pytest.raises(PaperSearchAttemptError) as exc_info:
        _search(runtime, run_id, rev)

    err = exc_info.value
    # The reason is exactly the adapter's safe message — nothing more is synthesized.
    assert err.reason == (
        "invalid paper search provider response: top-level status must be success"
    )

    # The audit log carries only that safe reason; no extra payload field appears.
    events = _audit_events(workspace, run_id)
    for event in events:
        assert event.reason in (
            None,
            "invalid paper search provider response: top-level status must be success",
        )
        # Audit details carry only JSON scalars; no raw response object is embedded.
        for value in event.details.values():
            assert isinstance(value, (str, int, float, bool)), value

    # The authoritative state.json carries no attempt payload beyond the safe reason.
    state_path = workspace / "runs" / run_id / "state.json"
    state_text = state_path.read_text(encoding="utf-8")
    assert "top-level status must be success" not in state_text


def test_failed_attempt_consumes_one_attempt_and_advances_revision(tmp_path):
    """A failed search still consumes one paper_search_attempt and bumps revision."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime = _make_runtime(workspace, _InvalidResponseProvider())
    run_id = _create_run(runtime)
    before = runtime.researcher.view(run_id)
    rev = before.state_revision
    before_usage = dict(before.resources.usage).get("paper_search_attempts", 0)

    with pytest.raises(PaperSearchAttemptError):
        _search(runtime, run_id, rev)

    after = runtime.researcher.view(run_id)
    assert after.state_revision == rev + 1
    assert dict(after.resources.usage).get("paper_search_attempts", 0) == before_usage + 1


def test_malformed_provider_page_surfaces_invalid_response_with_reason(tmp_path):
    """A structurally invalid page surfaces INVALID_RESPONSE + a safe reason."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime = _make_runtime(workspace, _MalformedPageProvider())
    run_id = _create_run(runtime)
    rev = runtime.researcher.view(run_id).state_revision

    with pytest.raises(PaperSearchAttemptError) as exc_info:
        _search(runtime, run_id, rev)

    err = exc_info.value
    assert err.failure_kind is ProviderFailureKind.INVALID_RESPONSE
    assert err.reason is not None
    assert "structurally invalid" in err.reason

    event = _audit_events(workspace, run_id)[-1]
    assert event.provider_outcome == "INVALID_RESPONSE"
    assert event.reason is not None
    assert "structurally invalid" in event.reason


def test_unrecognized_failure_surfaces_other_without_raw_text(tmp_path):
    """A non-ProviderError failure surfaces OTHER with no raw exception text."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    provider = _RawExceptionProvider()
    runtime = _make_runtime(workspace, provider)
    run_id = _create_run(runtime)
    rev = runtime.researcher.view(run_id).state_revision

    with pytest.raises(PaperSearchAttemptError) as exc_info:
        _search(runtime, run_id, rev)

    err = exc_info.value
    assert err.failure_kind is ProviderFailureKind.OTHER
    # No raw exception text is carried as a sanitized reason for OTHER.
    assert err.reason is None
    # The raw internal text does not appear on the attempt error.
    assert "connection reset" not in str(err)
    assert "10.0.0.1" not in str(err)

    event = _audit_events(workspace, run_id)[-1]
    assert event.provider_outcome == "OTHER"
    assert event.reason is None


# --- adapter-level regression (no token, no SDK) --------------------------


def test_adapter_malformed_response_yields_safe_schema_path_reason():
    """The DeepXiv adapter converts a malformed provider response into a sanitized
    ``PaperSearchProviderError`` whose message is a fixed, schema-path description,
    and never interpolates raw response object/body values into the message.

    ``_map_response`` is a classmethod taking a plain ``response: object``, so it
    can be exercised directly with a hand-built mapping — no token, no SDK, no
    network. This pins the real raw-response safety boundary at the layer that
    actually owns it (the adapter), rather than asserting it at a layer that only
    ever sees the already-sanitized reason.
    """
    # A response whose result[1].date is not ISO 8601. The raw date value below is
    # deliberately a string that must NOT appear in the safe reason.
    raw_secret_date = "RAW_NOT_A_DATE_VALUE_99999"
    response = {
        "status": "success",
        "total_count": 2,
        "result": [
            {
                "arxiv_id": "2401.00001",
                "title": "Paper A",
                "authors": "Alice",
                "date": "2024-01-01",
            },
            {
                "arxiv_id": "2401.00002",
                "title": "Paper B",
                "authors": "Bob",
                "date": raw_secret_date,
            },
        ],
    }

    with pytest.raises(PaperSearchProviderError) as exc_info:
        DeepXivPaperSearchProvider._map_response(response)

    err = exc_info.value
    assert err.failure_kind is ProviderFailureKind.INVALID_RESPONSE
    # The reason is the fixed schema-path description (the adapter stores it as the
    # exception message; PaperSearchService later copies str(exc) into the
    # PaperSearchAttemptError.reason / audit reason).
    assert str(err) == (
        "invalid paper search provider response: result[1].date must be an ISO 8601 date"
    )
    # The raw response value is never interpolated into the message.
    assert raw_secret_date not in str(err)


# --- CLI error JSON contract (in-process) ---------------------------------


def _load_harness_module():
    """Import scripts/harness.py as an isolated module (no subprocess).

    The module re-execs into the Skill venv only at the bottom under
    ``if __name__ == "__main__"``, so importing it as a library is safe and
    lets us drive ``main()`` with a fake runtime factory — exercising the real
    CLI error-shaping code path (``_safe_message`` + the failure_kind/reason
    payload) without any network or DeepXiv token.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "literature_research_harness", str(HARNESS)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_error_json_exposes_failure_kind_and_reason(tmp_path):
    """The CLI error JSON for a failed search exposes failure_kind + reason.

    Drives ``harness.main()`` in-process with a fake runtime whose
    ``search_papers`` raises a real ``PaperSearchAttemptError``. This exercises
    the actual CLI error-shaping path (``_safe_message`` + the
    failure_kind/reason/state_revision payload) end-to-end, without a
    subprocess, network, or DeepXiv token.
    """
    harness = _load_harness_module()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    # Create a real run in-process so the CLI has a valid run_id/revision to
    # target, then swap in a fake runtime whose researcher raises on search.
    real_runtime = _make_runtime(workspace, _InvalidResponseProvider())
    run_id = _create_run(real_runtime)
    rev = real_runtime.researcher.view(run_id).state_revision

    class _FailingResearcher:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def search_papers(self, run_id, expected_revision, query, **kwargs):
            raise PaperSearchAttemptError(
                state_revision=expected_revision + 1,
                failure_kind=ProviderFailureKind.INVALID_RESPONSE,
                reason=(
                    "invalid paper search provider response: "
                    "top-level status must be success"
                ),
            )

    class _FakeRuntime:
        def __init__(self, inner):
            self._inner = inner

        @property
        def researcher(self):
            return _FailingResearcher(self._inner.researcher)

    def factory(ws, external):
        return _FakeRuntime(real_runtime)

    stdout = []
    stderr = []

    class _Stream:
        def __init__(self, sink):
            self._sink = sink

        def write(self, text):
            self._sink.append(text)

        def flush(self):
            pass

    code = harness.main(
        [
            "--workspace",
            str(workspace),
            "search-papers",
            "--run-id",
            run_id,
            "--expected-revision",
            str(rev),
            "--query",
            "kv cache",
            "--limit",
            "5",
        ],
        stdout=_Stream(stdout),
        stderr=_Stream(stderr),
        runtime_factory=factory,
    )

    assert code == 2
    payload = json.loads("".join(stderr))
    assert payload["ok"] is False
    assert payload["command"] == "search-papers"
    error = payload["error"]
    assert error["type"] == "PaperSearchAttemptError"
    assert error["failure_kind"] == "INVALID_RESPONSE"
    assert error["reason"] == (
        "invalid paper search provider response: top-level status must be success"
    )
    assert error["state_revision"] == rev + 1


def test_cli_error_json_redacts_token_in_reason(tmp_path):
    """The CLI redacts DEEPXIV_TOKEN if it somehow appears in the reason."""
    harness = _load_harness_module()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    real_runtime = _make_runtime(workspace, _InvalidResponseProvider())
    run_id = _create_run(real_runtime)
    rev = real_runtime.researcher.view(run_id).state_revision

    token = "secret-token-XYZ"
    os.environ["DEEPXIV_TOKEN"] = token
    try:

        class _FailingResearcher:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def search_papers(self, run_id, expected_revision, query, **kwargs):
                # A reason that (incorrectly) contains the token; _safe_message
                # must redact it before it reaches the JSON.
                raise PaperSearchAttemptError(
                    state_revision=expected_revision + 1,
                    failure_kind=ProviderFailureKind.OTHER,
                    reason=f"provider echoed token: {token}",
                )

        class _FakeRuntime:
            def __init__(self, inner):
                self._inner = inner

            @property
            def researcher(self):
                return _FailingResearcher(self._inner.researcher)

        def factory(ws, external):
            return _FakeRuntime(real_runtime)

        stderr = []

        class _Stream:
            def __init__(self, sink):
                self._sink = sink

            def write(self, text):
                self._sink.append(text)

            def flush(self):
                pass

        code = harness.main(
            [
                "--workspace", str(workspace),
                "search-papers", "--run-id", run_id,
                "--expected-revision", str(rev),
                "--query", "kv cache", "--limit", "5",
            ],
            stdout=_Stream([]),
            stderr=_Stream(stderr),
            runtime_factory=factory,
        )
    finally:
        del os.environ["DEEPXIV_TOKEN"]

    assert code == 2
    payload = json.loads("".join(stderr))
    error = payload["error"]
    assert error["failure_kind"] == "OTHER"
    assert token not in error["reason"]
    assert "[REDACTED]" in error["reason"]
