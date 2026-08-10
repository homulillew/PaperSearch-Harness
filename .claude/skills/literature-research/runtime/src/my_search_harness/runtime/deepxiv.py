"""DeepXiv SDK adapters for arXiv paper search and primary-source access."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import NoReturn, Protocol

from deepxiv_sdk import (  # type: ignore[import-untyped]
    APIError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    RateLimitError,
    Reader,
    ServerError,
)

from my_search_harness.domain.model import (
    PaperSource,
    SourceLocator,
)
from my_search_harness.domain.paper_identity import normalize_arxiv_id

from .paper_search import (
    PaperSearchConfigurationError,
    PaperSearchHit,
    PaperSearchPage,
    PaperSearchProviderError,
    ProviderFailureKind,
)
from .source_access import (
    SourceAccessConfigurationError,
    SourceAccessFailureKind,
    SourceAccessProviderError,
    SourceContent,
    SourceOutline,
    SourceOutlineEntry,
)


class _Reader(Protocol):
    def search(
        self,
        query: str,
        *,
        size: int,
        source: str,
        offset: int,
        date_from: str | None,
        date_to: str | None,
    ) -> object: ...

    def head(self, arxiv_id: str) -> object: ...

    def section(self, arxiv_id: str, section_name: str) -> object: ...

    def raw(self, arxiv_id: str) -> object: ...


ReaderFactory = Callable[..., _Reader]


class DeepXivPaperSearchProvider:
    """Translate the DeepXiv 0.3.1 SDK into provider-neutral observations."""

    def __init__(
        self,
        token: str,
        *,
        reader_factory: ReaderFactory = Reader,
    ) -> None:
        if not isinstance(token, str) or not token.strip():
            raise PaperSearchConfigurationError("DEEPXIV_TOKEN is required")
        self._reader = reader_factory(token=token.strip(), max_retries=0)

    @classmethod
    def from_env(
        cls,
        *,
        reader_factory: ReaderFactory = Reader,
    ) -> DeepXivPaperSearchProvider:
        token = os.environ.get("DEEPXIV_TOKEN")
        if token is None or not token.strip():
            raise PaperSearchConfigurationError("DEEPXIV_TOKEN is required")
        return cls(token, reader_factory=reader_factory)

    def search(
        self,
        query: str,
        *,
        limit: int,
        offset: int = 0,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> PaperSearchPage:
        try:
            response = self._reader.search(
                query,
                source="arxiv",
                size=limit,
                offset=offset,
                date_from=date_from,
                date_to=date_to,
            )
        except AuthenticationError:
            raise PaperSearchProviderError(
                ProviderFailureKind.AUTHENTICATION,
                "paper search provider rejected authentication",
            ) from None
        except RateLimitError:
            raise PaperSearchProviderError(
                ProviderFailureKind.RATE_LIMIT,
                "paper search provider rate limit was reached",
            ) from None
        except ServerError:
            raise PaperSearchProviderError(
                ProviderFailureKind.UNAVAILABLE,
                "paper search provider is unavailable",
            ) from None
        except APIError:
            raise PaperSearchProviderError(
                ProviderFailureKind.OTHER,
                "paper search provider request failed",
            ) from None
        except Exception:
            raise PaperSearchProviderError(
                ProviderFailureKind.OTHER,
                "paper search provider failed unexpectedly",
            ) from None

        return self._map_response(response)

    @classmethod
    def _map_response(cls, response: object) -> PaperSearchPage:
        if not isinstance(response, Mapping):
            cls._invalid_response("top-level response must be an object")
        if response.get("status") != "success":
            cls._invalid_response("top-level status must be success")
        total_count = response.get("total_count")
        if (
            not isinstance(total_count, int)
            or isinstance(total_count, bool)
            or total_count < 0
        ):
            cls._invalid_response("total_count must be a non-negative integer")
        results = response.get("result")
        if not isinstance(results, list):
            cls._invalid_response("result must be an array")

        return PaperSearchPage(
            total_count=total_count,
            hits=tuple(
                cls._map_hit(item, index=index) for index, item in enumerate(results)
            ),
        )

    @classmethod
    def _map_hit(cls, value: object, *, index: int) -> PaperSearchHit:
        path = f"result[{index}]"
        if not isinstance(value, Mapping):
            cls._invalid_response(f"{path} must be an object")

        arxiv_id = cls._required_string(value.get("arxiv_id"), f"{path}.arxiv_id")
        title = cls._required_string(value.get("title"), f"{path}.title")
        authors = cls._authors(value.get("authors"), f"{path}.authors")
        publication_date = cls._publication_date(value.get("date"), f"{path}.date")
        canonical_url = cls._optional_string(value.get("url"), f"{path}.url")
        abstract = cls._optional_string(value.get("abstract"), f"{path}.abstract")
        provider_summary = cls._optional_string(value.get("tldr"), f"{path}.tldr")
        provider_score = cls._optional_float(value.get("score"), f"{path}.score")
        citation_count = cls._optional_integer(
            value.get("citation_count"), f"{path}.citation_count"
        )
        categories = cls._categories(value.get("categories"), f"{path}.categories")

        return PaperSearchHit(
            title=title,
            authors=authors,
            publication_year=(
                None if publication_date is None else int(publication_date[:4])
            ),
            publication_date=publication_date,
            doi=None,
            arxiv_id=arxiv_id,
            canonical_url=canonical_url,
            abstract=abstract,
            provider_summary=provider_summary,
            provider_score=provider_score,
            citation_count=citation_count,
            categories=categories,
        )

    @classmethod
    def _authors(cls, value: object, path: str) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            comma_names = tuple(name.strip() for name in value.split(","))
            if not comma_names or any(not name for name in comma_names):
                cls._invalid_response(
                    f"{path} must contain comma-separated non-empty names"
                )
            return comma_names
        if not isinstance(value, list):
            cls._invalid_response(f"{path} must be an array or comma-separated string")
        names: list[str] = []
        for index, author in enumerate(value):
            if isinstance(author, str):
                names.append(cls._required_string(author, f"{path}[{index}]"))
            elif isinstance(author, Mapping):
                names.append(
                    cls._required_string(author.get("name"), f"{path}[{index}].name")
                )
            else:
                cls._invalid_response(
                    f"{path}[{index}] must be a name string or author object"
                )
        return tuple(names)

    @classmethod
    def _publication_date(cls, value: object, path: str) -> str | None:
        if value is None:
            return None
        raw = cls._required_string(value, path)
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            cls._invalid_response(f"{path} must be an ISO 8601 date")

    @classmethod
    def _categories(cls, value: object, path: str) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list) or not all(
            isinstance(category, str) for category in value
        ):
            cls._invalid_response(f"{path} must be an array of strings")
        return tuple(value)

    @classmethod
    def _required_string(cls, value: object, path: str) -> str:
        if not isinstance(value, str) or not value.strip():
            cls._invalid_response(f"{path} must be a non-empty string")
        return value.strip()

    @classmethod
    def _optional_string(cls, value: object, path: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            cls._invalid_response(f"{path} must be a string or null")
        return value

    @classmethod
    def _optional_float(cls, value: object, path: str) -> float | None:
        if value is None:
            return None
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            cls._invalid_response(f"{path} must be numeric or null")
        return float(value)

    @classmethod
    def _optional_integer(cls, value: object, path: str) -> int | None:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            cls._invalid_response(f"{path} must be an integer or null")
        return value

    @staticmethod
    def _invalid_response(message: str) -> NoReturn:
        raise PaperSearchProviderError(
            ProviderFailureKind.INVALID_RESPONSE,
            f"invalid paper search provider response: {message}",
        )


class DeepXivSourceAccessProvider:
    """Translate DeepXiv progressive reading into stable source semantics."""

    def __init__(
        self,
        token: str,
        *,
        reader_factory: ReaderFactory = Reader,
    ) -> None:
        if not isinstance(token, str) or not token.strip():
            raise SourceAccessConfigurationError("DEEPXIV_TOKEN is required")
        self._reader = reader_factory(token=token.strip(), max_retries=0)

    @classmethod
    def from_env(
        cls,
        *,
        reader_factory: ReaderFactory = Reader,
    ) -> DeepXivSourceAccessProvider:
        token = os.environ.get("DEEPXIV_TOKEN")
        if token is None or not token.strip():
            raise SourceAccessConfigurationError("DEEPXIV_TOKEN is required")
        return cls(token, reader_factory=reader_factory)

    def validate_inspect(self, source: PaperSource) -> None:
        self._arxiv_id(source)

    def validate_read(
        self,
        source: PaperSource,
        locator: SourceLocator | None,
    ) -> None:
        self._arxiv_id(source)
        if locator is not None and locator.kind.casefold() != "section":
            raise SourceAccessProviderError(
                SourceAccessFailureKind.UNSUPPORTED_LOCATOR,
                f"DeepXiv does not support locator kind {locator.kind!r}",
            )

    def inspect_source(
        self,
        paper_ref: str,
        source: PaperSource,
    ) -> SourceOutline:
        arxiv_id = self._arxiv_id(source)
        try:
            response = self._reader.head(arxiv_id)
        except (NotFoundError, BadRequestError):
            self._source_unavailable("paper source is not available from DeepXiv")
        except (AuthenticationError, RateLimitError, ServerError, APIError):
            self._source_unavailable("DeepXiv source access failed")
        except Exception:
            self._source_unavailable("DeepXiv source access failed unexpectedly")
        return self._map_outline(paper_ref, response)

    def read_source(
        self,
        paper_ref: str,
        source: PaperSource,
        locator: SourceLocator | None,
    ) -> SourceContent:
        arxiv_id = self._arxiv_id(source)
        if locator is None:
            content = self._read_full(arxiv_id)
            return SourceContent(
                paper_ref=paper_ref,
                locator=None,
                content=content,
            )

        self.validate_read(source, locator)
        actual_section = self._resolve_section(arxiv_id, locator.value)
        try:
            response = self._reader.section(arxiv_id, actual_section)
        except ValueError:
            raise SourceAccessProviderError(
                SourceAccessFailureKind.LOCATOR_NOT_FOUND,
                f"section {locator.value!r} was not found",
            ) from None
        except (NotFoundError, BadRequestError):
            self._source_unavailable("paper source is not available from DeepXiv")
        except (AuthenticationError, RateLimitError, ServerError, APIError):
            self._source_unavailable("DeepXiv source access failed")
        except Exception:
            self._source_unavailable("DeepXiv source access failed unexpectedly")

        content = self._source_text(response, "section content")
        return SourceContent(
            paper_ref=paper_ref,
            locator=SourceLocator(kind="section", value=actual_section),
            content=content,
        )

    def _read_full(self, arxiv_id: str) -> str:
        try:
            response = self._reader.raw(arxiv_id)
        except (NotFoundError, BadRequestError):
            self._source_unavailable("paper source is not available from DeepXiv")
        except (AuthenticationError, RateLimitError, ServerError, APIError):
            self._source_unavailable("DeepXiv source access failed")
        except Exception:
            self._source_unavailable("DeepXiv source access failed unexpectedly")
        return self._source_text(response, "full source content")

    def _resolve_section(self, arxiv_id: str, requested: str) -> str:
        try:
            response = self._reader.head(arxiv_id)
        except (NotFoundError, BadRequestError):
            self._source_unavailable("paper source is not available from DeepXiv")
        except (AuthenticationError, RateLimitError, ServerError, APIError):
            self._source_unavailable("DeepXiv source access failed")
        except Exception:
            self._source_unavailable("DeepXiv source access failed unexpectedly")

        names, _ = self._outline_values(response)
        requested_key = requested.strip().casefold()
        exact = [name for name in names if name.casefold() == requested_key]
        if len(exact) == 1:
            return exact[0]

        partial = [
            name
            for name in names
            if requested_key in self._section_label(name).casefold()
        ]
        if len(partial) == 1:
            return partial[0]
        raise SourceAccessProviderError(
            SourceAccessFailureKind.LOCATOR_NOT_FOUND,
            f"section {requested!r} was not found unambiguously",
        )

    @classmethod
    def _map_outline(cls, paper_ref: str, response: object) -> SourceOutline:
        names, total_tokens = cls._outline_values(response)
        return SourceOutline(
            paper_ref=paper_ref,
            sections=tuple(
                SourceOutlineEntry(
                    title=name,
                    locator=SourceLocator(kind="section", value=name),
                )
                for name in names
            ),
            total_tokens=total_tokens,
        )

    @classmethod
    def _outline_values(cls, response: object) -> tuple[tuple[str, ...], int | None]:
        if not isinstance(response, Mapping):
            cls._invalid_source_response("head response must be an object")
        if "sections" not in response:
            cls._invalid_source_response("head response must contain sections")
        raw_sections = response.get("sections")
        names: list[str] = []
        if isinstance(raw_sections, Mapping):
            for name in raw_sections:
                names.append(cls._required_source_string(name, "section name"))
        elif isinstance(raw_sections, list):
            for index, item in enumerate(raw_sections):
                if isinstance(item, str):
                    name = item
                elif isinstance(item, Mapping):
                    name = item.get("name")
                else:
                    cls._invalid_source_response(
                        f"sections[{index}] must be a string or object"
                    )
                names.append(
                    cls._required_source_string(name, f"sections[{index}].name")
                )
        else:
            cls._invalid_source_response("sections must be an object or array")

        if len({name.casefold() for name in names}) != len(names):
            cls._invalid_source_response("section names must be unique")

        total_tokens = response.get("token_count")
        if total_tokens is not None and (
            not isinstance(total_tokens, int)
            or isinstance(total_tokens, bool)
            or total_tokens < 0
        ):
            cls._invalid_source_response(
                "token_count must be a non-negative integer or null"
            )
        return tuple(names), total_tokens

    @staticmethod
    def _section_label(value: str) -> str:
        label = value.strip()
        prefix, separator, remainder = label.partition(" ")
        if separator and prefix.rstrip(".").replace(".", "").isdigit():
            return remainder.strip()
        return label

    @classmethod
    def _arxiv_id(cls, source: PaperSource) -> str:
        if not isinstance(source, PaperSource) or source.arxiv_id is None:
            raise SourceAccessProviderError(
                SourceAccessFailureKind.SOURCE_UNAVAILABLE,
                "DeepXiv source access requires an arXiv identifier",
            )
        normalized = normalize_arxiv_id(source.arxiv_id)
        if not normalized:
            raise SourceAccessProviderError(
                SourceAccessFailureKind.SOURCE_UNAVAILABLE,
                "DeepXiv source access requires a non-empty arXiv identifier",
            )
        return normalized

    @classmethod
    def _source_text(cls, value: object, path: str) -> str:
        if not isinstance(value, str) or not value.strip():
            cls._invalid_source_response(f"{path} must be non-empty text")
        return value

    @classmethod
    def _required_source_string(cls, value: object, path: str) -> str:
        if not isinstance(value, str) or not value.strip():
            cls._invalid_source_response(f"{path} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _source_unavailable(message: str) -> NoReturn:
        raise SourceAccessProviderError(
            SourceAccessFailureKind.SOURCE_UNAVAILABLE,
            message,
        ) from None

    @staticmethod
    def _invalid_source_response(message: str) -> NoReturn:
        raise SourceAccessProviderError(
            SourceAccessFailureKind.INVALID_RESPONSE,
            f"invalid source provider response: {message}",
        )
