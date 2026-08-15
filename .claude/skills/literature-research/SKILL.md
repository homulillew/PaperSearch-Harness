---
name: literature-research
description: Conduct deep, recoverable academic literature research with DeepXiv scholarly search, native Web frontier discovery, primary-source reading, explicit synthesis, independent completion checking, and cited report delivery.
---

# Literature Research

Use this skill when the user asks for an academic literature review, technical-route
survey, state-of-the-art analysis, research landscape, or evidence-backed technical
report. Claude is the semantic Researcher; the Python Harness is the deterministic
runtime for authority, persistence, stable references, provenance, accounting, and
validation.

Never edit `state.json`, event logs, report artifacts, or repository files directly.
All authoritative mutations must go through the Python harness entrypoint; host Web
observations remain ephemeral until promoted with `retain-papers`.

## Harness entrypoint

Invoke the harness through its single Python entrypoint. Resolve the installed
Skill directory, then run `scripts/harness.py` with the available Python
interpreter:

```text
python "<SKILL_DIR>/scripts/harness.py" --workspace PATH COMMAND [OPTIONS]
```

`harness.py` resolves the bundled Runtime itself and, if a Skill-local `.venv`
exists (created by `python scripts/setup.py`), switches to that interpreter
automatically before executing the command. The caller never needs to know the
venv interpreter path. `CLAUDE_SKILL_DIR` is an optional override of the Skill
root; otherwise `harness.py` resolves it from its own location. On Windows
PowerShell, Linux, and macOS the same command works; if the host exposes Python
as `py` rather than `python`, use that interpreter. See `RUNTIME_API.md` for the
command schemas.

## Read the protocol before acting

Read only the supporting material needed for the current stage:

- [Research protocol](references/RESEARCH_PROTOCOL.md): contracts, adaptive search,
  source reading, synthesis, recovery, and the outer loop.
- [Runtime API](references/RUNTIME_API.md): commands, JSON input shapes, revision
  handling, and error behavior.
- [Completion guide](references/COMPLETION_GUIDE.md): the independent completion
  boundary and PASS / CONTINUE / UNCERTAIN criteria.
- [Report writing guide](references/REPORT_WRITING_GUIDE.md): the authoritative style
  and editorial standard used by all semantic writing stages.
- [Research integrity guide](references/RESEARCH_INTEGRITY_GUIDE.md): evidence-strength,
  benchmark, comparison, causality, recency, and high-risk claim checks used by the
  independent Research Integrity Reviewer.

## Start from the request

Treat `$ARGUMENTS` as the user's research request. If it is empty, ask for the topic,
intended audience, scope, and desired deliverable. Otherwise infer a conservative
initial Research Contract and state the important assumptions before creating it.

Convert the request into:

1. a precise mission;
2. independently checkable requirements;
3. an explicit in-scope / out-of-scope boundary;
4. a deliverable description;
5. required artifacts, normally `REPORT` for a written survey.

Do not encode fixed paper counts as completion conditions. Workload expectations can
guide exploration, but coverage, evidence quality, recency, contradiction handling,
and requirement satisfaction determine completion.

Run the environment check first:

```text
python "<SKILL_DIR>/scripts/harness.py" --workspace workspace doctor
```

Create a run using a JSON file, not shell-escaped inline JSON. Write the
contract file inside the chosen workspace, never to `/tmp` or the Skill
directory:

```text
python "<SKILL_DIR>/scripts/harness.py" --workspace workspace create-run \
  --input workspace/scratch/research-contract.json
```

All research work files stay inside the chosen workspace. Nothing the
Researcher writes — command inputs, captured stdout, or scratch notes —
belongs in the project root, the repository root, or the Skill directory.
See the Filesystem discipline section below for the exact layout.

## Filesystem discipline

All research work files are confined to the chosen `--workspace`. The Harness
owns authoritative state; the Researcher owns disposable working files. Keep
the two apart so the project root, repository root, and Skill directory stay
clean and a run can be recovered from authoritative state alone.

```text
<workspace>/
├── runs/<run_id>/                 # Runtime-owned persisted run data
│   ├── state.json          # authoritative Research State — never hand-edited
│   ├── events.jsonl        # Runtime-owned audit history
│   ├── artifacts/          # Runtime-owned delivery artifacts (report, etc.)
│   └── delivery/
│       └── report_session.json  # Runtime-owned certification operations
└── scratch/
    ├── research-contract.json      # pre-run: the create-run input
    └── <run_id>/
        ├── inputs/                 # JSON files passed via --input
        └── captures/report/        # non-authoritative observability only
```

Rules:

- Write every `--input FILE` inside the workspace. Pre-run inputs (before a
  `run_id` exists) go in `<workspace>/scratch/`; per-run inputs go in
  `<workspace>/scratch/<run_id>/inputs/`.
- The Harness writes one JSON object to stdout and nothing else. Capturing
  that output to a file is an explicit Researcher choice, not the default;
  when you do, put it in `<workspace>/scratch/<run_id>/captures/`. Never
  capture into the project root or the repository root.
- `scratch/` is disposable working area, not a second knowledge store. Source
  output is ephemeral: convert the evidence that matters into a `PaperAnalysis`
  and discard the raw text. Deleting `scratch/` must not change `state.json`,
  `events.jsonl`, or `artifacts/`.
- `delivery/report_session.json` is Runtime-owned operational authority for report
  sequencing and certification. It must never be hand-edited by the Agent. It is not
  Research State, a ResearchRun field, an Artifact, or cross-run knowledge.
- Do not write research files to `/tmp`, the project root, the repository
  root, or anywhere under `<SKILL_DIR>`. The Skill directory ships read-only
  instructions and the Runtime; it never holds run data.

## Own the semantic outer loop

The Harness does not prescribe a fixed research finite-state machine. Claude owns an
adaptive outer loop:

```text
view current state
→ identify the highest-value uncertainty or gap
→ search, inspect, or read evidence for that uncertainty
→ retain selected papers and synthesize durable research objects
→ reassess coverage and contradictions against updated State
→ repeat, request completion, or explain a blocker
```

Each turn of this loop is one research iteration: it starts from a specific uncertainty,
acquires evidence for it, and updates State before the next turn. Do not run the loop as
a pipeline where a discovery phase feeds a fixed analysis phase. Choose the next action
from the reassessed State, not from the previous step's momentum.

After every state-changing command, use the returned `state_revision` for the next
command. On a revision conflict, discard the stale plan, call `view`, and reason again.

## Keep discovery inside the research loop

A search call is not a research iteration. For deep research, do not finish a broad
discovery batch before beginning source reading and synthesis. After each meaningful
evidence cluster: integrate durable State, reassess current uncertainty, and let that
updated State choose the next search or read action. Discovery, Primary Source reading,
synthesis, and reassessment should interleave throughout the run.

The failure mode to avoid is staged batching: search a broad batch → retain all → batch
analysis → one synthesis → request completion. That collapses the adaptive outer loop
into a pipeline and lets volume substitute for judgment. A broad discovery sweep may
contain multiple search calls inside one research iteration, but it must end by returning
to State reassessment, not by proceeding to a fixed analysis stage.

### Candidate Promotion Gate

Retention is a semantic promotion decision, not a batch dump. Between discovery and
`retain-papers`, apply a materiality gate: retain only candidates whose loss could
plausibly change a contract-facing judgment — a major route, a representative method, a
competing result, a deployment condition, a recent frontier development, or a direct
challenge to a current Finding. A paper that merely confirms what retained evidence
already establishes, or that sits in a crowded region of an already-covered route, may
be left as a search observation rather than promoted into durable State.

This is a semantic policy, not a Python score. The Harness has no `CandidateScore`, no
fixed retention ratio, and no coverage metric; it cannot judge materiality for you. The
gate exists so that every retained paper is one the Researcher is prepared to either
deep-read or explicitly retire — never one that sits unresolved because it was retained
on momentum. Retaining a paper is a commitment to close it (see Inspect before reading);
promote only what is worth that commitment.

If a newly material search result resolves to an already-RETIRED Paper, `retain-papers`
does not reactivate it automatically. Re-examine whether the updated State makes further
investigation necessary, and if it does, explicitly set the paper back to `ACTIVE` with
`set-paper-status` before deep-reading or citing it again.

## Search through independent discovery channels

Use DeepXiv through `search-papers` first. It remains the primary scholarly semantic
search, pagination, source inspection, and targeted reading path. Begin broadly, learn
canonical routes and terminology, then expand by mechanism and explicit date windows.

Use Claude Code native `WebSearch` as an independent frontier counter-recall channel,
not a second authoritative paper database. For explicit latest/current/recent/SOTA
requests, always perform at least one Web frontier counter-search in addition to a
DeepXiv recent sweep. Also use it when retained recency is suspiciously stale, recent
terminology shifts, or a known paper is missing from DeepXiv results.

```text
foundation and route discovery → DeepXiv recent sweep → emerging-term expansion
→ native WebSearch counter-search → WebFetch canonical scholarly page
→ retain selected identity → Harness source verification → gap-driven follow-up
```

Keep this adaptive. Generate Web queries from actual methods, algorithms, rewards,
benchmarks, mechanisms, authors, and emerging terms; use `site:arxiv.org` or
`site:openreview.net` when useful. Prefer native `WebSearch` and `WebFetch`; never call
search engines through Bash or custom APIs.

```text
python "<SKILL_DIR>/scripts/harness.py" --workspace workspace search-papers \
  --run-id RUN --expected-revision REV --query "QUERY" \
  --limit 20 --offset 0 --date-from YYYY-MM-DD --date-to YYYY-MM-DD
```

Search output includes provider-reported `total_count`, and hits include publication
date as well as year when available. Use `total_count`, new unique identities, route
novelty, and recent-paper novelty to decide whether to page; do not mechanically exhaust
all results. A first page is discovery input, not proof of coverage, and local date
sorting of retrieved candidates is not a global latest-paper search.

DeepXiv hits, WebSearch results/snippets, and WebFetch pages are observations, not
evidence. Use WebSearch to find scholarly URLs and `WebFetch` to verify canonical
metadata. Follow blogs, labs, repositories, or news to an arXiv, OpenReview, DOI, or
publisher paper page; never retain the lead or fabricate an unconfirmed field.

Promote an important Web-discovered candidate with the existing `retain-papers` input,
using only verified metadata. Then use `inspect-source` and `read-source` before formal
analysis or claims; WebFetch does not bypass evidence access. If DeepXiv cannot inspect a
retained Web-only paper, record a source coverage gap rather than inventing a fallback.

## External Discovery Failure Closure

A tool invocation is not a usable discovery outcome, and a usable discovery outcome
is not the same as the research objective being satisfied. When DeepXiv or native
`WebSearch` fails or returns a clearly inconclusive result, close the loop
explicitly before deciding whether the research uncertainty is resolved:

```text
failure / inconclusive outcome
→ bounded diagnosis / recovery (semantic, not a fixed retry count)
→ reassess the original research uncertainty
├── resolved → continue (no Gap)
└── unresolved + contract-material → put-gap (describe the research consequence)
```

A failed tool call does **not** automatically create an InvestigationGap. Only an
**unresolved, contract-material research consequence** becomes a Gap — describe the
research consequence, never the tool log. Tool-failure history stays in the host
observation stream and the DeepXiv audit log (`audit-history`), never in
`ResearchRun`. Persist decisions, not trajectories.

`WebFetch` of a known URL is canonical verification, **not** independent candidate
recall — fetching an already-known URL does not answer "are there material
candidates we do not already know?" But judge the action by whether it can reveal
unknown candidates, not by the tool name: a `WebFetch` that visits a
search/listing/index page and surfaces previously-unknown candidates is a discovery
observation.

See `references/RESEARCH_PROTOCOL.md` for provider-specific diagnosis and recovery
guidance (DeepXiv `failure_kind` semantics, WebSearch inconclusive diagnosis,
known-target control query, and Gap creation / resolution semantics).

## Inspect before reading

For a retained paper, call `inspect-source` first to obtain provider-supported sections.
Then call `read-source` with a targeted locator for methods, experiments, limitations,
or another specific need. Read the full source only when the structure is unavailable
or broad context is genuinely necessary.

This is the Primary Evidence Gate: a `PaperAnalysis` with mechanism-level claims,
empirical results, or detailed comparisons must rest on `inspect-source` /
`read-source` evidence, not on the abstract or search metadata that selected the
paper. A paper may be retained on abstract alone, but its detailed analysis may not
be written from the abstract. If primary-source access fails, record a source coverage
gap rather than manufacturing analysis from discovery metadata. `SourceContent` is
ephemeral; convert the evidence that matters into a concise `PaperAnalysis` with
useful `key_locators`, leaving them empty when no targeted locator applies.

### Deep Reading Control Loop

A retained paper has four states, and the loop must drive every retained paper to one
of the two closed states before Completion:

```text
SearchHit (ephemeral observation)
  → ACTIVE + analysis=None   (durable unresolved candidate)
  → ACTIVE + analysis         (integrated: PaperAnalysis written)
  → RETIRED + retirement_reason   (explicitly closed: why this run no longer uses it)
```

The Deep Reading Control Loop is how a paper moves out of the unresolved middle state.
From State N, identify the highest-value uncertainty in the current landscape, select
the retained paper (or papers) whose primary source would most reduce that uncertainty,
inspect and read it against that specific need, write the `PaperAnalysis`, and let the
updated Landscape (ApproachFamily, Finding, OpenProblem) change what the next
uncertainty is. Then reassess from State N+1 — the next read or search is chosen from
the reassessed landscape, not from the momentum of a discovery batch.

The unresolved candidates — `ACTIVE + analysis is None` — are the **Reading Frontier**.
It is a derived view of current State, not a persisted queue: the Harness has no
`ReadingTask`, `ReadingPlan`, or reading subsystem. At any moment the Frontier is
"every retained paper that is neither integrated nor retired." A material candidate on
the Frontier cannot silently disappear: it must end `ACTIVE+analyzed` or
`RETIRED+reason`. Retiring a paper is itself a research act — `set-paper-status --status
RETIRED --reason TEXT` records a durable `retirement_reason` (why this run no longer
investigates or cites it) so a fresh Completion Checker and a resumed session can see
candidate closure, not an unexplained gap.

Close the loop on every Frontier paper before requesting Completion. Either deep-read
and integrate it, or retire it with a defensible reason tied to the current Contract and
landscape. A paper left `ACTIVE + analysis=None` at Completion time is a structural
contradiction: State claims it belongs to the corpus, yet no paper-level understanding
has been formed. The Harness rejects `request-completion` while any ACTIVE paper lacks
a `PaperAnalysis` — analyze or retire it first.

## Synthesize periodically

Synthesis is part of each research iteration, not a final stage. After each meaningful
cluster of reading, update the structured landscape before choosing the next action:

- `PaperAnalysis` records what a retained paper contributes, shows, and fails to show.
- `ApproachFamily` groups methods sharing a real mechanism, not merely vocabulary.
- `Finding` records a supported cross-paper technical judgment.
- `OpenProblem` records an unresolved field-level question supported by the landscape.
- `InvestigationGap` records work still required for this run or contract.

An Open Problem belongs to the researched domain. A Gap belongs to the current research
process. Do not substitute one for the other.

Use source relations (`SUPPORTS`, `CHALLENGES`, `QUALIFIES`) and locators to preserve
evidence boundaries. Retire or update obsolete semantic objects through explicit
commands instead of silently changing their meaning.

A landscape object may only cite a paper that is `ACTIVE` and has a `PaperAnalysis`:
`put-approach-family` rejects a representative paper that is RETIRED or unanalyzed, and
`put-finding` / `put-open-problem` reject a `source.paper_ref` in the same state. This
keeps landscape evidence grounded in primary-source understanding rather than search
metadata. Conversely, a paper still cited by the landscape cannot be retired — update
the referencing ApproachFamily (re-issue `put-approach-family` with a revised
`representative_paper_refs`) or retire the referencing Finding / OpenProblem
(`retire-finding` / `retire-open-problem`) first, since removing those references is a
semantic decision the Harness must not make for you.

For a deep technical-route survey, normally seek multiple query formulations, more
than one representative method per major route where the literature permits it,
seminal and frontier evidence, competing results, deployment conditions, and explicit
unknowns. These are exploration heuristics, never mechanical completion thresholds.

For an explicitly deep or comprehensive technical-route survey, a corpus of only a
few investigated papers is normally insufficient unless the scope is genuinely narrow.
Tens of deduplicated search candidates across multiple adaptive searches are a normal
exploration scale for a broad field survey. Before requesting Completion from a much
smaller corpus, the Researcher must be able to explain from the Contract and current
landscape why the scope is narrow enough that additional search is unlikely to reveal
a major route, representative method, disagreement, or recent frontier development.
This is workload guidance, not a paper-count or Completion threshold.

Before requesting Completion for a latest/recent task, answer from the retained corpus:
the newest relevant paper date, the searched recent window, the frontier queries used,
whether emerging terminology triggered follow-up searches, whether pagination added
recent work, whether native Web counter-search found DeepXiv misses, and whether every
important Web discovery was retained and source-verified. This is a Researcher self-check;
the fresh Completion Checker judges only retained state and the structured landscape.

## Resume from authoritative state

When resuming, ignore conversational memory as authority. Recover from:

1. `view` for the current lifecycle, contract, revision, gaps, and landscape;
2. `inspect` for exact objects behind stable references;
3. targeted source reads when evidence must be rechecked;
4. `audit-history` for prior search queries, pagination, filters, and external attempts.

Raw Web results are not recoverable state. Do not repeat searches blindly, but rerun a
needed Web counter-search after resume when a frontier gap remains unresolved. The
`scratch/` directory is disposable: if it is missing or deleted, recover from the
authoritative `runs/<run_id>/` state alone — `view`, `inspect`, and `audit-history`
rebuild the working context without any scratch file.

## Request independent completion

When the Research Contract appears satisfied, call `request-completion`. Completion is a
feedback boundary, not a loop counter: a CONTINUE verdict names concrete blocking gaps
that the Researcher resolves by returning to the loop, and a PASS authorizes Delivery.
There is no forced number of research iterations before completion is allowed, and a
CONTINUE does not reset a counter — it returns specific repair work to the loop.

Before requesting completion, close the Reading Frontier: every retained paper must be
`ACTIVE+analyzed` or `RETIRED+reason`. The Harness enforces this as a hard gate —
`request-completion` is rejected while any ACTIVE paper lacks a `PaperAnalysis`, listing
the unresolved candidates. Analyze or retire them first. This is deterministic state
consistency, not a paper count or a sufficiency score: the gate asks "is the corpus
closed?" not "is the corpus large enough?"

Then create a fresh checker context that has not participated in the research loop and
follow [COMPLETION_GUIDE.md](references/COMPLETION_GUIDE.md).

The fresh Completion Checker may only:

- call `completion-view`;
- call `completion-inspect` for exact retained objects;
- call `completion-read-source` for targeted evidence verification;
- submit PASS, CONTINUE, or UNCERTAIN.

`completion-view` exposes every retained paper — not only representative ones — as a
per-paper closure summary (`research_status`, `has_analysis`, `retirement_reason`).
Detailed `PaperAnalysis` stays behind `completion-inspect`. This is deliberate: the
bad_case was a run where 49 retained-but-unanalyzed candidates were invisible to the
checker, which then judged only the 10 it could see. The fresh checker now sees the whole
corpus and must judge candidate closure — whether each retired paper's reason is
defensible and whether the analyzed set actually supports the landscape — not merely
whether the read papers were enough.

It must not search, retain papers, mutate research objects, or inherit the Researcher's
private reasoning. It must not use WebSearch or broad Web discovery. CONTINUE must
identify concrete blocking gaps; control then returns to Research. UNCERTAIN is used
when available evidence cannot justify either PASS or a specific repair plan. PASS alone
authorizes complete Delivery.

## Deliver a report

In DELIVERY, build the report from a fresh `delivery-view` and targeted
inspections. Load the four authority documents separately — each drives a
different role and must not be handed to a role it does not govern:

```text
${CLAUDE_SKILL_DIR}/references/REPORT_QUALITY_STANDARD.md   → Report Constructor
${CLAUDE_SKILL_DIR}/references/REPORT_WRITING_GUIDE.md       → Report Writer / Reviser
${CLAUDE_SKILL_DIR}/references/REPORT_REVIEW_GUIDE.md        → Report Reviewer (Reader Gate)
${CLAUDE_SKILL_DIR}/references/RESEARCH_INTEGRITY_GUIDE.md   → Research Integrity Reviewer
```

The Report Brief is the single report-semantic middle layer: it selects,
expands, organizes, and omits from accepted Research State for a target
audience. It is a Delivery work product, not a Research Domain entity — it is
never stored in the run, has no stable identity, and no `ArtifactKind`. The
Constructor reads the Quality Standard (not the Writing Guide); the Writer
reads the Writing Guide (not the Quality Standard). Keep these inputs
separated by role.

The semantic stages are an Action loop, not a Report FSM — no new lifecycle
mode is introduced, everything runs inside DELIVERY:

```text
Report Constructor → Report Brief
→ Report Writer → Manuscript
→ Report Reviewer (fresh instance, two-phase cold reading)
   Phase 1 Blind Read: deliverable + audience + quality standard + review guide
                       + manuscript — NO Brief, NO Writing Guide
   → frozen BlindReadResult (understanding + cognitive structure + reader failures,
                             bound to manuscript_digest; no repair targets)
   Phase 2 Brief Check: frozen blind_read_digest + Brief + manuscript + review guide
   → ReviewResult{blocking_issues}
   ├─ blocking_issues → earliest repair layer (most-upstream fault wins):
   │     MANUSCRIPT → Reviser → new manuscript → fresh Reader again
   │     BRIEF → Constructor → new Brief → Writer → new manuscript → fresh Reader again
   │     POSSIBLE_RESEARCH_ISSUE → escalate (Reader cannot mutate State)
   └─ blocking_issues == () → Reader PASS (brief_digest + manuscript_digest)
→ Research Integrity Reviewer (independent of the Reader Gate)
   → IntegrityReview{PASS | REVISE_DELIVERY(target=MANUSCRIPT|BRIEF) | REOPEN_RESEARCH}
   ├─ PASS → deterministic citation renderer → certified publication
   ├─ REVISE_DELIVERY → route to earliest faulty layer → Reader again → Integrity again
   └─ REOPEN_RESEARCH → reopen-research → return to the research loop
→ validate-delivery → close-run
```

A fresh Reviewer instance is created for every manuscript version (the
factory creates a new reviewer each revision), so it re-reads cold and cannot
confirm "you fixed what I asked." Phase 1 is frozen before Phase 2 sees the
Brief; Phase 2 must not rewrite or reinterpret the blind read. Blocking
issues are root-cause consolidated — one issue per cognitive root cause, no
score, no severity rank. The pipeline routes by precedence
(POSSIBLE_RESEARCH_ISSUE > BRIEF > MANUSCRIPT); Python routes, it does not rank "which
problem is worse."

A frozen Blind Read with any blocking issue cannot become a Phase 2 PASS. Phase 2 may
consolidate or reattribute those failures, but it must return at least one blocking
issue. A Reader or Integrity blocker also establishes a pending repair obligation:
`MANUSCRIPT` requires a new manuscript digest, `BRIEF` requires a new Brief digest,
`POSSIBLE_RESEARCH_ISSUE` pauses certification for Research-authority confirmation,
and `REOPEN_RESEARCH` requires the explicit lifecycle transition. Submitting different
PASS JSON for the same work-product versions never clears an obligation.

Use the production staged commands in this order:

```text
put-report-brief
→ put-report-manuscript
→ submit-blind-review
→ submit-reader-review
→ submit-integrity-review
→ render-certified-report
→ publish-certified-report
```

Claude supplies the semantic values; the Harness persists Runtime-owned operational
certification data at `runs/<run_id>/delivery/report_session.json` and enforces order,
Blind freezing, digest binding, repair obligations, freshness, and publication
authority. Non-authoritative captures remain under
`scratch/<run_id>/captures/report/`. There is no direct manuscript-to-publication
command. Never treat the delivery session as Research truth or edit it by hand.

Reader PASS certifies a specific `(brief_digest, manuscript_digest)` pair;
Integrity PASS certifies `(delivery_basis, brief_digest, manuscript_digest)`.
Any semantic edit to the Brief or manuscript invalidates the downstream
certification and forces a re-run of the affected gate. Render and publish
happen only when the current version carries BOTH a Reader PASS and an
Integrity PASS.

The Report Reviewer judges whether the report works as an article for the
target reader: whether the reader can form a continuous, stable, retellable
domain understanding from the prose alone. Sections must be driven by research
questions or judgments rather than paper order; taxonomy must state its
classification criterion; each paragraph should make one main judgment and
start with a self-contained claim; giant paragraphs, abstract-noun chains,
bureaucratic or translated prose should be repaired. Representative methods
must serve synthesis, carry the required first-use hyperlink, and lead
naturally from evidence to gaps. These are semantic judgments, not Python
paragraph-length or style validators.

The Research Integrity Reviewer is independent of the Reader Gate and checks
research fidelity, not prose: author claims versus independent evidence,
single-paper evidence versus consensus, correlation versus causation, ablation
versus causal mechanism, numerical gains versus statistical significance, SOTA
and generalization scope, benchmark validity, robustness and efficiency
dimensions, test-time compute/tool budgets, comparison fairness, recency and
absolute claims, corpus-bounded absence, and citation-to-claim alignment. It
uses the Integrity Guide as its rubric (not the Writing Guide) and returns the
typed integrity result without a numeric score. `REVISE_DELIVERY` must carry
the earliest faulty Delivery target (`MANUSCRIPT` or `BRIEF`); the pipeline
loops, re-running the Reader after any repair and then Integrity again.

Integrity may inspect Delivery state and retained objects, reread targeted
sources, and review the manuscript. It must not search broadly, retain papers,
mutate Research state, create Findings, or silently add evidence. Completion
asks whether Research State satisfies the Contract; Integrity asks whether the
report faithfully represents that accepted State. Keep these authority
boundaries separate.

Delivery can restore detail density and cognitive continuity, but it cannot
expand the accepted semantic scope: no new consensus, no stronger
generalization, no new approach relationship, no new Open Problem, no
contract-facing judgment. If a stage needs any of those, request confirmation from an
actor with Research Authority. A Reader's `POSSIBLE_RESEARCH_ISSUE` result does not
reopen Research. Only after confirmation may that actor explicitly call
`reopen-research` and return to the research loop; do not manufacture the judgment in
Delivery. Use `delivery-inspect` / `delivery-read-source` for targeted confirmation. If
Research is sufficient and the fault is in Delivery, submit a genuinely new Brief or
manuscript version and run the affected gates again; the same-version Reader PASS is
blocked.

When a retained primary paper has a canonical URL, hyperlink the first formal occurrence
of its method or system name to that URL. This navigation link never replaces a structured
citation. Keep citations close to the technical judgments they support.

Paragraph rhythm, natural Chinese, terminology, title density, and appropriate table use
are semantic editorial criteria. Do not ask the Harness to encode them as structural
validators. Preserve experimental conditions and qualifications while improving prose.

Use `render-certified-report` only after matching Reader and Integrity PASS, then call
`publish-certified-report`. Both commands revalidate the current version bindings;
neither accepts arbitrary report content. Only close after `validate-delivery` succeeds.

## Wiki orchestration after closure

After a run closes COMPLETE, project accepted cross-run knowledge into the Wiki. The
Wiki is a rebuildable, non-authoritative Markdown projection of CLOSED+COMPLETE runs,
not a run artifact and not a second research runtime: it never enters the lifecycle, is
not a required artifact, and its failure never breaks a closed run or invalidates the
report. Only CLOSED+COMPLETE runs are eligible; partial runs are excluded.

Call `wiki-projection` to read the current authoritative projection of eligible runs.
The projection carries `source_runs` — the `(run_id, state_revision)` identity of every
eligible run at projection time. Preserve it. Synthesize Wiki pages from that projection
— pages that synthesize accepted approaches, findings, and open problems across runs,
each carrying contributing refs to real research entities. Perform the semantic review
of those pages yourself, against the same projection; this is a Claude-side semantic act,
not a harness command field.

Then call `publish-wiki` with `source_runs` (preserved from the projection) and the
reviewed `pages`. Python validates structure and provenance deterministically and
publishes a versioned local build, recording `source_runs` verbatim in the manifest as
honest build provenance. A published Wiki may become stale if a newer run closes COMPLETE
afterwards; that is allowed — the manifest honestly records which run revisions produced
it. Detect staleness with `is_current()` (or by re-projecting and comparing) and rebuild
when desired. There is no publish-time exact-current rejection: a stale `source_runs` is
published, not refused.

Invalid structure or provenance raises `WikiBuildError` before publication, preserving
any previous Wiki. A rejected semantic review is your decision — do not call `publish-wiki`
until the review passes. Either way the run remains CLOSED COMPLETE and the report remains
valid. See `RUNTIME_API.md` for the input shape.

## DeepXiv credential setup

The DeepXiv credential is a user-level install setting, not part of any
ResearchRun. If `doctor` reports that the DeepXiv credential is missing, ask
the user to run:

```text
python "<SKILL_DIR>/scripts/harness.py" configure-token
```

The command stores the token at:

```text
~/.literature-research/deepxiv-token
```

Do not ask the user to modify shell profiles, registry settings, `.env` files,
workspace files, or Skill files. The Harness reads that file back when
`DEEPXIV_TOKEN` is not already in the process environment, so the DeepXiv
providers need no changes. An explicit `DEEPXIV_TOKEN` environment variable
always takes precedence and is never overwritten. After `configure-token`,
rerun `doctor` to confirm.

## Handle failures explicitly

The adapter emits one JSON object to stdout. Successful commands return `"ok": true`.
Failures return nonzero and machine-readable JSON on stderr without a stack trace.

External search and source attempts may advance the revision even when the provider
fails because resource usage is authoritative. Read the error's `state_revision` or
call `view` before retrying. Never replay with a stale revision.

A failed `search-papers` attempt surfaces the provider's already-sanitized
`failure_kind` and `reason` in the error JSON (e.g. `INVALID_RESPONSE` +
"invalid paper search provider response: top-level status must be success") and in
the `paper_search_attempt` audit event. Use the `failure_kind` for semantic diagnosis
(see External Discovery Failure Closure); the reason is a safe description of what the
provider returned, never the raw response or HTTP body. The raw provider response is
not persisted into audit or error state.

Do not print, persist, or include `DEEPXIV_TOKEN` in JSON input, audit rationale, reports,
examples, logs, or commits. The runtime reads it from the environment, which the
Harness bootstraps from the user-local credential file when the environment is unset.

## Finish visibly

At handoff, report the run ID, final lifecycle, delivered artifact path, major coverage,
known limitations, and whether completion was PASS, CONTINUE, or UNCERTAIN. If blocked,
name the exact missing environment capability or unresolved research gap.
