# Completion Guide

Completion is an independent verification boundary. The checker must be freshly created
after `request-completion` and must not inherit the Researcher's hidden chain of thought,
scratch material, or conversational confidence.

## Allowed evidence actions

The checker may only use:

- `completion-view` for the complete contract-facing projection;
- `completion-inspect` for exact retained objects behind stable references;
- `completion-read-source` for targeted verification of primary evidence;
- `submit-completion` to record one verdict.

It must not search, retain papers, add or edit analyses, create findings, resolve gaps,
write the report, or modify persistence. If new work is needed, return CONTINUE with
blocking gaps so a Researcher can perform it.

The checker may challenge current knowledge, but does not repair it. It evaluates the
knowledge and evidence exposed through its completion capabilities; it does not audit
the Researcher's search procedure. It must not audit WebSearch logs, rerun WebSearch,
or judge tool implementation details — it sees only Research State, the Completion
projection, and the evidence actions allowed above. Frontier coverage is judged from
open Gaps and retained evidence, not from the attempt history.

## Evaluation criteria

Evaluate the current contract revision, not an earlier version. Check all of the
following:

1. Each requirement is addressed by explicit structured research state.
2. Major technical routes within scope are represented and meaningfully distinguished.
3. Important claims have retained-paper provenance and suitable evidence locators.
4. Representative methods have been inspected deeply enough to support mechanism-level
   comparisons, not merely named from search results. A PaperAnalysis derived only
   from abstract or search/discovery metadata does not satisfy this criterion: it is
   a blocking deficiency, not a borderline case. If a representative paper carries
   detailed `key_results`, `contributions`, or `limitations` but has no corresponding
   `inspect-source`/`read-source` grounding, return CONTINUE with a blocking gap
   naming that paper and the missing primary-source evidence.
5. Experimental results preserve model, task, baseline, hardware, or other material
   conditions needed to interpret them.
6. Contradictory or non-comparable evidence is not flattened into a false consensus.
7. Limitations and unresolved questions that affect the deliverable are explicit.
8. Open Investigation Gaps do not block a contract requirement.
9. For latest/current/recent requests, the retained corpus and structured landscape
   demonstrate reasonable frontier coverage. Recent primary work supports the relevant
   route, trend, comparison, or open-problem claims, and the evidence is recent enough
   for the Contract's stated time-sensitive scope. An **attempted** counter-recall is
   not the same as **usable** frontier coverage: the checker must not PASS merely
   because a WebSearch or DeepXiv call was made. If State carries an open,
   contract-material independent frontier recall gap, that gap blocks PASS regardless of
   how many discovery calls were logged — judge from the Gap and the retained evidence,
   not from the attempt history. If the uncertainty was resolved by a usable equivalent
   discovery path and the Gap is closed (`resolve-gap`), judge normally.
10. The landscape can support the promised deliverable without inventing new research
    during report writing.
11. Candidate closure holds across the whole retained corpus. `completion-view` exposes
    every retained paper — not only representative ones — as a per-paper closure summary
    (`research_status`, `has_analysis`, `retirement_reason`). Judge the corpus, not just
    the read subset: every ACTIVE paper must have a `PaperAnalysis`, and every RETIRED
    paper must carry a `retirement_reason` defensible against the current Contract and
    landscape. The hard gate at `request-completion` already blocks the obvious failure
    (an ACTIVE paper with no analysis), so a paper reaching the checker as ACTIVE has
    been analyzed; the checker's job is the semantic judgment the gate cannot make —
    whether the analyzed set actually supports the landscape, and whether each retired
    paper's reason is honest (superseded, out of scope, duplicate, primary-source access
    failed with the gap recorded) rather than a rationalization for skipping work that
    mattered. For a retired paper whose loss could plausibly change a contract-facing
    judgment — a frontier/mechanism-novel candidate, a contradictory result, a
    representative of a route not otherwise covered — use `completion-inspect` /
    `completion-read-source` to spot-check that the retirement reason survives the
    evidence. This is risk-based, not exhaustive: a defensible retirement in a
    well-covered region needs no spot-check; a retirement that would change the
    landscape if wrong does.

Do not demand a fixed number of papers. A mature narrow topic may need fewer sources than
a broad fragmented field. Conversely, a large hit count does not establish coverage.
Candidate closure is about whether each retained paper is closed honestly, not whether
enough papers were retained.

An empty `key_locators` tuple or a null `LiteratureSource.locator` is not by itself a
failure. Locators are required where one meaningfully exists for the cited claim; their
absence is a signal to inspect the paper and confirm the grounding, not an automatic
block. The block is the missing primary-source evidence behind a detailed claim, not
the empty field.

## Verdicts

### PASS

Use PASS only when every contract requirement is adequately supported, remaining limits
are compatible with the deliverable, and no open gap blocks completion. Reasons should
summarize why the evidence is sufficient and note accepted boundaries.

### CONTINUE

Use CONTINUE when specific research work can repair the deficiency. Include one or more
blocking gap specifications that say what evidence or synthesis is missing and connect it
to affected requirement or approach references when possible. Do not prescribe a fixed
query; describe the knowledge deficit.

Examples include a missing major route, inadequate primary-source evidence, absent
frontier coverage, unresolved contradiction, or a representative paper whose
PaperAnalysis records detailed mechanism-level or empirical claims but rests only on
its abstract or search metadata rather than inspected primary source. The last case is
a blocking evidence signal: name the paper and the missing primary-source grounding in
the blocking gap. A representative paper that has not been analyzed beyond its abstract is also a
blocking case when the contract requires mechanism-level understanding.

A retired paper whose `retirement_reason` does not survive the evidence is also a
CONTINUE case — name the paper and why the retirement is indefensible (e.g., it
represents a route not otherwise covered, or its loss would change a contract-facing
judgment). The hard gate at `request-completion` already blocks ACTIVE+unanalyzed
papers, so this case concerns a RETIRED paper whose reason is a rationalization for
skipping material work, not a structurally unresolved one.

### UNCERTAIN

Use UNCERTAIN when the available state does not justify PASS, but the checker also cannot
form a defensible concrete repair plan. Explain the uncertainty. UNCERTAIN returns control
to Research without manufacturing blocking gaps.

## Submission discipline

Submit exactly one verdict for the pending CompletionCheck and current state revision.
PASS and UNCERTAIN must not contain blocking-gap payloads. CONTINUE must contain at least
one valid new or reopened blocking gap. Once submitted, the request metadata and completed
verdict are immutable facts.
