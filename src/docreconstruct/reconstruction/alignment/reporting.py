"""Atomic serialization for portable alignment diagnostics."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .models import AlignmentReport


def write_alignment_report(report: AlignmentReport, destination: str | Path) -> Path:
    """Write a complete report atomically; a failed job never leaves partial JSON."""

    path = Path(destination).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(report.model_dump_json(indent=2))
            stream.write("\n")
            temporary = Path(stream.name)
        os.replace(temporary, path)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return path


__all__ = ["write_alignment_report"]
