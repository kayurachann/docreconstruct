"""Enforce branch-aware, scope-specific coverage ratchets."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CoverageGate:
    name: str
    minimum_percent: float
    files: tuple[str, ...] | None = None


GATES = (
    CoverageGate("whole project", 75.0),
    CoverageGate(
        "quality-critical evaluator and matcher",
        85.0,
        (
            "src/docreconstruct/evaluation/assignment.py",
            "src/docreconstruct/evaluation/fidelity.py",
            "src/docreconstruct/evaluation/metrics.py",
            "src/docreconstruct/evaluation/visual.py",
            "src/docreconstruct/reconstruction/evidence_matching.py",
        ),
    ),
    CoverageGate(
        "evidence fusion",
        80.0,
        (
            "src/docreconstruct/normalization/fusion.py",
            "src/docreconstruct/normalization/fusion_assignment.py",
            "src/docreconstruct/normalization/fusion_clustering.py",
            "src/docreconstruct/normalization/fusion_reduction.py",
            "src/docreconstruct/normalization/fusion_sources.py",
            "src/docreconstruct/normalization/fusion_spatial.py",
        ),
    ),
    CoverageGate(
        "DOCX rendering and validation",
        78.0,
        (
            "src/docreconstruct/evaluation/hybrid_validation.py",
            "src/docreconstruct/reconstruction/hybrid_docx.py",
            "src/docreconstruct/renderers/docx.py",
        ),
    ),
)


def _normalized_files(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(name).replace("\\", "/"): value for name, value in payload.items()}


def _covered_and_total(summary: Mapping[str, Any]) -> tuple[int, int]:
    covered = int(summary["covered_lines"]) + int(summary.get("covered_branches", 0))
    total = int(summary["num_statements"]) + int(summary.get("num_branches", 0))
    return covered, total


def evaluate_coverage(payload: Mapping[str, Any]) -> list[str]:
    """Return failures for all gates; never stop after only the first weak scope."""

    failures: list[str] = []
    files = _normalized_files(payload.get("files", {}))
    totals = payload.get("totals")
    if not isinstance(totals, Mapping):
        return ["coverage JSON does not contain a totals object"]

    for gate in GATES:
        if gate.files is None:
            covered, total = _covered_and_total(totals)
        else:
            missing = [name for name in gate.files if name not in files]
            if missing:
                failures.append(f"{gate.name}: missing coverage data for {', '.join(missing)}")
                continue
            counts = [_covered_and_total(files[name]["summary"]) for name in gate.files]
            covered = sum(value[0] for value in counts)
            total = sum(value[1] for value in counts)
        percent = 100.0 * covered / total if total else 100.0
        print(
            f"{gate.name}: {percent:.2f}% ({covered}/{total}), "
            f"required >= {gate.minimum_percent:.2f}%"
        )
        if percent + 1e-12 < gate.minimum_percent:
            failures.append(f"{gate.name}: {percent:.2f}% is below {gate.minimum_percent:.2f}%")
    return failures


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage_json", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read coverage JSON: {exc}")
        return 1
    failures = evaluate_coverage(payload)
    for failure in failures:
        print(f"ERROR: {failure}")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
