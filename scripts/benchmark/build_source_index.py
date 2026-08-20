#!/usr/bin/env python3
"""Maintainer tool: strip OmniDocBench GT down to source-only routing metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(args: argparse.Namespace) -> None:
    annotations: list[dict[str, Any]] = json.loads(args.annotations.read_text(encoding="utf-8"))
    result = []
    for item in annotations:
        info = item["page_info"]
        relative = Path(info["image_path"])
        source = args.images / relative
        result.append(
            {
                "page_info": {
                    "image_path": relative.as_posix(),
                    "page_attribute": {"subset": info.get("page_attribute", {}).get("subset")},
                },
                "source_bytes": source.stat().st_size,
                "source_sha256": sha256_file(source),
            }
        )
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
