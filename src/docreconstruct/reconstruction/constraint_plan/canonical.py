"""Canonical serialization helpers for immutable constraint plans."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> bytes:
    """Serialize JSON-compatible data without platform- or insertion-order variance."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def stable_digest(value: Any) -> str:
    """Return the lowercase SHA-256 digest of canonical JSON data."""

    return hashlib.sha256(canonical_json(value)).hexdigest()


__all__ = ["canonical_json", "stable_digest"]
