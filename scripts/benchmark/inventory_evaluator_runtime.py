#!/usr/bin/env python3
"""Inventory the documented native OmniDocBench evaluator runtime."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_record(name: str) -> dict[str, object]:
    path_value = shutil.which(name)
    if path_value is None:
        return {"name": name, "found": False}
    path = Path(path_value).resolve()
    completed = subprocess.run(
        [str(path), "--version"], capture_output=True, text=True, check=False, timeout=30
    )
    return {
        "name": name,
        "found": True,
        "path_name": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "return_code": completed.returncode,
        "version_output": (completed.stdout + completed.stderr).strip()[:4000],
    }


def output(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=120
        )
    except OSError as exc:
        return f"unavailable: {type(exc).__name__}: {exc}"
    return (completed.stdout + completed.stderr).strip()


def tree_record(root: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    count = 0
    total = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        file_digest = sha256_file(path)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\n")
        count += 1
        total += size
    return {"files": count, "bytes": total, "sha256": digest.hexdigest()}


def main(args: argparse.Namespace) -> None:
    packages = sorted(
        (
            {"name": dist.metadata["Name"], "version": dist.version}
            for dist in importlib.metadata.distributions()
        ),
        key=lambda item: (item["name"].casefold(), item["version"]),
    )
    evaluator_revision = output(["git", "-C", str(args.evaluator), "rev-parse", "HEAD"])
    texlive_packages = output(
        ["tlmgr", "info", "--only-installed", "--data", "name,revision,cat-version"]
    )
    system_packages = output(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"])
    payload = {
        "schema_version": 1,
        "runtime": "official evaluator code / documented native runtime",
        "canonical_container_reference": (
            "ghcr.io/zeng-weijun/omnidocbench-eval@"
            "sha256:6116ad72172e763b5c43e963d5efebf2093f2362b975f58156ce4f6c9142e617"
        ),
        "canonical_container_executed": False,
        "evaluator_revision": evaluator_revision,
        "python": sys.version,
        "platform": platform.platform(),
        "runner_image": {
            "image_os": os.environ.get("ImageOS"),  # noqa: SIM112 - GitHub's exact key
            "image_version": os.environ.get("ImageVersion"),  # noqa: SIM112
        },
        "requirements_lock": {
            "bytes": args.requirements.stat().st_size,
            "sha256": sha256_file(args.requirements),
        },
        "runtime_tree": tree_record(args.runtime_root),
        "commands": [
            command_record(name)
            for name in ("python", "pip", "pdflatex", "kpsewhich", "magick", "gs")
        ],
        "python_packages": packages,
        "texlive_packages": texlive_packages.splitlines(),
        "texlive_packages_sha256": hashlib.sha256(texlive_packages.encode()).hexdigest(),
        "system_packages": system_packages.splitlines(),
        "system_packages_sha256": hashlib.sha256(system_packages.encode()).hexdigest(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluator", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
