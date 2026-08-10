"""Rebuildable Local Wiki projection, validation, publication, and observation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from my_search_harness.domain.model import (
    LifecycleMode,
    LiteratureSource,
    RunOutcome,
    SourceLocator,
)
from my_search_harness.domain.validation import validate_ref

from .persistence import JsonResearchRunRepository


_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_INTERNAL_REF = re.compile(
    r"(?:run|requirement|paper|approach|finding|problem|gap|check)_"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-"
    r"[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
)


class WikiBuildError(RuntimeError):
    """Wiki projection or mechanical validation failed."""


class WikiPublicationError(WikiBuildError):
    """A validated Wiki could not atomically replace the published pointer."""


class WikiUnavailableError(RuntimeError):
    """No valid published Wiki is available for observation."""


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiSourceRef:
    paper_ref: str
    relation: str
    locator: SourceLocator | None


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiPaperInput:
    ref: str
    title: str
    authors: tuple[str, ...]
    publication_year: int | None
    publication_date: str | None
    doi: str | None
    arxiv_id: str | None
    canonical_url: str | None


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiApproachInput:
    ref: str
    name: str
    core_idea: str
    representative_paper_refs: tuple[str, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiFindingInput:
    ref: str
    statement: str
    approach_refs: tuple[str, ...]
    sources: tuple[WikiSourceRef, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiOpenProblemInput:
    ref: str
    statement: str
    approach_refs: tuple[str, ...]
    sources: tuple[WikiSourceRef, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiRunProjection:
    run_id: str
    state_revision: int
    approaches: tuple[WikiApproachInput, ...]
    findings: tuple[WikiFindingInput, ...]
    open_problems: tuple[WikiOpenProblemInput, ...]
    papers: tuple[WikiPaperInput, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiRunVersion:
    """The ``(run_id, state_revision)`` identity of one eligible run.

    A value object, not an entity. Carries the exact identity of an eligible
    run at projection time so a published Wiki manifest can honestly record
    which run revisions produced it. The same DTO is the projection's
    ``source_runs`` element and the manifest's ``source_runs`` element.
    """

    run_id: str
    state_revision: int


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiProjection:
    source_runs: tuple[WikiRunVersion, ...]
    runs: tuple[WikiRunProjection, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiProvenanceRef:
    run_id: str
    research_ref: str


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiPageDraft:
    slug: str
    title: str
    markdown: str
    contributing_refs: tuple[WikiProvenanceRef, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiManifestPage:
    path: str
    title: str
    content_sha256: str
    contributing_refs: tuple[WikiProvenanceRef, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiManifest:
    schema_version: int
    source_runs: tuple[WikiRunVersion, ...]
    pages: tuple[WikiManifestPage, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiPublicationResult:
    wiki_path: Path
    manifest: WikiManifest


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiQueryHit:
    title: str
    path: str
    excerpt: str
    contributing_refs: tuple[WikiProvenanceRef, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiQueryResult:
    query: str
    hits: tuple[WikiQueryHit, ...]


class WikiProjectionService:
    """Select only CLOSED+COMPLETE state and strip process/delivery data by type."""

    def __init__(self, repository: JsonResearchRunRepository) -> None:
        self._repository = repository

    def project(self) -> WikiProjection:
        runs: list[WikiRunProjection] = []
        for run_id in self._repository.list_run_ids():
            run = self._repository.load(run_id)
            if (
                run.lifecycle is not LifecycleMode.CLOSED
                or run.outcome is not RunOutcome.COMPLETE
            ):
                continue
            landscape = run.literature_landscape
            approaches = tuple(
                WikiApproachInput(
                    ref=approach.id,
                    name=approach.name,
                    core_idea=approach.core_idea,
                    representative_paper_refs=tuple(
                        sorted(approach.representative_papers)
                    ),
                )
                for approach in sorted(
                    landscape.approach_families.values(),
                    key=lambda item: item.id,
                )
            )
            findings = tuple(
                WikiFindingInput(
                    ref=finding.id,
                    statement=finding.statement,
                    approach_refs=tuple(sorted(finding.approach_refs)),
                    sources=self._sources(finding.sources),
                )
                for finding in sorted(
                    landscape.findings.values(), key=lambda item: item.id
                )
            )
            open_problems = tuple(
                WikiOpenProblemInput(
                    ref=problem.id,
                    statement=problem.statement,
                    approach_refs=tuple(sorted(problem.approach_refs)),
                    sources=self._sources(problem.sources),
                )
                for problem in sorted(
                    landscape.open_problems.values(), key=lambda item: item.id
                )
            )
            projected_paper_refs = {
                *(
                    paper_ref
                    for approach in approaches
                    for paper_ref in approach.representative_paper_refs
                ),
                *(
                    source.paper_ref
                    for finding in findings
                    for source in finding.sources
                ),
                *(
                    source.paper_ref
                    for problem in open_problems
                    for source in problem.sources
                ),
            }
            papers = tuple(
                WikiPaperInput(
                    ref=paper.id,
                    title=paper.source.title,
                    authors=paper.source.authors,
                    publication_year=paper.source.publication_year,
                    publication_date=paper.source.publication_date,
                    doi=paper.source.doi,
                    arxiv_id=paper.source.arxiv_id,
                    canonical_url=paper.source.canonical_url,
                )
                for paper in sorted(run.papers.values(), key=lambda item: item.id)
                if paper.id in projected_paper_refs
            )
            runs.append(
                WikiRunProjection(
                    run_id=run.id,
                    state_revision=run.state_revision,
                    approaches=approaches,
                    findings=findings,
                    open_problems=open_problems,
                    papers=papers,
                )
            )
        source_runs = tuple(
            WikiRunVersion(
                run_id=run.run_id,
                state_revision=run.state_revision,
            )
            for run in runs
        )
        return WikiProjection(source_runs=source_runs, runs=tuple(runs))

    @staticmethod
    def _sources(sources: set[LiteratureSource]) -> tuple[WikiSourceRef, ...]:
        return tuple(
            WikiSourceRef(
                paper_ref=source.paper_ref,
                relation=source.relation.value,
                locator=source.locator,
            )
            for source in sorted(
                sources,
                key=lambda item: (
                    item.paper_ref,
                    item.relation.value,
                    "" if item.locator is None else item.locator.kind,
                    "" if item.locator is None else item.locator.value,
                ),
            )
        )


class LocalWikiPublisher:
    """Publish validated builds under a versioned directory with an atomic pointer.

    Each publication writes a new ``builds/build-<UUID>/`` directory and
    atomically replaces ``current.json`` (which names the active build) via
    ``os.replace``. The layout is platform-agnostic: no symlink or junction is
    used, so POSIX and Windows share one publication algorithm. A failed
    publication leaves the previous ``current.json`` pointing at the previous
    build, so the published Wiki is preserved; an orphaned build directory
    left behind by a pointer-write failure is inert.
    """

    def __init__(self, wiki_path: str | Path) -> None:
        self._wiki_path = Path(wiki_path)
        self._builds_path = self._wiki_path / "builds"
        self._pointer_path = self._wiki_path / "current.json"

    def publish(
        self,
        source_runs: tuple[WikiRunVersion, ...],
        pages: tuple[WikiPageDraft, ...],
    ) -> WikiPublicationResult:
        self._wiki_path.parent.mkdir(parents=True, exist_ok=True)
        self._builds_path.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self._builds_path))
        try:
            manifest = self._write_staging(staging, source_runs, pages)
            self._validate_staging(staging, manifest)
            build_id = f"build-{uuid4()}"
            published_build = self._builds_path / build_id
            os.replace(staging, published_build)
            self._swap_pointer(build_id)
            return WikiPublicationResult(
                wiki_path=self._wiki_path,
                manifest=manifest,
            )
        except WikiBuildError:
            raise
        except BaseException as exc:
            raise WikiPublicationError("atomic Wiki publication failed") from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @staticmethod
    def validate_pages_against(
        pages: tuple[WikiPageDraft, ...],
        projection: WikiProjection,
    ) -> None:
        """Validate page structure, links, and provenance against a projection.

        Provenance is checked against the *current* projection so a page may
        only cite real approaches, findings, open problems, or papers in
        eligible runs. Structural checks (slugs, titles, markdown, internal
        refs, link targets) run here too. ``WikiService.publish`` calls this
        before publication so an invalid page raises ``WikiBuildError`` before
        any build is written, preserving any previous publication.
        """
        allowed_refs = {
            run.run_id: {
                *(item.ref for item in run.approaches),
                *(item.ref for item in run.findings),
                *(item.ref for item in run.open_problems),
                *(item.ref for item in run.papers),
            }
            for run in projection.runs
        }
        slugs: set[str] = set()
        pages_by_filename: set[str] = set()
        for page in pages:
            if (
                not isinstance(page.slug, str)
                or _SLUG.fullmatch(page.slug) is None
                or page.slug in slugs
            ):
                raise WikiBuildError("Wiki page slugs must be unique safe slugs")
            slugs.add(page.slug)
            pages_by_filename.add(f"{page.slug}.md")
            if not isinstance(page.title, str) or not page.title.strip():
                raise WikiBuildError("Wiki page title must be non-empty")
            if not isinstance(page.markdown, str) or not page.markdown.strip():
                raise WikiBuildError("Wiki page markdown must be non-empty")
            if _INTERNAL_REF.search(page.markdown):
                raise WikiBuildError("Wiki prose must not expose internal stable refs")
            if (
                not isinstance(page.contributing_refs, tuple)
                or not page.contributing_refs
                or len(set(page.contributing_refs)) != len(page.contributing_refs)
            ):
                raise WikiBuildError(
                    "Wiki page requires unique contributing Research refs"
                )
            for ref in page.contributing_refs:
                if not isinstance(ref, WikiProvenanceRef) or ref.research_ref not in (
                    allowed_refs.get(ref.run_id) or set()
                ):
                    raise WikiBuildError("Wiki page has invalid contributing ref")
        for page in pages:
            LocalWikiPublisher._validate_page_links(page.markdown, pages_by_filename)

    def _swap_pointer(self, build_id: str) -> None:
        """Atomically replace ``current.json`` to name the new active build.

        Writes the pointer to a sibling temp file and ``os.replace``-es it over
        any existing ``current.json``. ``os.replace`` is atomic on both POSIX
        and Windows, so a failure before the replace leaves the previous
        pointer intact. The new build directory is already on disk; if the
        pointer swap fails it becomes an inert orphan rather than a corrupt
        publication.
        """
        pointer_temp = self._pointer_path.with_name(
            f".{self._pointer_path.name}.swap-{uuid4()}"
        )
        pointer_temp.write_text(
            json.dumps({"build": build_id}, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(pointer_temp, self._pointer_path)

    def read_manifest(self) -> WikiManifest:
        try:
            pointer = json.loads(self._pointer_path.read_text(encoding="utf-8"))
            build_id = pointer["build"]
            build_dir = self._builds_path / build_id
            manifest = self._manifest_from_dict(
                json.loads((build_dir / "manifest.json").read_text(encoding="utf-8"))
            )
            self._validate_staging(build_dir, manifest)
            return manifest
        except (
            WikiBuildError,
            FileNotFoundError,
            KeyError,
            OSError,
            UnicodeError,
            ValueError,
            TypeError,
        ) as exc:
            raise WikiUnavailableError(
                "published Wiki manifest is unavailable"
            ) from exc

    def read_page(self, page: WikiManifestPage) -> str:
        try:
            pointer = json.loads(self._pointer_path.read_text(encoding="utf-8"))
            build_dir = self._builds_path / pointer["build"]
            content = (build_dir / page.path).read_bytes()
        except (FileNotFoundError, KeyError, OSError, UnicodeError, ValueError) as exc:
            raise WikiUnavailableError(
                f"published Wiki page is unavailable: {page.path}"
            ) from exc
        if hashlib.sha256(content).hexdigest() != page.content_sha256:
            raise WikiUnavailableError(
                f"published Wiki page digest mismatch: {page.path}"
            )
        try:
            return content.decode("utf-8")
        except UnicodeError as exc:
            raise WikiUnavailableError(
                f"published Wiki page is not UTF-8: {page.path}"
            ) from exc

    def _write_staging(
        self,
        staging: Path,
        source_runs: tuple[WikiRunVersion, ...],
        pages: tuple[WikiPageDraft, ...],
    ) -> WikiManifest:
        pages_directory = staging / "pages"
        pages_directory.mkdir()
        manifest_pages: list[WikiManifestPage] = []
        for page in sorted(pages, key=lambda item: item.slug):
            content = page.markdown.rstrip() + "\n"
            page_path = pages_directory / f"{page.slug}.md"
            page_path.write_text(content, encoding="utf-8", newline="\n")
            manifest_pages.append(
                WikiManifestPage(
                    path=f"pages/{page.slug}.md",
                    title=page.title,
                    content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    contributing_refs=page.contributing_refs,
                )
            )
        index = self._render_index(tuple(sorted(pages, key=lambda item: item.slug)))
        (staging / "INDEX.md").write_text(index, encoding="utf-8", newline="\n")
        manifest = WikiManifest(
            schema_version=1,
            source_runs=source_runs,
            pages=tuple(manifest_pages),
        )
        (staging / "manifest.json").write_text(
            self._manifest_json(manifest),
            encoding="utf-8",
            newline="\n",
        )
        return manifest

    @classmethod
    def _validate_staging(cls, staging: Path, manifest: WikiManifest) -> None:
        expected_files = {
            "INDEX.md",
            "manifest.json",
            *(page.path for page in manifest.pages),
        }
        actual_files = {
            path.relative_to(staging).as_posix()
            for path in staging.rglob("*")
            if path.is_file()
        }
        if actual_files != expected_files:
            raise WikiBuildError("Wiki staging files do not match the manifest")
        parsed = cls._manifest_from_dict(
            json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
        )
        if parsed != manifest:
            raise WikiBuildError("Wiki staging manifest does not round-trip")
        for page in manifest.pages:
            content = (staging / page.path).read_bytes()
            if hashlib.sha256(content).hexdigest() != page.content_sha256:
                raise WikiBuildError(f"Wiki page digest mismatch: {page.path}")

    @staticmethod
    def _validate_page_links(markdown: str, page_filenames: set[str]) -> None:
        for target in _MARKDOWN_LINK.findall(markdown):
            if target.startswith(("https://", "http://", "#")):
                continue
            if target == "../INDEX.md":
                continue
            path = target.split("#", maxsplit=1)[0]
            if "/" in path or path not in page_filenames:
                raise WikiBuildError(f"Wiki page link is invalid: {target!r}")

    @staticmethod
    def _render_index(pages: tuple[WikiPageDraft, ...]) -> str:
        lines = ["# Local Wiki", ""]
        if not pages:
            lines.append("No eligible research knowledge has been published yet.")
        else:
            lines.extend(f"- [{page.title}](pages/{page.slug}.md)" for page in pages)
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _manifest_json(manifest: WikiManifest) -> str:
        return (
            json.dumps(
                asdict(manifest),
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    @staticmethod
    def _manifest_from_dict(value: object) -> WikiManifest:
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "source_runs",
            "pages",
        }:
            raise WikiBuildError("Wiki manifest fields are invalid")
        if value["schema_version"] != 1:
            raise WikiBuildError("Wiki manifest schema_version is unsupported")
        raw_runs = value["source_runs"]
        raw_pages = value["pages"]
        if not isinstance(raw_runs, list) or not isinstance(raw_pages, list):
            raise WikiBuildError("Wiki manifest collections are invalid")
        source_runs: list[WikiRunVersion] = []
        for raw in raw_runs:
            if not isinstance(raw, dict) or set(raw) != {"run_id", "state_revision"}:
                raise WikiBuildError("Wiki manifest source run is invalid")
            source_runs.append(
                WikiRunVersion(
                    run_id=raw["run_id"],
                    state_revision=raw["state_revision"],
                )
            )
        pages: list[WikiManifestPage] = []
        for raw in raw_pages:
            if not isinstance(raw, dict) or set(raw) != {
                "content_sha256",
                "contributing_refs",
                "path",
                "title",
            }:
                raise WikiBuildError("Wiki manifest page is invalid")
            raw_refs = raw["contributing_refs"]
            if not isinstance(raw_refs, list):
                raise WikiBuildError("Wiki manifest provenance is invalid")
            refs = tuple(
                WikiProvenanceRef(
                    run_id=item["run_id"],
                    research_ref=item["research_ref"],
                )
                for item in raw_refs
                if isinstance(item, dict) and set(item) == {"run_id", "research_ref"}
            )
            if len(refs) != len(raw_refs):
                raise WikiBuildError("Wiki manifest provenance is invalid")
            pages.append(
                WikiManifestPage(
                    path=raw["path"],
                    title=raw["title"],
                    content_sha256=raw["content_sha256"],
                    contributing_refs=refs,
                )
            )
        manifest = WikiManifest(
            schema_version=1,
            source_runs=tuple(source_runs),
            pages=tuple(pages),
        )
        LocalWikiPublisher._validate_manifest_shape(manifest)
        return manifest

    @staticmethod
    def _validate_manifest_shape(manifest: WikiManifest) -> None:
        if manifest.schema_version != 1:
            raise WikiBuildError("Wiki manifest schema version is invalid")
        seen_runs: set[str] = set()
        for run in manifest.source_runs:
            if (
                not isinstance(run.run_id, str)
                or not isinstance(run.state_revision, int)
                or isinstance(run.state_revision, bool)
                or run.state_revision < 1
                or run.run_id in seen_runs
            ):
                raise WikiBuildError("Wiki manifest source runs are invalid")
            validate_ref(run.run_id, "run", "Wiki manifest source run")
            seen_runs.add(run.run_id)
        seen_paths: set[str] = set()
        for page in manifest.pages:
            if (
                not isinstance(page.path, str)
                or _SLUG.fullmatch(page.path.removeprefix("pages/").removesuffix(".md"))
                is None
                or page.path
                != (
                    "pages/"
                    + page.path.removeprefix("pages/").removesuffix(".md")
                    + ".md"
                )
                or page.path in seen_paths
                or not isinstance(page.title, str)
                or not page.title
                or not isinstance(page.content_sha256, str)
                or len(page.content_sha256) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in page.content_sha256
                )
            ):
                raise WikiBuildError("Wiki manifest pages are invalid")
            seen_paths.add(page.path)
            if not page.contributing_refs:
                raise WikiBuildError("Wiki manifest page provenance is empty")
            for ref in page.contributing_refs:
                if not isinstance(ref.run_id, str) or ref.run_id not in seen_runs:
                    raise WikiBuildError("Wiki manifest provenance run is invalid")
                prefix = ref.research_ref.split("_", 1)[0]
                if prefix not in {"approach", "finding", "problem", "paper"}:
                    raise WikiBuildError("Wiki manifest provenance ref is invalid")
                validate_ref(ref.research_ref, prefix, "Wiki provenance ref")


class WikiService:
    """Unified projection, publication, query, and freshness observation.

    The Wiki is a rebuildable, non-authoritative Markdown projection of
    CLOSED+COMPLETE runs. Python projects accepted structured state, validates
    page provenance, publishes versioned local builds, and exposes query.
    Claude performs semantic synthesis and review outside the harness; this
    service does not re-abstract those semantic actors.

    A published Wiki may go stale when a newer run closes COMPLETE, but the
    manifest honestly records which run revisions produced it. ``is_current()``
    detects staleness without rejecting publication: a stale Wiki is allowed
    to exist and can be rebuilt when desired.
    """

    def __init__(
        self,
        projection: WikiProjectionService,
        publisher: LocalWikiPublisher,
    ) -> None:
        self._projection = projection
        self._publisher = publisher

    def project(self) -> WikiProjection:
        return self._projection.project()

    def publish(
        self,
        source_runs: tuple[WikiRunVersion, ...],
        pages: tuple[WikiPageDraft, ...],
    ) -> WikiPublicationResult:
        projection = self._projection.project()
        self._publisher.validate_pages_against(pages, projection)
        return self._publisher.publish(source_runs, pages)

    def query(self, query: str, *, limit: int = 10) -> WikiQueryResult:
        return WikiQueryService(self._publisher).query(query, limit=limit)

    def is_current(self) -> bool:
        try:
            manifest = self._publisher.read_manifest()
        except WikiUnavailableError:
            return False
        return manifest.source_runs == self._projection.project().source_runs


class WikiQueryService:
    """Return published Wiki snippets as non-authoritative observations."""

    def __init__(self, publisher: LocalWikiPublisher) -> None:
        self._publisher = publisher

    def query(self, query: str, *, limit: int = 10) -> WikiQueryResult:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Wiki query must be a non-empty string")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > 100
        ):
            raise ValueError("Wiki query limit must be from 1 to 100")
        manifest = self._publisher.read_manifest()
        needle = query.strip().casefold()
        hits: list[WikiQueryHit] = []
        for page in manifest.pages:
            content = self._publisher.read_page(page)
            if needle not in f"{page.title}\n{content}".casefold():
                continue
            excerpt = " ".join(content.split())[:280]
            hits.append(
                WikiQueryHit(
                    title=page.title,
                    path=page.path,
                    excerpt=excerpt,
                    contributing_refs=page.contributing_refs,
                )
            )
            if len(hits) == limit:
                break
        return WikiQueryResult(query=query.strip(), hits=tuple(hits))
