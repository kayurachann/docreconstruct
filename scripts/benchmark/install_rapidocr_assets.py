#!/usr/bin/env python3
"""Install byte-pinned RapidOCR assets into the pinned candidate package."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from materialize_pinned_models import safe_record_path, verify_allowlist, verify_file_record


def rapidocr_models_directory() -> Path:
    spec = importlib.util.find_spec("rapidocr")
    if spec is None or spec.origin is None:
        raise RuntimeError("the pinned rapidocr package is not installed")
    package = Path(spec.origin).absolute().parent
    models = package / "models"
    if package.is_symlink() or models.is_symlink():
        raise RuntimeError(f"unsafe RapidOCR package model directory: {models}")
    return models


def install_assets(
    cache: Path,
    models: Path,
    records: list[dict[str, Any]],
    *,
    verify_only: bool,
) -> None:
    verify_allowlist(cache, records)
    if models.is_symlink() or (models.exists() and not models.is_dir()):
        raise RuntimeError(f"unsafe RapidOCR package model directory: {models}")
    if not verify_only:
        models.mkdir(parents=True, exist_ok=True)
        for record in records:
            source = safe_record_path(cache, record["path"])
            destination = safe_record_path(models, record["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=".docreconstruct-",
                    dir=destination.parent,
                    delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    with source.open("rb") as source_stream:
                        shutil.copyfileobj(source_stream, stream, length=1024 * 1024)
                verify_file_record(temporary, record)
                temporary.replace(destination)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
    for record in records:
        verify_file_record(safe_record_path(models, record["path"]), record)


def main(args: argparse.Namespace) -> None:
    pins = json.loads(args.pins.read_text(encoding="utf-8"))
    records = pins["systems"]["docling"]["rapidocr_assets"]
    models = args.models_dir or rapidocr_models_directory()
    install_assets(args.cache, models, records, verify_only=args.verify_only)
    print(models.absolute())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pins", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--models-dir", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    main(parser.parse_args())
