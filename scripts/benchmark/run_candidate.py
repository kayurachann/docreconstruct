#!/usr/bin/env python3
"""Normalize native parser Markdown to the exact harness destination."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(command: list[str], *, cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, stdin=subprocess.DEVNULL, check=False)
    if completed.returncode < 0 and os.name == "posix":
        os.kill(os.getpid(), -completed.returncode)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def unique_markdown(root: Path, expected_stem: str) -> Path:
    exact = sorted(root.rglob(f"{expected_stem}.md"))
    candidates = exact or sorted(root.rglob("*.md"))
    if len(candidates) != 1:
        names = ", ".join(path.relative_to(root).as_posix() for path in candidates[:10])
        raise RuntimeError(f"expected one native Markdown output, found {len(candidates)}: {names}")
    return candidates[0]


def run_docling(source: Path, native: Path) -> Path:
    run(
        [
            "docling",
            "convert",
            str(source),
            "--to",
            "md",
            "--image-export-mode",
            "placeholder",
            "--output",
            str(native),
            "--device",
            "cpu",
            "--num-threads",
            "4",
            "--page-batch-size",
            "1",
            "--document-timeout",
            "840",
            "--abort-on-error",
        ],
        cwd=native,
    )
    return unique_markdown(native, source.stem)


def run_mineru(source: Path, native: Path) -> Path:
    run(
        [
            "mineru",
            "-p",
            str(source),
            "-o",
            str(native),
            "-b",
            "pipeline",
            "-m",
            "ocr",
            "-f",
            "true",
            "-t",
            "true",
        ],
        cwd=native,
    )
    return unique_markdown(native, source.stem)


def run_marker(source: Path, native: Path) -> Path:
    run(
        [
            "marker_single",
            str(source),
            "--mode",
            "fast",
            "--output_format",
            "markdown",
            "--output_dir",
            str(native),
        ],
        cwd=native,
    )
    return unique_markdown(native, source.stem)


def run_tesseract(source: Path, destination: Path, tessdata: Path) -> None:
    from docreconstruct.pipeline import export
    from docreconstruct.providers import ProviderContext, get_provider

    provider = get_provider("tesseract_local")
    result = provider.parse(
        source,
        context=ProviderContext(
            source=str(source),
            options={
                "language": "eng+chi_sim+chi_tra",
                "tessdata_dir": str(tessdata),
                "page_segmentation": 3,
                "timeout_seconds": 240,
            },
        ),
    )
    export(result.document, destination, output_format="markdown")


def main(args: argparse.Namespace) -> None:
    source = args.input.resolve()
    destination = args.output.resolve()
    native = args.work_dir.resolve() / "native-output"
    native.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if args.system == "docreconstruct-tesseract":
        if args.tessdata is None:
            raise ValueError("--tessdata is required for docreconstruct-tesseract")
        run_tesseract(source, destination, args.tessdata.resolve())
        return
    runners = {"docling": run_docling, "mineru": run_mineru, "marker": run_marker}
    native_markdown = runners[args.system](source, native)
    shutil.copyfile(native_markdown, destination)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--system",
        choices=("docreconstruct-tesseract", "docling", "mineru", "marker"),
        required=True,
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--tessdata", type=Path)
    return parser


if __name__ == "__main__":
    try:
        main(build_parser().parse_args())
    except Exception as exc:
        print(f"candidate wrapper failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
