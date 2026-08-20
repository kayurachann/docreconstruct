#!/usr/bin/env python3
"""Reject incomplete or duplicate merged prediction sets before official eval."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def subset_matches(selection: str, page_subset: str | None) -> bool:
    actual = (page_subset or "").casefold()
    return selection == "all" or (selection == "hard" and actual.endswith("_hard"))


def normalized_environment_digest(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    normalized = payload.get("normalized_environment")
    if not isinstance(normalized, dict):
        raise RuntimeError(f"normalized_environment is missing from {path}")
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def expected_source_files_digest(
    source_index: list[dict[str, Any]], subset: str, shard_index: int, shard_count: int
) -> tuple[int, str]:
    selected = [
        item
        for item in source_index
        if subset_matches(subset, item["page_info"].get("page_attribute", {}).get("subset"))
    ]
    shard = [item for ordinal, item in enumerate(selected) if ordinal % shard_count == shard_index]
    files = [
        {
            "input_name": Path(item["page_info"]["image_path"]).as_posix(),
            "source_bytes": item["source_bytes"],
            "source_sha256": item["source_sha256"],
        }
        for item in shard
    ]
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return len(files), hashlib.sha256(canonical).hexdigest()


def main(args: argparse.Namespace) -> None:
    source_index: list[dict[str, Any]] = json.loads(args.index.read_text(encoding="utf-8"))
    expected = {
        f"{Path(item['page_info']['image_path']).stem}.md"
        for item in source_index
        if subset_matches(args.subset, item["page_info"].get("page_attribute", {}).get("subset"))
    }
    prediction_dir = args.root / "predictions" / args.system
    actual_paths = list(prediction_dir.glob("*.md"))
    actual = {path.name for path in actual_paths}
    expected_count = 296 if args.subset == "hard" else 1651
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    duplicate_names = len(actual_paths) - len(actual)
    files = []
    for path in sorted(actual_paths):
        payload = path.read_bytes()
        files.append(
            {
                "name": path.name,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "empty": not payload,
            }
        )
    report_count = len(list((args.root / "reports" / args.system).glob("*/report.json")))
    environment_paths = sorted((args.root / "reports" / args.system).glob("*/environment.json"))
    verification_paths = sorted(
        (args.root / "reports" / args.system).glob("*/source-verification.json")
    )
    environment_digests = []
    errors = []
    for path in environment_paths:
        try:
            environment_digests.append(normalized_environment_digest(path))
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            errors.append(f"invalid shard environment {path.parent.name}: {type(exc).__name__}")
    if len(expected) != expected_count:
        errors.append(f"source index selected {len(expected)}, expected {expected_count}")
    if missing or extra or duplicate_names:
        errors.append(
            f"prediction set is incomplete: {len(actual)}/{len(expected)}; "
            f"missing={missing[:20]}; extra={extra[:20]}; duplicates={duplicate_names}"
        )
    if report_count != args.shard_count:
        errors.append(f"found {report_count} shard reports, expected {args.shard_count}")
    if len(environment_paths) != args.shard_count:
        errors.append(
            f"found {len(environment_paths)} shard environments, expected {args.shard_count}"
        )
    if len(set(environment_digests)) > 1:
        errors.append("Python/package/external runtime inventory differs across shards")
    source_index_sha = hashlib.sha256(args.index.read_bytes()).hexdigest()
    verified_shards: set[int] = set()
    for path in verification_paths:
        try:
            verification = json.loads(path.read_text(encoding="utf-8"))
            report_path = path.with_name("report.json")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            shard = verification.get("shard")
            shard_index = shard.get("index") if isinstance(shard, dict) else None
            shard_count = shard.get("count") if isinstance(shard, dict) else None
            if (
                verification.get("valid") is not True
                or verification.get("subset") != args.subset
                or not isinstance(shard_index, int)
                or isinstance(shard_index, bool)
                or shard_count != args.shard_count
                or not 0 <= shard_index < args.shard_count
            ):
                raise RuntimeError("invalid source-verification coverage")
            expected_file_count, expected_files_sha = expected_source_files_digest(
                source_index, args.subset, shard_index, args.shard_count
            )
            if (
                verification.get("source_index_sha256") != source_index_sha
                or verification.get("file_count") != expected_file_count
                or verification.get("source_files_sha256") != expected_files_sha
            ):
                raise RuntimeError("source-verification digest differs from committed index")
            report_verification = report.get("source_verification")
            if not isinstance(report_verification, dict):
                raise RuntimeError("report has no source-verification digest")
            if report_verification != {
                "verification_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "source_files_sha256": verification.get("source_files_sha256"),
                "corpus_manifest_sha256": verification.get("corpus_manifest_sha256"),
                "source_index_sha256": verification.get("source_index_sha256"),
                "file_count": verification.get("file_count"),
            }:
                raise RuntimeError("report source-verification digest does not match its fixture")
            if shard_index in verified_shards:
                raise RuntimeError(f"duplicate source-verification shard {shard_index}")
            verified_shards.add(shard_index)
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            errors.append(f"invalid source verification {path.parent.name}: {type(exc).__name__}")
    if len(verification_paths) != args.shard_count:
        errors.append(
            f"found {len(verification_paths)} source verifications, expected {args.shard_count}"
        )
    if verified_shards != set(range(args.shard_count)):
        errors.append("source verifications do not cover every deterministic shard exactly once")
    output = {
        "schema_version": 1,
        "valid": not errors,
        "errors": errors,
        "system": args.system,
        "subset": args.subset,
        "selected_cases": len(files),
        "empty_predictions": sum(item["empty"] for item in files),
        "expected_shards": args.shard_count,
        "found_shards": report_count,
        "environment_count": len(environment_paths),
        "source_verification_count": len(verification_paths),
        "source_index_sha256": source_index_sha,
        "normalized_environment_sha256": (
            environment_digests[0]
            if len(environment_digests) == args.shard_count and len(set(environment_digests)) == 1
            else None
        ),
        "missing_count": len(missing),
        "missing": missing,
        "extra_count": len(extra),
        "extra": extra,
        "duplicate_names": duplicate_names,
        "files": files,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    if errors:
        raise RuntimeError("; ".join(errors))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--index", type=Path, required=True)
    result.add_argument("--root", type=Path, required=True)
    result.add_argument("--system", required=True)
    result.add_argument("--subset", choices=("hard", "all"), required=True)
    result.add_argument("--shard-count", type=int, required=True)
    result.add_argument("--output", type=Path, required=True)
    return result


if __name__ == "__main__":
    main(parser().parse_args())
