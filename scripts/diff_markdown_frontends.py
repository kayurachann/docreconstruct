"""Diff the two Markdown front-ends over a corpus of content files.

Both front-ends must produce the same block stream for the same source;
this script measures where they do not.  It is the parallel-run evidence
for keeping the legacy scanner selectable while markdown-it segments by
default: run it over the committed showcases (the default), a test corpus,
or any real document, and paste the numbers into the change that alters
either front-end.

Usage:
    python scripts/diff_markdown_frontends.py [PATH ...]

Each PATH is a Markdown file or a directory searched recursively for
``*.md``.  Exit code 1 when any file parses differently.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _corpus(arguments: list[Path]) -> list[Path]:
    if not arguments:
        arguments = [ROOT / "docs" / "showcases"]
    files: list[Path] = []
    for argument in arguments:
        if argument.is_dir():
            files.extend(sorted(argument.rglob("*.md")))
        elif argument.is_file():
            files.append(argument)
        else:
            raise FileNotFoundError(argument)
    return files


def _dumps(path: Path, frontend: str) -> list[dict[str, Any]]:
    from docreconstruct.reconstruction.markdown_content import parse_markdown_content

    return [block.model_dump() for block in parse_markdown_content(path, frontend=frontend).blocks]


def _first_difference(
    legacy: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
) -> str:
    for index, (left, right) in enumerate(zip(legacy, candidate, strict=False)):
        if left != right:
            fields = sorted(key for key in left if left[key] != right.get(key))
            details = "; ".join(
                f"{key}: legacy={left[key]!r} markdown-it={right.get(key)!r}" for key in fields
            )
            return f"block {index} differs — {details}"
    index = min(len(legacy), len(candidate))
    longer = "legacy" if len(legacy) > len(candidate) else "markdown-it"
    extra = (legacy if len(legacy) > len(candidate) else candidate)[index]
    count = abs(len(legacy) - len(candidate))
    preview = {"kind": extra["kind"], "text": extra["text"][:80]}
    return f"{longer} has {count} extra block(s) from index {index}: {preview}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    arguments = parser.parse_args()

    files = _corpus(list(arguments.paths))
    if not files:
        print("no Markdown files found")
        return 1
    identical = 0
    different = 0
    for path in files:
        legacy = _dumps(path, "legacy")
        candidate = _dumps(path, "markdown-it")
        if legacy == candidate:
            identical += 1
            print(f"IDENTICAL  {path} ({len(legacy)} blocks)")
        else:
            different += 1
            print(f"DIFFERENT  {path} (legacy={len(legacy)}, markdown-it={len(candidate)})")
            print(f"           {_first_difference(legacy, candidate)}")
    print(f"\n{identical} identical, {different} different of {identical + different} file(s)")
    return 1 if different else 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    sys.exit(main())
