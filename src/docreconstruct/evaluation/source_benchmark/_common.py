"""Shared deterministic helpers for the source-only benchmark harness."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

SOURCE_BENCHMARK_SCHEMA_VERSION = "0.1"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_bounded(path: Path, maximum_bytes: int) -> bytes:
    """Read at most one byte beyond a declared limit from one open handle."""

    with path.open("rb") as stream:
        return stream.read(maximum_bytes + 1)


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_digest(value: Any) -> str:
    return sha256_bytes(stable_json(value).encode("utf-8"))


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def string_sequence(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{label} must be an array of strings")
    result = tuple(required_string(item, f"{label}[]") for item in value)
    if not result:
        raise ValueError(f"{label} must not be empty")
    return result


def resolve_path(root: Path, value: Any, label: str) -> Path:
    text = required_string(value, label)
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def relative_public_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def percentile(values: Sequence[float], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile_value
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


@lru_cache(maxsize=128)
def executable_identity(command: str) -> tuple[str, str | None]:
    """Resolve and hash a candidate executable once per harness process."""

    executable = shutil.which(command)
    executable_path = Path(executable) if executable else Path(command)
    digest = sha256_file(executable_path) if executable_path.is_file() else None
    return executable_path.name, digest
