"""Reproducible benchmark cases, runner, and JSON report."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docreconstruct.renderers.json import to_jsonable

from .evaluator import EvaluationReport, evaluate


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    reference: Any
    candidate: Any
    reference_images: Any = None
    candidate_images: Any = None
    output_format: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.case_id).strip():
            raise ValueError("benchmark case_id must not be empty")


def _digest(source: Any) -> str:
    digest = hashlib.sha256()
    is_path = isinstance(source, Path)
    if isinstance(source, str) and "\n" not in source and len(source) <= 240:
        try:
            is_path = Path(source).is_file()
        except OSError:
            is_path = False
    if is_path:
        path = Path(source)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    else:
        payload = json.dumps(
            to_jsonable(source), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        digest.update(payload)
    return digest.hexdigest()


@dataclass(frozen=True)
class BenchmarkResult:
    case_id: str
    fingerprint: str
    evaluation: EvaluationReport | None
    metadata: dict[str, Any]
    error: str | None = None
    duration_seconds: float | None = None

    @property
    def score(self) -> float | None:
        return self.evaluation.score if self.evaluation else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "fingerprint": self.fingerprint,
            "score": self.score,
            "evaluation": self.evaluation.to_dict() if self.evaluation else None,
            "metadata": to_jsonable(self.metadata),
            "error": self.error,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True)
class BenchmarkReport:
    profile: str
    seed: int
    results: tuple[BenchmarkResult, ...]
    configuration: dict[str, Any] = field(default_factory=dict)
    model_versions: dict[str, str] = field(default_factory=dict)
    schema_version: str = "0.1"

    @property
    def successful_cases(self) -> int:
        return sum(result.evaluation is not None for result in self.results)

    @property
    def failed_cases(self) -> int:
        return len(self.results) - self.successful_cases

    @property
    def mean_score(self) -> float | None:
        """Average across every case, scoring a crashed case as zero.

        ``EvaluationReport.score`` is a non-optional float, so ``score is None``
        means exactly "this case raised and was recorded with an error".
        Dropping those from the denominator let a run that mostly crashed
        report the headline score of its few survivors, and matches neither
        ``ReconstructionBenchmarkRunner`` (which records 0.0 for a failed case)
        nor the operator's reading of the number.
        """

        if not self.results:
            return None
        return sum(
            result.score if result.score is not None else 0.0 for result in self.results
        ) / len(self.results)

    @property
    def component_means(self) -> dict[str, float | None]:
        components = ("text", "layout", "structure", "editability", "visual")
        means: dict[str, float | None] = {}
        for component in components:
            scores = [
                getattr(result.evaluation.fidelity, component)
                for result in self.results
                if result.evaluation is not None
                and getattr(result.evaluation.fidelity, component) is not None
            ]
            # A component mean cannot be zero-filled: "no successful case
            # measured this" and "every case scored zero" are different claims.
            # Withhold the number instead of averaging a shrunken population.
            means[component] = (
                sum(scores) / len(scores) if scores and not self.failed_cases else None
            )
        return means

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile": self.profile,
            "seed": self.seed,
            "configuration": to_jsonable(self.configuration),
            "model_versions": dict(sorted(self.model_versions.items())),
            "summary": {
                "total_cases": len(self.results),
                "successful_cases": self.successful_cases,
                "failed_cases": self.failed_cases,
                "mean_score": self.mean_score,
                "component_means": self.component_means,
            },
            "results": [result.to_dict() for result in self.results],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return (
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                indent=indent,
                separators=(",", ":") if indent is None else None,
            )
            + "\n"
        )

    def write(self, destination: str | Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self.to_json(), encoding="utf-8", newline="\n")
        return destination.resolve()


class BenchmarkRunner:
    def __init__(
        self,
        *,
        profile: str = "balanced",
        seed: int = 0,
        evaluator: Callable[..., EvaluationReport] = evaluate,
        configuration: Mapping[str, Any] | None = None,
        model_versions: Mapping[str, str] | None = None,
        record_timings: bool = False,
        fail_fast: bool = False,
    ) -> None:
        self.profile = profile
        self.seed = int(seed)
        self.evaluator = evaluator
        self.configuration = dict(configuration or {})
        self.model_versions = {
            str(key): str(value) for key, value in (model_versions or {}).items()
        }
        self.record_timings = record_timings
        self.fail_fast = fail_fast

    def run(self, cases: Iterable[BenchmarkCase]) -> BenchmarkReport:
        ordered = sorted(cases, key=lambda case: case.case_id)
        identifiers = [case.case_id for case in ordered]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("benchmark case IDs must be unique")
        results: list[BenchmarkResult] = []
        for case in ordered:
            started = time.perf_counter()
            candidate = case.candidate
            try:
                if callable(candidate):
                    candidate = candidate(case.reference)
                fingerprint = hashlib.sha256(
                    f"{_digest(case.reference)}:{_digest(candidate)}".encode("ascii")
                ).hexdigest()
                report = self.evaluator(
                    case.reference,
                    candidate,
                    profile=self.profile,
                    reference_images=case.reference_images,
                    candidate_images=case.candidate_images,
                    output_format=case.output_format,
                )
                error = None
            except Exception as exc:  # a benchmark should record provider failures
                if self.fail_fast:
                    raise
                fingerprint = hashlib.sha256(case.case_id.encode("utf-8")).hexdigest()
                report = None
                error = f"{type(exc).__name__}: {exc}"
            duration = time.perf_counter() - started if self.record_timings else None
            results.append(
                BenchmarkResult(
                    case_id=case.case_id,
                    fingerprint=fingerprint,
                    evaluation=report,
                    metadata=dict(case.metadata),
                    error=error,
                    duration_seconds=duration,
                )
            )
        return BenchmarkReport(
            profile=self.profile,
            seed=self.seed,
            results=tuple(results),
            configuration=self.configuration,
            model_versions=self.model_versions,
        )


def _resolve_manifest_value(value: Any, base: Path) -> Any:
    if isinstance(value, list):
        return [_resolve_manifest_value(item, base) for item in value]
    if isinstance(value, dict):
        # Inline IR objects must remain dictionaries. Resolving arbitrary
        # nested strings would corrupt source text that happens to name a file.
        return value
    if isinstance(value, str):
        candidate = base / value
        if candidate.is_file():
            return candidate
    return value


def load_manifest(dataset: str | Path) -> tuple[list[BenchmarkCase], dict[str, Any]]:
    path = Path(dataset)
    manifest_path = path / "manifest.json" if path.is_dir() else path
    if not manifest_path.is_file():
        raise FileNotFoundError(f"benchmark manifest not found: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries: Any
    if isinstance(payload, list):
        entries = payload
        manifest: dict[str, Any] = {}
    elif isinstance(payload, dict):
        entries = payload.get("cases")
        manifest = payload
    else:
        entries = None
        manifest = {}
    if not isinstance(entries, list):
        raise ValueError("benchmark manifest must contain a `cases` array")
    cases: list[BenchmarkCase] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"benchmark case {index} must be an object")
        case_id = str(entry.get("id", entry.get("case_id", f"case-{index + 1:04d}")))
        if "reference" not in entry or "candidate" not in entry:
            raise ValueError(f"benchmark case {case_id!r} needs reference and candidate")
        base = manifest_path.parent
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                reference=_resolve_manifest_value(entry["reference"], base),
                candidate=_resolve_manifest_value(entry["candidate"], base),
                reference_images=_resolve_manifest_value(entry.get("reference_images"), base),
                candidate_images=_resolve_manifest_value(entry.get("candidate_images"), base),
                output_format=entry.get("output_format"),
                metadata=dict(entry.get("metadata") or {}),
            )
        )
    return cases, manifest


def run_benchmark(
    dataset: str | Path | Iterable[BenchmarkCase],
    *,
    profile: str = "balanced",
    seed: int = 0,
    output_path: str | Path | None = None,
    record_timings: bool = False,
    fail_fast: bool = False,
) -> BenchmarkReport:
    """Run cases directly or load ``manifest.json`` from a path/directory."""

    manifest: dict[str, Any] = {}
    if isinstance(dataset, (str, Path)):
        cases, manifest = load_manifest(dataset)
    else:
        cases = list(dataset)
    runner = BenchmarkRunner(
        profile=profile,
        seed=seed,
        configuration=dict(manifest.get("configuration") or manifest.get("config") or {}),
        model_versions=dict(manifest.get("model_versions") or {}),
        record_timings=record_timings,
        fail_fast=fail_fast,
    )
    report = runner.run(cases)
    if output_path is not None:
        report.write(output_path)
    return report
