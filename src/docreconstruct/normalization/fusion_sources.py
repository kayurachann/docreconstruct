"""Stable source-document identities for provider-independent fusion."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from docreconstruct.ir import Document, Page

from .fusion_clustering import FusionPageSource, provider_sort_key


def page_sources(pages: list[Page]) -> list[FusionPageSource]:
    """Wrap standalone pages with content-derived source identities."""

    return [
        FusionPageSource(page=page, source_identity=f"page-source:{_page_fingerprint(page)}")
        for page in pages
    ]


def document_source_identity(document: Document) -> str:
    """Return an order-invariant source identity independent of page IDs."""

    payload = _normalized_document(document)
    payload["source_document_id"] = document.id
    return f"document-source:{_fingerprint(payload)}"


def page_sort_key(page: Page) -> tuple[object, ...]:
    engines = sorted(
        {
            element.provenance.engine.casefold()
            for element in page.elements
            if element.provenance is not None
        },
        key=provider_sort_key,
    )
    return (
        tuple(provider_sort_key(engine) for engine in engines),
        _canonical_json(_normalized_page(page)),
        page.id,
    )


def document_sort_key(document: Document) -> tuple[object, ...]:
    engines = sorted(
        {
            element.provenance.engine.casefold()
            for page in document.pages
            for element in page.elements
            if element.provenance is not None
        },
        key=provider_sort_key,
    )
    return (
        tuple(provider_sort_key(engine) for engine in engines),
        _canonical_json(_normalized_document(document)),
        document.id,
    )


def _page_fingerprint(page: Page) -> str:
    return _fingerprint(_normalized_page(page))


def _normalized_document(document: Document) -> dict[str, Any]:
    payload = document.model_dump(mode="json")
    payload.pop("id", None)
    payload["pages"] = [_normalized_page(page) for page in document.pages]
    payload["pages"].sort(key=_canonical_json)
    return payload


def _normalized_page(page: Page) -> dict[str, Any]:
    payload = page.model_dump(mode="json")
    payload.pop("id", None)
    elements = list(payload.get("elements", []))
    elements.sort(key=_canonical_json)
    payload["elements"] = elements
    return payload


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "document_sort_key",
    "document_source_identity",
    "page_sort_key",
    "page_sources",
]
