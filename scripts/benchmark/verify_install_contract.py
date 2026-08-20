#!/usr/bin/env python3
"""Emit and verify the exact candidate installation contract."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
from pathlib import Path
from typing import Any


def exact_requirement(requirement: str) -> tuple[str, str]:
    name_and_extras, separator, version = requirement.partition("==")
    name = name_and_extras.partition("[")[0]
    if not separator or not name or not version or "==" in version:
        raise RuntimeError(f"installation contract requires an exact version: {requirement!r}")
    return name, version


def installation_requirements(system: dict[str, Any]) -> list[str]:
    requirements = [str(system.get("install_spec", system["package"]))]
    compatibility = system.get("compatibility_packages", [])
    if not isinstance(compatibility, list) or not all(
        isinstance(requirement, str) for requirement in compatibility
    ):
        raise RuntimeError("compatibility_packages must be a list of strings")
    requirements.extend(compatibility)
    for requirement in requirements:
        exact_requirement(requirement)
    if len(requirements) != len(set(requirements)):
        raise RuntimeError(f"duplicate installation requirement: {requirements}")
    return requirements


def verify(system: dict[str, Any]) -> None:
    for requirement in installation_requirements(system):
        package, expected_version = exact_requirement(requirement)
        actual_version = importlib.metadata.version(package)
        if actual_version != expected_version:
            raise RuntimeError(
                f"installed {package} version {actual_version}, expected {expected_version}"
            )
    required_imports = system.get("required_imports", [])
    if not isinstance(required_imports, list) or not all(
        isinstance(module, str) and module for module in required_imports
    ):
        raise RuntimeError("required_imports must be a list of non-empty strings")
    for module in required_imports:
        importlib.import_module(module)


def main(args: argparse.Namespace) -> None:
    pins: dict[str, Any] = json.loads(args.pins.read_text(encoding="utf-8"))
    system = pins["systems"][args.system]
    if args.print_requirements:
        print("\n".join(installation_requirements(system)))
        return
    verify(system)
    print(f"Verified installation contract for {args.system}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pins", type=Path, required=True)
    parser.add_argument("--system", choices=("docling", "mineru", "marker"), required=True)
    parser.add_argument("--print-requirements", action="store_true")
    main(parser.parse_args())
