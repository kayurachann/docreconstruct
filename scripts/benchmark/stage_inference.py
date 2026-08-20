#!/usr/bin/env python3
"""Copy only public benchmark evidence into an Actions artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(source)
    shutil.copytree(source, destination, dirs_exist_ok=True)


def main(args: argparse.Namespace) -> None:
    label = f"{args.shard_index:05d}-of-{args.shard_count:05d}"
    report_root = args.run / "shards" / label
    report = report_root / "source-benchmark-report.json"
    ledger = report_root / "ledger.jsonl"
    if not report.is_file() or not ledger.is_file():
        raise FileNotFoundError(f"missing harness report or ledger for {label}")
    copy_tree(
        args.run / args.system / "predictions",
        args.output / "predictions" / args.system,
    )
    copy_tree(args.run / "records" / args.system, args.output / "records" / args.system)
    public_report = args.output / "reports" / args.system / label
    public_report.mkdir(parents=True, exist_ok=True)
    verification = json.loads(args.source_verification.read_text(encoding="utf-8"))
    if verification.get("valid") is not True:
        raise RuntimeError("source verification is missing or invalid")
    verification_bytes = args.source_verification.read_bytes()
    report_payload = json.loads(report.read_text(encoding="utf-8"))
    report_payload["source_verification"] = {
        "verification_sha256": hashlib.sha256(verification_bytes).hexdigest(),
        "source_files_sha256": verification.get("source_files_sha256"),
        "corpus_manifest_sha256": verification.get("corpus_manifest_sha256"),
        "source_index_sha256": verification.get("source_index_sha256"),
        "file_count": verification.get("file_count"),
    }
    (public_report / "report.json").write_text(
        json.dumps(report_payload, indent=2) + "\n", encoding="utf-8"
    )
    shutil.copy2(ledger, public_report / "ledger.jsonl")
    shutil.copy2(args.corpus_manifest, public_report / "corpus-manifest.json")
    shutil.copy2(args.source_verification, public_report / "source-verification.json")
    shutil.copy2(args.environment, public_report / "environment.json")
    (public_report / "harness-exit-code.txt").write_text(
        f"{args.harness_exit_code}\n", encoding="ascii"
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--run", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--system", required=True)
    result.add_argument("--shard-index", type=int, required=True)
    result.add_argument("--shard-count", type=int, required=True)
    result.add_argument("--corpus-manifest", type=Path, required=True)
    result.add_argument("--source-verification", type=Path, required=True)
    result.add_argument("--environment", type=Path, required=True)
    result.add_argument("--harness-exit-code", type=int, required=True)
    return result


if __name__ == "__main__":
    main(parser().parse_args())
