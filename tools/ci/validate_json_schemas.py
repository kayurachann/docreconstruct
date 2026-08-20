"""Validate portable JSON Schemas and their runtime-model compatibility."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for

from docreconstruct.api.models import (
    AnalyzeOptions,
    CompareOptions,
    ReconstructOptions,
    RouteOptions,
)
from docreconstruct.ir import Document

Schema = dict[str, Any]

_RUNTIME_SCHEMAS: dict[str, Callable[[], Schema]] = {
    "analyze-options.schema.json": AnalyzeOptions.model_json_schema,
    "compare-options.schema.json": CompareOptions.model_json_schema,
    "document-ir.schema.json": Document.model_json_schema,
    "reconstruct-options.schema.json": ReconstructOptions.model_json_schema,
    "route-options.schema.json": RouteOptions.model_json_schema,
}


def validate_schema_directory(schema_directory: Path) -> list[str]:
    """Return deterministic validation errors for a repository schema directory."""

    errors: list[str] = []
    schema_paths = sorted(schema_directory.glob("*.schema.json"))
    if not schema_paths:
        return [f"no *.schema.json files found in {schema_directory}"]

    identifiers: dict[str, str] = {}
    for path in schema_paths:
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: cannot load JSON: {exc}")
            continue
        if not isinstance(schema, dict):
            errors.append(f"{path.name}: schema root must be a JSON object")
            continue

        dialect = schema.get("$schema", "JSON Schema Draft 2020-12")
        if not isinstance(dialect, str) or not dialect:
            errors.append(f"{path.name}: $schema must be a non-empty string when present")
            continue
        try:
            validator_for(schema, default=Draft202012Validator).check_schema(schema)
        except SchemaError as exc:
            errors.append(f"{path.name}: invalid {dialect} schema: {exc}")

        identifier = schema.get("$id")
        if isinstance(identifier, str):
            previous = identifiers.setdefault(identifier, path.name)
            if previous != path.name:
                errors.append(f"{path.name}: duplicate $id also used by {previous}: {identifier}")

        runtime_factory = _RUNTIME_SCHEMAS.get(path.name)
        if runtime_factory is not None:
            runtime_schema = runtime_factory()
            if schema != runtime_schema:
                errors.append(
                    f"{path.name}: checked-in schema differs from its runtime Pydantic model"
                )

    missing_runtime_files = sorted(set(_RUNTIME_SCHEMAS) - {path.name for path in schema_paths})
    errors.extend(f"{name}: runtime-owned schema file is missing" for name in missing_runtime_files)
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parents[2]
    parser.add_argument(
        "schema_directory",
        nargs="?",
        type=Path,
        default=default_root / "schemas",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    errors = validate_schema_directory(args.schema_directory.resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    count = len(list(args.schema_directory.glob("*.schema.json")))
    print(f"Validated {count} JSON Schemas, including all runtime-model mirrors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
