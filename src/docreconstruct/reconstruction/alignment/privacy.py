"""Opaque identifiers and canonical hashes for content-safe alignment reports."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def canonical_sha256(value: Any) -> str:
    """Hash JSON-compatible data without exposing the underlying authority content."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def opaque_block_id(index: int) -> str:
    return f"block-{index + 1:06d}"


def opaque_source_id(document_payload: Mapping[str, Any]) -> str:
    """Use a digest so source order, paths, provider names, and text stay private."""

    return f"source-{canonical_sha256(document_payload)[:16]}"


def opaque_element_id(source_id: str, page_number: int, identity: int | str) -> str:
    payload = {"source": source_id, "page": page_number, "identity": identity}
    return f"element-{canonical_sha256(payload)[:24]}"


def opaque_candidate_id(
    *,
    block_id: str,
    element_ids: Sequence[str],
    page_number: int,
    source_id: str,
    start: int,
    end: int,
    origin: str,
) -> str:
    return canonical_sha256(
        {
            "block_id": block_id,
            "element_ids": sorted(set(element_ids)),
            "page_number": page_number,
            "source_id": source_id,
            "start": start,
            "end": end,
            "origin": origin,
        }
    )


__all__ = [
    "canonical_sha256",
    "opaque_block_id",
    "opaque_candidate_id",
    "opaque_element_id",
    "opaque_source_id",
]
