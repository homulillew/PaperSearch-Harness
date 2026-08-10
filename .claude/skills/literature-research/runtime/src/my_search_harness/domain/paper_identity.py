"""Conservative deterministic identity keys for persistent papers."""

from __future__ import annotations

import re
from typing import TypeAlias

from .model import PaperSource


PaperIdentityKey: TypeAlias = tuple[str, str]
_ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)


def normalize_doi(value: str) -> str:
    normalized = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    return normalized


def normalize_arxiv_id(value: str) -> str:
    normalized = value.strip().lower()
    for prefix in (
        "https://arxiv.org/abs/",
        "http://arxiv.org/abs/",
        "https://arxiv.org/pdf/",
        "http://arxiv.org/pdf/",
        "arxiv:",
    ):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    if normalized.endswith(".pdf"):
        normalized = normalized[:-4]
    return _ARXIV_VERSION.sub("", normalized)


def paper_identity_keys(source: PaperSource) -> tuple[PaperIdentityKey, ...]:
    """Return only identifiers with frozen deterministic identity semantics."""

    keys: list[PaperIdentityKey] = []
    if source.doi is not None:
        keys.append(("doi", normalize_doi(source.doi)))
    if source.arxiv_id is not None:
        keys.append(("arxiv", normalize_arxiv_id(source.arxiv_id)))
    if source.canonical_url is not None:
        keys.append(("url", source.canonical_url.strip()))
    return tuple(keys)
