#!/usr/bin/env python3
"""Machine-readable Claude Code adapter over public V1 capabilities."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, NoReturn, TextIO


def _skill_dir() -> Path:
    configured = os.environ.get("CLAUDE_SKILL_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _reexec_skill_venv() -> None:
    # If a Skill-local .venv exists and we are not already running under it,
    # re-exec this script under the venv interpreter so the bundled runtime
    # and its dependencies (e.g. deepxiv-sdk) are available without the caller
    # having to know the venv path. Cross-platform: Windows uses
    # Scripts\python.exe, POSIX uses bin/python. Mirrors doctor.py's logic.
    skill = _skill_dir()
    candidates = (
        skill / ".venv" / "Scripts" / "python.exe",
        skill / ".venv" / "bin" / "python",
    )
    venv_python = next((path for path in candidates if path.is_file()), None)
    if venv_python is None:
        return
    if Path(sys.executable).resolve() == venv_python.resolve():
        return
    # os.execv replaces the process cleanly on POSIX. On Windows execv does not
    # fully replace the running image, so spawn the venv Python as a child and
    # forward its exit code, preserving all original argv.
    if os.name == "posix":
        os.execv(
            str(venv_python),
            [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        )
    else:
        import subprocess

        result = subprocess.run(
            [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]
        )
        raise SystemExit(result.returncode)


def _bootstrap_runtime() -> None:
    skill = _skill_dir()
    bundled = skill / "runtime" / "src"
    project = skill.parents[2] / "src" if len(skill.parents) >= 3 else Path()
    for candidate in (bundled, project):
        if (candidate / "my_search_harness").is_dir():
            sys.path.insert(0, str(candidate))
            return


_bootstrap_runtime()

from my_search_harness.domain import (  # noqa: E402
    ArtifactKind,
    CompletionVerdict,
    LiteratureSource,
    PaperAnalysis,
    PaperResearchStatus,
    SourceLocator,
    SourceRelation,
)
from my_search_harness.runtime import (  # noqa: E402
    CitationReference,
    BlockingGapSpec,
    ContextContinuation,
    ContextSection,
    CreateRunRequest,
    DeterministicCitationRenderer,
    LocalAuditLog,
    LocalV1Runtime,
    NewBlockingGap,
    PaperSearchHit,
    PutPaperAnalysis,
    ReopenBlockingGap,
    ReportManuscript,
    ResearchMutationBatch,
    WikiPageDraft,
    WikiProvenanceRef,
    WikiRunVersion,
)


class AdapterInputError(ValueError):
    """The caller supplied malformed typed command input."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise AdapterInputError(message)


RuntimeFactory = Callable[[Path, bool], LocalV1Runtime]


def _default_runtime_factory(workspace: Path, external: bool) -> LocalV1Runtime:
    audit = LocalAuditLog(workspace / "runs")
    if external:
        return LocalV1Runtime.from_deepxiv_env(workspace, audit_sink=audit)
    return LocalV1Runtime(
        workspace,
        paper_search_provider=None,
        source_access_provider=None,
        audit_sink=audit,
    )


def _jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return [_jsonable(item) for item in sorted(value, key=str)]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _emit(stream: TextIO, payload: Mapping[str, object]) -> None:
    stream.write(json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True))
    stream.write("\n")


def _safe_message(exc: Exception) -> str:
    message = str(exc) or exc.__class__.__name__
    token = os.environ.get("DEEPXIV_TOKEN")
    if token:
        message = message.replace(token, "[REDACTED]")
    return message


def _load_input(path: str) -> dict[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AdapterInputError(f"input file not found: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdapterInputError(f"input file is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise AdapterInputError("input JSON must be an object")
    return value


def _shape(
    value: Mapping[str, object],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing:
        raise AdapterInputError(f"missing input fields: {', '.join(sorted(missing))}")
    if unknown:
        raise AdapterInputError(f"unknown input fields: {', '.join(sorted(unknown))}")


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdapterInputError(f"{name} must be a non-empty string")
    return value


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise AdapterInputError(f"{name} must be an array of non-empty strings")
    return tuple(value)


def _optional_strings(value: Mapping[str, object], name: str) -> tuple[str, ...]:
    raw = value.get(name, [])
    return _strings(raw, name)


def _locator(value: object, name: str = "locator") -> SourceLocator | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AdapterInputError(f"{name} must be an object or null")
    _shape(value, required=frozenset({"kind", "value"}))
    return SourceLocator(
        kind=_string(value["kind"], f"{name}.kind"),
        value=_string(value["value"], f"{name}.value"),
    )


def _locator_args(args: argparse.Namespace) -> SourceLocator | None:
    if args.locator_kind is None and args.locator_value is None:
        return None
    if args.locator_kind is None or args.locator_value is None:
        raise AdapterInputError(
            "locator-kind and locator-value must be provided together"
        )
    return SourceLocator(kind=args.locator_kind, value=args.locator_value)


def _literature_source(value: object) -> LiteratureSource:
    if not isinstance(value, dict):
        raise AdapterInputError("sources entries must be objects")
    _shape(
        value,
        required=frozenset({"paper_ref", "relation"}),
        optional=frozenset({"locator"}),
    )
    try:
        relation = SourceRelation(_string(value["relation"], "relation"))
    except ValueError as exc:
        raise AdapterInputError(
            "relation must be SUPPORTS, CHALLENGES, or QUALIFIES"
        ) from exc
    return LiteratureSource(
        paper_ref=_string(value["paper_ref"], "paper_ref"),
        relation=relation,
        locator=_locator(value.get("locator")),
    )


def _sources(value: Mapping[str, object]) -> frozenset[LiteratureSource]:
    raw = value.get("sources", [])
    if not isinstance(raw, list):
        raise AdapterInputError("sources must be an array")
    return frozenset(_literature_source(item) for item in raw)


_HIT_FIELDS = frozenset(
    {
        "title",
        "authors",
        "publication_year",
        "publication_date",
        "doi",
        "arxiv_id",
        "canonical_url",
        "other_identifiers",
        "abstract",
        "provider_summary",
        "provider_score",
        "citation_count",
        "categories",
    }
)


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise AdapterInputError(f"{name} must be a non-empty string or null")
    return value


def _optional_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise AdapterInputError(f"{name} must be an integer or null")
    return value


def _integer(value: object, name: str) -> int:
    result = _optional_int(value, name)
    if result is None:
        raise AdapterInputError(f"{name} must be an integer")
    return result


def _optional_float(value: object, name: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AdapterInputError(f"{name} must be a number or null")
    return float(value)


def _hit(value: object) -> PaperSearchHit:
    if not isinstance(value, dict):
        raise AdapterInputError("hits entries must be objects")
    _shape(value, required=frozenset({"title"}), optional=_HIT_FIELDS - {"title"})
    other = value.get("other_identifiers", {})
    if not isinstance(other, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in other.items()
    ):
        raise AdapterInputError("other_identifiers must map strings to strings")
    return PaperSearchHit(
        title=_string(value["title"], "title"),
        authors=_strings(value.get("authors", []), "authors"),
        publication_year=_optional_int(
            value.get("publication_year"), "publication_year"
        ),
        publication_date=_optional_string(
            value.get("publication_date"), "publication_date"
        ),
        doi=_optional_string(value.get("doi"), "doi"),
        arxiv_id=_optional_string(value.get("arxiv_id"), "arxiv_id"),
        canonical_url=_optional_string(value.get("canonical_url"), "canonical_url"),
        other_identifiers=other,
        abstract=_optional_string(value.get("abstract"), "abstract"),
        provider_summary=_optional_string(
            value.get("provider_summary"), "provider_summary"
        ),
        provider_score=_optional_float(value.get("provider_score"), "provider_score"),
        citation_count=_optional_int(value.get("citation_count"), "citation_count"),
        categories=_strings(value.get("categories", []), "categories"),
    )


def _analysis(value: Mapping[str, object]) -> PaperAnalysis:
    locators_raw = value.get("key_locators", [])
    if not isinstance(locators_raw, list):
        raise AdapterInputError("key_locators must be an array")
    locators = tuple(_locator(item, "key_locators entry") for item in locators_raw)
    if any(item is None for item in locators):
        raise AdapterInputError("key_locators entries cannot be null")
    return PaperAnalysis(
        summary=_string(value["summary"], "summary"),
        relevance_to_run=_string(value["relevance_to_run"], "relevance_to_run"),
        contributions=_optional_strings(value, "contributions"),
        key_results=_optional_strings(value, "key_results"),
        limitations=_optional_strings(value, "limitations"),
        key_locators=tuple(item for item in locators if item is not None),
    )


def _manuscript(value: Mapping[str, object]) -> ReportManuscript:
    _shape(
        value,
        required=frozenset({"markdown"}),
        optional=frozenset({"citations"}),
    )
    raw_citations = value.get("citations", [])
    if not isinstance(raw_citations, list):
        raise AdapterInputError("citations must be an array")
    citations: list[CitationReference] = []
    for raw in raw_citations:
        if not isinstance(raw, dict):
            raise AdapterInputError("citations entries must be objects")
        _shape(
            raw,
            required=frozenset({"citation_id", "paper_ref"}),
            optional=frozenset({"locator"}),
        )
        citations.append(
            CitationReference(
                citation_id=_string(raw["citation_id"], "citation_id"),
                paper_ref=_string(raw["paper_ref"], "paper_ref"),
                locator=_locator(raw.get("locator")),
            )
        )
    return ReportManuscript(
        markdown=_string(value["markdown"], "markdown"),
        citations=tuple(citations),
    )


def _add_run_revision(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-revision", required=True, type=int)


def _add_source_args(parser: argparse.ArgumentParser) -> None:
    _add_run_revision(parser)
    parser.add_argument("--paper-ref", required=True)
    parser.add_argument("--locator-kind")
    parser.add_argument("--locator-value")


def _parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="workspace")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor")

    create = commands.add_parser("create-run")
    create.add_argument("--input", required=True)

    for name in ("view", "completion-view", "delivery-view"):
        item = commands.add_parser(name)
        item.add_argument("--run-id", required=True)
        if name == "view":
            item.add_argument("--input")

    for name in ("inspect", "completion-inspect", "delivery-inspect"):
        item = commands.add_parser(name)
        _add_run_revision(item)
        item.add_argument("--refs", nargs="+", required=True)

    search = commands.add_parser("search-papers")
    _add_run_revision(search)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--offset", type=int, default=0)
    search.add_argument("--date-from")
    search.add_argument("--date-to")

    retain = commands.add_parser("retain-papers")
    _add_run_revision(retain)
    retain.add_argument("--input", required=True)

    for name in (
        "inspect-source",
        "read-source",
        "completion-read-source",
        "delivery-read-source",
    ):
        item = commands.add_parser(name)
        _add_source_args(item)

    for name in (
        "put-paper-analysis",
        "put-approach-family",
        "put-finding",
        "put-open-problem",
        "put-gap",
    ):
        item = commands.add_parser(name)
        _add_run_revision(item)
        item.add_argument("--input", required=True)

    merge = commands.add_parser("merge-approach-family")
    _add_run_revision(merge)
    merge.add_argument("--target-approach-ref", required=True)
    merge.add_argument("--source-approach-ref", required=True)

    retire_finding = commands.add_parser("retire-finding")
    _add_run_revision(retire_finding)
    retire_finding.add_argument("--finding-ref", required=True)

    retire_problem = commands.add_parser("retire-open-problem")
    _add_run_revision(retire_problem)
    retire_problem.add_argument("--problem-ref", required=True)

    resolve = commands.add_parser("resolve-gap")
    _add_run_revision(resolve)
    resolve.add_argument("--gap-ref", required=True)
    resolve.add_argument("--resolution", required=True)

    reopen_gap = commands.add_parser("reopen-gap")
    _add_run_revision(reopen_gap)
    reopen_gap.add_argument("--gap-ref", required=True)

    status = commands.add_parser("set-paper-status")
    _add_run_revision(status)
    status.add_argument("--paper-ref", required=True)
    status.add_argument("--status", required=True, choices=["ACTIVE", "RETIRED"])

    request = commands.add_parser("request-completion")
    _add_run_revision(request)
    request.add_argument("--rationale", required=True)

    submit = commands.add_parser("submit-completion")
    _add_run_revision(submit)
    submit.add_argument("--input", required=True)

    render = commands.add_parser("render-report")
    render.add_argument("--run-id", required=True)
    render.add_argument("--input", required=True)

    publish = commands.add_parser("publish-report")
    _add_run_revision(publish)
    publish.add_argument("--input", required=True)

    validate = commands.add_parser("validate-delivery")
    validate.add_argument("--run-id", required=True)

    for name in ("reopen-research", "close-run"):
        item = commands.add_parser(name)
        _add_run_revision(item)

    audit = commands.add_parser("audit-history")
    audit.add_argument("--run-id", required=True)

    wiki = commands.add_parser("wiki-query")
    wiki.add_argument("--query", required=True)
    wiki.add_argument("--limit", type=int, default=10)

    commands.add_parser("wiki-projection")

    publish_wiki = commands.add_parser("publish-wiki")
    publish_wiki.add_argument("--input", required=True)
    return parser


def _research_dispatch(args: argparse.Namespace, runtime: LocalV1Runtime) -> object:
    researcher = runtime.researcher
    command = args.command
    if command == "view":
        continuation = None
        if args.input:
            value = _load_input(args.input)
            _shape(
                value,
                required=frozenset({"state_revision", "section", "after"}),
            )
            try:
                section = ContextSection(_string(value["section"], "section"))
            except ValueError as exc:
                raise AdapterInputError(
                    "section is not a valid context section"
                ) from exc
            continuation = ContextContinuation(
                state_revision=_integer(value["state_revision"], "state_revision"),
                section=section,
                after=_string(value["after"], "after"),
            )
        return researcher.view(args.run_id, continuation)
    if command == "inspect":
        return researcher.inspect(args.run_id, args.expected_revision, tuple(args.refs))
    if command == "search-papers":
        return researcher.search_papers(
            args.run_id,
            args.expected_revision,
            args.query,
            limit=args.limit,
            offset=args.offset,
            date_from=args.date_from,
            date_to=args.date_to,
        )
    if command == "retain-papers":
        value = _load_input(args.input)
        raw = value.get("hits")
        nested_result = value.get("result")
        if raw is None and isinstance(nested_result, dict):
            raw = nested_result.get("hits")
        if not isinstance(raw, list):
            raise AdapterInputError("retain input must contain a hits array")
        return researcher.retain_papers(
            args.run_id, args.expected_revision, tuple(_hit(item) for item in raw)
        )
    if command == "inspect-source":
        return researcher.inspect_source(
            args.run_id, args.expected_revision, args.paper_ref
        )
    if command == "read-source":
        return researcher.read_source(
            args.run_id,
            args.expected_revision,
            args.paper_ref,
            _locator_args(args),
        )
    if command == "put-paper-analysis":
        value = _load_input(args.input)
        _shape(
            value,
            required=frozenset({"paper_ref", "summary", "relevance_to_run"}),
            optional=frozenset(
                {"contributions", "key_results", "limitations", "key_locators"}
            ),
        )
        return researcher.apply_research_mutation(
            args.run_id,
            args.expected_revision,
            ResearchMutationBatch(
                puts=(
                    PutPaperAnalysis(
                        paper_ref=_string(value["paper_ref"], "paper_ref"),
                        analysis=_analysis(value),
                    ),
                )
            ),
        )
    if command == "put-approach-family":
        value = _load_input(args.input)
        _shape(
            value,
            required=frozenset({"name", "core_idea", "representative_paper_refs"}),
            optional=frozenset({"approach_ref"}),
        )
        return researcher.put_approach_family(
            args.run_id,
            args.expected_revision,
            name=_string(value["name"], "name"),
            core_idea=_string(value["core_idea"], "core_idea"),
            representative_paper_refs=frozenset(
                _strings(
                    value["representative_paper_refs"], "representative_paper_refs"
                )
            ),
            approach_ref=_optional_string(value.get("approach_ref"), "approach_ref"),
        )
    if command == "merge-approach-family":
        return researcher.merge_approach_family(
            args.run_id,
            args.expected_revision,
            args.target_approach_ref,
            args.source_approach_ref,
        )
    if command in {"put-finding", "put-open-problem"}:
        value = _load_input(args.input)
        ref_name = "finding_ref" if command == "put-finding" else "problem_ref"
        _shape(
            value,
            required=frozenset({"statement"}),
            optional=frozenset({"approach_refs", "sources", ref_name}),
        )
        statement = _string(value["statement"], "statement")
        approach_refs = frozenset(_optional_strings(value, "approach_refs"))
        sources = _sources(value)
        if command == "put-finding":
            return researcher.put_landscape_finding(
                args.run_id,
                args.expected_revision,
                statement=statement,
                approach_refs=approach_refs,
                sources=sources,
                finding_ref=_optional_string(value.get(ref_name), ref_name),
            )
        return researcher.put_open_problem(
            args.run_id,
            args.expected_revision,
            statement=statement,
            approach_refs=approach_refs,
            sources=sources,
            problem_ref=_optional_string(value.get(ref_name), ref_name),
        )
    if command == "retire-finding":
        return researcher.retire_landscape_finding(
            args.run_id, args.expected_revision, args.finding_ref
        )
    if command == "retire-open-problem":
        return researcher.retire_open_problem(
            args.run_id, args.expected_revision, args.problem_ref
        )
    if command == "put-gap":
        value = _load_input(args.input)
        _shape(
            value,
            required=frozenset({"description"}),
            optional=frozenset({"requirement_refs", "approach_refs", "gap_ref"}),
        )
        return researcher.put_investigation_gap(
            args.run_id,
            args.expected_revision,
            description=_string(value["description"], "description"),
            requirement_refs=frozenset(_optional_strings(value, "requirement_refs")),
            approach_refs=frozenset(_optional_strings(value, "approach_refs")),
            gap_ref=_optional_string(value.get("gap_ref"), "gap_ref"),
        )
    if command == "resolve-gap":
        return researcher.resolve_investigation_gap(
            args.run_id, args.expected_revision, args.gap_ref, args.resolution
        )
    if command == "reopen-gap":
        return researcher.reopen_investigation_gap(
            args.run_id, args.expected_revision, args.gap_ref
        )
    if command == "set-paper-status":
        return researcher.set_paper_research_status(
            args.run_id,
            args.expected_revision,
            args.paper_ref,
            PaperResearchStatus(args.status),
        )
    if command == "request-completion":
        return researcher.request_completion_check(
            args.run_id, args.expected_revision, args.rationale
        )
    raise AdapterInputError(f"unsupported researcher command: {command}")


def _completion_dispatch(args: argparse.Namespace, runtime: LocalV1Runtime) -> object:
    checker = runtime.completion_checker
    if args.command == "completion-view":
        return checker.view(args.run_id)
    if args.command == "completion-inspect":
        return checker.inspect(args.run_id, args.expected_revision, tuple(args.refs))
    if args.command == "completion-read-source":
        return checker.read_source(
            args.run_id,
            args.expected_revision,
            args.paper_ref,
            _locator_args(args),
        )
    value = _load_input(args.input)
    _shape(
        value,
        required=frozenset({"completion_check_ref", "verdict", "reasons"}),
        optional=frozenset({"blocking_gaps"}),
    )
    try:
        verdict = CompletionVerdict(_string(value["verdict"], "verdict"))
    except ValueError as exc:
        raise AdapterInputError("verdict must be PASS, CONTINUE, or UNCERTAIN") from exc
    raw_gaps = value.get("blocking_gaps", [])
    if not isinstance(raw_gaps, list):
        raise AdapterInputError("blocking_gaps must be an array")
    gaps: list[BlockingGapSpec] = []
    for raw in raw_gaps:
        if not isinstance(raw, dict):
            raise AdapterInputError("blocking_gaps entries must be objects")
        if set(raw) == {"gap_ref"}:
            gaps.append(ReopenBlockingGap(gap_ref=_string(raw["gap_ref"], "gap_ref")))
            continue
        _shape(
            raw,
            required=frozenset({"description"}),
            optional=frozenset({"requirement_refs", "approach_refs"}),
        )
        gaps.append(
            NewBlockingGap(
                description=_string(raw["description"], "description"),
                requirement_refs=frozenset(_optional_strings(raw, "requirement_refs")),
                approach_refs=frozenset(_optional_strings(raw, "approach_refs")),
            )
        )
    return checker.submit_completion_check(
        args.run_id,
        args.expected_revision,
        _string(value["completion_check_ref"], "completion_check_ref"),
        verdict,
        _strings(value["reasons"], "reasons"),
        tuple(gaps),
    )


def _delivery_dispatch(args: argparse.Namespace, runtime: LocalV1Runtime) -> object:
    delivery = runtime.delivery
    if args.command == "delivery-view":
        return delivery.view(args.run_id)
    if args.command == "delivery-inspect":
        return delivery.inspect(args.run_id, args.expected_revision, tuple(args.refs))
    if args.command == "delivery-read-source":
        return delivery.read_source(
            args.run_id,
            args.expected_revision,
            args.paper_ref,
            _locator_args(args),
        )
    if args.command == "render-report":
        manuscript = _manuscript(_load_input(args.input))
        return {
            "content": DeterministicCitationRenderer().render(
                delivery.view(args.run_id), manuscript
            )
        }
    if args.command == "publish-report":
        value = _load_input(args.input)
        _shape(value, required=frozenset({"content"}))
        return delivery.publish_report(
            args.run_id,
            args.expected_revision,
            _string(value["content"], "content"),
        )
    if args.command == "validate-delivery":
        return delivery.validate_delivery(args.run_id)
    if args.command == "reopen-research":
        return delivery.reopen_research(args.run_id, args.expected_revision)
    if args.command == "close-run":
        return delivery.close_run(args.run_id, args.expected_revision)
    raise AdapterInputError(f"unsupported delivery command: {args.command}")


def _wiki_provenance_ref(value: object) -> WikiProvenanceRef:
    if not isinstance(value, dict):
        raise AdapterInputError("contributing_refs entries must be objects")
    _shape(value, required=frozenset({"run_id", "research_ref"}))
    return WikiProvenanceRef(
        run_id=_string(value["run_id"], "run_id"),
        research_ref=_string(value["research_ref"], "research_ref"),
    )


def _wiki_run_version(value: object) -> WikiRunVersion:
    if not isinstance(value, dict):
        raise AdapterInputError("source_runs entries must be objects")
    _shape(value, required=frozenset({"run_id", "state_revision"}))
    revision = value["state_revision"]
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise AdapterInputError("state_revision must be an integer")
    return WikiRunVersion(
        run_id=_string(value["run_id"], "run_id"),
        state_revision=revision,
    )


def _wiki_page_draft(value: object) -> WikiPageDraft:
    if not isinstance(value, dict):
        raise AdapterInputError("pages entries must be objects")
    _shape(
        value,
        required=frozenset({"slug", "title", "markdown", "contributing_refs"}),
    )
    raw_refs = value["contributing_refs"]
    if not isinstance(raw_refs, list) or not raw_refs:
        raise AdapterInputError("contributing_refs must be a non-empty array")
    return WikiPageDraft(
        slug=_string(value["slug"], "slug"),
        title=_string(value["title"], "title"),
        markdown=_string(value["markdown"], "markdown"),
        contributing_refs=tuple(
            _wiki_provenance_ref(item) for item in raw_refs
        ),
    )


def _wiki_dispatch(args: argparse.Namespace, runtime: LocalV1Runtime) -> object:
    """Wiki CLI bridge: Claude synthesizes pages; Python validates + publishes.

    ``wiki-projection`` returns the current authoritative projection of
    CLOSED+COMPLETE runs, carrying ``source_runs`` (the ``(run_id,
    state_revision)`` identity of every eligible run) and the structured
    landscape. Claude synthesizes Wiki pages from that projection and performs
    the semantic review outside the harness. ``publish-wiki`` accepts the
    ``source_runs`` preserved from the projection plus the synthesized
    ``pages``; Python validates structure and provenance deterministically and
    publishes a versioned local build, recording ``source_runs`` verbatim in
    the manifest as honest build provenance.

    A published Wiki may go stale if a newer run closes COMPLETE between
    projection and publish. That is allowed: the manifest honestly records
    which run revisions produced it, and ``is_current()`` detects staleness
    without rejecting publication. Invalid structure or provenance raises
    ``WikiBuildError`` before any build is written, preserving any previous
    publication. Wiki failure never affects run state.
    """
    if args.command == "wiki-projection":
        return runtime.wiki.project()

    value = _load_input(args.input)
    _shape(value, required=frozenset({"source_runs", "pages"}))
    raw_source_runs = value["source_runs"]
    if not isinstance(raw_source_runs, list):
        raise AdapterInputError("source_runs must be an array")
    source_runs = tuple(_wiki_run_version(item) for item in raw_source_runs)
    raw_pages = value["pages"]
    if not isinstance(raw_pages, list) or not raw_pages:
        raise AdapterInputError("pages must be a non-empty array")
    pages = tuple(_wiki_page_draft(item) for item in raw_pages)
    return runtime.wiki.publish(source_runs, pages)


_RESEARCH_COMMANDS = {
    "view",
    "inspect",
    "search-papers",
    "retain-papers",
    "inspect-source",
    "read-source",
    "put-paper-analysis",
    "put-approach-family",
    "merge-approach-family",
    "put-finding",
    "retire-finding",
    "put-open-problem",
    "retire-open-problem",
    "put-gap",
    "resolve-gap",
    "reopen-gap",
    "set-paper-status",
    "request-completion",
}
_COMPLETION_COMMANDS = {
    "completion-view",
    "completion-inspect",
    "completion-read-source",
    "submit-completion",
}
_DELIVERY_COMMANDS = {
    "delivery-view",
    "delivery-inspect",
    "delivery-read-source",
    "render-report",
    "publish-report",
    "validate-delivery",
    "reopen-research",
    "close-run",
}
_EXTERNAL_COMMANDS = {
    "search-papers",
    "inspect-source",
    "read-source",
    "completion-read-source",
    "delivery-read-source",
}
_WIKI_COMMANDS = {
    "wiki-query",
    "wiki-projection",
    "publish-wiki",
}


def _execute(
    args: argparse.Namespace,
    runtime_factory: RuntimeFactory,
) -> object:
    workspace = Path(args.workspace).expanduser().resolve()
    if args.command == "doctor":
        doctor_path = Path(__file__).resolve().with_name("doctor.py")
        spec = importlib.util.spec_from_file_location(
            "literature_research_skill_doctor", doctor_path
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load doctor: {doctor_path}")
        doctor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(doctor)
        return doctor.run_checks(workspace, _skill_dir())
    if args.command == "create-run":
        value = _load_input(args.input)
        _shape(
            value,
            required=frozenset(
                {
                    "mission",
                    "requirements",
                    "scope",
                    "deliverable_description",
                }
            ),
            optional=frozenset({"required_artifacts"}),
        )
        raw_artifacts = _optional_strings(value, "required_artifacts")
        try:
            artifacts = frozenset(ArtifactKind(item) for item in raw_artifacts)
        except ValueError as exc:
            raise AdapterInputError(
                "required_artifacts contains an unknown kind"
            ) from exc
        runtime = runtime_factory(workspace, False)
        return runtime.researcher.create_run(
            CreateRunRequest(
                mission=_string(value["mission"], "mission"),
                requirements=_strings(value["requirements"], "requirements"),
                scope=_string(value["scope"], "scope"),
                deliverable_description=_string(
                    value["deliverable_description"], "deliverable_description"
                ),
                required_artifacts=artifacts,
            )
        )
    if args.command == "audit-history":
        return {"events": LocalAuditLog(workspace / "runs").read(args.run_id)}
    runtime = runtime_factory(workspace, args.command in _EXTERNAL_COMMANDS)
    if args.command == "wiki-query":
        return runtime.wiki.query(args.query, limit=args.limit)
    if args.command in _WIKI_COMMANDS:
        return _wiki_dispatch(args, runtime)
    if args.command in _RESEARCH_COMMANDS:
        return _research_dispatch(args, runtime)
    if args.command in _COMPLETION_COMMANDS:
        return _completion_dispatch(args, runtime)
    if args.command in _DELIVERY_COMMANDS:
        return _delivery_dispatch(args, runtime)
    raise AdapterInputError(f"unsupported command: {args.command}")


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    runtime_factory: RuntimeFactory = _default_runtime_factory,
) -> int:
    command: str | None = None
    try:
        args = _parser().parse_args(argv)
        command = args.command
        result = _execute(args, runtime_factory)
        if (
            command == "doctor"
            and isinstance(result, dict)
            and result.get("healthy") is False
        ):
            _emit(
                stderr,
                {
                    "ok": False,
                    "command": command,
                    "error": {
                        "type": "DoctorCheckError",
                        "message": "one or more environment checks failed",
                    },
                    "result": result,
                },
            )
            return 1
        _emit(stdout, {"ok": True, "command": command, "result": result})
        return 0
    except Exception as exc:
        _emit(
            stderr,
            {
                "ok": False,
                "command": command,
                "error": {
                    "type": exc.__class__.__name__,
                    "message": _safe_message(exc),
                    **(
                        {"state_revision": exc.state_revision}
                        if hasattr(exc, "state_revision")
                        else {}
                    ),
                },
            },
        )
        return 2


if __name__ == "__main__":
    _reexec_skill_venv()
    raise SystemExit(main())
