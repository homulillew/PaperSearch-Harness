"""Deterministic validation and rendering for structured report citations."""

from __future__ import annotations

import re
from dataclasses import dataclass

from my_search_harness.domain.model import SourceLocator

from . import math_preflight
from .context import DeliveryView, PaperIndexEntry
from .reporting import CitationReference, ReportManuscript


_CITATION_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_CITATION_PLACEHOLDER = re.compile(r"\{\{cite:([A-Za-z][A-Za-z0-9_-]{0,63})\}\}")
_PAPER_NAVIGATION_PLACEHOLDER = re.compile(
    r"\{\{paper:([A-Za-z][A-Za-z0-9_-]{0,63})\|([^{}|\r\n]+)\}\}"
)
_INTERNAL_REF = re.compile(
    r"(?:run|requirement|paper|approach|finding|problem|gap|check)_"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-"
    r"[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
)
_REFERENCES_HEADING = re.compile(
    r"^#{1,6}\s+(?:references|bibliography)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_DETERMINISTIC_TOKEN = re.compile(r"\{\{[^{}]*\}\}")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(?:[^\r\n]*)$")


class CitationValidationError(RuntimeError):
    """Structured citations cannot be safely resolved or rendered."""


@dataclass(slots=True, frozen=True, kw_only=True)
class ResolvedCitation:
    citation_id: str
    paper_ref: str
    citation_number: int
    locator: SourceLocator | None


@dataclass(slots=True, frozen=True, kw_only=True)
class CitationAuditResult:
    citations: tuple[ResolvedCitation, ...]
    bibliography_paper_refs: tuple[str, ...]


class DeterministicCitationRenderer:
    """Resolve placeholders to current Run papers and render stable Markdown."""

    def audit(
        self,
        view: DeliveryView,
        manuscript: ReportManuscript,
    ) -> CitationAuditResult:
        if not isinstance(view, DeliveryView):
            raise CitationValidationError("citation audit requires a DeliveryView")
        if not isinstance(manuscript, ReportManuscript):
            raise CitationValidationError("citation audit requires a ReportManuscript")
        if not isinstance(manuscript.markdown, str) or not manuscript.markdown.strip():
            raise CitationValidationError("report markdown must be non-empty")
        if _INTERNAL_REF.search(manuscript.markdown):
            raise CitationValidationError(
                "report prose must not expose internal stable references"
            )
        if _REFERENCES_HEADING.search(manuscript.markdown):
            raise CitationValidationError(
                "bibliography must be produced by deterministic citation rendering"
            )
        self._validate_presentation_structure(manuscript.markdown)
        self._validate_math_renderability(manuscript.markdown)
        if not isinstance(manuscript.citations, tuple):
            raise CitationValidationError("citations must be a tuple")

        papers = {paper.ref: paper for paper in view.papers}
        declarations: dict[str, CitationReference] = {}
        for citation in manuscript.citations:
            self._validate_declaration(citation, papers)
            if citation.citation_id in declarations:
                raise CitationValidationError(
                    f"duplicate citation id {citation.citation_id!r}"
                )
            declarations[citation.citation_id] = citation

        placeholder_ids = tuple(
            match.group(1)
            for match in _CITATION_PLACEHOLDER.finditer(manuscript.markdown)
        )
        navigation_matches = tuple(
            _PAPER_NAVIGATION_PLACEHOLDER.finditer(manuscript.markdown)
        )
        navigation_ids = tuple(match.group(1) for match in navigation_matches)
        if any(
            match.group(2) != match.group(2).strip() for match in navigation_matches
        ):
            raise CitationValidationError(
                "paper navigation labels must not have outer whitespace"
            )
        malformed_probe = _CITATION_PLACEHOLDER.sub("", manuscript.markdown)
        malformed_probe = _PAPER_NAVIGATION_PLACEHOLDER.sub("", malformed_probe)
        if "{{cite" in malformed_probe:
            raise CitationValidationError("report contains a malformed citation token")
        if "{{paper" in malformed_probe:
            raise CitationValidationError(
                "report contains a malformed paper navigation token"
            )
        if (
            _DETERMINISTIC_TOKEN.search(malformed_probe)
            or "{{" in malformed_probe
            or "}}" in malformed_probe
        ):
            raise CitationValidationError(
                "report contains an unsupported deterministic presentation token"
            )
        missing = set(placeholder_ids) - set(declarations)
        if missing:
            raise CitationValidationError(
                f"citation tokens have no declaration: {sorted(missing)!r}"
            )
        missing_navigation = set(navigation_ids) - set(declarations)
        if missing_navigation:
            raise CitationValidationError(
                "paper navigation tokens have no citation declaration: "
                f"{sorted(missing_navigation)!r}"
            )
        navigation_without_citation = set(navigation_ids) - set(placeholder_ids)
        if navigation_without_citation:
            raise CitationValidationError(
                "paper navigation tokens must accompany a structured citation "
                f"token: {sorted(navigation_without_citation)!r}"
            )
        unused = set(declarations) - set(placeholder_ids)
        if unused:
            raise CitationValidationError(
                f"citation declarations are unused: {sorted(unused)!r}"
            )

        paper_numbers: dict[str, int] = {}
        resolved_by_id: dict[str, ResolvedCitation] = {}
        bibliography_paper_refs: list[str] = []
        for citation_id in placeholder_ids:
            citation = declarations[citation_id]
            number = paper_numbers.get(citation.paper_ref)
            if number is None:
                number = len(paper_numbers) + 1
                paper_numbers[citation.paper_ref] = number
                bibliography_paper_refs.append(citation.paper_ref)
            resolved_by_id.setdefault(
                citation_id,
                ResolvedCitation(
                    citation_id=citation_id,
                    paper_ref=citation.paper_ref,
                    citation_number=number,
                    locator=citation.locator,
                ),
            )
        return CitationAuditResult(
            citations=tuple(resolved_by_id.values()),
            bibliography_paper_refs=tuple(bibliography_paper_refs),
        )

    def render(self, view: DeliveryView, manuscript: ReportManuscript) -> str:
        audit = self.audit(view, manuscript)
        resolved = {citation.citation_id: citation for citation in audit.citations}
        papers = {paper.ref: paper for paper in view.papers}
        linked_papers: set[str] = set()
        navigation_papers = {
            resolved[match.group(1)].paper_ref
            for match in _PAPER_NAVIGATION_PLACEHOLDER.finditer(manuscript.markdown)
        }

        def replace(match: re.Match[str]) -> str:
            citation = resolved[match.group(1)]
            paper = papers[citation.paper_ref]
            if (
                paper.canonical_url
                and citation.paper_ref not in navigation_papers
                and citation.paper_ref not in linked_papers
            ):
                linked_papers.add(citation.paper_ref)
                return (
                    f"[{citation.citation_number}]"
                    f"({self._markdown_link_destination(paper.canonical_url)})"
                )
            return f"[{citation.citation_number}]"

        rendered = _CITATION_PLACEHOLDER.sub(replace, manuscript.markdown).rstrip()

        def replace_navigation(match: re.Match[str]) -> str:
            citation = resolved[match.group(1)]
            paper = papers[citation.paper_ref]
            label = match.group(2)
            if paper.canonical_url and citation.paper_ref not in linked_papers:
                linked_papers.add(citation.paper_ref)
                return (
                    f"[{self._markdown_link_label(label)}]"
                    f"({self._markdown_link_destination(paper.canonical_url)})"
                )
            return label

        rendered = _PAPER_NAVIGATION_PLACEHOLDER.sub(
            replace_navigation, rendered
        ).rstrip()
        if audit.bibliography_paper_refs:
            entries = tuple(
                self._bibliography_entry(number, papers[paper_ref])
                for number, paper_ref in enumerate(
                    audit.bibliography_paper_refs,
                    start=1,
                )
            )
            rendered = f"{rendered}\n\n## References\n\n" + "\n".join(entries)
        rendered += "\n"
        if "{{cite" in rendered:
            raise CitationValidationError("citation rendering left an unresolved token")
        if _INTERNAL_REF.search(rendered):
            raise CitationValidationError(
                "rendered report must not expose internal stable references"
            )
        return rendered

    @staticmethod
    def _validate_presentation_structure(markdown: str) -> None:
        """Reject a small set of reliable Markdown-LaTeX structural defects.

        This is deliberately not a Markdown or TeX parser.  It checks only
        deterministic delimiter invariants and never rewrites prose.
        """

        fence_character: str | None = None
        fence_length = 0
        prose_lines: list[str] = []
        for line in markdown.splitlines():
            if fence_character is not None:
                if re.match(
                    rf"^ {{0,3}}{re.escape(fence_character)}"
                    rf"{{{fence_length},}}[ \t]*$",
                    line,
                ):
                    fence_character = None
                    fence_length = 0
                continue
            fence = _FENCE.match(line)
            if fence is not None:
                marker = fence.group(1)
                fence_character = marker[0]
                fence_length = len(marker)
                continue
            prose_lines.append(line)
        if fence_character is not None:
            raise CitationValidationError("report contains an unclosed fenced block")

        prose = "\n".join(prose_lines)
        if len(re.findall(r"(?<!\\)\$\$", prose)) % 2:
            raise CitationValidationError(
                "report contains unmatched $$ math delimiters"
            )
        for opener, closer, label in (
            (r"\\\(", r"\\\)", r"\(...\)"),
            (r"\\\[", r"\\\]", r"\[...\]"),
        ):
            opens = [match.start() for match in re.finditer(opener, prose)]
            closes = [match.start() for match in re.finditer(closer, prose)]
            if len(opens) != len(closes) or any(
                open_at > close_at for open_at, close_at in zip(opens, closes)
            ):
                raise CitationValidationError(
                    f"report contains unmatched {label} math delimiters"
                )

    @staticmethod
    def _validate_math_renderability(markdown: str) -> None:
        """Reject math the target renderer cannot render.

        Delegates to the real MathJax renderer (via :mod:`math_preflight`) so
        that a manuscript cannot reach the Reader or publication with TeX the
        renderer rejects. This is mechanical renderability only — it never
        judges mathematical meaning, style, or fidelity, and never rewrites
        prose. Manuscripts without math spans short-circuit (no renderer call).
        """

        try:
            rejections = math_preflight.renderability_report(markdown)
        except math_preflight.MathRendererUnavailable as err:
            raise CitationValidationError(str(err)) from err
        if rejections:
            raise CitationValidationError(
                "report contains math the target renderer rejects: "
                + "; ".join(
                    f"{r.expression!r} ({r.error})" for r in rejections
                )
            )

    @staticmethod
    def _markdown_link_destination(value: str) -> str:
        return value.strip().replace(" ", "%20").replace("(", "%28").replace(")", "%29")

    @staticmethod
    def _markdown_link_label(value: str) -> str:
        return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")

    @staticmethod
    def _validate_declaration(
        citation: object,
        papers: dict[str, PaperIndexEntry],
    ) -> None:
        if not isinstance(citation, CitationReference):
            raise CitationValidationError(
                "citations must contain CitationReference values"
            )
        if (
            not isinstance(citation.citation_id, str)
            or _CITATION_ID.fullmatch(citation.citation_id) is None
        ):
            raise CitationValidationError("citation_id has an invalid format")
        if _INTERNAL_REF.fullmatch(citation.citation_id):
            raise CitationValidationError("citation_id must not be an internal ref")
        if citation.paper_ref not in papers:
            raise CitationValidationError(
                f"citation targets unknown paper {citation.paper_ref!r}"
            )
        # Formal report citations share the Landscape evidence boundary: a
        # citation may only target a paper that is ACTIVE and has a
        # PaperAnalysis. A RETIRED paper is a closed candidate (not current
        # evidence); an unanalyzed ACTIVE paper would let search metadata ground
        # a report claim. This keeps the Final Report from re-introducing papers
        # that the Deep Reading invariants closed off — including under Partial
        # Delivery, which routes through the same renderer.
        target = papers[citation.paper_ref]
        if target.research_status.value != "ACTIVE" or not target.has_analysis:
            raise CitationValidationError(
                f"citation targets paper {citation.paper_ref!r} which is not "
                f"eligible formal evidence (must be ACTIVE with a "
                f"PaperAnalysis; is {target.research_status.value}, "
                f"has_analysis={target.has_analysis})"
            )
        locator = citation.locator
        if locator is not None and (
            not isinstance(locator, SourceLocator)
            or not isinstance(locator.kind, str)
            or not locator.kind.strip()
            or not isinstance(locator.value, str)
            or not locator.value.strip()
            or any(character in locator.kind + locator.value for character in "\r\n")
        ):
            raise CitationValidationError("citation locator is mechanically invalid")

    @classmethod
    def _bibliography_entry(cls, number: int, paper: PaperIndexEntry) -> str:
        components: list[str] = []
        if paper.authors:
            components.append(
                ", ".join(cls._markdown_text(author) for author in paper.authors)
            )
        components.append(f"“{cls._markdown_text(paper.title)}.”")
        if paper.publication_year is not None:
            components.append(str(paper.publication_year))

        identifiers: list[str] = []
        if paper.doi is not None:
            identifiers.append(f"DOI {cls._markdown_text(paper.doi.strip())}")
        if paper.arxiv_id is not None:
            identifiers.append(f"arXiv {cls._markdown_text(paper.arxiv_id.strip())}")
        if paper.canonical_url is not None:
            identifiers.append(cls._markdown_text(paper.canonical_url.strip()))
        if identifiers:
            components.append("; ".join(identifiers))
        return f"{number}. " + " ".join(components)

    @staticmethod
    def _markdown_text(value: str) -> str:
        collapsed = " ".join(value.split())
        escaped = collapsed.replace("\\", "\\\\")
        for character in ("[", "]", "*", "_", "<", ">"):
            escaped = escaped.replace(character, f"\\{character}")
        return escaped
