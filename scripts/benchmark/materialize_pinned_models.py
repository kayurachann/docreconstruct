#!/usr/bin/env python3
"""Download only declared immutable Hugging Face model snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def repository_cache_name(repo: str) -> str:
    return "models--" + repo.replace("/", "--")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_allowlist(path: Path, records: list[dict[str, Any]]) -> None:
    declared = {record["path"] for record in records}
    if len(declared) != len(records):
        raise RuntimeError(f"duplicate file in snapshot allowlist: {path}")
    actual = {item.relative_to(path).as_posix() for item in path.rglob("*") if item.is_file()}
    if actual != declared:
        raise RuntimeError(
            f"snapshot differs from exact allowlist: {path}; "
            f"missing={sorted(declared - actual)}, extra={sorted(actual - declared)}"
        )
    for record in records:
        item = path / record["path"]
        size = item.stat().st_size
        digest = sha256_file(item)
        if size != record["bytes"] or digest != record["sha256"]:
            raise RuntimeError(
                f"pinned snapshot file changed: {item}; expected "
                f"{record['bytes']}/{record['sha256']}, got {size}/{digest}"
            )


def write_mineru_config(destination: Path, model_root: Path) -> None:
    payload = {
        "config_version": "1.3.2",
        "model-source": "local",
        "models-dir": {"pipeline": str(model_root.resolve()), "vlm": ""},
        "latex-delimiter-config": {
            "display": {"left": "$$", "right": "$$"},
            "inline": {"left": "$", "right": "$"},
        },
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main(args: argparse.Namespace) -> None:
    from huggingface_hub import snapshot_download

    pins: dict[str, Any] = json.loads(args.pins.read_text(encoding="utf-8"))
    system = pins["systems"][args.system]
    hub = args.hf_home / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    realized = []
    mineru_root: Path | None = None
    for snapshot in system["huggingface_snapshots"]:
        repo = snapshot["repo"]
        revision = snapshot["revision"]
        records = snapshot.get("files", [])
        path = Path(
            snapshot_download(
                repo_id=repo,
                repo_type="model",
                revision=revision,
                cache_dir=hub,
                local_files_only=args.offline,
                allow_patterns=[record["path"] for record in records] or None,
            )
        )
        if path.name != revision or not any(item.is_file() for item in path.rglob("*")):
            raise RuntimeError(f"incomplete pinned snapshot for {repo}@{revision}: {path}")
        if records:
            verify_allowlist(path, records)
        if args.system == "mineru" and repo == "opendatalab/PDF-Extract-Kit-1.0":
            mineru_root = path
        reference = hub / repository_cache_name(repo) / "refs" / "main"
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_text(revision, encoding="ascii")
        realized.append(
            {
                "repo": repo,
                "revision": revision,
                "declared_files": len(records) if records else None,
                "declared_bytes": sum(record["bytes"] for record in records),
            }
        )
    if args.system == "mineru":
        if args.mineru_config is None or mineru_root is None:
            raise RuntimeError("MinerU requires --mineru-config and its pinned pipeline snapshot")
        write_mineru_config(args.mineru_config, mineru_root)
    args.output.write_text(json.dumps(realized, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pins", type=Path, required=True)
    parser.add_argument("--system", choices=("docling", "mineru", "marker"), required=True)
    parser.add_argument("--hf-home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mineru-config", type=Path)
    parser.add_argument("--offline", action="store_true")
    main(parser.parse_args())
