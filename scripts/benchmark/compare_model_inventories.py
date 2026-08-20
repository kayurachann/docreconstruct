#!/usr/bin/env python3
"""Require every inference shard to use the exact prepared model-cache bytes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def cache_map(payload: dict[str, Any], source: Path) -> dict[str, list[dict[str, Any]]]:
    caches = payload.get("model_caches")
    if not isinstance(caches, list):
        raise RuntimeError(f"model_caches is not a list in {source}")
    result: dict[str, list[dict[str, Any]]] = {}
    for cache in caches:
        if not isinstance(cache, dict) or not isinstance(cache.get("label"), str):
            raise RuntimeError(f"invalid model-cache entry in {source}")
        label = cache["label"]
        files = cache.get("files")
        if label in result or not isinstance(files, list):
            raise RuntimeError(f"duplicate or invalid model-cache label {label!r} in {source}")
        normalized = []
        for record in files:
            if not isinstance(record, dict) or set(record) != {"name", "bytes", "sha256"}:
                raise RuntimeError(f"invalid file record under {label!r} in {source}")
            normalized.append(record)
        result[label] = sorted(normalized, key=lambda record: record["name"])
    return result


def main(args: argparse.Namespace) -> None:
    prepared = json.loads(args.prepared.read_text(encoding="utf-8"))
    inference = json.loads(args.inference.read_text(encoding="utf-8"))
    prepared_caches = cache_map(prepared, args.prepared)
    inference_caches = cache_map(inference, args.inference)
    if inference_caches != prepared_caches:
        prepared_labels = set(prepared_caches)
        inference_labels = set(inference_caches)
        changed = sorted(
            label
            for label in prepared_labels & inference_labels
            if prepared_caches[label] != inference_caches[label]
        )
        raise RuntimeError(
            "inference model cache differs from prepared inventory: "
            f"missing={sorted(prepared_labels - inference_labels)}, "
            f"extra={sorted(inference_labels - prepared_labels)}, changed={changed}"
        )
    prepared_environment = prepared.get("normalized_environment")
    inference_environment = inference.get("normalized_environment")
    if not isinstance(prepared_environment, dict) or not isinstance(inference_environment, dict):
        raise RuntimeError("normalized_environment is missing from a model inventory")
    if inference_environment != prepared_environment:
        raise RuntimeError(
            "inference Python/external runtime differs from the prepared environment"
        )
    file_count = sum(len(files) for files in inference_caches.values())
    print(f"Verified {file_count} model-cache files against the prepared inventory")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--inference", type=Path, required=True)
    main(parser.parse_args())
