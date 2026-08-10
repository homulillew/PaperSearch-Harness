# Research Protocol

This protocol explains how a semantic Claude researcher uses the deterministic V1
Runtime. The Frozen Architecture and Domain Model remain the authority for runtime
semantics; this document supplies operating guidance, not a second workflow model.

## Contract formation

Translate the user's request into a Research Contract before searching. Requirements
must describe observable coverage or analysis obligations rather than a hidden process.
Examples include covering major technical routes, preserving deployment conditions,
contrasting conflicting results, and including developments within a stated recent
window. Scope must state meaningful exclusions. The deliverable says what the user will
receive and normally requires a report artifact.

Numbers of searches, papers, sections, or tokens are workload guidance, not proof that
the contract is satisfied. If the user provides ambiguous recency language, state a
reasonable date window and retain it in the contract or active gap.

## Adaptive outer loop

At each iteration, view authoritative state and choose the action that most reduces a
contract-relevant uncertainty. The loop is deliberately adaptive:

```text
observe state → select uncertainty → acquire evidence → retain → synthesize → reassess
```

Search is useful for discovery. Source inspection and reading establish evidence.
Structured synthesis makes knowledge recoverable. Completion is a separate judgment.

## Loop discipline

Distinguish a search loop from a research loop. A search loop is paging or reformulating
queries to gather candidates; a research loop is the adaptive cycle above, where each
turn starts from a specific uncertainty, acquires evidence for it, and updates State
before the next turn. Broad discovery may contain multiple search calls inside one
research iteration, but it must end by returning to State reassessment — not by proceeding
to a fixed analysis stage.

The failure mode is staged batching: search a broad batch → retain all → batch analysis →
one synthesis → request completion. That collapses the adaptive outer loop into a
pipeline and lets search volume substitute for judgment. To keep the loop real:

- After each meaningful evidence cluster, integrate durable State (PaperAnalysis,
  ApproachFamily, Finding, OpenProblem) before choosing the next action.
- Reassess the highest-value uncertainty against the updated State, and let that
  reassessment choose the next search or read — not the momentum of the previous step.
- Interleave discovery, Primary Source reading, synthesis, and reassessment throughout
  the run. Do not finish a broad discovery batch before beginning source reading.
- Completion is a feedback boundary, not a loop counter. A CONTINUE verdict names
  concrete blocking gaps and returns specific repair work to the loop; there is no
  forced number of iterations before completion is allowed.

This is a semantic policy, not a Runtime mechanism. The Harness does not detect loops,
count iterations, or gate completion on a fixed loop count.

## Discovery and frontier coverage

Use broad discovery to learn field vocabulary, seminal work, surveys, major mechanism
families, and known disagreements. Use multiple query formulations and routes: problem
terms, method names, benchmark names, influential authors, and limitations can expose
different literature.

For latest/current/recent requests, add frontier searches with explicit dates. Choose a
window appropriate to the field and user intent, then execute a Frontier Sweep:

```text
broad recent search → inspect terminology in recent hits → emerging-term expansion
→ route-specific recent searches → pagination → frontier-coverage reassessment
```

Do not build frontier queries only from paraphrases of the user's wording. Extract actual
method names, training algorithms, reward designs, benchmarks, mechanisms, new terms,
and authors when useful from recent hits and retained sources. Feed those terms back into
the next search round. A ranking without date filters is not evidence of recency coverage.

For broad discovery, request 30–100 results when provider cost permits. Tens of
deduplicated candidates are normal for a deep survey, but retain selectively and read
only the candidates that reduce a contract-relevant uncertainty. This preserves the
large observation funnel → selective retention → targeted reading relationship.

Treat provider-reported `total_count` as an observation for pagination planning. Compare
new unique identities, new routes, and recent-paper novelty across offsets. Continue
while marginal novelty remains useful; do not mechanically exhaust every page. Do not
sort one semantic page by date and claim it represents the globally latest literature.

Search results are observations. Retain only candidates useful for the current contract,
and do so explicitly. Arbitrary provider identifiers do not establish paper identity.

If a citation, related-work section, user-provided name, or known identifier identifies a
specific primary paper, try its exact title or arXiv ID for verification or targeted
discovery. Exact lookup does not replace broad search. Semantic search is not guaranteed
exhaustive; if a required recent window or known relevant paper cannot be retrieved,
record the provider limitation instead of silently claiming frontier completeness.

A field-level deep survey normally progresses from broad discovery to route discovery,
route-specific expansion, frontier search, and gap-driven follow-up. For a broad field,
multiple searches, tens of unique candidate observations, and multiple retained
representatives for important routes where useful are a normal workload. A genuinely
narrow scope may justify less exploration, but the Researcher should be able to explain
why further search is unlikely to reveal a major route, disagreement, or recent
development. No fixed paper count proves completion.

Search history helps the Researcher judge exploration quality and resume work. It is
operational evidence for planning, not an additional authority available to the
Completion Checker.

Before requesting Completion for a latest/recent task, the Researcher must be able to
state the newest retained relevant publication date, searched recent window, frontier
queries, terminology-driven follow-ups, whether pagination exposed newer work, whether
an independent Web counter-search ran, what it found beyond DeepXiv, and whether every
important Web discovery was retained and source-verified. This is a self-check against
the retained corpus and audit history. The fresh Completion Checker still sees only
retained state and structured synthesis.

## Independent frontier counter-recall

Keep DeepXiv as the primary scholarly discovery and source-access path. Use Claude Code
native `WebSearch` as an independent frontier counter-recall channel for recent papers,
terminology-shifted work, and known papers absent from semantic results. Explicit
latest/current/recent/SOTA intent requires both a DeepXiv recent sweep and at least one
independent Web frontier sweep. A suspicious gap between the current date and newest
retained relevant paper also blocks Completion until counter-recall is attempted.

Design Web queries from terminology learned during research rather than surface
paraphrases: method and model names, training algorithms, reward designs, mechanisms,
benchmarks, authors, and lab names can expose different candidate sets. Prefer scholarly
targets on arXiv and OpenReview. A publisher page or DOI is also suitable; a lab post,
repository, blog, news item, or social post is only a lead to a canonical paper.

Use `WebSearch` to discover scholarly URLs and `WebFetch` to verify canonical identity and
metadata. Confirm only fields actually present on the canonical page; leave unavailable
authors, dates, identifiers, or venue data empty instead of inferring them from memory,
an identifier, title, or snippet. Promote a selected candidate through the existing
provider-neutral `retain-papers` command. No Web observation creates State directly.

`WebSearch` results, snippets, and `WebFetch` pages are not research evidence. After
retention, use Harness `inspect-source` and `read-source`; only targeted primary-source
evidence may become `PaperAnalysis`, Findings, Landscape synthesis, or report claims.
Agreement between DeepXiv and Web discovery does not prove exhaustive coverage. A
Web-only relevant paper or disagreement between channels is a reason to continue.

Native Web calls belong to the Claude Code host. Do not manufacture Python resource
counters or audit events for them, and do not persist raw Web results. Resume from State,
gaps, retained papers, Landscape, and DeepXiv audit history; rerun only the Web search
needed to repair an unresolved frontier gap. Completion Checkers cannot use WebSearch or
broad discovery, and Integrity Reviewers cannot use snippets or non-primary Web pages to
support report claims.

If canonical Web discovery and retention succeed but DeepXiv source inspection fails,
record a primary-source provider coverage gap. Do not add a source fallback in this
workflow; source-provider expansion requires a separate architecture decision.

## Primary Evidence Gate

Search results, abstracts, and provider/Web metadata are **discovery only**. They
identify candidates; they do not establish the technical content of a survey. A
PaperAnalysis that records mechanism-level claims, empirical results, or detailed
comparisons must rest on primary-source evidence acquired through `inspect-source`
and `read-source` after retention — not on the abstract or search metadata that
selected the paper.

The gate is enforced at the point of writing `PaperAnalysis`:

- A paper selected only by abstract may be retained, but its PaperAnalysis must not
  contain mechanism-level or empirical claims that the abstract alone cannot support.
- Before `put-paper-analysis` records detailed `key_results`, `contributions`, or
  `limitations` for a representative paper, that paper must have been inspected and
  the relevant section read via the primary-source path.
- `key_locators` record where in the primary source a claim is grounded. Populate
  them when a locator meaningfully points to the supporting section, table, or
  figure; leave them empty when no targeted locator applies rather than inventing
  one. An empty locator is a signal to the Completion Checker that the claim's
  grounding needs inspection, not a mechanical failure by itself.
- Landscape Findings and Open Problems synthesize across papers. Their
  `LiteratureSource.locator` should carry a locator where one meaningfully exists
  for the cited relation; omitting it is acceptable only when no targeted locator
  applies, not as a default.

Abstract-derived analysis is the single most common cause of a shallow-evidence
PASS. If the only grounding for a detailed PaperAnalysis is the abstract or search
metadata, the correct action is to inspect and read the source before writing the
analysis — or to narrow the analysis to what the abstract can honestly support and
record the missing depth as an Investigation Gap.

## Evidence acquisition

Inspect a retained source before reading it. Select method, experiment, results,
limitations, appendix, or other targeted sections by provider-supported locator. Use a
full read only when targeted navigation cannot answer the current question.

Abstracts and provider summaries are selection aids. Do not use them as substitutes for
primary evidence when writing detailed technical claims. Retrieved source text is
ephemeral; durable knowledge belongs in typed state with stable paper references and
locators.

## Structured synthesis

Write PaperAnalysis only after primary-source evidence is sufficiently understood
(see Primary Evidence Gate). A PaperAnalysis derived only from abstract or search
metadata does not satisfy mechanism-level or empirical-evidence requirements.
Preserve:

- the paper's actual contribution;
- relevance to the current mission;
- key results and their experimental conditions;
- limitations and evidence boundaries;
- locators that permit later verification.

Group methods into ApproachFamily only when they share a meaningful core mechanism.
Use representative papers to make the family inspectable. Findings synthesize supported
technical judgments across papers. Open Problems describe unresolved domain questions.
Investigation Gaps describe research work still blocking this run.

Use `SUPPORTS`, `CHALLENGES`, and `QUALIFIES` deliberately. A source that demonstrates a
result in a narrow setting may qualify a broad claim rather than support it outright.

Synthesize throughout the run, inside each research iteration. Waiting until the end
creates an unrecoverable pile of ephemeral observations and encourages report-first
reasoning. Synthesis is what turns a search loop into a research loop.

## State and revision discipline

Every mutation is optimistic and revision-bound. Use the revision returned by the last
command. Search and source access count external attempts before making the call, so a
failed attempt can still produce a newer revision. Treat revision errors as a signal to
view state again, never as permission to alter persistence directly.

The run workspace is authoritative. Conversation history, scratch notes, and search hit
output are not. Resume by viewing state, inspecting stable references, reading current
open gaps, and consulting `audit-history` for external attempt parameters.

## Completion and delivery

The Researcher requests completion only after it can point to structured evidence for
each contract requirement and explain known limits. A fresh checker then follows
`COMPLETION_GUIDE.md`; it cannot search or mutate research state.

A PASS moves the run to Delivery. Delivery produces a report from current authorized
state, applies the separate Writing and Research Integrity guides to their respective
semantic stages, resolves citations deterministically, publishes the artifact, validates
it, and closes the run. Unsupported report content returns to Research instead of being
repaired through prose alone.

## Recovery checklist

On a resumed run:

1. Call the lifecycle-appropriate view command.
2. Record the current revision and current contract revision.
3. Inspect open gaps and latest completion feedback.
4. Inspect relevant approaches, findings, problems, and papers.
5. Call `audit-history` to review search terms, date filters, offsets, and failed attempts.
6. Select the next evidence need; do not repeat the previous session mechanically.
