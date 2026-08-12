"""Deep Reading Control Loop — invariant bypass / escape-path tests.

These tests pin the five bypass paths closed by the correctness-polish pass.
They drive the in-process runtime (no subprocess, no network) so they can
exercise the capabilities API directly — ``reconcile_paper_identity`` and
``apply_research_mutation`` have no CLI subcommand, so the subprocess harness
cannot reach them.

Coverage (11 cases):

  1.  reconcile_paper_identity cannot modify research_status (param removed).
  2.  identity merge cannot rewrite Landscape refs onto a RETIRED / unanalyzed
      primary (merge-to-ineligible rejected).
  3.  apply_research_mutation(PutLandscapeFinding) rejects a RETIRED source.
  4.  the same path rejects an ACTIVE + unanalyzed source.
  5.  legacy RETIRED + reason=None state still loads (validate_run weak).
  6.  that legacy state is rejected at request-completion (new transition rule).
  7.  report citation → RETIRED paper is rejected.
  8.  report citation → ACTIVE + unanalyzed paper is rejected.
  9.  report citation → ACTIVE + analyzed paper is allowed.
  10. validate_transition blocks ACTIVE→RETIRED without reason, and changed
      Landscape → ineligible paper ref.
  11. history / bad_case run still loads / inspects / audits (no retroactive
      rejection from the new transition invariants).

Run:

    python -m pytest tests/test_deep_reading_bypass.py --basetemp=./.pytest_tmp
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SKILL_DIR = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "literature-research"
)
RUNTIME_SRC = SKILL_DIR / "runtime" / "src"
REPO_ROOT = Path(__file__).resolve().parents[1]

if str(RUNTIME_SRC) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SRC))

from my_search_harness.domain.model import (  # noqa: E402
    ArtifactKind,
    PaperAnalysis,
    PaperResearchStatus,
    PaperSource,
    Paper,
    LiteratureSource,
    SourceRelation,
)
from my_search_harness.domain.validation import (  # noqa: E402
    DomainValidationError,
    validate_run,
    validate_transition,
)
from my_search_harness.runtime.codec import run_from_dict, run_to_dict  # noqa: E402
from my_search_harness.runtime.commands import (  # noqa: E402
    CommandRejectedError,
    CreateRunRequest,
    PutLandscapeFinding,
    PutPaperAnalysis,
    ResearchMutationBatch,
)
from my_search_harness.runtime.local_runtime import LocalV1Runtime  # noqa: E402
from my_search_harness.runtime.paper_search import PaperSearchHit  # noqa: E402


# --- in-process runtime helpers ------------------------------------------


def _make_runtime(workspace: Path) -> LocalV1Runtime:
    return LocalV1Runtime(
        workspace,
        paper_search_provider=None,
        source_access_provider=None,
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


def _view(runtime: LocalV1Runtime, run_id: str):
    return runtime.researcher.view(run_id)


def _retain(runtime: LocalV1Runtime, run_id: str, rev: int, titles: list[str]):
    result = runtime.researcher.retain_papers(
        run_id,
        rev,
        tuple(PaperSearchHit(title=t) for t in titles),
    )
    return result


def _put_analysis(runtime: LocalV1Runtime, run_id: str, rev: int, paper_ref: str):
    return runtime.researcher.apply_research_mutation(
        run_id,
        rev,
        ResearchMutationBatch(
            puts=(
                PutPaperAnalysis(
                    paper_ref=paper_ref,
                    analysis=PaperAnalysis(
                        summary="analysis", relevance_to_run="relevant"
                    ),
                ),
            )
        ),
    )


def _set_status(
    runtime: LocalV1Runtime,
    run_id: str,
    rev: int,
    paper_ref: str,
    status: PaperResearchStatus,
    *,
    reason: str | None = None,
):
    return runtime.researcher.set_paper_research_status(
        run_id, rev, paper_ref, status, retirement_reason=reason
    )


def _put_approach_family(
    runtime: LocalV1Runtime, run_id: str, rev: int, refs: list[str], name: str = "A"
):
    return runtime.researcher.put_approach_family(
        run_id,
        rev,
        name=name,
        core_idea="idea",
        representative_paper_refs=frozenset(refs),
    )


def _put_finding_mutation(
    runtime: LocalV1Runtime, run_id: str, rev: int, sources: list[dict]
):
    """apply_research_mutation(PutLandscapeFinding) — the bypass path."""

    batch = ResearchMutationBatch(
        puts=(
            PutLandscapeFinding(
                statement="a finding",
                approach_refs=frozenset(),
                sources=frozenset(
                    LiteratureSource(
                        paper_ref=s["paper_ref"],
                        relation=SourceRelation.SUPPORTS,
                    )
                    for s in sources
                ),
            ),
        )
    )
    return runtime.researcher.apply_research_mutation(run_id, rev, batch)


def _request_completion(runtime: LocalV1Runtime, run_id: str, rev: int):
    return runtime.researcher.request_completion_check(run_id, rev, "ready")


def _inspect_paper(runtime: LocalV1Runtime, run_id: str, rev: int, paper_ref: str):
    result = runtime.researcher.inspect(run_id, rev, (paper_ref,))
    objs = result.objects
    assert objs and objs[0].kind == "paper", objs
    return objs[0].value


# ===========================================================================
# 1. reconcile_paper_identity cannot modify research_status
# ===========================================================================


def test_reconcile_paper_identity_has_no_research_status_param(tmp_path):
    """The research_status parameter was removed from reconcile_paper_identity
    so identity reconciliation cannot bypass candidate disposition rules."""

    import inspect

    from my_search_harness.runtime.capabilities import ResearcherCapabilities
    from my_search_harness.runtime.commands import ResearchCommands

    # The capabilities layer (what callers actually invoke).
    cap_sig = inspect.signature(ResearcherCapabilities.reconcile_paper_identity)
    assert "research_status" not in cap_sig.parameters, (
        "reconcile_paper_identity must not accept research_status — identity "
        "reconciliation must not mutate candidate disposition"
    )
    # The command layer (the implementation).
    cmd_sig = inspect.signature(ResearchCommands.reconcile_paper_identity)
    assert "research_status" not in cmd_sig.parameters


def test_reconcile_paper_identity_does_not_change_status(tmp_path):
    """Even if a caller tries to pass research_status positionally or by
    building a Paper with a different status, reconcile only reconciles
    identity — it leaves research_status and retirement_reason untouched."""

    runtime = _make_runtime(tmp_path / "ws")
    run_id = _create_run(runtime)
    rev = _view(runtime, run_id).state_revision
    retained = _retain(runtime, run_id, rev, ["P1"])
    ref = retained.paper_refs[0]
    rev = retained.state_revision

    # Retire P1 first (so we can prove reconcile does not reactivate it).
    result = _set_status(
        runtime, run_id, rev, ref, PaperResearchStatus.RETIRED, reason="out of scope"
    )
    rev = result.state_revision

    # Reconcile identity (enrich source). research_status is not an argument.
    reconciled = runtime.researcher.reconcile_paper_identity(
        run_id,
        rev,
        ref,
        PaperSource(title="P1 enriched", doi="10.0/enriched"),
    )
    rev = reconciled.state_revision

    paper = _inspect_paper(runtime, run_id, rev, ref)
    assert paper.research_status == "RETIRED"
    assert paper.retirement_reason == "out of scope"


# ===========================================================================
# 2. identity merge cannot rewrite Landscape refs onto an ineligible primary
# ===========================================================================


def test_merge_rejects_ineligible_primary(tmp_path):
    """A RETIRED primary + ACTIVE duplicate is rejected by the
    disposition-conflict guard before the eligibility guard even runs. This
    is the concrete shape of "identity merge must not silently resolve a
    candidate disposition conflict": merging an unresolved ACTIVE candidate
    into a RETIRED primary would drop it. The caller must align statuses
    explicitly first.
    """

    runtime = _make_runtime(tmp_path / "ws")
    run_id = _create_run(runtime)
    rev = _view(runtime, run_id).state_revision
    retained = _retain(runtime, run_id, rev, ["P1", "P2"])
    refs = list(retained.paper_refs)
    rev = retained.state_revision
    p1, p2 = refs

    # P2 is ACTIVE+analyzed and referenced by an ApproachFamily; P1 is retired.
    rev = _put_analysis(runtime, run_id, rev, p2).state_revision
    rev = _put_approach_family(runtime, run_id, rev, [p2]).state_revision
    rev = _set_status(
        runtime, run_id, rev, p1, PaperResearchStatus.RETIRED, reason="out of scope"
    ).state_revision

    with pytest.raises(CommandRejectedError) as exc_info:
        runtime.researcher.reconcile_paper_identity(
            run_id,
            rev,
            p1,
            PaperSource(title="merged", doi="10.0/merged"),
            duplicate_paper_ref=p2,
        )
    msg = str(exc_info.value).lower()
    assert "conflicting candidate dispositions" in msg, msg


def test_merge_rejects_active_primary_retired_duplicate(tmp_path):
    """The symmetric case: an ACTIVE primary + RETIRED duplicate is also a
    disposition conflict. Merging would bury a RETIRED disposition (with its
    reason) under an ACTIVE primary — silent disappearance of the retirement
    record. Rejected; the caller must align statuses first."""

    runtime = _make_runtime(tmp_path / "ws")
    run_id = _create_run(runtime)
    rev = _view(runtime, run_id).state_revision
    retained = _retain(runtime, run_id, rev, ["P1", "P2"])
    refs = list(retained.paper_refs)
    rev = retained.state_revision
    p1, p2 = refs

    # Analyze and retire P2 (with a reason). P1 stays ACTIVE+unanalyzed.
    rev = _put_analysis(runtime, run_id, rev, p2).state_revision
    rev = _set_status(
        runtime, run_id, rev, p2, PaperResearchStatus.RETIRED, reason="superseded"
    ).state_revision

    with pytest.raises(CommandRejectedError) as exc_info:
        runtime.researcher.reconcile_paper_identity(
            run_id,
            rev,
            p1,
            PaperSource(title="merged", doi="10.0/merged"),
            duplicate_paper_ref=p2,
        )
    assert "conflicting candidate dispositions" in str(exc_info.value).lower()


def test_merge_allows_matching_active_statuses(tmp_path):
    """ACTIVE + ACTIVE is not a disposition conflict; the merge proceeds (other
    invariants still apply)."""

    runtime = _make_runtime(tmp_path / "ws")
    run_id = _create_run(runtime)
    rev = _view(runtime, run_id).state_revision
    retained = _retain(runtime, run_id, rev, ["P1", "P2"])
    refs = list(retained.paper_refs)
    rev = retained.state_revision
    p1, p2 = refs

    # Both ACTIVE; analyze P2 so the merge carries an analysis onto P1.
    rev = _put_analysis(runtime, run_id, rev, p2).state_revision

    result = runtime.researcher.reconcile_paper_identity(
        run_id,
        rev,
        p1,
        PaperSource(title="merged", doi="10.0/merged"),
        duplicate_paper_ref=p2,
    )
    assert result.removed_paper_ref == p2
    paper = _inspect_paper(runtime, run_id, result.state_revision, p1)
    assert paper.research_status == "ACTIVE"
    assert paper.analysis is not None


def test_merge_allows_matching_retired_statuses(tmp_path):
    """RETIRED + RETIRED (both with reasons) is mechanically possible when the
    other invariants permit it — identity merge remains an identity operation,
    not a disposition decision. Different retirement_reason values do not block
    the merge; only status equality matters."""

    runtime = _make_runtime(tmp_path / "ws")
    run_id = _create_run(runtime)
    rev = _view(runtime, run_id).state_revision
    retained = _retain(runtime, run_id, rev, ["P1", "P2"])
    refs = list(retained.paper_refs)
    rev = retained.state_revision
    p1, p2 = refs

    # Retire both with different reasons. Neither is referenced by the Landscape
    # (no ApproachFamily/Finding cites them), so retirement is allowed.
    rev = _set_status(
        runtime, run_id, rev, p1, PaperResearchStatus.RETIRED, reason="out of scope"
    ).state_revision
    rev = _set_status(
        runtime, run_id, rev, p2, PaperResearchStatus.RETIRED, reason="superseded"
    ).state_revision

    result = runtime.researcher.reconcile_paper_identity(
        run_id,
        rev,
        p1,
        PaperSource(title="merged", doi="10.0/merged"),
        duplicate_paper_ref=p2,
    )
    assert result.removed_paper_ref == p2
    # Primary keeps its own disposition; reconcile does not interpret semantic
    # retirement meaning.
    paper = _inspect_paper(runtime, run_id, result.state_revision, p1)
    assert paper.research_status == "RETIRED"
    assert paper.retirement_reason == "out of scope"


# ===========================================================================
# 3 & 4. apply_research_mutation(PutLandscapeFinding) evidence gate
# ===========================================================================


def test_finding_mutation_rejects_retired_source(tmp_path):
    """The mutation path (apply_research_mutation) must reject a Finding whose
    source is a RETIRED paper — same rule as the put-finding command path."""

    runtime = _make_runtime(tmp_path / "ws")
    run_id = _create_run(runtime)
    rev = _view(runtime, run_id).state_revision
    retained = _retain(runtime, run_id, rev, ["P1"])
    ref = retained.paper_refs[0]
    rev = retained.state_revision

    # Analyze then retire P1.
    rev = _put_analysis(runtime, run_id, rev, ref).state_revision
    rev = _set_status(
        runtime, run_id, rev, ref, PaperResearchStatus.RETIRED, reason="superseded"
    ).state_revision

    with pytest.raises(CommandRejectedError) as exc_info:
        _put_finding_mutation(runtime, run_id, rev, [{"paper_ref": ref}])
    assert "ACTIVE" in str(exc_info.value) or "active" in str(exc_info.value).lower()


def test_finding_mutation_rejects_unanalyzed_source(tmp_path):
    """The mutation path must reject a Finding whose source is ACTIVE but has
    no PaperAnalysis."""

    runtime = _make_runtime(tmp_path / "ws")
    run_id = _create_run(runtime)
    rev = _view(runtime, run_id).state_revision
    retained = _retain(runtime, run_id, rev, ["P1"])
    ref = retained.paper_refs[0]
    rev = retained.state_revision

    # P1 is ACTIVE + analysis=None.
    with pytest.raises(CommandRejectedError) as exc_info:
        _put_finding_mutation(runtime, run_id, rev, [{"paper_ref": ref}])
    msg = str(exc_info.value).lower()
    assert "active" in msg or "analysis" in msg


def test_finding_mutation_accepts_analyzed_active_source(tmp_path):
    """Happy path: ACTIVE + analyzed is eligible as a Finding source via the
    mutation path."""

    runtime = _make_runtime(tmp_path / "ws")
    run_id = _create_run(runtime)
    rev = _view(runtime, run_id).state_revision
    retained = _retain(runtime, run_id, rev, ["P1"])
    ref = retained.paper_refs[0]
    rev = retained.state_revision
    rev = _put_analysis(runtime, run_id, rev, ref).state_revision

    result = _put_finding_mutation(runtime, run_id, rev, [{"paper_ref": ref}])
    assert result.finding_refs


# ===========================================================================
# PutPaperAnalysis only allowed on ACTIVE Paper
# ===========================================================================


def test_put_paper_analysis_allowed_on_active(tmp_path):
    """A PutPaperAnalysis mutation on an ACTIVE paper succeeds — the happy path."""

    runtime = _make_runtime(tmp_path / "ws")
    run_id = _create_run(runtime)
    rev = _view(runtime, run_id).state_revision
    retained = _retain(runtime, run_id, rev, ["P1"])
    ref = retained.paper_refs[0]
    rev = retained.state_revision

    result = _put_analysis(runtime, run_id, rev, ref)
    paper = _inspect_paper(runtime, run_id, result.state_revision, ref)
    assert paper.analysis is not None
    assert paper.analysis.summary == "analysis"


def test_put_paper_analysis_rejected_on_retired(tmp_path):
    """A PutPaperAnalysis mutation on a RETIRED paper is rejected — deep
    reading produces analysis only for ACTIVE papers. The caller must
    reactivate explicitly with set-paper-status first."""

    runtime = _make_runtime(tmp_path / "ws")
    run_id = _create_run(runtime)
    rev = _view(runtime, run_id).state_revision
    retained = _retain(runtime, run_id, rev, ["P1"])
    ref = retained.paper_refs[0]
    rev = retained.state_revision

    # Analyze then retire P1 (so it carries a historical analysis).
    rev = _put_analysis(runtime, run_id, rev, ref).state_revision
    rev = _set_status(
        runtime, run_id, rev, ref, PaperResearchStatus.RETIRED, reason="superseded"
    ).state_revision

    with pytest.raises(CommandRejectedError) as exc_info:
        _put_analysis(runtime, run_id, rev, ref)
    msg = str(exc_info.value).lower()
    assert "reactivate" in msg, msg


def test_retired_paper_with_historical_analysis_still_loads(tmp_path):
    """A RETIRED paper that carries a historical PaperAnalysis (written before
    retirement) is valid state — it loads normally. The new PutPaperAnalysis
    gate only blocks *new* analysis writes, not existing state."""

    runtime = _make_runtime(tmp_path / "ws")
    run_id = _create_run(runtime)
    rev = _view(runtime, run_id).state_revision
    retained = _retain(runtime, run_id, rev, ["P1"])
    ref = retained.paper_refs[0]
    rev = retained.state_revision

    # Analyze then retire — the analysis persists on the RETIRED paper.
    rev = _put_analysis(runtime, run_id, rev, ref).state_revision
    rev = _set_status(
        runtime, run_id, rev, ref, PaperResearchStatus.RETIRED, reason="superseded"
    ).state_revision

    # Reload from persisted state — must load without error.
    run = runtime._repository.load(run_id)  # type: ignore[attr-defined]
    validate_run(run)
    paper = run.papers[ref]
    assert paper.research_status is PaperResearchStatus.RETIRED
    assert paper.analysis is not None  # historical analysis preserved





def _legacy_retired_without_reason_run() -> dict:
    """Build a minimal run dict with a RETIRED paper that has no
    retirement_reason — the legacy shape that predates the feature."""

    runtime = None  # build manually below
    import my_search_harness.runtime.codec as codec

    # Start from a real run's dict shape, then mutate a paper to RETIRED with
    # no reason. Use a fresh in-process run to get a valid skeleton.
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        rt = _make_runtime(Path(td))
        rid = _create_run(rt)
        rev = _view(rt, rid).state_revision
        retained = _retain(rt, rid, rev, ["P1"])
        ref = retained.paper_refs[0]
        rev = retained.state_revision
        rev = _put_analysis(rt, rid, rev, ref).state_revision
        # Load the run dict.
        run = rt._repository.load(rid)  # type: ignore[attr-defined]
        payload = run_to_dict(run)
    # Simulate legacy: set the paper RETIRED with no retirement_reason.
    if ref not in payload["papers"]:
        raise AssertionError(f"paper {ref!r} missing from payload papers")
    payload["papers"][ref]["research_status"] = "RETIRED"
    payload["papers"][ref]["retirement_reason"] = None
    return payload


def test_legacy_retired_without_reason_loads():
    """An old snapshot with RETIRED + reason=None still loads — validate_run
    stays weak so historical runs remain readable."""

    payload = _legacy_retired_without_reason_run()
    # Must not raise.
    run = run_from_dict(payload)
    validate_run(run)
    retired = [p for p in run.papers.values() if p.research_status is PaperResearchStatus.RETIRED]
    assert retired
    assert all(p.retirement_reason is None for p in retired)


def test_legacy_retired_without_reason_blocks_completion(tmp_path):
    """A legacy run with RETIRED + reason=None loads, but request-completion
    on that state is rejected — new lifecycle transitions must satisfy the
    current closure invariant."""

    payload = _legacy_retired_without_reason_run()
    # Persist it into a fresh workspace so we can drive request-completion.
    run_id = payload["id"]
    runs_dir = tmp_path / "ws" / "runs"
    runs_dir.mkdir(parents=True)
    state_path = runs_dir / run_id / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    runtime = _make_runtime(tmp_path / "ws")
    rev = _view(runtime, run_id).state_revision
    with pytest.raises(CommandRejectedError) as exc_info:
        _request_completion(runtime, run_id, rev)
    msg = str(exc_info.value)
    assert "RETIRED" in msg
    # The offending paper ref is named.
    retired_refs = [
        ref
        for ref, paper in run_from_dict(payload).papers.items()
        if paper.research_status is PaperResearchStatus.RETIRED
    ]
    assert any(ref in msg for ref in retired_refs), msg


# ===========================================================================
# 7, 8, 9. report citation evidence gate
# ===========================================================================


def _paper_entry(
    ref: str,
    *,
    status: PaperResearchStatus = PaperResearchStatus.ACTIVE,
    has_analysis: bool = True,
):
    from my_search_harness.runtime.context import PaperIndexEntry

    return PaperIndexEntry(
        ref=ref,
        title=f"Paper {ref}",
        authors=(),
        publication_year=2024,
        publication_date=None,
        doi=None,
        arxiv_id=None,
        canonical_url=None,
        research_status=status,
        retirement_reason=None,
        has_analysis=has_analysis,
    )


def _delivery_view(papers: list):
    from my_search_harness.domain.model import CompletionPassBasis, LifecycleMode
    from my_search_harness.runtime.context import (
        ContractContext,
        DeliveryView,
        RequirementContext,
    )

    return DeliveryView(
        state_revision=1,
        lifecycle=LifecycleMode.DELIVERY,
        contract=ContractContext(
            contract_revision=1,
            mission="m",
            requirements=(RequirementContext(ref="req_1", statement="r"),),
            scope="s",
            deliverable_description="d",
            required_artifacts=("REPORT",),
        ),
        delivery_basis=CompletionPassBasis(
            completion_check_ref="check_00000000-0000-4000-8000-000000000000"
        ),
        approach_families=(),
        findings=(),
        open_problems=(),
        open_gaps=(),
        papers=tuple(papers),
    )


def _manuscript(citation_id: str, paper_ref: str):
    from my_search_harness.runtime.reporting import CitationReference, ReportManuscript

    return ReportManuscript(
        markdown=f"See result {{{{cite:{citation_id}}}}}.",
        citations=(
            CitationReference(citation_id=citation_id, paper_ref=paper_ref),
        ),
    )


def test_citation_rejects_retired_paper():
    from my_search_harness.runtime.citations import (
        CitationValidationError,
        DeterministicCitationRenderer,
    )

    ref = "paper_11111111-1111-4111-8111-111111111111"
    view = _delivery_view(
        [_paper_entry(ref, status=PaperResearchStatus.RETIRED, has_analysis=True)]
    )
    manuscript = _manuscript("c1", ref)
    with pytest.raises(CitationValidationError) as exc_info:
        DeterministicCitationRenderer().audit(view, manuscript)
    assert "not eligible" in str(exc_info.value).lower() or "ACTIVE" in str(
        exc_info.value
    )


def test_citation_rejects_unanalyzed_active_paper():
    from my_search_harness.runtime.citations import (
        CitationValidationError,
        DeterministicCitationRenderer,
    )

    ref = "paper_22222222-2222-4222-8222-222222222222"
    view = _delivery_view([_paper_entry(ref, has_analysis=False)])
    manuscript = _manuscript("c1", ref)
    with pytest.raises(CitationValidationError) as exc_info:
        DeterministicCitationRenderer().audit(view, manuscript)
    assert "not eligible" in str(exc_info.value).lower() or "analysis" in str(
        exc_info.value
    ).lower()


def test_citation_accepts_analyzed_active_paper():
    from my_search_harness.runtime.citations import DeterministicCitationRenderer

    ref = "paper_33333333-3333-4333-8333-333333333333"
    view = _delivery_view([_paper_entry(ref, has_analysis=True)])
    manuscript = _manuscript("c1", ref)
    audit = DeterministicCitationRenderer().audit(view, manuscript)
    assert audit.bibliography_paper_refs == (ref,)


# ===========================================================================
# 10. validate_transition defense-in-depth
# ===========================================================================


def test_transition_blocks_retire_without_reason(tmp_path):
    """validate_transition rejects a transition that retires a paper without a
    retirement_reason — the global safety net behind set_paper_research_status."""

    runtime = _make_runtime(tmp_path / "ws")
    run_id = _create_run(runtime)
    rev = _view(runtime, run_id).state_revision
    retained = _retain(runtime, run_id, rev, ["P1"])
    ref = retained.paper_refs[0]
    rev = retained.state_revision

    before = runtime._repository.load(run_id)  # type: ignore[attr-defined]
    after = runtime._repository.load(run_id)  # type: ignore[attr-defined]
    # Hand-craft a transition that retires P1 with no reason — bypassing the
    # command-level gate — to prove validate_transition catches it.
    after = copy.deepcopy(before)
    after.papers[ref].research_status = PaperResearchStatus.RETIRED
    after.papers[ref].retirement_reason = None
    after.state_revision = before.state_revision + 1
    with pytest.raises(DomainValidationError) as exc_info:
        validate_transition(before, after)
    assert "retirement_reason" in str(exc_info.value)


def test_transition_blocks_changed_landscape_ineligible(tmp_path):
    """validate_transition rejects a transition that changes a Landscape
    Finding to cite an ineligible (unanalyzed) paper."""

    runtime = _make_runtime(tmp_path / "ws")
    run_id = _create_run(runtime)
    rev = _view(runtime, run_id).state_revision
    retained = _retain(runtime, run_id, rev, ["P1", "P2"])
    refs = list(retained.paper_refs)
    rev = retained.state_revision
    p1, p2 = refs

    # Analyze P1, make a finding citing P1. P2 stays unanalyzed.
    rev = _put_analysis(runtime, run_id, rev, p1).state_revision
    rev = _put_finding_mutation(runtime, run_id, rev, [{"paper_ref": p1}]).state_revision

    before = runtime._repository.load(run_id)  # type: ignore[attr-defined]
    after = copy.deepcopy(before)
    # Rewrite the finding's source to point at P2 (unanalyzed).
    for finding in after.literature_landscape.findings.values():
        finding.sources = {
            LiteratureSource(paper_ref=p2, relation=SourceRelation.SUPPORTS)
        }
    after.state_revision = before.state_revision + 1
    with pytest.raises(DomainValidationError) as exc_info:
        validate_transition(before, after)
    assert "not ACTIVE" in str(exc_info.value) or "not active" in str(
        exc_info.value
    ).lower()


def test_transition_blocks_reason_cleared_on_retired(tmp_path):
    """Mutating a RETIRED paper's retirement_reason to None is a disposition
    change — validate_transition rejects it even though the paper was already
    RETIRED. (Legacy RETIRED+reason=None untouched by an unrelated mutation
    is still allowed — see test_transition_ignores_unchanged_invalid_legacy_state.)
    """

    runtime = _make_runtime(tmp_path / "ws")
    run_id = _create_run(runtime)
    rev = _view(runtime, run_id).state_revision
    retained = _retain(runtime, run_id, rev, ["P1"])
    ref = retained.paper_refs[0]
    rev = retained.state_revision

    # Retire P1 with a valid reason.
    rev = _put_analysis(runtime, run_id, rev, ref).state_revision
    rev = _set_status(
        runtime, run_id, rev, ref, PaperResearchStatus.RETIRED, reason="superseded"
    ).state_revision

    before = runtime._repository.load(run_id)  # type: ignore[attr-defined]
    after = copy.deepcopy(before)
    # Clear the reason — a disposition-state change on an already-RETIRED paper.
    after.papers[ref].retirement_reason = None
    after.state_revision = before.state_revision + 1
    with pytest.raises(DomainValidationError) as exc_info:
        validate_transition(before, after)
    assert "retirement_reason" in str(exc_info.value)


def test_transition_blocks_renamed_approach_with_ineligible_rep(tmp_path):
    """Full-object inequality: renaming an ApproachFamily (a non-evidence
    field change) still triggers the eligibility re-check on its
    representative_papers. The change need not touch the paper refs."""

    runtime = _make_runtime(tmp_path / "ws")
    run_id = _create_run(runtime)
    rev = _view(runtime, run_id).state_revision
    retained = _retain(runtime, run_id, rev, ["P1", "P2"])
    refs = list(retained.paper_refs)
    rev = retained.state_revision
    p1, p2 = refs

    # Analyze P1, make an approach family with P1 as representative.
    rev = _put_analysis(runtime, run_id, rev, p1).state_revision
    rev = _put_approach_family(runtime, run_id, rev, [p1], name="Original").state_revision

    before = runtime._repository.load(run_id)  # type: ignore[attr-defined]
    after = copy.deepcopy(before)
    # Strip P1's analysis (ACTIVE + unanalyzed now) AND rename the approach
    # family. The rename makes before_approach != after_approach, so the
    # eligibility re-check fires on representative_papers — which now cite an
    # unanalyzed ACTIVE paper. (We do NOT retire P1, so the paper-disposition
    # check does not fire first.)
    after.papers[p1].analysis = None
    for approach in after.literature_landscape.approach_families.values():
        approach.name = "Renamed"
    after.state_revision = before.state_revision + 1
    with pytest.raises(DomainValidationError) as exc_info:
        validate_transition(before, after)
    assert "not ACTIVE" in str(exc_info.value) or "not active" in str(
        exc_info.value
    ).lower()


# ===========================================================================
# 11. history / bad_case still loads / inspects / audits
# ===========================================================================


def test_bad_case_still_loads_after_transition_invariants():
    """The real bad_case run (CLOSED, 59 papers, all ACTIVE, reason=None) must
    still load after the new validate_transition invariants. Transition
    invariants only constrain *new* transitions, not historical snapshots."""

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
    validate_run(run)
    assert len(run.papers) == 59
    analyzed = sum(1 for p in run.papers.values() if p.analysis is not None)
    assert analyzed == 10
    # A no-op transition (before == after except revision) must not be
    # rejected by the new invariants — they only constrain changed state.
    after = copy.deepcopy(run)
    after.state_revision = run.state_revision + 1
    validate_transition(run, after)  # must not raise


def test_transition_ignores_unchanged_invalid_legacy_state(tmp_path):
    """A transition that does not touch an existing invalid paper must not be
    rejected just because the legacy state is invalid-by-new-rules. Only
    state introduced/changed by *this* transition is constrained."""

    # Build a legacy run with a RETIRED+no-reason paper (loads fine).
    payload = _legacy_retired_without_reason_run()
    run = run_from_dict(payload)
    # A transition that only increments revision (touches nothing) must pass.
    after = copy.deepcopy(run)
    after.state_revision = run.state_revision + 1
    validate_transition(run, after)  # must not raise
