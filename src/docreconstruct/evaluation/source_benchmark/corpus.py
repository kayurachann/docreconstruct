"""OmniDocBench-style corpus selection, sharding, and source hashing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ._common import required_string, sha256_file
from .models import SourceBenchmarkCase, SourceBenchmarkManifest


def _subset_matches(subset: str, page_subset: str | None) -> bool:
    selected = subset.strip().casefold()
    actual = (page_subset or "").strip().casefold()
    if selected == "all":
        return True
    if selected == "hard":
        return actual.endswith("_hard")
    return actual == selected


def load_omnidocbench_cases(
    manifest: SourceBenchmarkManifest,
    *,
    subset: str | None = None,
    shard_index: int = 0,
    shard_count: int = 1,
) -> tuple[list[SourceBenchmarkCase], list[Any]]:
    """Select and hash OmniDocBench-style pages without exposing annotations."""

    if shard_count <= 0:
        raise ValueError("shard_count must be greater than zero")
    if not 0 <= shard_index < shard_count:
        raise ValueError("shard_index must satisfy 0 <= index < shard_count")
    raw = json.loads(manifest.dataset_json.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("dataset annotations must be a JSON array")
    selected_subset = subset or manifest.subset
    candidates: list[tuple[int, Any, str, str | None]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"dataset item {index} must be an object")
        page_info = item.get("page_info")
        if not isinstance(page_info, Mapping):
            raise ValueError(f"dataset item {index} has no page_info object")
        input_name = required_string(page_info.get("image_path"), f"item {index} image_path")
        attribute = page_info.get("page_attribute")
        page_subset: str | None = None
        if isinstance(attribute, Mapping) and attribute.get("subset") is not None:
            page_subset = str(attribute["subset"])
        if _subset_matches(selected_subset, page_subset):
            candidates.append((index, item, input_name, page_subset))
    sharded = [
        value for ordinal, value in enumerate(candidates) if ordinal % shard_count == shard_index
    ]
    if not sharded:
        raise ValueError(
            f"selection {selected_subset!r} shard {shard_index}/{shard_count} contains no cases"
        )
    cases: list[SourceBenchmarkCase] = []
    annotations: list[Any] = []
    prediction_names: set[str] = set()
    images_root = manifest.images_dir.resolve()
    for index, item, input_name, page_subset in sharded:
        source_relative = Path(input_name)
        if manifest.source_suffix is not None:
            source_relative = source_relative.with_suffix(manifest.source_suffix)
        source = (images_root / source_relative).resolve()
        try:
            source.relative_to(images_root)
        except ValueError as exc:
            raise ValueError(f"dataset image escapes images directory: {input_name}") from exc
        if not source.is_file():
            raise ValueError(f"dataset image does not exist: {input_name}")
        prediction_name = f"{Path(input_name).stem}.md"
        collision_key = prediction_name.casefold()
        if collision_key in prediction_names:
            raise ValueError(f"official evaluator prediction filename collision: {prediction_name}")
        prediction_names.add(collision_key)
        cases.append(
            SourceBenchmarkCase(
                index=index,
                case_id=input_name,
                input_name=input_name,
                source_name=source_relative.as_posix(),
                source=source,
                source_sha256=sha256_file(source),
                source_bytes=source.stat().st_size,
                prediction_name=prediction_name,
                subset=page_subset,
            )
        )
        annotations.append(item)
    return cases, annotations
