# Runtime Adapter API

Invoke commands through the single Python entrypoint:

```text
python "<SKILL_DIR>/scripts/harness.py" --workspace PATH COMMAND [OPTIONS]
```

`harness.py` resolves the bundled Runtime itself; if a Skill-local `.venv`
exists (created by `python scripts/setup.py`), it switches to that interpreter
automatically before executing the command, so all research logic runs through
one Python entry point with the bundled dependencies available. Commands are
otherwise platform-neutral — the same invocation works on Windows PowerShell,
Linux, and macOS. `CLAUDE_SKILL_DIR` is an optional override of the Skill root;
otherwise `harness.py` resolves it from its own location.

The adapter writes exactly one JSON object. Success goes to stdout with `"ok": true`.
Errors go to stderr with `"ok": false`, an error type, and a safe message; exit status is
nonzero and no traceback is printed. Credentials are never command arguments.

`configure-token` is a workspace-independent configuration command. It does not take
`--workspace`, `--run-id`, or `--expected-revision` because the DeepXiv credential is a
user-level install setting, not part of any ResearchRun. It prompts for the token with
`getpass` (no echo), stores it at `~/.literature-research/deepxiv-token`, and returns
only a success flag and the credential file path — never the token itself:

```text
python "<SKILL_DIR>/scripts/harness.py" configure-token
```

The Harness reads that file back when `DEEPXIV_TOKEN` is not already in the
process environment, so the DeepXiv providers read it from `DEEPXIV_TOKEN` as
before. An explicit `DEEPXIV_TOKEN` environment variable always takes
precedence and is never overwritten.

Commands that accept semantic structures use `--input FILE`. The file must contain one
JSON object with exactly the documented fields. This is typed command input, not JSON
Patch or raw state replacement. Write every input file inside the chosen `--workspace`:
pre-run inputs (before a `run_id` exists) go in `<workspace>/scratch/`, and per-run inputs
go in `<workspace>/scratch/<run_id>/inputs/`. Never point `--input` at `/tmp`, the project
root, the repository root, or anywhere under the Skill directory.

The adapter writes exactly one JSON object to stdout and nothing else. Capturing that
output to a file is an explicit caller choice, not the default; when you do, write it to
`<workspace>/scratch/<run_id>/captures/`. The `scratch/` tree is disposable working area,
not a second knowledge store — deleting it must not change `state.json`, `events.jsonl`,
or `artifacts/`.

## Environment and observation

```text
doctor
view --run-id RUN
inspect --run-id RUN --expected-revision REV --refs REF [REF ...]
audit-history --run-id RUN
wiki-query --query TEXT [--limit N]
```

`view` accepts optional `--input` containing a continuation object with
`state_revision`, `section`, and `after`. Wiki results are non-authoritative observations.

## Create and search

`create-run --input FILE`:

```json
{
  "mission": "Map the field",
  "requirements": ["Compare major routes", "Cover recent work"],
  "scope": "Peer-reviewed and arXiv primary literature",
  "deliverable_description": "Chinese technical-route survey",
  "required_artifacts": ["REPORT"]
}
```

`search-papers` requires `--run-id`, `--expected-revision`, `--query`; it supports
`--limit`, `--offset`, `--date-from`, and `--date-to`. Dates use `YYYY-MM-DD`. Search
output is observation-only, exposes the provider-reported `total_count`, and contains
all provider-neutral hit fields. Each hit exposes `publication_date` (`YYYY-MM-DD`) and
`publication_year` when available.

`retain-papers --input FILE` accepts either `{"hits": [...]}` or one complete prior
search result object containing a `hits` array. Each hit uses the fields emitted by
`search-papers`. Search hits remain observations. Retain changes state; search alone does
not add papers, while an explicit retain preserves both date fields on `PaperSource`.

## Web-discovered paper promotion

Claude Code native Web tools are host-level discovery capabilities, not Harness commands.
After `WebSearch` discovers a candidate, use `WebFetch` on its canonical arXiv,
OpenReview, DOI, or publisher page and construct the same provider-neutral hit shape for
`retain-papers`. Fill only metadata verified on that page; optional fields may be omitted.

```json
{
  "hits": [
    {
      "title": "SearchMaster: Grounded and Regulated Self-Play for Search Agents",
      "publication_year": 2026,
      "publication_date": "2026-08-03",
      "arxiv_id": "2608.01822",
      "canonical_url": "https://arxiv.org/abs/2608.01822",
      "other_identifiers": {},
      "categories": []
    }
  ]
}
```

This input promotes bibliographic identity into authoritative State; it does not promote
a Web snippet or page into technical evidence. Use `inspect-source` and `read-source`
before synthesis. Do not invent missing authors, dates, identifiers, abstracts, or venue
metadata, and do not create host-Web audit events in the Harness.

## Research evidence

```text
inspect-source --run-id RUN --expected-revision REV --paper-ref PAPER
read-source --run-id RUN --expected-revision REV --paper-ref PAPER
            [--locator-kind KIND --locator-value VALUE]
```

Source output is ephemeral and stays in context, never on disk. Persist selected
meaning with the following commands; do not write raw source text into `scratch/`.

`put-paper-analysis --input FILE`:

```json
{
  "paper_ref": "paper_...",
  "summary": "...",
  "relevance_to_run": "...",
  "contributions": ["..."],
  "key_results": ["..."],
  "limitations": ["..."],
  "key_locators": [{"kind": "section", "value": "Results"}]
}
```

`put-approach-family --input FILE` accepts `name`, `core_idea`,
`representative_paper_refs`, and optional `approach_ref` for update. Each representative
paper must be `ACTIVE` and have a `PaperAnalysis`; a RETIRED or unanalyzed paper is
rejected, so landscape evidence rests on primary-source understanding rather than search
metadata.

`merge-approach-family` uses `--target-approach-ref` and `--source-approach-ref`.

`put-finding --input FILE` and `put-open-problem --input FILE` accept `statement`,
optional `approach_refs`, optional `sources`, and optional existing `finding_ref` or
`problem_ref`. Each `source.paper_ref` must be `ACTIVE` and have a `PaperAnalysis`; a
RETIRED or unanalyzed source paper is rejected. A source object is:

```json
{
  "paper_ref": "paper_...",
  "relation": "SUPPORTS",
  "locator": {"kind": "section", "value": "Experiments"}
}
```

`retire-finding --finding-ref REF` and `retire-open-problem --problem-ref REF` retire
their targets.

`put-gap --input FILE` accepts `description`, optional `requirement_refs`, optional
`approach_refs`, and optional `gap_ref`. `resolve-gap` uses `--gap-ref` and
`--resolution`; `reopen-gap` uses `--gap-ref`.

`set-paper-status --paper-ref REF --status ACTIVE|RETIRED [--reason TEXT]` changes
explicit research status. `--status RETIRED` requires a non-empty `--reason`, which is
persisted as the paper's durable `retirement_reason` (why this run no longer investigates
or cites it) so a fresh Completion Checker and a resumed session can see candidate
closure. `--status ACTIVE` clears any prior `retirement_reason`. A paper still cited by
the landscape — as an ApproachFamily representative or a Finding / OpenProblem source —
cannot be retired until the referencing object is updated or retired first; the call is
rejected listing the referencing refs.

All research mutations also require `--run-id` and `--expected-revision`.

## Completion

```text
request-completion --run-id RUN --expected-revision REV --rationale TEXT
completion-view --run-id RUN
completion-inspect --run-id RUN --expected-revision REV --refs REF [REF ...]
completion-read-source --run-id RUN --expected-revision REV --paper-ref PAPER
                       [--locator-kind KIND --locator-value VALUE]
submit-completion --run-id RUN --expected-revision REV --input FILE
```

`request-completion` is rejected if any retained paper is `ACTIVE` without a
`PaperAnalysis` — the Reading Frontier must be closed first (analyze or retire each
unresolved candidate). This is a hard gate on corpus closure, not a paper count or
sufficiency score. The rejection lists the unresolved paper refs.

`completion-view` exposes the contract-facing projection: approach families, findings,
open problems, open gaps, and a `papers` tuple carrying every retained paper's closure
summary — `research_status`, `has_analysis`, and `retirement_reason` — not only the
representative papers. Detailed `PaperAnalysis` stays behind `completion-inspect`; the
per-paper summary lets the fresh checker judge candidate closure across the whole corpus.

Submission input contains `completion_check_ref`, `verdict`, `reasons`, and optional
`blocking_gaps`. A new blocking gap contains `description`, `requirement_refs`, and
`approach_refs`; a reopened gap contains only `gap_ref`.

## Delivery

```text
delivery-view --run-id RUN
delivery-inspect --run-id RUN --expected-revision REV --refs REF [REF ...]
delivery-read-source --run-id RUN --expected-revision REV --paper-ref PAPER ...
put-report-brief --run-id RUN --input FILE
put-report-manuscript --run-id RUN --input FILE
submit-blind-review --run-id RUN --input FILE
submit-reader-review --run-id RUN --input FILE
submit-integrity-review --run-id RUN --input FILE
render-certified-report --run-id RUN
publish-certified-report --run-id RUN --expected-revision REV
validate-delivery --run-id RUN
reopen-research --run-id RUN --expected-revision REV
close-run --run-id RUN --expected-revision REV
```

These staged commands are the only supported production path for a formal `REPORT`
artifact. The former direct `render-report` / `publish-report` commands are not exposed:
arbitrary text cannot cross the artifact boundary without matching current Reader and
Integrity certifications.

`put-report-brief` accepts the ADR-012 Brief shape. Python validates Requirement and
Research ref namespaces independently; a material `source_ref` must name a retained
paper, and a material `locator` requires `source_ref`. Accepting a new Brief binds it to
the current `DeliveryBasis` and invalidates all downstream work.

`put-report-manuscript` input contains `markdown` and optional `citations`. Markdown uses
tokens such as `{{cite:method}}`; each citation declares `citation_id`, `paper_ref`, and
optional `locator`. A new manuscript invalidates Blind, Reader, Integrity, and render
results.

`submit-blind-review` accepts `core_understanding`, `domain_model`,
`comparison_coordinates`, `reverse_outline`, `manuscript_digest`, and optional
`blocking_issues`. Each Blind issue has `problem`, `reader_effect`, `why_blocking`, and
optional `location`; it has no repair target. Python freezes the complete result and
returns `blind_read_digest`.

`submit-reader-review` accepts the returned `blind_read_digest`, current `brief_digest`,
current `manuscript_digest`, and Phase 2 `blocking_issues`. Each Phase 2 issue adds a
`repair_target` (`MANUSCRIPT`, `BRIEF`, or `POSSIBLE_RESEARCH_ISSUE`). A mismatched Blind
digest is rejected. `POSSIBLE_RESEARCH_ISSUE` returns a confirmation-required result and
does not change lifecycle; an actor with Research Authority must explicitly call
`reopen-research` after confirmation.

`submit-integrity-review` accepts `disposition`, `issues`, and optional `revise_target`.
It requires a matching current Reader PASS. `PASS` creates the version-bound Integrity
certification; `REVISE_DELIVERY` requires a `MANUSCRIPT` or `BRIEF` target;
`REOPEN_RESEARCH` confirms insufficiency but lifecycle transition remains the explicit
`reopen-research` command.

`render-certified-report` revalidates the current DeliveryBasis, Brief, Manuscript,
Reader PASS, and Integrity PASS before running the deterministic citation renderer.
`publish-certified-report` revalidates those dependencies and the rendered content at
the artifact boundary. Delivery session data and captures live under
`workspace/scratch/<run_id>/`; they are observable, removable work products and never
enter `state.json` or `ArtifactKind`. Validate the published artifact before closing.

## Wiki projection and publication

```text
wiki-projection
publish-wiki --input FILE
wiki-query --query TEXT [--limit N]
```

The Wiki is a rebuildable, non-authoritative Markdown projection of CLOSED+COMPLETE
runs, not a run artifact and not a second research runtime. It never enters the run
lifecycle and is not a required artifact. Only runs closed COMPLETE are eligible;
partial runs are excluded. Wiki failure never breaks a closed run: the run remains
CLOSED COMPLETE, the report remains valid, and any previously published Wiki is
preserved.

`wiki-projection` returns the current authoritative projection of eligible runs. It
omits process, delivery, and report data — it carries only approaches, findings, open
problems, and papers with stable refs. The projection also carries a top-level
`source_runs`: the `(run_id, state_revision)` identity of every eligible run at
projection time. Claude inspects the projection, synthesizes Wiki pages from it, and
performs the semantic review outside the harness. Preserve `source_runs` and pass it
back into `publish-wiki` as honest build provenance.

`publish-wiki --input FILE` accepts `source_runs` (the value returned by the
`wiki-projection` the pages were synthesized from) and the reviewed `pages`. Python
records `source_runs` verbatim in the manifest and publishes a versioned local build,
updating a `current.json` pointer atomically. Input shape:

```json
{
  "source_runs": [
    {"run_id": "run_...", "state_revision": 116}
  ],
  "pages": [
    {
      "slug": "methods",
      "title": "Methods",
      "markdown": "# Methods\n\nAccepted cross-run knowledge.",
      "contributing_refs": [
        {"run_id": "run_...", "research_ref": "finding_..."}
      ]
    }
  ]
}
```

`source_runs` is required and must be the `source_runs` value returned by the
`wiki-projection` the pages were built from. There is no silent fallback for input
that omits it: the adapter rejects the call so a stale interface is surfaced
explicitly.

A published Wiki may become stale if a newer run closes COMPLETE between projection
and publish (or after publish). That is **allowed, not rejected**: the manifest
honestly records the `source_runs` the pages were built from, even when it no longer
equals the current projection. Detect staleness with `is_current()` (manifest
`source_runs` equals the current projection's `source_runs`) or by re-running
`wiki-projection` and comparing. Rebuild when desired — there is no publish-time
exact-current rejection and no stale exception.

Slugs must be unique safe slugs (`[a-z0-9]+(?:-[a-z0-9]+)*`). Each page requires at
least one contributing ref pointing at a real approach, finding, open problem, or
paper in an eligible run. Markdown links must resolve to sibling pages or external
URLs; internal stable refs must not appear in prose. Invalid structure or provenance
raises `WikiBuildError` before any publication occurs, leaving any previous Wiki
intact. Semantic review is Claude's semantic orchestration, not a runtime command
field — do not call `publish-wiki` until the review passes.

`wiki-query --query TEXT [--limit N]` reads the currently published Wiki and returns
matching excerpts with their contributing refs. It is a non-authoritative observation
over the published projection; it never mutates run state.

## Revision failures

Most commands use optimistic `expected_revision`. Search and source access record an
external attempt before calling the provider, so their safe error JSON may include a
new `state_revision`. Use it or call the lifecycle-appropriate view before retrying.

A failed `search-papers` attempt also surfaces the provider's already-sanitized
`failure_kind` (`AUTHENTICATION`, `RATE_LIMIT`, `UNAVAILABLE`, `INVALID_RESPONSE`, or
`OTHER`) and a safe `reason` string in the error JSON, and records both in the
`paper_search_attempt` audit event. The reason is a description of what the provider
returned or how it failed (e.g. "invalid paper search provider response: top-level
status must be success"), never the raw provider response or HTTP body. The raw
response is not persisted into audit or error state.
