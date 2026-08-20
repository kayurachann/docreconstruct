#!/usr/bin/env python3
"""Run a one-page official quick-match + TEDS smoke with serial workers."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def nested(payload: object, *path: str) -> object:
    current = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def nonresolving_absolute(path: Path) -> Path:
    """Make a path absolute without dereferencing a POSIX venv Python symlink."""
    return Path(os.path.abspath(os.fspath(path)))


def main(args: argparse.Namespace) -> None:
    evaluator = args.evaluator.resolve()
    evaluator_python = nonresolving_absolute(args.python)
    if not evaluator_python.is_file():
        raise FileNotFoundError(evaluator_python)
    demo_gt = evaluator / "demo_data" / "omnidocbench_demo" / "OmniDocBench_demo.json"
    demo_predictions = evaluator / "demo_data" / "end2end"
    payload = json.loads(demo_gt.read_text(encoding="utf-8"))
    selected = next(
        item
        for item in payload
        if any(
            block.get("category_type") == "table" and not block.get("ignore", False)
            for block in item.get("layout_dets", [])
        )
    )
    image_name = Path(selected["page_info"]["image_path"]).name
    prediction = demo_predictions / f"{Path(image_name).stem}.md"
    if not prediction.is_file():
        raise FileNotFoundError(prediction)

    with tempfile.TemporaryDirectory(prefix="omnidocbench-native-smoke-") as directory:
        root = Path(directory)
        gt = root / "demo-table.json"
        predictions = root / "predictions"
        predictions.mkdir()
        gt.write_text(json.dumps([selected], ensure_ascii=False), encoding="utf-8")
        shutil.copy2(prediction, predictions / prediction.name)
        config = root / "end2end.yaml"
        config.write_text(
            "\n".join(
                (
                    "end2end_eval:",
                    "  metrics:",
                    "    table:",
                    "      metric: [TEDS, Edit_dist]",
                    "      teds_workers: 1",
                    "  dataset:",
                    "    dataset_name: end2end_dataset",
                    "    ground_truth:",
                    f"      data_path: {json.dumps(str(gt))}",
                    "    prediction:",
                    f"      data_path: {json.dumps(str(predictions))}",
                    "    match_method: quick_match",
                    "    match_workers: 1",
                    "    quick_match_truncated_timeout_sec: 300",
                    "    match_timeout_sec: 420",
                    "    timeout_fallback_max_chunk_span: 10",
                    "    timeout_fallback_order_penalty: 0.10",
                    "",
                )
            ),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(evaluator)
        try:
            completed = subprocess.run(
                [
                    str(evaluator_python),
                    str(evaluator / "pdf_validation.py"),
                    "--config",
                    str(config),
                ],
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=args.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("serial matcher/TEDS smoke timed out") from exc
        if completed.returncode != 0:
            raise RuntimeError(
                f"serial matcher/TEDS smoke exited {completed.returncode}; "
                f"private stdout/stderr bytes={len(completed.stdout)}/{len(completed.stderr)}"
            )

        result = root / "result"
        metric_paths = list(result.glob("*_metric_result.json"))
        summary_paths = list(result.glob("*_run_summary.json"))
        if len(metric_paths) != 1 or len(summary_paths) != 1:
            raise RuntimeError("serial matcher/TEDS smoke produced incomplete official reports")
        metrics = json.loads(metric_paths[0].read_text(encoding="utf-8"))
        summary = json.loads(summary_paths[0].read_text(encoding="utf-8"))
        teds = nested(metrics, "table", "page", "TEDS", "ALL")
        if (
            not isinstance(teds, (int, float))
            or isinstance(teds, bool)
            or not math.isfinite(float(teds))
        ):
            raise RuntimeError(f"serial matcher/TEDS smoke score is invalid: {teds!r}")
        checks = {
            "page_count": nested(summary, "stage_execution", "page_match", "page_count"),
            "match_workers": nested(summary, "stage_execution", "page_match", "workers"),
            "teds_workers": nested(
                summary, "stage_execution", "metrics", "table", "TEDS", "workers"
            ),
            "teds_timeout_count": nested(
                summary,
                "stage_execution",
                "metrics",
                "table",
                "TEDS",
                "timeout_case_count",
            ),
            "teds_error_count": nested(
                summary,
                "stage_execution",
                "metrics",
                "table",
                "TEDS",
                "error_case_count",
            ),
            "teds_denominator": nested(summary, "page_denominators", "table", "TEDS", "ALL"),
        }
        expected = {
            "page_count": 1,
            "match_workers": 1,
            "teds_workers": 1,
            "teds_timeout_count": 0,
            "teds_error_count": 0,
            "teds_denominator": 1,
        }
        if checks != expected:
            raise RuntimeError(f"serial matcher/TEDS diagnostics differ: {checks!r}")
        print("Official serial quick-match + TEDS smoke passed (1 page, 0 metric failures)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluator", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    main(parser.parse_args())
