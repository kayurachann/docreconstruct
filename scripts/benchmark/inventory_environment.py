#!/usr/bin/env python3
"""Write a path-redacted inventory of the materialized parser environment."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def labeled_path(value: str) -> tuple[str, Path]:
    label, separator, raw_path = value.partition("=")
    if not separator or not _LABEL.fullmatch(label) or not raw_path:
        raise ValueError("runtime values must use a safe LABEL=PATH form")
    return label, Path(raw_path).resolve()


def runtime_file_record(value: str) -> dict[str, object]:
    label, path = labeled_path(value)
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        "label": label,
        "name": path.name,
        "bytes": path.stat().st_size,
        "sha256": hash_file(path),
    }


def runtime_command_record(value: str) -> dict[str, object]:
    record = runtime_file_record(value)
    _label, path = labeled_path(value)
    completed = subprocess.run(
        [str(path), "--version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    version_lines = (completed.stdout + completed.stderr).splitlines()
    record.update(
        {
            "version_return_code": completed.returncode,
            "version_line": version_lines[0].strip()[:500] if version_lines else "",
        }
    )
    return record


def sanitized_pip_inventory(path: Path | None) -> list[dict[str, object]]:
    if path is None:
        return []
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = []
    for item in payload.get("install", []):
        metadata = item.get("metadata", {})
        download = item.get("download_info", {})
        archive = download.get("archive_info", {})
        directory = download.get("dir_info", {})
        records.append(
            {
                "name": metadata.get("name"),
                "version": metadata.get("version"),
                "requested": bool(item.get("requested", False)),
                "requested_extras": sorted(item.get("requested_extras") or []),
                "local_editable": bool(directory.get("editable", False)),
                "archive_hashes": dict(sorted(archive.get("hashes", {}).items())),
            }
        )
    return sorted(records, key=lambda item: (str(item["name"]).casefold(), str(item["version"])))


def installation_contract(path: Path | None, system_name: str | None) -> dict[str, object] | None:
    if (path is None) != (system_name is None):
        raise ValueError("--model-pins and --system must be provided together")
    if path is None or system_name is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    system = payload["systems"][system_name]
    return {
        "model_pins_sha256": hash_file(path),
        "system": system_name,
        "package": system["package"],
        "install_spec": system.get("install_spec", system["package"]),
        "compatibility_packages": system.get("compatibility_packages", []),
        "required_imports": system.get("required_imports", []),
    }


def main(args: argparse.Namespace) -> None:
    packages = sorted(
        (
            {"name": item.metadata["Name"], "version": item.version}
            for item in importlib.metadata.distributions()
        ),
        key=lambda item: (item["name"].casefold(), item["version"]),
    )
    caches = []
    for root in args.cache_root:
        if not root.is_dir():
            continue
        files = []
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            files.append(
                {
                    "name": path.relative_to(root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": hash_file(path),
                }
            )
        caches.append({"label": root.name, "files": files})
    pip_inventory = sanitized_pip_inventory(args.pip_report)
    if args.pip_report is not None:
        projects = [
            item
            for item in pip_inventory
            if str(item.get("name", "")).casefold() == "docreconstruct"
            and item.get("requested") is True
            and item.get("local_editable") is True
        ]
        if len(projects) != 1:
            raise RuntimeError("pip report does not cover one requested editable project install")
        if projects[0].get("requested_extras") != ["hybrid"]:
            raise RuntimeError("benchmark project install must request exactly the hybrid extra")
    runtime_commands = sorted(
        (runtime_command_record(value) for value in args.runtime_command),
        key=lambda item: str(item["label"]),
    )
    runtime_files = sorted(
        (runtime_file_record(value) for value in args.runtime_file),
        key=lambda item: str(item["label"]),
    )
    labels = [item["label"] for item in [*runtime_commands, *runtime_files]]
    if len(labels) != len(set(labels)):
        raise RuntimeError("runtime inventory labels must be unique")
    contract = installation_contract(args.model_pins, args.system)
    normalized_environment = {
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "packages": packages,
        "pip_install_inventory": pip_inventory,
        "runtime_commands": runtime_commands,
        "runtime_files": runtime_files,
        "installation_contract": contract,
    }
    payload = {
        "schema_version": 1,
        "python": sys.version,
        "platform": platform.platform(),
        "packages": packages,
        "pip_install_inventory": pip_inventory,
        "installation_contract": contract,
        "model_caches": caches,
        "normalized_environment": normalized_environment,
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--pip-report", type=Path)
    result.add_argument("--model-pins", type=Path)
    result.add_argument("--system")
    result.add_argument("--cache-root", type=Path, action="append", default=[])
    result.add_argument("--runtime-command", action="append", default=[])
    result.add_argument("--runtime-file", action="append", default=[])
    return result


if __name__ == "__main__":
    main(parser().parse_args())
