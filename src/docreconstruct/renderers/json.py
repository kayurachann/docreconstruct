"""Canonical, deterministic JSON renderer."""

from __future__ import annotations

import dataclasses
import enum
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from .base import Renderer


def to_jsonable(obj: Any) -> Any:
    """Convert IR and plugin values to JSON primitives deterministically."""

    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        return obj
    if isinstance(obj, enum.Enum):
        return to_jsonable(obj.value)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (bytes, bytearray, memoryview)):
        # Arbitrary binary data does not belong in the canonical IR.  Encoding
        # it as a byte list would be huge and ambiguous, so use a stable marker.
        import base64

        return {
            "$binary": base64.b64encode(bytes(obj)).decode("ascii"),
            "encoding": "base64",
        }
    if hasattr(obj, "model_dump"):
        return to_jsonable(obj.model_dump(mode="json", exclude_none=False))
    if dataclasses.is_dataclass(obj):
        return to_jsonable(dataclasses.asdict(cast(Any, obj)))
    if isinstance(obj, Mapping):
        return {str(key): to_jsonable(obj[key]) for key in sorted(obj, key=lambda item: str(item))}
    if isinstance(obj, (set, frozenset)):
        values = [to_jsonable(item) for item in obj]
        return sorted(values, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
        return [to_jsonable(item) for item in obj]
    if hasattr(obj, "__dict__"):
        return to_jsonable(
            {key: value for key, value in vars(obj).items() if not key.startswith("_")}
        )
    raise TypeError(f"value of type {type(obj).__name__} is not JSON serializable")


class JSONRenderer(Renderer[str]):
    """Serialize the complete IR without dropping geometry or provenance."""

    format = "json"
    extension = ".json"
    media_type = "application/json"

    def __init__(
        self,
        *,
        indent: int | None = 2,
        sort_keys: bool = True,
        trailing_newline: bool = True,
    ) -> None:
        self.indent = indent
        self.sort_keys = sort_keys
        self.trailing_newline = trailing_newline

    def render(self, document: Any) -> str:
        payload = json.dumps(
            to_jsonable(document),
            ensure_ascii=False,
            allow_nan=False,
            indent=self.indent,
            sort_keys=self.sort_keys,
            separators=(",", ":") if self.indent is None else None,
        )
        return payload + ("\n" if self.trailing_newline else "")


# Friendly spelling for callers that prefer conventional initialism casing.
JsonRenderer = JSONRenderer
