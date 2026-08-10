"""Deterministic validation and rendering for structured report citations."""

from __future__ import annotations

import re
from dataclasses import dataclass

from my_search_harness.domain.model import SourceLocator

from .context import DeliveryView, PaperIndexEntry
from .reporting import CitationReference, ReportManuscript


_CITATION_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_CITATION_PLACEHOLDER = re.compile(r"\{\{cite:([A-Za-z][A-Za-z0-9_-]{0,63})\}\}")
_INTERNAL_REF = re.compile(
    r"(?:run|requirement|paper|approach|finding|problem|gap|check)_"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-"
    r"[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
)
_REFERENCES_HEADING = re.compile(
    r"^#{1,6}\s+(?:references|bibliography)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


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
        malformed_probe = _CITATION_PLACEHOLDER.sub("", manuscript.markdown)
        if "{{cite" in malformed_probe:
            raise CitationValidationError("report contains a malformed citation token")
        missing = set(placeholder_ids) - set(declarations)
        if missing:
            raise CitationValidationError(
                f"citation tokens have no declaration: {sorted(missing)!r}"
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

        def replace(match: re.Match[str]) -> str:
            citation = resolved[match.group(1)]
            if citation.locator is None:
                return f"[{citation.citation_number}]"
            locator_kind = self._markdown_text(citation.locator.kind)
            locator_value = self._markdown_text(citation.locator.value)
            return f"[{citation.citation_number}, {locator_kind}: {locator_value}]"

        rendered = _CITATION_PLACEHOLDER.sub(replace, manuscript.markdown).rstrip()
        if audit.bibliography_paper_refs:
            papers = {paper.ref: paper for paper in view.papers}
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
