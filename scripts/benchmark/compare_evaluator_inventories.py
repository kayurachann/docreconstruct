#!/usr/bin/env python3
"""Require the restored evaluator cache to match its prepared byte inventory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(args: argparse.Namespace) -> None:
    prepared = json.loads(args.prepared.read_text(encoding="utf-8"))
    restored = json.loads(args.restored.read_text(encoding="utf-8"))
    for key in ("runtime_tree", "requirements_lock", "evaluator_revision"):
        if prepared.get(key) != restored.get(key):
            raise RuntimeError(f"restored evaluator inventory differs at {key}")
    print(
        "Verified restored evaluator runtime: "
        f"{restored['runtime_tree']['files']} files, "
        f"{restored['runtime_tree']['bytes']} bytes"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--restored", type=Path, required=True)
    main(parser.parse_args())
