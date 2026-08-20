#!/usr/bin/env python3
"""Publish aggregate official metrics without republishing evaluator GT samples."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path

EVALUATOR_REVISION = "193627ae9e97d89188468ed1ee3b7a856ff76044"
CANONICAL_EVALUATOR_IMAGE_REFERENCE = (
    "ghcr.io/zeng-weijun/omnidocbench-eval@"
    "sha256:6116ad72172e763b5c43e963d5efebf2093f2362b975f58156ce4f6c9142e617"
)
EXPECTED_METRICS = {
    "text_block": {"Edit_dist"},
    "display_formula": {"Edit_dist", "CDM"},
    "table": {"TEDS", "Edit_dist"},
    "reading_order": {"Edit_dist"},
}
REQUIRED_AGGREGATE_PATHS = (
    ("text_block", "all", "Edit_dist", "ALL_page_avg"),
    ("display_formula", "all", "Edit_dist", "ALL_page_avg"),
    ("display_formula", "page", "CDM", "ALL"),
    ("table", "all", "Edit_dist", "ALL_page_avg"),
    ("table", "page", "TEDS", "ALL"),
    ("reading_order", "all", "Edit_dist", "ALL_page_avg"),
)
REQUIRED_EXECUTION_METRICS = {
    ("display_formula", "CDM"): ("timeout_case_count", "exception_case_count"),
    ("table", "TEDS"): ("timeout_case_count", "error_case_count"),
}


def copy_if_present(source: Path, destination: Path) -> None:
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def digest_if_present(path: Path) -> dict[str, int | str] | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def nested_value(value: object, path: tuple[str, ...]) -> object:
    current = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def curated_metrics(metrics: dict[str, object]) -> dict[str, object]:
    """Keep only the six finite headline aggregates validated below."""
    result: dict[str, object] = {"schema_version": 1}
    for path in REQUIRED_AGGREGATE_PATHS:
        value = nested_value(metrics, path)
        if not finite_number(value):
            raise ValueError(f"required aggregate is not finite numeric: {'.'.join(path)}")
        current = result
        for key in path[:-1]:
            child = current.setdefault(key, {})
            if not isinstance(child, dict):
                raise TypeError(f"aggregate path collision at {key}")
            current = child
        current[path[-1]] = value
    return result


def optional_integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def curated_run_summary(summary: dict[str, object]) -> dict[str, object]:
    """Remove paths, filenames, exception reasons, and case payloads from the report."""
    stage = summary.get("stage_execution")
    page_match = stage.get("page_match") if isinstance(stage, dict) else None
    curated_page_match: dict[str, object] = {}
    if isinstance(page_match, dict):
        for key in (
            "workers",
            "page_count",
            "quick_match_truncated_timeout_sec",
            "match_timeout_sec",
        ):
            value = optional_integer(page_match.get(key))
            if value is not None:
                curated_page_match[key] = value
        fallbacks = page_match.get("fallbacks")
        if isinstance(fallbacks, dict):
            fallback_counts = [
                optional_integer(payload.get("count"))
                for payload in fallbacks.values()
                if isinstance(payload, dict)
            ]
            curated_page_match["fallback_case_count"] = sum(
                count for count in fallback_counts if count is not None
            )

    curated_metrics_execution: dict[str, object] = {}
    metric_execution = stage.get("metrics") if isinstance(stage, dict) else None
    if isinstance(metric_execution, dict):
        for section, names in EXPECTED_METRICS.items():
            raw_section = metric_execution.get(section)
            if not isinstance(raw_section, dict):
                continue
            safe_section: dict[str, object] = {}
            for name in names:
                raw_metric = raw_section.get(name)
                if not isinstance(raw_metric, dict):
                    continue
                safe_metric = {
                    key: value
                    for key in (
                        "workers",
                        "timeout_sec",
                        "sample_count",
                        "timeout_case_count",
                        "error_case_count",
                        "exception_case_count",
                    )
                    if (value := optional_integer(raw_metric.get(key))) is not None
                }
                if safe_metric:
                    safe_section[name] = safe_metric
            if safe_section:
                curated_metrics_execution[section] = safe_section

    curated_denominators: dict[str, object] = {}
    raw_denominators = summary.get("page_denominators")
    if isinstance(raw_denominators, dict):
        for section, names in EXPECTED_METRICS.items():
            raw_section = raw_denominators.get(section)
            if not isinstance(raw_section, dict):
                continue
            safe_section = {}
            for name in names:
                raw_metric = raw_section.get(name)
                if not isinstance(raw_metric, dict):
                    continue
                value = optional_integer(raw_metric.get("ALL"))
                if value is not None:
                    safe_section[name] = {"ALL": value}
            if safe_section:
                curated_denominators[section] = safe_section

    return {
        "schema_version": 1,
        "stage_execution": {
            "page_match": curated_page_match,
            "metrics": curated_metrics_execution,
        },
        "page_denominators": curated_denominators,
    }


def validate_execution_summary(summary: dict[str, object]) -> list[str]:
    errors = []
    stage = summary.get("stage_execution")
    if not isinstance(stage, dict):
        return ["run summary has no stage_execution object"]
    page_match = stage.get("page_match")
    if not isinstance(page_match, dict) or page_match.get("workers") != 1:
        errors.append("official matcher did not report exactly one worker")
    metric_execution = stage.get("metrics")
    if not isinstance(metric_execution, dict):
        return [*errors, "run summary has no metric execution diagnostics"]

    for (section, name), required_counts in REQUIRED_EXECUTION_METRICS.items():
        section_payload = metric_execution.get(section)
        payload = section_payload.get(name) if isinstance(section_payload, dict) else None
        if not isinstance(payload, dict):
            errors.append(f"run summary is missing execution diagnostics for {section}.{name}")
            continue
        if payload.get("workers") != 1:
            errors.append(f"official {section}.{name} did not report exactly one worker")
        for key in required_counts:
            value = payload.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value != 0:
                errors.append(f"invalid execution count for {section}.{name}.{key}: {value!r}")

    for section, section_payload in metric_execution.items():
        if not isinstance(section_payload, dict):
            errors.append(f"invalid metric execution section {section!r}")
            continue
        for name, payload in section_payload.items():
            if not isinstance(payload, dict):
                errors.append(f"invalid metric execution payload {section}.{name}")
                continue
            for key in ("timeout_case_count", "error_case_count", "exception_case_count"):
                if key not in payload:
                    continue
                value = payload[key]
                if not isinstance(value, int) or isinstance(value, bool) or value != 0:
                    errors.append(f"non-zero or invalid {section}.{name}.{key}: {value!r}")
    return errors


def validate_official_results(
    result_root: Path, expected_pages: int, expected_path: Path
) -> list[str]:
    errors: list[str] = []
    try:
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid expected-denominator manifest: {type(exc).__name__}"]
    if (
        not isinstance(expected, dict)
        or expected.get("selected_pages") != expected_pages
        or not isinstance(expected.get("metrics"), dict)
    ):
        return ["expected-denominator manifest has invalid coverage or schema"]
    metric_paths = sorted(result_root.glob("*_metric_result.json"))
    summary_paths = sorted(result_root.glob("*_run_summary.json"))
    if len(metric_paths) != 1:
        errors.append(f"expected exactly one metric result, found {len(metric_paths)}")
    if len(summary_paths) != 1:
        errors.append(f"expected exactly one run summary, found {len(summary_paths)}")
    if len(metric_paths) != 1 or len(summary_paths) != 1:
        return errors
    try:
        metrics = json.loads(metric_paths[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid metric result JSON: {type(exc).__name__}")
        return errors
    try:
        summary = json.loads(summary_paths[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid run summary JSON: {type(exc).__name__}")
        return errors
    if not isinstance(metrics, dict) or not isinstance(summary, dict):
        return [*errors, "metric result and run summary must be JSON objects"]
    for path in REQUIRED_AGGREGATE_PATHS:
        value = nested_value(metrics, path)
        if not finite_number(value):
            errors.append(f"required aggregate {'.'.join(path)} is not a finite number: {value!r}")
    errors.extend(validate_execution_summary(summary))
    page_count = (
        summary.get("stage_execution", {}).get("page_match", {}).get("page_count")
        if isinstance(summary.get("stage_execution"), dict)
        else None
    )
    if page_count != expected_pages:
        errors.append(f"official page coverage is {page_count!r}, expected {expected_pages}")
    denominators = summary.get("page_denominators")
    if not isinstance(denominators, dict):
        errors.append("run summary has no page_denominators object")
        return errors
    for section, names in EXPECTED_METRICS.items():
        section_denominators = denominators.get(section)
        if not isinstance(section_denominators, dict):
            errors.append(f"run summary is missing denominators for {section}")
            continue
        for name in names:
            metric_denominator = section_denominators.get(name)
            value = metric_denominator.get("ALL") if isinstance(metric_denominator, dict) else None
            expected_value = expected["metrics"].get(section, {}).get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value != expected_value:
                errors.append(
                    f"invalid denominator for {section}.{name}: {value!r}; "
                    f"expected {expected_value!r}"
                )
    return errors


def main(args: argparse.Namespace) -> None:
    args.output.mkdir(parents=True, exist_ok=True)
    for name in (
        "evaluator-exit-code.txt",
        "prediction-manifest.json",
        "native-runtime.json",
        "native-smoke.stdout.log",
        "native-smoke.stderr.log",
        "native-smoke-exit-code.txt",
        "runtime-restore-outcome.txt",
        "inference-download-outcome.txt",
        "prediction-validation-exit-code.txt",
        "ground-truth-outcome.txt",
        "runtime-inventory-outcome.txt",
        "expected-denominators.json",
        "prediction-validation.stdout.log",
        "prediction-validation.stderr.log",
    ):
        copy_if_present(args.run / name, args.output / name)
    result_root = args.run / "result"
    expected_pages = 296 if args.subset == "hard" else 1651
    result_errors = validate_official_results(
        result_root, expected_pages, args.expected_denominators
    )
    statuses = {}
    for label, name in (
        ("runtime_restore", "runtime-restore-outcome.txt"),
        ("inference_download", "inference-download-outcome.txt"),
        ("prediction_validation", "prediction-validation-exit-code.txt"),
        ("ground_truth", "ground-truth-outcome.txt"),
        ("runtime_inventory", "runtime-inventory-outcome.txt"),
        ("native_smoke", "native-smoke-exit-code.txt"),
        ("evaluator", "evaluator-exit-code.txt"),
    ):
        path = args.run / name
        statuses[label] = path.read_text(encoding="utf-8").strip() if path.is_file() else "missing"
    expected = {
        "runtime_restore": "success",
        "inference_download": "success",
        "prediction_validation": "0",
        "ground_truth": "success",
        "runtime_inventory": "success",
        "native_smoke": "0",
        "evaluator": "0",
    }
    reasons = [
        f"{name}={statuses[name]} (expected {value})"
        for name, value in expected.items()
        if statuses[name] != value
    ]
    reasons.extend(result_errors)
    validation_path = args.run / "prediction-manifest.json"
    validation = None
    if validation_path.is_file():
        try:
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            validation = None
    if not isinstance(validation, dict):
        reasons.append("prediction validation manifest is missing or malformed")
    else:
        if validation.get("valid") is not True:
            reasons.append("prediction validation manifest is not valid")
        if validation.get("selected_cases") != expected_pages:
            reasons.append(
                f"prediction manifest covers {validation.get('selected_cases')!r} pages, "
                f"expected {expected_pages}"
            )
        if validation.get("missing_count") != 0 or validation.get("extra_count") != 0:
            reasons.append("prediction manifest contains missing or extra filenames")
    copied: list[str] = []
    if not reasons:
        metric_path = next(iter(sorted(result_root.glob("*_metric_result.json"))))
        summary_path = next(iter(sorted(result_root.glob("*_run_summary.json"))))
        safe_root = args.output / "result"
        safe_root.mkdir(parents=True, exist_ok=True)
        safe_metrics = curated_metrics(json.loads(metric_path.read_text(encoding="utf-8")))
        safe_summary = curated_run_summary(json.loads(summary_path.read_text(encoding="utf-8")))
        (safe_root / "official-metrics.json").write_text(
            json.dumps(safe_metrics, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        (safe_root / "official-run-summary.curated.json").write_text(
            json.dumps(safe_summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        copied.extend(("official-metrics.json", "official-run-summary.curated.json"))

    provenance = {
        "schema_version": 1,
        "system": args.system,
        "subset": args.subset,
        "evaluator_revision": EVALUATOR_REVISION,
        "runtime": "official evaluator code / documented native runtime",
        "canonical_evaluator_image_reference": CANONICAL_EVALUATOR_IMAGE_REFERENCE,
        "canonical_evaluator_image_executed": False,
        "aggregate_result_files": copied,
        "private_evaluator_log_digests": {
            name: digest
            for name in ("evaluator.stdout.log", "evaluator.stderr.log")
            if (digest := digest_if_present(args.run / name)) is not None
        },
        "excluded": [
            "ground-truth JSON and filtered evaluator slices",
            "generated evaluator config containing the private GT path",
            "per-element matched-sample result JSON containing GT/pred content",
            "raw evaluator stdout/stderr, which may echo GT-derived LaTeX or text snippets",
            "raw run-summary, stage-execution, and runtime-environment reports",
        ],
    }
    (args.output / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )

    validity = {
        "schema_version": 1,
        "valid": not reasons,
        "system": args.system,
        "subset": args.subset,
        "statuses": statuses,
        "reasons": reasons,
        "prediction_validation": validation,
    }
    (args.output / "run-validity.json").write_text(
        json.dumps(validity, indent=2) + "\n", encoding="utf-8"
    )
    if reasons:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--system", required=True)
    parser.add_argument("--subset", choices=("hard", "all"), required=True)
    parser.add_argument("--expected-denominators", type=Path, required=True)
    main(parser.parse_args())
