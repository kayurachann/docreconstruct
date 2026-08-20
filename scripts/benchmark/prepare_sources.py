#!/usr/bin/env python3
"""Prepare one immutable OmniDocBench raw-image shard from a GT-free index.

The source index contains only page filenames, subset labels, source byte hashes,
and sizes.  It deliberately excludes text, formula, table, order, and geometry
annotations. Every parser job for a shard restores the same byte-verified raw
images made by this script; no candidate-specific container conversion occurs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

DATASET_REPOSITORY = "opendatalab/OmniDocBench"
DATASET_REVISION = "aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec"
_SOURCE_INDEX_KEYS = {"page_info", "source_bytes", "source_sha256"}
_PAGE_INFO_KEYS = {"image_path", "page_attribute"}
_PAGE_ATTRIBUTE_KEYS = {"subset"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def subset_matches(selection: str, page_subset: str | None) -> bool:
    requested = selection.casefold()
    actual = (page_subset or "").casefold()
    if requested == "all":
        return True
    if requested == "hard":
        return actual.endswith("_hard")
    return requested == actual


def validate_source_index_item(item: dict[str, Any]) -> None:
    if set(item) != _SOURCE_INDEX_KEYS:
        raise ValueError(
            f"source index item has forbidden keys: {sorted(set(item) - _SOURCE_INDEX_KEYS)}"
        )
    info = item.get("page_info")
    if not isinstance(info, dict) or set(info) != _PAGE_INFO_KEYS:
        raise ValueError("source index page_info must contain only image_path and page_attribute")
    attributes = info.get("page_attribute")
    if not isinstance(attributes, dict) or set(attributes) != _PAGE_ATTRIBUTE_KEYS:
        raise ValueError("source index page_attribute must contain only subset")
    digest = item.get("source_sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise ValueError("source index source_sha256 must be lowercase SHA-256")
    if not isinstance(item.get("source_bytes"), int) or item["source_bytes"] <= 0:
        raise ValueError("source index source_bytes must be a positive integer")


def load_index(path: Path, subset: str, shard_index: int, shard_count: int) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("source index must be a JSON array")
    selected: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("source index items must be JSON objects")
        validate_source_index_item(item)
        info = item["page_info"]
        page_subset = info.get("page_attribute", {}).get("subset")
        if subset_matches(subset, page_subset):
            selected.append(item)
    sharded = [
        item for ordinal, item in enumerate(selected) if ordinal % shard_count == shard_index
    ]
    if not sharded:
        raise ValueError(f"empty selection for {subset=} shard {shard_index}/{shard_count}")
    return sharded


def download_source(item: dict[str, Any], sources_dir: Path) -> Path:
    relative = Path(item["page_info"]["image_path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe source path: {relative}")
    destination = sources_dir / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    quoted = urllib.parse.quote(relative.as_posix(), safe="/")
    url = (
        f"https://huggingface.co/datasets/{DATASET_REPOSITORY}/resolve/"
        f"{DATASET_REVISION}/images/{quoted}?download=true"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "docreconstruct-benchmark/0.1"})
    with (
        urllib.request.urlopen(request, timeout=180) as response,  # noqa: S310
        tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream,
    ):
        temporary = Path(stream.name)
        shutil.copyfileobj(response, stream)
    try:
        actual_size = temporary.stat().st_size
        actual_sha = sha256_file(temporary)
        expected_size = int(item["source_bytes"])
        expected_sha = str(item["source_sha256"])
        if actual_size != expected_size or actual_sha != expected_sha:
            raise RuntimeError(
                f"source integrity mismatch for {relative}: "
                f"expected {expected_size}/{expected_sha}, got {actual_size}/{actual_sha}"
            )
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def corpus_manifest(
    index: Path,
    subset: str,
    shard_index: int,
    shard_count: int,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "dataset_repository": DATASET_REPOSITORY,
        "dataset_revision": DATASET_REVISION,
        "source_index_sha256": sha256_file(index),
        "subset": subset,
        "shard": {"index": shard_index, "count": shard_count},
        "input_format": "official-raw-image-bytes",
        "files": [
            {
                "input_name": Path(item["page_info"]["image_path"]).as_posix(),
                "source_sha256": item["source_sha256"],
                "source_bytes": item["source_bytes"],
            }
            for item in items
        ],
    }


def validate_arguments(args: argparse.Namespace) -> None:
    if args.shard_count <= 0 or not 0 <= args.shard_index < args.shard_count:
        raise ValueError("require 0 <= shard-index < shard-count")
    if args.workers <= 0:
        raise ValueError("workers must be positive")


def prepare(args: argparse.Namespace) -> None:
    validate_arguments(args)
    output = args.output.resolve()
    sources = output / "sources"
    output.mkdir(parents=True, exist_ok=True)
    items = load_index(args.index, args.subset, args.shard_index, args.shard_count)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        downloaded = list(executor.map(lambda item: download_source(item, sources), items))

    if len(downloaded) != len(items):
        raise RuntimeError("downloaded source count changed unexpectedly")

    shutil.copy2(args.index, output / "source-index.json")
    manifest = corpus_manifest(args.index, args.subset, args.shard_index, args.shard_count, items)
    (output / "corpus-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Prepared {len(items)} shared raw images in {output}")


def verify(args: argparse.Namespace) -> dict[str, Any]:
    """Byte-verify a restored cache against the committed GT-free source index."""
    validate_arguments(args)
    output = args.output.resolve()
    sources = output / "sources"
    items = load_index(args.index, args.subset, args.shard_index, args.shard_count)
    expected = corpus_manifest(args.index, args.subset, args.shard_index, args.shard_count, items)
    expected_by_name = {item["input_name"]: item for item in expected["files"]}

    if not sources.is_dir():
        raise RuntimeError(f"source directory is missing: {sources}")
    actual_by_name: dict[str, Path] = {}
    for path in sorted(sources.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(
                f"source cache contains a symbolic link: {path.relative_to(sources)}"
            )
        if path.is_file():
            actual_by_name[path.relative_to(sources).as_posix()] = path
    missing = sorted(set(expected_by_name) - set(actual_by_name))
    extra = sorted(set(actual_by_name) - set(expected_by_name))
    if missing or extra:
        raise RuntimeError(f"source cache set mismatch: missing={missing[:20]}, extra={extra[:20]}")

    verified_files = []
    for name, item in expected_by_name.items():
        path = actual_by_name[name]
        actual_size = path.stat().st_size
        actual_sha = sha256_file(path)
        if actual_size != item["source_bytes"] or actual_sha != item["source_sha256"]:
            raise RuntimeError(
                f"source cache integrity mismatch for {name}: expected "
                f"{item['source_bytes']}/{item['source_sha256']}, got "
                f"{actual_size}/{actual_sha}"
            )
        verified_files.append(
            {"input_name": name, "source_bytes": actual_size, "source_sha256": actual_sha}
        )

    cached_index = output / "source-index.json"
    manifest_path = output / "corpus-manifest.json"
    if not cached_index.is_file() or sha256_file(cached_index) != sha256_file(args.index):
        raise RuntimeError("cached source-index.json does not match the committed source index")
    try:
        actual_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("cached corpus manifest is missing or malformed") from exc
    if actual_manifest != expected:
        raise RuntimeError("cached corpus manifest does not match the selected committed index")

    canonical_files = json.dumps(verified_files, sort_keys=True, separators=(",", ":")).encode()
    verification = {
        "schema_version": 1,
        "valid": True,
        "dataset_revision": DATASET_REVISION,
        "source_index_sha256": sha256_file(args.index),
        "corpus_manifest_sha256": sha256_file(manifest_path),
        "subset": args.subset,
        "shard": {"index": args.shard_index, "count": args.shard_count},
        "file_count": len(verified_files),
        "source_files_sha256": hashlib.sha256(canonical_files).hexdigest(),
    }
    verification_path = output / "source-verification.json"
    verification_path.write_text(json.dumps(verification, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(verified_files)} cached raw images in {output}")
    return verification


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--subset", choices=("hard", "all"), required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--verify-only", action="store_true")
    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    if not parsed.verify_only:
        prepare(parsed)
    verify(parsed)
