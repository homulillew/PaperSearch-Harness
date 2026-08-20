# reopen-delivery Cleanup Patch — Completion Report

## Summary

Applied a narrow cleanup/fix patch to the `reopen-delivery` capability on
`homulillew/PaperSearch-Harness`, branch `feat/adr12-v04-report-information-architecture`.
The CLOSED → reopen-delivery → DELIVERY architecture is unchanged. Four issues
fixed: (1) basis validation split from artifact validation; (2) basis validation
checks the right things; (3) outcome ↔ basis consistency enforced; (4) repository
hygiene (removed 3 ADR design files, reverted workspace E2E mutations). Plus
RUNTIME_API/SKILL documentation, documentation contract tests, focused tests,
and a manual E2E demonstration. Normal follow-up commit, no rewrite/force-push.

---

## §14 — Required Answers (17 items)

### 1. Starting HEAD

```
b4c34834b97c642596da797f9c0bec9f011ec067
b4c3483 feat(reporting): add reopen-delivery lifecycle transition (CLOSED -> DELIVERY)
```

Fetched latest remote first; local and remote both at `b4c3483`.

### 2. Final HEAD

```
594780401c636710f0d3fc9582f84824b00ac197
5947804 fix(reporting): split reopen-delivery basis validation from artifact validation
```

Pushed to `origin/feat/adr12-v04-report-information-architecture`
(`b4c3483..5947804`). Normal follow-up commit; **no rewrite, no force-push**.

### 3. Files changed

| File | Change |
|---|---|
| `runtime/.../delivery.py` | +30/-22: extracted `_validate_delivery_basis`; `_validate_delivery` calls it then validates artifacts; `reopen_delivery` uses basis-only validation + outcome↔basis check |
| `references/RUNTIME_API.md` | +10: `reopen-delivery` in command list + concise prose |
| `SKILL.md` | +9: reopen-delivery affordance rule |
| `tests/test_reporting_adapter.py` | +268: 6 new focused tests (D, E, C×2, F) |
| `tests/test_reporting.py` | +35: 2 doc contract assertions |
| `ADR-012-v0.6-delivery-simplification-amendment.md` | **removed** (856 lines) |
| `ADR-012-v0.6-implementation-diff.md` | **removed** (1193 lines) |
| `ADR-012-v0.6-implementation-plan.md` | **removed** (241 lines) |
| `workspace/runs/run_2ca834ac…/state.json` | reverted to rev 88 (CLOSED COMPLETE) |
| `workspace/runs/run_2ca834ac…/events.jsonl` | reverted (3 feature audit events removed) |
| `workspace/runs/run_2ca834ac…/artifacts/report.md` | reverted to original content (sha `fc727bda…`) |
| `workspace/runs/run_2ca834ac…/artifacts/report.meta.json` | reverted to original metadata |
| `workspace/runs/run_2ca834ac…/delivery/report_session.json` | **removed** (added only by feature) |
| `workspace/runs/run_2ca834ac…/delivery/rendered_report.md` | **removed** (added only by feature) |

**No change** to: `certified_delivery.py`, `artifacts.py`, `validation.py`,
`model.py`, `codec.py`, `persistence.py`, `capabilities.py`, `harness.py`,
or any Research/Completion code.

### 4. How basis validation separated from artifact validation

`delivery.py` was refactored into two methods (no duplicated logic):

- **`_validate_delivery_basis(run) -> None`** — validates ONLY the stored
  `DeliveryBasis` against current `ResearchRun`/`Contract` invariants:
  - `CompletionPassBasis`: the referenced completion check must exist
    (`completion_check_ref in run.completion_checks`), and its
    `basis_contract_revision` must match the current contract revision.
  - `PartialAuthorizationBasis`: its `basis_contract_revision` must match
    the current contract revision.
  - Unknown/malformed `DeliveryBasis` → `CommandRejectedError` (reject, do not
    repair).
  - Resolves the current contract revision and confirms exactly one matching
    contract revision exists.
  - Does **NOT** call `artifact_store.validate_report` — the old report
    artifact is not required here.
- **`_validate_delivery(run) -> DeliveryValidationResult`** — calls
  `_validate_delivery_basis(run)`, THEN validates required published artifacts
  (`artifact_store.validate_report` for REPORT). Returns the validated
  artifact set.

Callers:
- `reopen_delivery` → `_validate_delivery_basis(current)` (artifact-free).
- `validate_delivery` → `_validate_delivery(run)` (full).
- `close_run` → `_validate_delivery(current)` (full).

### 5. Exact reopen-delivery preconditions

`reopen_delivery` succeeds iff all hold:
1. `expected_revision == run.state_revision` (else `RevisionConflictError`).
2. `run.lifecycle == CLOSED` (else `CommandRejectedError`).
3. `run.delivery_basis is not None` (else `CommandRejectedError`; load-time
   domain validator also rejects CLOSED + no basis).
4. `run.outcome in (COMPLETE, PARTIAL)` (else `CommandRejectedError`).
5. **Outcome ↔ basis consistency** (NEW): `CompletionPassBasis` ↔ `COMPLETE`,
   `PartialAuthorizationBasis` ↔ `PARTIAL`; mismatch or unknown basis →
   `CommandRejectedError`. (Load-time `validate_run` already enforces this for
   disk-loaded state; this is defense-in-depth for in-memory calls.)
6. `_validate_delivery_basis(current)` passes (basis contract revision is
   current; referenced check exists). Invalid basis → reject, do not repair.

The OLD report artifact is **NOT** required (Issue 1 fixed).

### 6. Outcome ↔ basis mapping enforced

In `reopen_delivery`, an explicit check after the outcome-validity check:

```python
if isinstance(basis, CompletionPassBasis):
    if outcome is not RunOutcome.COMPLETE:
        raise CommandRejectedError(
            "reopen_delivery: CompletionPassBasis requires outcome COMPLETE")
elif isinstance(basis, PartialAuthorizationBasis):
    if outcome is not RunOutcome.PARTIAL:
        raise CommandRejectedError(
            "reopen_delivery: PartialAuthorizationBasis requires outcome PARTIAL")
else:
    raise CommandRejectedError("reopen_delivery found an unknown delivery basis")
```

Simple explicit `isinstance` check — no generic lifecycle validation framework.
This mirrors the existing `close_run` basis→outcome derivation (L173-178) and
the load-time domain validator (`validation.py:509-521`). For CLI calls the
load-time validator catches corrupted CLOSED state first; the explicit check is
defense-in-depth for direct in-memory capability calls.

### 7. Proof: absent old artifact no longer blocks reopening

**Test `test_reopen_delivery_succeeds_with_old_report_missing`** (Case D):
a CLOSED COMPLETE run has `report.md` + `report.meta.json` deleted from disk;
`reopen-delivery` succeeds (returns to DELIVERY, outcome=None, basis preserved).

**Manual CASE 1**: removed `report.md` + `report.meta.json` from a freshly
closed COMPLETE run → `reopen-delivery` SUCCEEDED (revision 4 → 5, DELIVERY,
outcome=None). The basis was validated; the missing artifact did not block.

### 8. Proof: valid current artifact still required to close

**Test `test_reopen_delivery_then_close_without_report_fails`** (Case E):
after reopening (old report removed), both `validate-delivery` and `close-run`
reject with `ArtifactValidationError` / `CommandRejectedError` ("report
content is missing"). A valid current REPORT artifact is still required at
closure.

**Test `test_reopen_delivery_then_publish_then_close_succeeds`** (Case E cont.):
once a new certified report is published on the reopened run, `close-run`
succeeds (outcome=COMPLETE).

**Manual CASE 2**: reopened run, no new report → `close-run` FAILED
(`ArtifactValidationError: report content is missing`).
**Manual CASE 3**: new report certified + published → `close-run` SUCCEEDED
(outcome=COMPLETE).

### 9. RUNTIME_API / SKILL affordance changes

**RUNTIME_API.md**: added `reopen-delivery --run-id RUN --expected-revision REV`
to the Delivery command list (between `reopen-research` and `close-run`). Added
one concise prose paragraph: `reopen-delivery` returns a CLOSED run directly to
DELIVERY, reusing accepted Research State and existing `DeliveryBasis`; preserves
basis, preserves Research State, clears `outcome`; does not reopen Research,
does not create a new `CompletionCheck`, does not re-run Completion policy; a new
Brief/Manuscript still requires fresh Reader and Integrity certification through
the normal digest-bound Delivery path; reopening validates the stored basis (not
the old report artifact); a valid current REPORT artifact remains required to
`validate-delivery`/`close-run`.

**SKILL.md**: added a short rule in the Deliver-a-report section: if the user
wants to regenerate/improve a report from an already CLOSED run and the accepted
Research itself does not need revision, use `reopen-delivery` (not reopen-research).
It reuses the existing accepted `DeliveryBasis` and does not re-certify Research
under today's Completion rules. Then continue: `report-construction-input` → new
Brief → Authoring → Reader → Integrity → Publish → `close-run`. A new Brief
resets old downstream Delivery certification, so the normal digest-bound path must
run again. No new mode ("polish mode"/"report rerun mode") added.

### 10. Historical ADR/design files removed and why

Removed (introduced only by the feature commit `b4c3483`, confirmed via
`git log --follow` — no prior history):
- `ADR-012-v0.6-delivery-simplification-amendment.md` (856 lines)
- `ADR-012-v0.6-implementation-diff.md` (1193 lines)
- `ADR-012-v0.6-implementation-plan.md` (241 lines)

**Why:** large historical implementation/design records unrelated to the narrow
reopen-delivery feature. They are not ADRs of record for the shipped capability
(the capability introduces no new authority boundary — see item 16). No
replacement documents or new ADR created.

### 11. What happened to workspace E2E files and why

The feature commit `b4c3483` mutated the tracked authoritative run
`workspace/runs/run_2ca834ac-48de-4ce8-a325-bc70a7aa760f/` as E2E demonstration
evidence (rev 88→90, 3 audit events, re-polished report, new delivery session).
Per the workspace tracking policy (commit `23f2fb6`: `workspace/` is committed
authoritative run data, NOT gitignored; `test_workspace_is_tracked_not_ignored`
pins this), these are operational state files, not production source. Manual E2E
evidence belongs in the completion report / commit message, not as committed
production source.

Reverted via `git checkout 23f2fb6 -- workspace/runs/run_2ca834ac…/` (restores
the 4 pre-existing files to pre-feature content) + `git rm` of the 2 files that
did not exist at `23f2fb6` (`delivery/report_session.json`,
`delivery/rendered_report.md`). Confirmed no intermediate commits touched the
run between `23f2fb6` and `b4c3483`. Result: run back at rev 88, CLOSED COMPLETE,
original report (sha `fc727bda…`), original events. Older pre-existing run
history was not deleted.

**Note on `workspace/scratch/`:** the feature commit also reorganized scratch
captures (flat → `captures/` subdirectory). Per §4(b) the revert scope is
strictly `workspace/runs/…`; scratch is disposable by the filesystem-boundary
contract and was left as-is. Demo scratch from this patch's manual run was
removed and not committed.

### 12. Tests and exact results

| Suite | Command | Result |
|---|---|---|
| Focused reopen-delivery | `pytest tests/test_reporting_adapter.py -k "reopen_delivery" --basetemp=./.pytest_tmp` | **16 passed**, 28 deselected |
| Reporting + adapter + FS boundary | `pytest tests/test_reporting_adapter.py tests/test_reporting.py tests/test_filesystem_boundary.py --basetemp=./.pytest_tmp` | **175 passed** (63.37s) |
| Doc contract (new) | `pytest tests/test_reporting.py -k "runtime_api_documents or skill_tells_when" --basetemp=./.pytest_tmp` | **2 passed** |
| Full suite | `pytest --basetemp=./.pytest_tmp` | **249 passed, 1 failed** (709.43s) |

The single full-suite failure
(`test_reader_manuscript_blocker_requires_changed_manuscript_and_new_reader`)
was `CertifiedDeliveryError: cannot persist report delivery session` — the known
Windows `os.replace` file-handle race on `report_session.json` in
`certified_delivery.py` (a file NOT modified by this patch). It passes in
isolation and on clean re-runs; the adapter suite run twice showed 2 failures
then 0 failures (44 passed) — confirming intermittent flakiness, not a
regression. All failures are exclusively in the `os.replace` atomic-write path,
unrelated to the basis/artifact validation split.

New focused tests (6):
- **D** `test_reopen_delivery_succeeds_with_old_report_missing` — old report
  artifact removed → reopen succeeds.
- **E** `test_reopen_delivery_then_close_without_report_fails` — reopened run,
  no current report → validate-delivery + close-run reject.
- **E (cont.)** `test_reopen_delivery_then_publish_then_close_succeeds` — new
  report published → close-run succeeds.
- **C** `test_reopen_delivery_rejects_outcome_basis_mismatch_complete_partial` —
  CompletionPassBasis + PARTIAL → rejected.
- **C** `test_reopen_delivery_rejects_outcome_basis_mismatch_partial_complete` —
  PartialAuthorizationBasis + COMPLETE → rejected.
- **F** `test_reopen_delivery_rejects_stale_basis_contract_revision` — basis
  pointing at a stale contract revision → rejected.

Existing reopen-delivery tests (A, B, C-lifecycle, D-missing-basis, E-revision,
F-audit, G-immediately-usable, H-research-state, I-fresh-report, J-brief-reset)
retained and unchanged. Doc contract tests (2): RUNTIME_API contains
`reopen-delivery`; SKILL tells Claude when a CLOSED run can reuse accepted
Research via `reopen-delivery`.

**Manual demonstration** (fresh temp workspace, `.scratch_repolish/reopen_demo.py`):
- CASE 1: CLOSED COMPLETE, old report removed → `reopen-delivery` SUCCEEDED
  (rev 4→5, DELIVERY, outcome=None).
- CASE 2: reopened, no new report → `close-run` FAILED
  (`ArtifactValidationError: report content is missing`).
- CASE 3: new report certified + published → `close-run` SUCCEEDED
  (outcome=COMPLETE).

### 13. Confirmation: DeliveryBasis remains unchanged

`reopen_delivery` does **not** clear or mutate `delivery_basis`. The
implementation comment at `delivery.py:239` states `# delivery_basis is
intentionally preserved.` The only fields mutated are `state_revision` (+1),
`lifecycle` (CLOSED→DELIVERY), `outcome` (→None), plus one audit event.
Verified by `test_reopen_delivery_preserves_research_state` (basis before ==
basis after) and manual CASE 1 (basis preserved through reopen).

### 14. Confirmation: no CompletionCheck created

`reopen_delivery` does not transition through RESEARCH or COMPLETION_CHECK, does
not call any completion command, does not append to `completion_checks`. Verified
by `test_reopen_delivery_preserves_research_state`
(`after.completion_checks == before_checks`). The original
`CompletionPassBasis.completion_check_ref` still references the original check.

### 15. Confirmation: schema unchanged

**No schema bump.** `report_session.json` still reports `schema_version: 6`.
`state.json` structure unchanged — `reopen_delivery` only mutates existing
fields (`state_revision`, `lifecycle`, `outcome`) and appends an audit event.
No new collections, no new basis type, no format change. The refactor only
reorganized validation logic within `delivery.py`; the persistence format is
identical.

### 16. Confirmation: no Certified Delivery versioning/history machinery added

**None added.** No delivery attempt IDs, no report generations, no report
versions, no report history, no delivery DAG, no certification migration, no
certification reuse, no new pending states. `certified_delivery.py` was **not
modified**. The certified-delivery session is still keyed by `delivery_basis_key`
(unchanged); `put_brief` still resets the session via `_empty_session` (nulls
manuscript/blind_read/reader_pass/integrity_pass/rendered); digest binding still
blocks stale PASS. No new lifecycle mode (still exactly four: RESEARCH,
COMPLETION_CHECK, DELIVERY, CLOSED; the one transition addition CLOSED→DELIVERY
was from the prior feature commit, not this patch). No new ADR.

### 17. Confirmation: Research/Reader/Integrity semantics unchanged

**All unchanged.**
- **Research semantics**: no Research command invoked; no paper/analysis/finding/
  gap mutated; `completion_checks` untouched.
- **Completion semantics / Completion Checker authority**: unchanged; no new
  `CompletionCheck`; the original `CompletionPassBasis` remains authoritative.
- **Reader semantics**: unchanged (blind Phase 1 + attribution Phase 2,
  `repair_target` PASS semantics).
- **Integrity semantics**: unchanged (`disposition: PASS` with empty issues).
- **ReportBrief schema**: unchanged (5 lean fields).
- **Constructor / Authoring / math preflight / citation / source access / Deep
  Reading**: all unchanged.

The only runtime code change is the validation split in `delivery.py`
(`_validate_delivery_basis` extracted; `reopen_delivery` uses it + the outcome↔
basis check). `certified_delivery.py`, `artifacts.py`, `validation.py`,
`model.py`, `codec.py`, `persistence.py`, `capabilities.py`, `harness.py` were
read but **not modified**.

---

## Constraint compliance

- ✅ `certified_delivery.py` not modified (no failing regression proved
  insufficiency — the `os.replace` flakiness is a pre-existing Windows platform
  issue, not a correctness defect).
- ✅ No delivery attempt IDs / report generations / report versions / report
  history / delivery DAG / certification migration / certification reuse / new
  pending states.
- ✅ No change to Research/Completion/Completion Checker/ReportBrief/Constructor/
  Authoring/Reader/Integrity/math preflight/citation/source access/Research
  State/Certified Delivery semantics.
- ✅ No schema version bump (still 6).
- ✅ No new lifecycle mode.
- ✅ No new ADR.
- ✅ Normal follow-up commit, no rewrite/force-push.
- ✅ Fetched latest remote first; worked from actual current HEAD.
