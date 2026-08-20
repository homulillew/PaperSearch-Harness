# Delivery-Only E2E Evaluation — Completion Report

**Run:** `run_2ca834ac-48de-4ce8-a325-bc70a7aa760f`
**Repository/branch:** `homulillew/PaperSearch-Harness` @ `feat/adr12-v04-report-information-architecture`
**Harness HEAD at run start:** `5947804` (cleanup patch; fetched latest remote before run)
**Evaluation type:** Delivery-only E2E (reopen-delivery → full Delivery process → close). NOT an implementation task.
**Date:** 2026-08-19/20

This report records facts only. It does not compare the new report to the old report, does not
state whether the new report is better or worse, and proposes no changes to the Writing Guide,
Constructor, Reader, or any system component. No Runtime, Skill, Guide, test, schema, or
architecture was modified during this run.

---

## 1. Target run selection

A previously CLOSED/COMPLETE run with a valid DeliveryBasis, non-trivial Research State, and a
REPORT deliverable was selected: `run_2ca834ac-48de-4ce8-a325-bc70a7aa760f`. Pre-reopen state
(captured to `.scratch_e2e/.../pre_reopen_snapshot.json`): `lifecycle=CLOSED`,
`outcome=COMPLETE`, `state_revision=88`, `delivery_basis={type: completion_pass,
completion_check_ref: check_b7fb57de-b32a-45ae-aad3-838ef0d586d4}`, exactly 1 CompletionCheck
(verdict PASS, ref `check_b7fb57de...`), 59 papers all `research_status=ACTIVE`, 0
investigation_gaps, Contract `current_revision=1`.

## 2. Old report not used as authoring input

The prior report was snapshotted to scratch (`.scratch_e2e/.../old_report.md` +
`old_report.meta.json`) for observational record only. It was NOT supplied to the Report
Constructor, Authoring, Fresh Reader, or Integrity Reviewer as a reference, example, style
target, or revision source. The only semantic inputs to each Delivery role were the persisted
Research State / Delivery context, the Research Contract, and the current Construction /
Writing / Review / Integrity Guides.

## 3. Starting state verification

Pre-reopen invariants recorded (item 1). DeliveryBasis was `completion_pass` bound to
`check_b7fb57de...`; Research State (59 ACTIVE papers, 8 ApproachFamilies, 0 gaps) and the
single PASS CompletionCheck were intact.

## 4. reopen-delivery

`reopen-delivery` succeeded. Post-reopen state (`.scratch_e2e/.../post_reopen_snapshot.json`):
`lifecycle=DELIVERY`, `outcome=None`, `state_revision=89`. DeliveryBasis, the Research State
(59 ACTIVE papers, 8 ApproachFamilies, 0 gaps), and the single PASS CompletionCheck
(`check_b7fb57de...`) were unchanged. Event `delivery_reopened` recorded at rev 89.

## 5. Normal Delivery process from report-construction-input

`report-construction-input` captured to `.scratch_e2e/.../construction_input.json`. It
provided the construction context (Deliverable description, Contract requirements, Research
State summary, ApproachFamilies, paper inventory metadata) without exposing paper raw-evidence
locators to the Constructor.

## 6. Report Constructor — Brief decision

Acting as Constructor per the Report Construction Guide v0.6.3, the Brief was designed from
the construction context + Construction Guide only (no old report, no external instructions).
The Brief is the lean 5-field form (audience, promise, frame, arc, focus) — editorial intent
only; it contains no headings, section order, paragraph plans, or paper checklists. Submitted
via `put-report-brief`; the certified-delivery session was reset (manuscript/blind_read/
reader_pass/integrity_pass/rendered nulled) and `brief_digest` bound. Brief persisted to
`.scratch_e2e/.../brief_input.json`; `brief_digest=45f1d534990c538ddf031cc455fb39cf254a3f01f5a7caa437d91d6f8f490a74`.

## 7. Authoring from Brief

`report-authoring-context` captured to `.scratch_e2e/.../authoring_context.json`. Acting as
Authoring per the Report Writing Guide v0.6.3, the manuscript was authored from the Brief +
authoring context + Writing Guide only. Authoring owned title, ATX headings (naming themes),
section order, paragraphs, and paper selection. Idea-first then papers; presentation form
chosen by information shape (prose + one comparison table + bulleted open problems). Targeted
`delivery-inspect` (8 ApproachFamilies → 10 representative papers; then 10 papers for metadata
+ PaperAnalysis) and `delivery-read-source` (RocketKV Experiments section, to verify the
primary-source path) were used only where Authoring determined a concrete evidentiary need.
All 10 citation declarations point to section-level locators in primary-source PaperAnalysis
(not abstracts). Manuscript persisted to `.scratch_e2e/.../manuscript_input.json`;
`manuscript_digest=858a37b257dcffc5dfbc16b24d3f3071b122988f80a43c35bc0d981063320233`.

## 8. Presentation preflight (not bypassed)

`put-report-manuscript` ran the full Presentation preflight: `_validate_presentation_structure`
(delimiter invariants) → `_validate_math_renderability` (MathJax via `math_preflight`) →
citation/token validation. All passed; the manuscript was accepted.

**E2E observation (deterministic invariant, not a bug/crash):** the deterministic brace check
in `citations.py` (`_validate_presentation_structure`, L102-117) strips `{{cite:...}}` and
`{{paper:...|...}}` tokens but does NOT strip math spans, so any `}}` substring inside a TeX
expression triggers "unsupported deterministic presentation token" rejection even though
MathJax renders it correctly. An initial TeX form `2^{4}\cdot C_{U}^{\text{INT4}} +
C_{L}^{\text{INT4}}` (contains `}}` from `\text{INT4}` close-brace immediately followed by the
superscript group close-brace) was rejected. Authoring rewrote it to
`2^{4}\cdot C_U^{\rm INT4} + C_L^{\rm INT4}` (`\rm` is a switch, not a braced argument, so no
`}}` adjacency) — a legitimate Authoring presentation choice within the Writing Guide's math
rules. This is a real deterministic invariant of the current production Harness. It was
recorded, not fixed, per the no-modification constraint.

## 9. Fresh Reader review (Phase 1 blind + Phase 2 brief-check)

Per the Report Review Guide v0.6.2. `render-reader-preview` produced the rendered reader
surface (`.scratch_e2e/.../reader_surface_rendered.md`).

- **Phase 1 blind read** (`submit-blind-review`): the Reader received only the Deliverable
  description / Audience / Review Guide / rendered manuscript — NOT the Brief, Writing Guide,
  or Research State. `received_understanding` recorded a detailed formed understanding (three
  design axes, per-route mechanisms with deployment conditions, cross-route trade-offs, open
  frontier, 2026 frontier). `blocking_issues=[]`. `blind_read_digest=c1e57c4108e6afed4427df11788c3a264eb81cb5f9b10a9c711f861bc8690b86`.
- **Phase 2 brief-check** (`submit-reader-review`): the Reader received the frozen blind read
  + Brief + Contract + Review Guide (NOT the manuscript/surface). `repair_target=null`
  (PASS). Rationale: blind-read formed understanding matched the Brief's declared cognitive
  path and covered all 6 Contract requirements; no Brief-omitted delivery concern; no
  manuscript-vs-Brief structural divergence. **Zero repair cycles.** `reader_pass` bound to
  `brief_digest` + `manuscript_digest`. Stage advanced to `READER_PASS`.

## 10. Research Integrity review

Per the Research Integrity Guide v0.6 (24 sections). The manuscript's important judgments were
examined against Research State + Primary Evidence strength. Findings: every empirical claim is
attributed to its single paper (author-report tier) and not written as field consensus; no
unscoped SOTA; no "significant" without a statistical test; "证明" reserved for KQ-SVD's formal
theorems (定理 1/2/4) and CAKE's cascade-allocation equivalence — empirical results use
"达到"/"优于"; all numbers carry deployment conditions (models, budgets, hardware) and trace to
PaperAnalysis key_results with section-level locators; the cross-route comparison table is
framed as making trade-offs comparable, not as a ranking; open problems are each tied to
specific retained evidence and framed as corpus-bounded. Judgment strength matched evidence
strength throughout. **Disposition: PASS**, `issues=[]`. Submitted via
`submit-integrity-review`; `integrity_pass` bound to `brief_digest` + `manuscript_digest`.
Stage advanced to `INTEGRITY_PASS`. Matching Reader+Integrity PASS achieved.

## 11. Certification, publication, validation, close

With matching Reader+Integrity PASS:

- `render-certified-report` → succeeded; rendered content returned,
  `content_sha256=a7c58e6ef0809c93e6f13fc40b08f97a4b460d64164d3e7491cd5dbc53862953`,
  `state_revision` 89→90.
- `publish-certified-report --expected-revision 90` → succeeded. Published artifact at
  `workspace/runs/.../artifacts/report.md` (19220 bytes) + `artifacts/report.meta.json`.
  `delivery_basis` preserved (`completion_check_ref: check_b7fb57de...`). Event
  `report_published` at rev 90.
- `validate-delivery` → succeeded. `validated_artifacts=["REPORT"]`, basis preserved.
- `close-run --expected-revision 90` → succeeded. `outcome=COMPLETE`, `state_revision=91`.
  Event `run_closed` at rev 91.

## 12. No code changes during experiment

No Runtime, Skill, Guide, test, schema, or architecture file was modified. The stale plan
file `C:\Users\wushuhong\.claude\plans\lexical-gathering-milner.md` (a math-preflight
implementation plan) was explicitly disregarded because it conflicts with the E2E's
no-modification constraint. `git status` shows only workspace run artifacts
(`state.json`, `events.jsonl`, `artifacts/report.md`, `artifacts/report.meta.json`, new
`delivery/` dir) and untracked scratch dirs (`.scratch_e2e/`, `.scratch_repolish/`,
`workspace/scratch/...`) — all left unstaged; nothing committed.

## 13. Observability / scratch boundary

All E2E inputs and intermediate captures were kept under
`.scratch_e2e/run_2ca834ac-48de-4ce8-a325-bc70a7aa760f/` (NOT committed; gitignored scratch).
The old-report snapshot there was never exposed to any semantic Delivery role.

## 14. Final state verification

- `lifecycle=CLOSED`, `outcome=COMPLETE`, `state_revision=91`.
- DeliveryBasis unchanged: `{type: completion_pass, completion_check_ref:
  check_b7fb57de-b32a-45ae-aad3-838ef0d586d4}`.
- Exactly 1 CompletionCheck (count=1), verdict PASS, ref `check_b7fb57de...` — **no new
  CompletionCheck** was created during the Delivery-only run.
- Contract `current_revision=1` unchanged; deliverable = Chinese-language KV Cache
  optimization survey, `required_artifacts=["REPORT"]`, 6 requirements.
- 59 papers, all `research_status=ACTIVE`; 0 investigation_gaps.
- `report_session.json` `schema_version=6` (not bumped); `rendered` bound to
  brief+manuscript digests; `reader_pass` and `integrity_pass` both bound; `pending_action=NONE`;
  `delivery_basis_key` preserved.

## 15. E2E outcome

The Delivery-only E2E completed end-to-end through the normal Delivery process
(reopen-delivery → construction-input → Brief → authoring-context → primary-source evidence →
manuscript → Presentation preflight → Reader Phase 1 + Phase 2 → Integrity → render → publish
→ validate → close) and reached CLOSED/COMPLETE with the original DeliveryBasis intact and no
new CompletionCheck. Reader PASS was achieved with zero repair cycles; Integrity returned
PASS (not REVISE_DELIVERY, not REOPEN_RESEARCH). One deterministic invariant was observed and
recorded (math-`}}` adjacency rejection in the presentation brace check; satisfiable by
Authoring TeX-notation choice; not fixed per the no-modification constraint). No Harness bug,
crash, impossible transition, packaging failure, or deterministic-invariant problem prevented
completion.
