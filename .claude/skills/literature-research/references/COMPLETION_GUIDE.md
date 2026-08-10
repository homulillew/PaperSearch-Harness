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
the Researcher's search procedure.

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
   for the Contract's stated time-sensitive scope.
10. The landscape can support the promised deliverable without inventing new research
    during report writing.

Do not demand a fixed number of papers. A mature narrow topic may need fewer sources than
a broad fragmented field. Conversely, a large hit count does not establish coverage.

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
a P0 signal: name the paper and the missing primary-source grounding in the blocking
gap. A representative paper that has not been analyzed beyond its abstract is also a
blocking case when the contract requires mechanism-level understanding.

### UNCERTAIN

Use UNCERTAIN when the available state does not justify PASS, but the checker also cannot
form a defensible concrete repair plan. Explain the uncertainty. UNCERTAIN returns control
to Research without manufacturing blocking gaps.

## Submission discipline

Submit exactly one verdict for the pending CompletionCheck and current state revision.
PASS and UNCERTAIN must not contain blocking-gap payloads. CONTINUE must contain at least
one valid new or reopened blocking gap. Once submitted, the request metadata and completed
verdict are immutable facts.
