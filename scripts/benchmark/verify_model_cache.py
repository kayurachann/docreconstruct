#!/usr/bin/env python3
"""Reject a model cache that does not match the declared immutable revisions."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
from typing import Any

from materialize_pinned_models import (
    repository_cache_name,
    verify_allowlist,
    verify_revision_aliases,
)


def populated(path: Path) -> bool:
    return path.is_dir() and any(item.is_file() for item in path.rglob("*"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: Path, record: dict[str, Any]) -> None:
    if not path.is_file():
        raise RuntimeError(f"pinned cache file is missing: {path}")
    actual_size = path.stat().st_size
    actual_sha256 = sha256_file(path)
    if actual_size != record["bytes"] or actual_sha256 != record["sha256"]:
        raise RuntimeError(
            f"pinned cache file changed: {path}; expected "
            f"{record['bytes']}/{record['sha256']}, got {actual_size}/{actual_sha256}"
        )


def main(args: argparse.Namespace) -> None:
    pins: dict[str, Any] = json.loads(args.pins.read_text(encoding="utf-8"))
    system = pins["systems"][args.system]
    package_name, expected_version = str(system["package"]).split("==", maxsplit=1)
    actual_version = importlib.metadata.version(package_name)
    if actual_version != expected_version:
        raise RuntimeError(
            f"installed {package_name} version {actual_version}, expected {expected_version}"
        )
    missing = []
    for snapshot in system["huggingface_snapshots"]:
        repo_root = args.hf_home / "hub" / repository_cache_name(snapshot["repo"])
        path = repo_root / "snapshots" / snapshot["revision"]
        if not populated(path):
            missing.append(str(path.relative_to(args.hf_home)))
        records = snapshot.get("files", [])
        if records and populated(path):
            verify_allowlist(path, records, allow_symlinks=True)
        verify_revision_aliases(repo_root, snapshot)
    for relative in system["direct_cache_directories"]:
        path = args.direct_cache / relative
        if not populated(path):
            missing.append(f"direct:{relative}")
    if missing:
        raise RuntimeError(f"pinned model cache is incomplete: {missing}")
    rapidocr_assets = system.get("rapidocr_assets", [])
    if rapidocr_assets:
        if args.rapidocr_cache is None:
            raise RuntimeError("Docling RapidOCR assets require --rapidocr-cache")
        verify_allowlist(args.rapidocr_cache, rapidocr_assets)
    if args.system == "mineru":
        if args.mineru_config is None or not args.mineru_config.is_file():
            raise RuntimeError("pinned MinerU config is missing")
        config = json.loads(args.mineru_config.read_text(encoding="utf-8"))
        snapshot = system["huggingface_snapshots"][0]
        expected_root = (
            args.hf_home
            / "hub"
            / repository_cache_name(snapshot["repo"])
            / "snapshots"
            / snapshot["revision"]
        ).resolve()
        configured_root = Path(config.get("models-dir", {}).get("pipeline", "")).resolve()
        if config.get("model-source") != "local" or configured_root != expected_root:
            raise RuntimeError(
                f"MinerU config does not select the pinned offline snapshot: {args.mineru_config}"
            )
    for record in system.get("direct_cache_files", []):
        verify_file(args.direct_cache / record["path"], record)
    font = system.get("font")
    if font is not None:
        if args.font_path is None:
            raise RuntimeError("--font-path is required for this system")
        if args.font_path.name != font["name"]:
            raise RuntimeError(
                f"pinned font name changed: expected {font['name']}, got {args.font_path.name}"
            )
        verify_file(args.font_path, font)
    print(f"Verified pinned model cache for {args.system}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pins", type=Path, required=True)
    parser.add_argument("--system", choices=("docling", "mineru", "marker"), required=True)
    parser.add_argument("--hf-home", type=Path, required=True)
    parser.add_argument("--direct-cache", type=Path, required=True)
    parser.add_argument("--font-path", type=Path)
    parser.add_argument("--mineru-config", type=Path)
    parser.add_argument("--rapidocr-cache", type=Path)
    main(parser.parse_args())
