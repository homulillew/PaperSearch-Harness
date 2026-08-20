# Task C — `reopen-delivery` Lifecycle Capability: Completion Report

## Summary

Implemented a narrow lifecycle transition allowing an already **CLOSED** research run to re-enter **DELIVERY** without reopening Research. The run reuses its accepted Research State and existing `DeliveryBasis` while rerunning only Delivery work. Demonstrated end-to-end on the real credit-allocation run: a closed report was reopened, a re-polished report version was authored, certified, and published, and the run was re-closed — all with the original `DeliveryBasis` preserved and no new `CompletionCheck`.

---

## §13 — Required Answers

### 1. Starting HEAD

```
cfb49cd @ fix(reporting): ADR-012 v0.6.4 math preflight cleanup patch
```

### 2. Final HEAD

```
b4c3483 feat(reporting): add reopen-delivery lifecycle transition (CLOSED -> DELIVERY)
```

Pushed to `origin/feat/adr12-v04-report-information-architecture` (`cfb49cd..b4c3483`).

### 3. Files changed

**Implementation (3 files):**
| File | Change |
|---|---|
| `runtime/src/my_search_harness/runtime/delivery.py` | +41 lines: `ReopenDeliveryResult` dataclass + `reopen_delivery()` method |
| `runtime/src/my_search_harness/runtime/capabilities.py` | +8 lines: import + thin delegation method |
| `scripts/harness.py` | +5/-1 lines: parser registration + dispatch + command set |

**Tests (1 file):**
| File | Change |
|---|---|
| `tests/test_reporting_adapter.py` | +437 lines: 10 focused tests (A–I) + 2 helpers (`_publish_certified`, `_closed_delivery_run`, `_load_run`) |

**Real-run demonstration (workspace data, 5 files):**
| File | Change |
|---|---|
| `workspace/runs/run_2ca834ac…/state.json` | rev 88→90, CLOSED→DELIVERY→CLOSED, outcome COMPLETE→None→COMPLETE |
| `workspace/runs/run_2ca834ac…/events.jsonl` | +3 audit events (`delivery_reopened`, `report_published`, `run_closed`) |
| `workspace/runs/run_2ca834ac…/artifacts/report.md` | re-polished report (sha256 `8c63ddf8…`) |
| `workspace/runs/run_2ca834ac…/artifacts/report.meta.json` | new content_sha256 + preserved basis |
| `workspace/runs/run_2ca834ac…/delivery/report_session.json` | new certified session (rendered_report.md added) |

**ADR docs (3 files, pre-existing design docs from Tasks A/B, committed with this task):**
- `ADR-012-v0.6-delivery-simplification-amendment.md`
- `ADR-012-v0.6-implementation-diff.md`
- `ADR-012-v0.6-implementation-plan.md`

**No change** to: `certified_delivery.py`, `citations.py`, `reporting.py`, `artifacts.py`, `validation.py`, `model.py`, or any Research/Completion code.

### 4. Exact `reopen-delivery` preconditions

A `reopen-delivery` call succeeds if and only if **all** of the following hold:

1. **Expected revision matches** — `expected_revision == run.state_revision` (else `RevisionConflictError`). Mandatory.
2. **Source lifecycle is CLOSED** — `run.lifecycle == CLOSED` (else `CommandRejectedError`). Rejected sources: `RESEARCH`, `COMPLETION_CHECK`, `DELIVERY`.
3. **Delivery basis exists** — `run.delivery_basis is not None` (else `CommandRejectedError` / `DomainValidationError` at load if the state is malformed).
4. **Outcome is a valid closed Delivery outcome** — `run.outcome in (COMPLETE, PARTIAL)` (else `CommandRejectedError`).
5. **Stored basis still supports Delivery under existing invariants** — `_validate_delivery(current)` passes (basis contract revision is current; if a REPORT artifact exists, `validate_report` confirms content digest matches metadata). If invalid, **reject rather than repair**.

No new basis type, no new authority boundary.

### 5. Exact state fields changed

On a successful `reopen-delivery`:

| Field | Before | After |
|---|---|---|
| `state_revision` | N | N + 1 |
| `lifecycle` | `CLOSED` | `DELIVERY` |
| `outcome` | `COMPLETE` or `PARTIAL` | `None` |

Plus one audit event appended (`action="delivery_reopened"`, `details={"outcome": <previous outcome>}`, `state_revision` = N+1).

**That is the complete set of mutated fields.** Nothing else in `ResearchRun` is touched.

### 6. Exact state fields intentionally preserved

Everything else, specifically including:

- `contract` (and its `current_revision`)
- `papers` (all 59 papers, their `research_status`, `analysis`, dispositions)
- `literature_landscape`
- `investigation_gaps`
- `completion_checks` (the dict, the referenced check, its `basis_contract_revision`, `CompletionPassBasis`/`PartialAuthorizationBasis`)
- **`delivery_basis`** (the authoritative basis — see §7)
- `resources` (resource accounting)
- `id`
- accepted research semantics, ApproachFamilies, Findings, OpenProblems, all other Research State

### 7. Confirmation: `DeliveryBasis` preserved

**Yes — `delivery_basis` is intentionally NOT cleared.** The implementation comment at `delivery.py:222` states this explicitly:

```python
# delivery_basis is intentionally preserved.
```

This is the key difference from `reopen_research` (which *clears* `delivery_basis` at `delivery.py:162`). For `reopen_delivery`, the prior accepted Completion/Partial authorization remains the authoritative basis for the new Delivery pass.

Verified on the real run: the basis before reopen (`{"completion_check_ref": "check_b7fb57de-b32a-45ae-aad3-838ef0d586d4", "type": "completion_pass"}`) is byte-identical to the basis after reopen and after re-close.

### 8. Confirmation: no new `CompletionCheck` created

**Yes — no new `CompletionCheck` is created.** `reopen_delivery` does not transition through `RESEARCH` or `COMPLETION_CHECK`, does not call any completion command, and does not append to `completion_checks`.

Verified on the real run: `len(completion_checks) == 1` before reopen, after reopen, and after re-close — the same single check `check_b7fb57de-b32a-45ae-aad3-838ef0d586d4` throughout. The `CompletionPassBasis.completion_check_ref` still references the original check.

### 9. How the old report delivery session behaves after reopening

After `reopen-delivery`, the old `report_session.json` is **not deleted** — but it is **reset on the next `put_brief`**. The certified-delivery session is keyed by `delivery_basis_key`; since the basis is preserved, the same session file is reused. `CertifiedReportDelivery.put_brief()` loads the existing session via `_load_if_exists`, then calls `_empty_session(basis_key)` which nulls **all** downstream state:

- `manuscript` → `None`
- `blind_read` → `None`
- `reader_pass` → `None`
- `integrity_pass` → `None`
- `rendered` → `None`

So the user does **not** need to delete files manually. The simplest UX works exactly as specified:

```
reopen-delivery → report-construction-input → put-report-brief(new brief)
  → put-report-manuscript → blind → reader → integrity → render → publish → close
```

Verified on the real run: after reopen (rev 89), `put-report-brief` accepted a fresh Brief; the session showed `pending_action: NONE` with `manuscript_digest: null`, `reader_pass: null`, `integrity_pass: null`, `rendered: null` — a clean slate, despite the previous closed run having a fully certified session.

### 10. How stale Reader/Integrity certification is prevented from certifying new work

Stale `ReaderPass` / `IntegrityPass` from the previous closed run **cannot** certify a newly authored report, because the certified-delivery layer binds every PASS to the digests of the work it certified, and `_require_certified()` enforces the binding:

- `ReaderPass` stores `{brief_digest, manuscript_digest}`.
- `IntegrityPass` stores `{brief_digest, delivery_basis_key, manuscript_digest}`.
- When a new Brief is put, `_empty_session` nulls the old passes (so they cannot be reused as-is).
- Even if an old PASS were somehow retained, `_require_certified()` validates that `brief_digest`, `manuscript_digest`, and the reader/integrity pass digests **all match** the current session. A new Brief produces a new `brief_digest`; a new manuscript produces a new `manuscript_digest`. The old PASS digests no longer match → rejected.

Additionally, `validate_report` (called by `_validate_delivery` at close time) reads `report.md` from disk and checks both the content SHA256 and the basis match — so an old rendered certification cannot publish a new manuscript.

Verified on the real run: the new report has `content_sha256 = 8c63ddf8…`, distinct from the original `fc727bda…`. The new Reader/Integrity PASS carries the new `manuscript_digest = 96da0165…` (distinct from any prior manuscript). Publication succeeded only after the fresh Reader→Integrity→render→publish path.

### 11. CLI command added

```
harness.py reopen-delivery --run-id <run> --expected-revision <revision>
```

No extra parameters. Wired at three points in `harness.py`:
- Parser registration (alongside `reopen-research` and `close-run`, sharing `_add_run_revision`).
- Dispatch block (`delivery.reopen_delivery(args.run_id, args.expected_revision)`).
- `_DELIVERY_COMMANDS` set (for command classification).

### 12. Tests run and exact results

| Suite | Command | Result |
|---|---|---|
| Focused reopen-delivery | `pytest tests/test_reporting_adapter.py -k "reopen_delivery"` | **10 passed**, 28 deselected (9.90s) |
| Full reporting | `pytest tests/test_reporting_adapter.py tests/test_reporting.py` | **157 passed** (29.18s) |
| Complete suite | `pytest` (all tests) | **242 passed** (613.98s, exit 0) |

The 10 focused tests cover all required cases (§9):
- **A** `test_reopen_delivery_complete_happy_path` — COMPLETE happy path
- **B** `test_reopen_delivery_partial_happy_path` — PARTIAL happy path
- **C** `test_reopen_delivery_rejects_wrong_lifecycle` + `test_reopen_delivery_rejects_delivery_and_completion_sources` — wrong lifecycle rejected (RESEARCH/COMPLETION_CHECK/DELIVERY)
- **D** `test_reopen_delivery_rejects_missing_basis` — missing basis rejected
- **E** `test_reopen_delivery_rejects_revision_conflict` — revision conflict
- **F** `test_reopen_delivery_emits_audit_event` — audit event
- **G** `test_reopen_delivery_immediately_usable` — Delivery immediately usable (view, construction context, authoring context)
- **H** `test_reopen_delivery_preserves_research_state` — no Research rerun (no new CompletionCheck, no Research mutation, basis still references original check)
- **I** `test_reopen_delivery_fresh_report_attempt` — fresh report attempt (new Brief+Manuscript accepted, stale PASS not reusable, old rendered certification cannot publish new manuscript, normal new path works)

### 13. Did the schema version change?

**No.** The persistence format is unchanged. `report_session.json` still reports `schema_version: 6`. `state.json` structure is unchanged — `reopen_delivery` only mutates existing fields (`state_revision`, `lifecycle`, `outcome`) and appends an audit event. No new collections, no new basis type, no format change. **Strong preference for no schema bump — honored.**

### 14. Did ReportBrief / Reader / Integrity / Research semantics change?

**No.**
- **ReportBrief schema**: unchanged. The re-polished Brief used the same 5 lean fields (audience, promise, frame, arc, focus).
- **Reader semantics**: unchanged. Blind-read Phase 1 + attribution Phase 2 with `repair_target` PASS semantics intact.
- **Integrity semantics**: unchanged. `disposition: PASS` with empty issues.
- **Research semantics**: unchanged. No Research command was invoked; no paper/analysis/finding/gap was mutated; `completion_checks` untouched.
- **Completion semantics / Completion Checker authority**: unchanged. No new `CompletionCheck`; the original `CompletionPassBasis` remains authoritative.
- **Constructor / Authoring / math preflight / citation / source access / Deep Reading**: all unchanged.

The only code added is the `reopen_delivery` method (and its thin capability/CLI wiring). `certified_delivery.py`, `citations.py`, `reporting.py`, `artifacts.py`, `validation.py`, `model.py` were read but **not modified**.

### 15. Was any new lifecycle mode or architecture concept introduced?

**No.**
- The lifecycle remains exactly four modes: `RESEARCH`, `COMPLETION_CHECK`, `DELIVERY`, `CLOSED`.
- The allowed transitions are now: `RESEARCH→COMPLETION_CHECK`, `COMPLETION_CHECK→DELIVERY`, `DELIVERY→RESEARCH`, `DELIVERY→CLOSED`, **`CLOSED→DELIVERY`** (the one addition).
- No new mode, no report rerun concept, no report polishing mode, no delivery version DAG, no report branch, no delivery fork, no research clone, no completion reuse framework, no report-generation job.
- No new ADR was needed — the implementation revealed no genuinely new authority boundary. The transition reuses the existing `DeliveryBasis` authority and the existing certified-delivery digest binding; it introduces no new authority.
- The command is simply `reopen-delivery`, as specified.

---

## End-to-end demonstration (credit-allocation run)

The real run `run_2ca834ac-48de-4ce8-a325-bc70a7aa760f` (KV Cache optimization survey, 59 papers, COMPLETE) was exercised:

| Step | Revision | Lifecycle | Outcome | Audit event |
|---|---|---|---|---|
| (before) | 88 | CLOSED | COMPLETE | `run_closed` |
| `reopen-delivery` | **89** | **DELIVERY** | **None** | **`delivery_reopened`** (outcome=COMPLETE) |
| `put-report-brief` (fresh re-polished Brief) | 89 | DELIVERY | None | — |
| `put-report-manuscript` (re-polished, `{{cite:ID}}` placeholders + 14 declared citations) | 89 | DELIVERY | None | — |
| `submit-blind-review` (frozen) | 89 | DELIVERY | None | — |
| `submit-reader-review` (PASS) | 89 | DELIVERY | None | — |
| `submit-integrity-review` (PASS) | 89 | DELIVERY | None | — |
| `render-certified-report` | 89 | DELIVERY | None | — |
| `publish-certified-report` | 89 | DELIVERY | None | `report_published` (sha256 `8c63ddf8…`) |
| `close-run` | **90** | **CLOSED** | **COMPLETE** | `run_closed` (outcome=COMPLETE) |

**Invariants confirmed throughout:**
- `delivery_basis` byte-identical at rev 88, 89, 90: `{"completion_check_ref": "check_b7fb57de-b32a-45ae-aad3-838ef0d586d4", "type": "completion_pass"}`.
- `len(completion_checks) == 1` at rev 88, 89, 90 — no new CompletionCheck.
- New report `content_sha256` (`8c63ddf8…`) ≠ original (`fc727bda…`) — a genuinely new report version, not a re-certification of the old one.
- Published `report.md` (21095 bytes, pure LF) digest matches `report.meta.json` — integrity verified.
- The deterministic citation renderer auto-generated the `## References` section (10 entries) from the 14 declared `{{cite:ID}}` citations; the input manuscript correctly contained no hand-written References section.

The re-polished report reorganizes the KV Cache survey around a single discriminative axis ("which KV cache property does each route reduce"), with tightened cross-route comparability judgments and an explicit evidence-boundary statement — a genuine new Delivery pass on unchanged Research State.
