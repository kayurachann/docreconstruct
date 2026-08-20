#!/usr/bin/env python3
"""Create one source-only harness manifest without ground-truth annotations."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

DATASET_REVISION = "aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec"
SYSTEMS = {
    "docreconstruct-tesseract": {
        "version": "0.1.0+tesseract-5.3.4",
        "revision": "{git_sha}+tessdata-fast-87416418657359cb625c412a48b6e1d6d41c29bd",
        "timeout": 180,
    },
    "docling": {
        "version": "2.120.3",
        "revision": "46a1103b8c4adc6bbde1e30ec48fd0f7142d5600",
        "timeout": 180,
    },
    "mineru": {
        "version": "3.4.5",
        "revision": "fbb1257a555a3fde78ae5aaaa931e3b3f8fb2883",
        "timeout": 180,
    },
    "marker": {
        "version": "2.0.0+fast-cpu",
        "revision": "947d7688c0739297a7b9eb08b1a463e3a6853981+llama.cpp-b10507",
        "timeout": 180,
    },
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def subset_matches(selection: str, page_subset: str | None) -> bool:
    actual = (page_subset or "").casefold()
    return selection == "all" or (selection == "hard" and actual.endswith("_hard"))


def main(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    index = root / "source-index.json"
    metadata = SYSTEMS[args.system]
    source_index = json.loads(index.read_text(encoding="utf-8"))
    candidates = [
        item
        for item in source_index
        if subset_matches(
            args.subset,
            item["page_info"].get("page_attribute", {}).get("subset"),
        )
    ]
    selected = [
        item
        for position, item in enumerate(candidates)
        if position % args.shard_count == args.shard_index
    ]
    worst_case_seconds = len(selected) * int(metadata["timeout"])
    if worst_case_seconds > args.shard_budget_seconds:
        raise RuntimeError(
            f"shard worst-case {worst_case_seconds}s exceeds the declared "
            f"{args.shard_budget_seconds}s inference budget"
        )
    revision = str(metadata["revision"]).format(git_sha=args.git_sha)
    command = [
        sys.executable,
        str(args.wrapper.resolve()),
        "--system",
        args.system,
        "--input",
        "{input}",
        "--output",
        "{output}",
        "--work-dir",
        "{work_dir}",
    ]
    if args.system == "docreconstruct-tesseract":
        if args.tessdata is None:
            raise ValueError("--tessdata is required for docreconstruct-tesseract")
        command.extend(["--tessdata", str(args.tessdata.resolve())])
    manifest = {
        "schema_version": "0.1",
        "dataset": {
            "annotations": str(index),
            "images": str(root / "sources"),
            "revision": DATASET_REVISION,
            "sha256": sha256_file(index),
        },
        "output_dir": str(args.output.resolve()),
        "subset": args.subset,
        "max_output_bytes": 512 * 1024,
        "shard_budget": {
            "shard_index": args.shard_index,
            "shard_count": args.shard_count,
            "selected_cases": len(selected),
            "per_page_timeout_seconds": metadata["timeout"],
            "worst_case_seconds": worst_case_seconds,
            "budget_seconds": args.shard_budget_seconds,
        },
        "systems": [
            {
                "name": args.system,
                "version": metadata["version"],
                "revision": revision,
                "command": command,
                "cwd": str(args.checkout.resolve()),
                "timeout_seconds": metadata["timeout"],
            }
        ],
    }
    args.destination.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--root", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--destination", type=Path, required=True)
    result.add_argument("--wrapper", type=Path, required=True)
    result.add_argument("--checkout", type=Path, required=True)
    result.add_argument("--system", choices=tuple(SYSTEMS), required=True)
    result.add_argument("--subset", choices=("hard", "all"), required=True)
    result.add_argument("--git-sha", required=True)
    result.add_argument("--shard-index", type=int, required=True)
    result.add_argument("--shard-count", type=int, required=True)
    result.add_argument("--shard-budget-seconds", type=int, default=18000)
    result.add_argument("--tessdata", type=Path)
    return result


if __name__ == "__main__":
    main(parser().parse_args())
