"""Manifest loading and validation for source-only benchmark runs."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ._common import (
    SOURCE_BENCHMARK_SCHEMA_VERSION,
    required_string,
    resolve_path,
    string_sequence,
)
from .models import OfficialEvaluator, SourceBenchmarkManifest, SourceBenchmarkSystem

_SENSITIVE_ENVIRONMENT_NAME = re.compile(
    r"(?:api[_-]?key|token|secret|password|passwd|credential|authorization|cookie)",
    re.IGNORECASE,
)


def _load_mapping(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"manifest must contain a JSON object: {path}")
    return value


def _load_environment(value: Any, label: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    environment: dict[str, str] = {}
    for key, item in value.items():
        name = required_string(key, f"{label} key")
        if _SENSITIVE_ENVIRONMENT_NAME.search(name):
            raise ValueError(
                f"{label}.{name} looks secret-bearing; inherit credentials from the process "
                "environment instead of storing them in a benchmark manifest"
            )
        if not isinstance(item, (str, int, float, bool)):
            raise ValueError(f"{label}.{name} must be a scalar")
        environment[name] = str(item)
    return environment


def load_source_benchmark_manifest(path: str | Path) -> SourceBenchmarkManifest:
    """Load and validate a source benchmark manifest."""

    manifest_path = Path(path).expanduser().resolve()
    if manifest_path.is_dir():
        manifest_path = manifest_path / "source-benchmark.json"
    if not manifest_path.is_file():
        raise ValueError(f"source benchmark manifest does not exist: {manifest_path}")
    root = manifest_path.parent
    raw = _load_mapping(manifest_path)
    if raw.get("schema_version") != SOURCE_BENCHMARK_SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SOURCE_BENCHMARK_SCHEMA_VERSION!r}")
    dataset = raw.get("dataset")
    if not isinstance(dataset, Mapping):
        raise ValueError("dataset must be an object")
    dataset_json = resolve_path(root, dataset.get("annotations"), "dataset.annotations")
    images_dir = resolve_path(root, dataset.get("images"), "dataset.images")
    if not dataset_json.is_file():
        raise ValueError(f"dataset.annotations does not exist: {dataset_json}")
    if not images_dir.is_dir():
        raise ValueError(f"dataset.images is not a directory: {images_dir}")
    dataset_revision = required_string(dataset.get("revision"), "dataset.revision")
    source_suffix_raw = dataset.get("source_suffix")
    source_suffix: str | None = None
    if source_suffix_raw is not None:
        source_suffix = required_string(source_suffix_raw, "dataset.source_suffix")
        if not re.fullmatch(r"\.[A-Za-z0-9][A-Za-z0-9._-]{0,31}", source_suffix):
            raise ValueError("dataset.source_suffix must be a filename suffix such as '.pdf'")
    expected_sha = dataset.get("sha256")
    if expected_sha is not None:
        expected_sha = required_string(expected_sha, "dataset.sha256").casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise ValueError("dataset.sha256 must be a 64-character lowercase hex digest")
    output_value = raw.get("output_dir", "source-benchmark-output")
    output_dir = resolve_path(root, output_value, "output_dir")
    subset = required_string(raw.get("subset", "all"), "subset")
    max_output_bytes = int(raw.get("max_output_bytes", 100 * 1024 * 1024))
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be greater than zero")
    default_timeout = float(raw.get("timeout_seconds", 900.0))
    if default_timeout <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    systems_raw = raw.get("systems")
    if not isinstance(systems_raw, Sequence) or isinstance(systems_raw, (str, bytes, bytearray)):
        raise ValueError("systems must be an array")
    systems: list[SourceBenchmarkSystem] = []
    for index, item in enumerate(systems_raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"systems[{index}] must be an object")
        cwd = (
            None
            if item.get("cwd") is None
            else resolve_path(root, item.get("cwd"), f"systems[{index}].cwd")
        )
        systems.append(
            SourceBenchmarkSystem(
                name=required_string(item.get("name"), f"systems[{index}].name"),
                version=required_string(item.get("version"), f"systems[{index}].version"),
                revision=required_string(item.get("revision"), f"systems[{index}].revision"),
                command=string_sequence(item.get("command"), f"systems[{index}].command"),
                cwd=cwd,
                timeout_seconds=float(item.get("timeout_seconds", default_timeout)),
                environment=_load_environment(
                    item.get("environment"), f"systems[{index}].environment"
                ),
            )
        )
    if not systems:
        raise ValueError("systems must contain at least one candidate")
    names = [system.name.casefold() for system in systems]
    if len(names) != len(set(names)):
        raise ValueError("system names must be unique (case-insensitive)")
    evaluator_raw = raw.get("official_evaluator")
    evaluator: OfficialEvaluator | None = None
    if evaluator_raw is not None:
        if not isinstance(evaluator_raw, Mapping):
            raise ValueError("official_evaluator must be an object")
        evaluator_cwd = (
            None
            if evaluator_raw.get("cwd") is None
            else resolve_path(root, evaluator_raw.get("cwd"), "official_evaluator.cwd")
        )
        evaluator = OfficialEvaluator(
            name=required_string(evaluator_raw.get("name"), "official_evaluator.name"),
            version=required_string(evaluator_raw.get("version"), "official_evaluator.version"),
            revision=required_string(evaluator_raw.get("revision"), "official_evaluator.revision"),
            command=string_sequence(evaluator_raw.get("command"), "official_evaluator.command"),
            cwd=evaluator_cwd,
            timeout_seconds=float(evaluator_raw.get("timeout_seconds", 3600.0)),
            environment=_load_environment(
                evaluator_raw.get("environment"), "official_evaluator.environment"
            ),
        )
    return SourceBenchmarkManifest(
        path=manifest_path,
        dataset_json=dataset_json,
        images_dir=images_dir,
        source_suffix=source_suffix,
        dataset_revision=dataset_revision,
        expected_dataset_sha256=expected_sha,
        output_dir=output_dir,
        subset=subset,
        max_output_bytes=max_output_bytes,
        systems=tuple(systems),
        official_evaluator=evaluator,
    )
