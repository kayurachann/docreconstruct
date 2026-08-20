#!/usr/bin/env python3
"""Negative privacy and validity smokes for official result staging."""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from stage_official_evaluator import (
    curated_metrics,
    curated_run_summary,
    main,
    validate_official_results,
)

SECRET = "GT_SENTINEL_DO_NOT_PUBLISH_7f09"


def valid_metrics() -> dict[str, object]:
    return {
        "text_block": {"all": {"Edit_dist": {"ALL_page_avg": 0.1}}},
        "display_formula": {
            "all": {"Edit_dist": {"ALL_page_avg": 0.2}},
            "page": {"CDM": {"ALL": 0.3}},
        },
        "table": {
            "all": {"Edit_dist": {"ALL_page_avg": 0.4}},
            "page": {"TEDS": {"ALL": 0.5}},
        },
        "reading_order": {"all": {"Edit_dist": {"ALL_page_avg": 0.6}}},
    }


def expected_denominators() -> dict[str, object]:
    return {
        "schema_version": 1,
        "subset": "hard",
        "selected_pages": 296,
        "metrics": {
            "text_block": {"Edit_dist": 267},
            "display_formula": {"Edit_dist": 106, "CDM": 106},
            "table": {"TEDS": 107, "Edit_dist": 107},
            "reading_order": {"Edit_dist": 293},
        },
    }


def valid_summary() -> dict[str, object]:
    expected = expected_denominators()["metrics"]
    assert isinstance(expected, dict)
    denominators = {
        section: {name: {"ALL": count} for name, count in values.items()}
        for section, values in expected.items()
        if isinstance(values, dict)
    }
    return {
        "stage_execution": {
            "page_match": {
                "workers": 1,
                "page_count": 296,
                "quick_match_truncated_timeout_sec": 300,
                "match_timeout_sec": 420,
                "fallbacks": {
                    SECRET: {"count": 1, "cases": [SECRET]},
                },
            },
            "metrics": {
                "display_formula": {
                    "CDM": {
                        "workers": 1,
                        "sample_count": 100,
                        "timeout_case_count": 0,
                        "exception_case_count": 0,
                    }
                },
                "table": {
                    "TEDS": {
                        "workers": 1,
                        "timeout_sec": 30,
                        "sample_count": 97,
                        "timeout_case_count": 0,
                        "error_case_count": 0,
                        "error_cases": [{"reason": SECRET, "case_name": SECRET}],
                    }
                },
            },
        },
        "page_denominators": {**denominators, SECRET: {"path": SECRET}},
        "runtime_environment": {
            "python_executable": f"/private/{SECRET}/bin/python",
        },
        "notebook_metric_summary": {"raw_gt": SECRET},
    }


class OfficialResultStagingTests(unittest.TestCase):
    def validate(self, metrics: dict[str, object]) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "demo_metric_result.json").write_text(
                json.dumps(metrics, allow_nan=True), encoding="utf-8"
            )
            (root / "demo_run_summary.json").write_text(
                json.dumps(valid_summary()), encoding="utf-8"
            )
            expected = root / "expected.json"
            expected.write_text(json.dumps(expected_denominators()), encoding="utf-8")
            return validate_official_results(root, 296, expected)

    def test_exact_official_aggregate_paths_are_accepted(self) -> None:
        self.assertEqual(self.validate(valid_metrics()), [])

    def test_non_finite_null_and_string_aggregates_are_rejected(self) -> None:
        for bad_value in (None, float("nan"), float("inf"), "0.5", True):
            with self.subTest(value=bad_value):
                metrics = valid_metrics()
                metrics["text_block"]["all"]["Edit_dist"]["ALL_page_avg"] = bad_value
                errors = self.validate(metrics)
                self.assertTrue(any("not a finite number" in error for error in errors))

    def test_metric_name_at_wrong_path_is_rejected(self) -> None:
        metrics = valid_metrics()
        metrics["display_formula"]["page"].pop("CDM")
        metrics["display_formula"]["misleading"] = {"CDM": {"ALL": 0.3}}
        errors = self.validate(metrics)
        self.assertTrue(any("display_formula.page.CDM.ALL" in error for error in errors))

    def test_nonzero_metric_failure_count_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "demo_metric_result.json").write_text(
                json.dumps(valid_metrics()), encoding="utf-8"
            )
            summary = valid_summary()
            summary["stage_execution"]["metrics"]["table"]["TEDS"]["error_case_count"] = 1
            (root / "demo_run_summary.json").write_text(json.dumps(summary), encoding="utf-8")
            expected = root / "expected.json"
            expected.write_text(json.dumps(expected_denominators()), encoding="utf-8")
            errors = validate_official_results(root, 296, expected)
            self.assertTrue(any("error_case_count" in error for error in errors))

    def test_curated_public_payload_excludes_gt_sentinel_and_paths(self) -> None:
        metrics = valid_metrics()
        metrics[SECRET] = {"raw_gt": SECRET}
        safe = {
            "metrics": curated_metrics(metrics),
            "summary": curated_run_summary(valid_summary()),
        }
        encoded = json.dumps(safe, allow_nan=False)
        self.assertNotIn(SECRET, encoded)
        self.assertNotIn("python_executable", encoded)
        self.assertNotIn("error_cases", encoded)
        self.assertEqual(
            safe["summary"]["stage_execution"]["page_match"]["fallback_case_count"],
            1,
        )

    def test_complete_public_artifact_excludes_gt_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            result = run / "result"
            output = root / "public"
            result.mkdir(parents=True)
            metrics = valid_metrics()
            metrics[SECRET] = {"raw_gt": SECRET}
            (result / "demo_metric_result.json").write_text(json.dumps(metrics), encoding="utf-8")
            (result / "demo_run_summary.json").write_text(
                json.dumps(valid_summary()), encoding="utf-8"
            )
            (result / "demo_runtime_environment.json").write_text(SECRET, encoding="utf-8")
            (result / "demo_stage_execution.log").write_text(SECRET, encoding="utf-8")
            (run / "evaluator.stdout.log").write_text(SECRET, encoding="utf-8")
            (run / "evaluator.stderr.log").write_text(SECRET, encoding="utf-8")
            for name, value in (
                ("runtime-restore-outcome.txt", "success"),
                ("inference-download-outcome.txt", "success"),
                ("prediction-validation-exit-code.txt", "0"),
                ("ground-truth-outcome.txt", "success"),
                ("runtime-inventory-outcome.txt", "success"),
                ("native-smoke-exit-code.txt", "0"),
                ("evaluator-exit-code.txt", "0"),
            ):
                (run / name).write_text(value + "\n", encoding="utf-8")
            (run / "prediction-manifest.json").write_text(
                json.dumps(
                    {
                        "valid": True,
                        "selected_cases": 296,
                        "missing_count": 0,
                        "extra_count": 0,
                    }
                ),
                encoding="utf-8",
            )
            expected = run / "expected-denominators.json"
            expected.write_text(json.dumps(expected_denominators()), encoding="utf-8")
            main(
                argparse.Namespace(
                    run=run,
                    output=output,
                    system="demo",
                    subset="hard",
                    expected_denominators=expected,
                )
            )
            public_bytes = b"\n".join(
                path.read_bytes() for path in output.rglob("*") if path.is_file()
            )
            self.assertNotIn(SECRET.encode(), public_bytes)
            public_names = {path.name for path in output.rglob("*") if path.is_file()}
            self.assertNotIn("demo_runtime_environment.json", public_names)
            self.assertNotIn("demo_stage_execution.log", public_names)


if __name__ == "__main__":
    unittest.main()
