"""End-to-end OCR-to-Markdown benchmarks with deterministic slice reports.

The benchmark deliberately calls :func:`docreconstruct.extraction.extract_to_markdown`
instead of evaluating precomputed candidates.  Hosted providers therefore retain the
orchestrator's explicit cloud-consent gate, while tests and private deployments can
inject an isolated registry or a compatible extraction callable.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docreconstruct.extraction import (
    ExtractionMode,
    ExtractionResult,
    extract_to_markdown,
)
from docreconstruct.providers import ProviderRegistry
from docreconstruct.providers import registry as global_registry
from docreconstruct.providers._hosted import safe_raw
from docreconstruct.renderers.json import to_jsonable

from .evaluator import EvaluationReport, evaluate

ExtractionCallable = Callable[..., ExtractionResult]
_COMPONENTS = ("text", "layout", "structure", "editability", "visual")
_SLICE_FIELDS = {
    "language": "languages",
    "script": "scripts",
    "document_type": "document_types",
    "degradation": "degradations",
    "content_kind": "content_kinds",
}


def _normalized_strings(value: Any, *, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values: Sequence[Any] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        raise ValueError(f"{label} must be a string or array of strings")
    normalized: dict[str, str] = {}
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} entries must be non-empty strings")
        text = item.strip()
        normalized.setdefault(text.casefold(), text)
    return tuple(sorted(normalized.values(), key=str.casefold))


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(
        to_jsonable(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _stable_digest(value: Any) -> str:
    return _sha256_bytes(_stable_json(value).encode("utf-8"))


def _path_digest(path: Path) -> str:
    if path.is_file():
        return _sha256_file(path)
    return _stable_digest({"missing_path": path.as_posix()})


@dataclass(frozen=True, slots=True)
class OCRBenchmarkFeatures:
    """Extraction switches that can materially change OCR output."""

    languages: tuple[str, ...] = ()
    handwriting: bool = False
    formulas: bool = True
    tables: bool = True
    charts: bool = False
    distorted_photo: bool = False
    dewarping: bool = False
    ensemble: bool = False
    maximum_providers: int = 2

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "languages",
            _normalized_strings(self.languages, label="features.languages"),
        )
        if not 1 <= self.maximum_providers <= 8:
            raise ValueError("features.maximum_providers must be between 1 and 8")

    def to_dict(self) -> dict[str, Any]:
        return {
            "languages": list(self.languages),
            "handwriting": self.handwriting,
            "formulas": self.formulas,
            "tables": self.tables,
            "charts": self.charts,
            "distorted_photo": self.distorted_photo,
            "dewarping": self.dewarping,
            "ensemble": self.ensemble,
            "maximum_providers": self.maximum_providers,
        }


@dataclass(frozen=True, slots=True)
class OCRBenchmarkTags:
    """Dataset taxonomy used for deterministic performance slices."""

    languages: tuple[str, ...] = ()
    scripts: tuple[str, ...] = ()
    document_types: tuple[str, ...] = ()
    degradations: tuple[str, ...] = ()
    content_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in _SLICE_FIELDS.values():
            object.__setattr__(
                self,
                field_name,
                _normalized_strings(
                    getattr(self, field_name),
                    label=f"tags.{field_name}",
                ),
            )

    def merged_languages(self, languages: Sequence[str]) -> OCRBenchmarkTags:
        combined = (*self.languages, *languages)
        return OCRBenchmarkTags(
            languages=_normalized_strings(combined, label="tags.languages"),
            scripts=self.scripts,
            document_types=self.document_types,
            degradations=self.degradations,
            content_kinds=self.content_kinds,
        )

    def to_dict(self) -> dict[str, list[str]]:
        return {
            field_name: list(getattr(self, field_name)) for field_name in _SLICE_FIELDS.values()
        }


@dataclass(frozen=True, slots=True)
class OCRBenchmarkCase:
    """One source image/PDF, Markdown truth, and extraction configuration."""

    case_id: str
    source: Path
    ground_truth: Path
    mode: ExtractionMode = ExtractionMode.LOCAL
    providers: tuple[str, ...] = ()
    features: OCRBenchmarkFeatures = field(default_factory=OCRBenchmarkFeatures)
    tags: OCRBenchmarkTags = field(default_factory=OCRBenchmarkTags)
    provider_options: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        case_id = str(self.case_id).strip()
        if not case_id:
            raise ValueError("OCR benchmark case_id must not be empty")
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "source", Path(self.source).expanduser().resolve())
        truth = Path(self.ground_truth).expanduser().resolve()
        if truth.suffix.casefold() not in {".md", ".markdown"}:
            raise ValueError(f"ground_truth for {case_id!r} must be Markdown")
        object.__setattr__(self, "ground_truth", truth)
        object.__setattr__(self, "mode", ExtractionMode(self.mode))
        providers = tuple(
            name.strip().casefold().replace("-", "_")
            for name in self.providers
            if name.strip() and name.strip().casefold() != "auto"
        )
        object.__setattr__(self, "providers", tuple(dict.fromkeys(providers)))
        object.__setattr__(self, "provider_options", dict(self.provider_options))
        sanitized_metadata = safe_raw(dict(self.metadata))
        assert isinstance(sanitized_metadata, dict)
        object.__setattr__(self, "metadata", sanitized_metadata)

    @property
    def effective_tags(self) -> OCRBenchmarkTags:
        return self.tags.merged_languages(self.features.languages)

    def fingerprint(self, *, model_versions: Mapping[str, str]) -> str:
        """Fingerprint inputs/configuration without exposing provider secrets."""

        payload = {
            "schema": "ocr-case/0.1",
            "source_sha256": _path_digest(self.source),
            "ground_truth_sha256": _path_digest(self.ground_truth),
            "mode": self.mode.value,
            "providers": list(self.providers) or ["auto"],
            "features": self.features.to_dict(),
            "tags": self.effective_tags.to_dict(),
            "provider_options_sha256": _stable_digest(self.provider_options),
            "model_versions": dict(sorted(model_versions.items())),
        }
        return _stable_digest(payload)


@dataclass(frozen=True, slots=True)
class OCRBenchmarkFailure:
    phase: str
    error_type: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "phase": self.phase,
            "error_type": self.error_type,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class OCRBenchmarkResult:
    case_id: str
    fingerprint: str
    output_sha256: str | None
    evaluation: EvaluationReport | None
    extraction_manifest: dict[str, Any] | None
    model_versions: dict[str, str]
    tags: OCRBenchmarkTags
    metadata: dict[str, Any]
    failure: OCRBenchmarkFailure | None = None
    duration_seconds: float | None = None

    @property
    def score(self) -> float | None:
        return self.evaluation.score if self.evaluation is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "fingerprint": self.fingerprint,
            "output_sha256": self.output_sha256,
            "score": self.score,
            "evaluation": self.evaluation.to_dict() if self.evaluation else None,
            "extraction_manifest": to_jsonable(self.extraction_manifest),
            "model_versions": dict(sorted(self.model_versions.items())),
            "tags": self.tags.to_dict(),
            "metadata": to_jsonable(self.metadata),
            "failure": self.failure.to_dict() if self.failure else None,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True, slots=True)
class OCRSliceSummary:
    total_cases: int
    successful_cases: int
    mean_score: float | None
    component_means: dict[str, float | None]

    @property
    def failed_cases(self) -> int:
        return self.total_cases - self.successful_cases

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "successful_cases": self.successful_cases,
            "failed_cases": self.failed_cases,
            "mean_score": self.mean_score,
            "component_means": self.component_means,
        }


def _component_means(results: Sequence[OCRBenchmarkResult]) -> dict[str, float | None]:
    means: dict[str, float | None] = {}
    for component in _COMPONENTS:
        scores = [
            value
            for result in results
            if result.evaluation is not None
            and (value := getattr(result.evaluation.fidelity, component)) is not None
        ]
        means[component] = sum(scores) / len(scores) if scores else None
    return means


def _slice_summary(results: Sequence[OCRBenchmarkResult]) -> OCRSliceSummary:
    scores = [result.score for result in results if result.score is not None]
    return OCRSliceSummary(
        total_cases=len(results),
        successful_cases=len(scores),
        mean_score=sum(scores) / len(scores) if scores else None,
        component_means=_component_means(results),
    )


@dataclass(frozen=True, slots=True)
class OCRBenchmarkReport:
    profile: str
    seed: int
    results: tuple[OCRBenchmarkResult, ...]
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
        scores = [result.score for result in self.results if result.score is not None]
        return sum(scores) / len(scores) if scores else None

    @property
    def component_means(self) -> dict[str, float | None]:
        return _component_means(self.results)

    @property
    def slice_means(self) -> dict[str, dict[str, OCRSliceSummary]]:
        slices: dict[str, dict[str, OCRSliceSummary]] = {}
        for dimension, field_name in _SLICE_FIELDS.items():
            members: dict[str, list[OCRBenchmarkResult]] = {}
            for result in self.results:
                for value in getattr(result.tags, field_name):
                    members.setdefault(value, []).append(result)
            slices[dimension] = {
                value: _slice_summary(members[value]) for value in sorted(members, key=str.casefold)
            }
        return slices

    @property
    def run_fingerprint(self) -> str:
        payload = {
            "schema": f"ocr-report/{self.schema_version}",
            "profile": self.profile,
            "seed": self.seed,
            "configuration": self.configuration,
            "model_versions": dict(sorted(self.model_versions.items())),
            "results": [
                {
                    "case_id": result.case_id,
                    "fingerprint": result.fingerprint,
                    "output_sha256": result.output_sha256,
                    "failure_type": result.failure.error_type if result.failure else None,
                    "failure_phase": result.failure.phase if result.failure else None,
                }
                for result in self.results
            ],
        }
        return _stable_digest(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_fingerprint": self.run_fingerprint,
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
                "slice_means": {
                    dimension: {value: summary.to_dict() for value, summary in values.items()}
                    for dimension, values in self.slice_means.items()
                },
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
        path = Path(destination).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8", newline="\n")
        return path


def _provider_version(registry: ProviderRegistry, provider: str) -> str | None:
    try:
        capabilities = registry.get_capabilities(provider)
    except KeyError:
        return None
    if capabilities is None:
        return None
    model = capabilities.model_name
    version = capabilities.model_version
    if model and version:
        return f"{model}@{version}"
    return model or version


def _merge_version(target: dict[str, str], provider: str, version: str) -> None:
    existing = target.get(provider)
    if existing is None or existing == version:
        target[provider] = version
        return
    values: set[str] = set()
    for item in (existing, version):
        if item.startswith("mixed[") and item.endswith("]"):
            values.update(item[6:-1].split("|"))
        else:
            values.add(item)
    target[provider] = f"mixed[{'|'.join(sorted(values))}]"


def _versions_for_names(
    registry: ProviderRegistry,
    names: Iterable[str],
) -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in sorted(set(names)):
        version = _provider_version(registry, name)
        if version:
            versions[name] = version
    return versions


def _versions_from_extraction(
    extraction: ExtractionResult,
    registry: ProviderRegistry,
) -> dict[str, str]:
    names = extraction.manifest.selected_providers
    versions = _versions_for_names(registry, names)
    metadata = extraction.document.metadata
    declared = metadata.get("model_versions")
    if isinstance(declared, Mapping):
        for provider, version in declared.items():
            if isinstance(provider, str) and isinstance(version, str) and version:
                _merge_version(versions, provider, version)
    model = metadata.get("model")
    successful = extraction.manifest.successful_providers
    if isinstance(model, str) and model and len(successful) == 1:
        versions[successful[0]] = model
    return versions


def _safe_name(case_id: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", case_id).strip("-.")
    return name[:80] or "case"


class OCRBenchmarkRunner:
    """Execute extraction and evaluate the emitted Markdown for every case."""

    def __init__(
        self,
        *,
        output_dir: str | Path,
        profile: str = "balanced",
        seed: int = 0,
        allow_cloud: bool = False,
        extractor: ExtractionCallable = extract_to_markdown,
        registry: ProviderRegistry | None = None,
        evaluator: Callable[..., EvaluationReport] = evaluate,
        configuration: Mapping[str, Any] | None = None,
        model_versions: Mapping[str, str] | None = None,
        record_timings: bool = False,
        fail_fast: bool = False,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.profile = profile
        self.seed = int(seed)
        self.allow_cloud = bool(allow_cloud)
        self.extractor = extractor
        self.registry = registry or global_registry
        self.evaluator = evaluator
        self.configuration = dict(configuration or {})
        self.model_versions = {
            str(key): str(value) for key, value in (model_versions or {}).items()
        }
        self.record_timings = record_timings
        self.fail_fast = fail_fast

    def run(self, cases: Iterable[OCRBenchmarkCase]) -> OCRBenchmarkReport:
        ordered = sorted(cases, key=lambda case: case.case_id)
        identifiers = [case.case_id for case in ordered]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("OCR benchmark case IDs must be unique")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        results: list[OCRBenchmarkResult] = []
        report_versions = dict(self.model_versions)
        for index, case in enumerate(ordered, start=1):
            started = time.perf_counter()
            candidate = self.output_dir / f"{index:04d}-{_safe_name(case.case_id)}.md"
            case_versions = dict(self.model_versions)
            for provider, version in _versions_for_names(self.registry, case.providers).items():
                _merge_version(case_versions, provider, version)
            extraction_manifest: dict[str, Any] | None = None
            evaluation: EvaluationReport | None = None
            output_sha256: str | None = None
            failure: OCRBenchmarkFailure | None = None
            phase = "extraction"
            try:
                extraction_kwargs: dict[str, Any] = {
                    "output": candidate,
                    "mode": case.mode,
                    "providers": list(case.providers) or None,
                    "allow_cloud": self.allow_cloud,
                    "ensemble": case.features.ensemble,
                    "maximum_providers": case.features.maximum_providers,
                    "languages": case.features.languages,
                    "handwriting": case.features.handwriting,
                    "formulas": case.features.formulas,
                    "tables": case.features.tables,
                    "charts": case.features.charts,
                    "distorted_photo": case.features.distorted_photo,
                    "dewarping": case.features.dewarping,
                    "provider_options": case.provider_options or None,
                    "registry": self.registry,
                }
                extraction = self.extractor(case.source, **extraction_kwargs)
                extraction_manifest = extraction.manifest.model_dump(mode="json")
                extracted_versions = _versions_from_extraction(extraction, self.registry)
                for provider, version in extracted_versions.items():
                    _merge_version(case_versions, provider, version)
                output_sha256 = _sha256_file(extraction.output)
                phase = "evaluation"
                evaluation = self.evaluator(
                    case.ground_truth,
                    extraction.output,
                    profile=self.profile,
                    output_format="markdown",
                )
            except Exception as exc:
                if self.fail_fast:
                    raise
                failure = OCRBenchmarkFailure(
                    phase=phase,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            for provider, version in case_versions.items():
                _merge_version(report_versions, provider, version)
            duration = time.perf_counter() - started if self.record_timings else None
            results.append(
                OCRBenchmarkResult(
                    case_id=case.case_id,
                    fingerprint=case.fingerprint(model_versions=case_versions),
                    output_sha256=output_sha256,
                    evaluation=evaluation,
                    extraction_manifest=extraction_manifest,
                    model_versions=case_versions,
                    tags=case.effective_tags,
                    metadata=dict(case.metadata),
                    failure=failure,
                    duration_seconds=duration,
                )
            )
        return OCRBenchmarkReport(
            profile=self.profile,
            seed=self.seed,
            results=tuple(results),
            configuration=self.configuration,
            model_versions=report_versions,
        )


def _mapping(value: Any, *, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _boolean(value: Any, *, label: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be true or false")
    return value


def _features(value: Any) -> OCRBenchmarkFeatures:
    data = _mapping(value, label="features")
    aliases = {"distorted_photos": "distorted_photo"}
    for old, new in aliases.items():
        if old in data:
            if new in data:
                raise ValueError(f"features cannot specify both {old!r} and {new!r}")
            data[new] = data.pop(old)
    allowed = {
        "languages",
        "handwriting",
        "formulas",
        "tables",
        "charts",
        "distorted_photo",
        "dewarping",
        "ensemble",
        "maximum_providers",
    }
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"unknown OCR benchmark feature(s): {', '.join(unknown)}")
    maximum = data.get("maximum_providers", 2)
    if isinstance(maximum, bool) or not isinstance(maximum, int):
        raise ValueError("features.maximum_providers must be an integer")
    return OCRBenchmarkFeatures(
        languages=_normalized_strings(data.get("languages"), label="features.languages"),
        handwriting=_boolean(data.get("handwriting"), label="features.handwriting", default=False),
        formulas=_boolean(data.get("formulas"), label="features.formulas", default=True),
        tables=_boolean(data.get("tables"), label="features.tables", default=True),
        charts=_boolean(data.get("charts"), label="features.charts", default=False),
        distorted_photo=_boolean(
            data.get("distorted_photo"),
            label="features.distorted_photo",
            default=False,
        ),
        dewarping=_boolean(data.get("dewarping"), label="features.dewarping", default=False),
        ensemble=_boolean(data.get("ensemble"), label="features.ensemble", default=False),
        maximum_providers=maximum,
    )


def _tags(value: Any) -> OCRBenchmarkTags:
    data = _mapping(value, label="tags")
    aliases = {
        "language": "languages",
        "script": "scripts",
        "document_type": "document_types",
        "degradation": "degradations",
        "content_kind": "content_kinds",
    }
    for old, new in aliases.items():
        if old in data:
            if new in data:
                raise ValueError(f"tags cannot specify both {old!r} and {new!r}")
            data[new] = data.pop(old)
    unknown = sorted(set(data) - set(_SLICE_FIELDS.values()))
    if unknown:
        raise ValueError(f"unknown OCR benchmark tag(s): {', '.join(unknown)}")
    return OCRBenchmarkTags(
        **{
            field_name: _normalized_strings(data.get(field_name), label=f"tags.{field_name}")
            for field_name in _SLICE_FIELDS.values()
        }
    )


def _merge_nested_options(
    base: Mapping[str, Any],
    override: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = {**dict(merged[key]), **dict(value)}
        else:
            merged[key] = value
    return merged


def _manifest_path(dataset: str | Path) -> Path:
    path = Path(dataset).expanduser().resolve()
    if not path.is_dir():
        return path
    preferred = path / "ocr-benchmark.json"
    return preferred if preferred.is_file() else path / "manifest.json"


def load_ocr_benchmark_manifest(
    dataset: str | Path,
) -> tuple[list[OCRBenchmarkCase], dict[str, Any]]:
    """Load relative OCR benchmark cases from JSON without executing providers."""

    manifest_path = _manifest_path(dataset)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"OCR benchmark manifest not found: {manifest_path}")
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
        raise ValueError("OCR benchmark manifest must contain a `cases` array")
    defaults = _mapping(manifest.get("extraction"), label="extraction")
    default_features = _mapping(defaults.get("features"), label="extraction.features")
    default_options = _mapping(
        defaults.get("provider_options"),
        label="extraction.provider_options",
    )
    base = manifest_path.parent
    cases: list[OCRBenchmarkCase] = []
    for index, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, Mapping):
            raise ValueError(f"OCR benchmark case {index} must be an object")
        entry = dict(raw_entry)
        case_id = str(entry.get("id", entry.get("case_id", f"case-{index + 1:04d}")))
        if "source" not in entry or "ground_truth" not in entry:
            raise ValueError(
                f"OCR benchmark case {case_id!r} needs source and ground_truth Markdown"
            )
        source = Path(str(entry["source"]))
        truth = Path(str(entry["ground_truth"]))
        if not source.is_absolute():
            source = base / source
        if not truth.is_absolute():
            truth = base / truth
        feature_data = {**default_features, **_mapping(entry.get("features"), label="features")}
        providers = entry.get("providers", defaults.get("providers"))
        provider_names = _normalized_strings(providers, label="providers")
        options = _merge_nested_options(
            default_options,
            _mapping(entry.get("provider_options"), label="provider_options"),
        )
        cases.append(
            OCRBenchmarkCase(
                case_id=case_id,
                source=source,
                ground_truth=truth,
                mode=entry.get("mode", defaults.get("mode", "local")),
                providers=provider_names,
                features=_features(feature_data),
                tags=_tags(entry.get("tags")),
                provider_options=options,
                metadata=_mapping(entry.get("metadata"), label="metadata"),
            )
        )
    return cases, manifest


def run_ocr_benchmark(
    dataset: str | Path | Iterable[OCRBenchmarkCase],
    *,
    output_dir: str | Path | None = None,
    output_path: str | Path | None = None,
    profile: str | None = None,
    seed: int | None = None,
    allow_cloud: bool = False,
    extractor: ExtractionCallable = extract_to_markdown,
    registry: ProviderRegistry | None = None,
    evaluator: Callable[..., EvaluationReport] = evaluate,
    model_versions: Mapping[str, str] | None = None,
    record_timings: bool = False,
    fail_fast: bool = False,
) -> OCRBenchmarkReport:
    """Load/run an OCR dataset and optionally write one deterministic report.

    A cloud or hybrid case still requires ``allow_cloud=True`` at runtime.  The
    manifest cannot grant permission to upload document bytes.
    """

    manifest: dict[str, Any] = {}
    if isinstance(dataset, (str, Path)):
        cases, manifest = load_ocr_benchmark_manifest(dataset)
        manifest_path = _manifest_path(dataset)
        default_output_dir = manifest_path.parent / "ocr-benchmark-output"
    else:
        cases = list(dataset)
        default_output_dir = Path.cwd() / "ocr-benchmark-output"
    configured_versions = {
        str(key): str(value)
        for key, value in _mapping(manifest.get("model_versions"), label="model_versions").items()
    }
    for key, value in (model_versions or {}).items():
        configured_versions[str(key)] = str(value)
    raw_configuration = safe_raw(_mapping(manifest.get("configuration"), label="configuration"))
    assert isinstance(raw_configuration, dict)
    configuration = raw_configuration
    runner = OCRBenchmarkRunner(
        output_dir=output_dir or default_output_dir,
        profile=profile or str(manifest.get("profile") or "balanced"),
        seed=int(manifest.get("seed", 0) if seed is None else seed),
        allow_cloud=allow_cloud,
        extractor=extractor,
        registry=registry,
        evaluator=evaluator,
        configuration=configuration,
        model_versions=configured_versions,
        record_timings=record_timings,
        fail_fast=fail_fast,
    )
    report = runner.run(cases)
    if output_path is not None:
        report.write(output_path)
    return report


__all__ = [
    "OCRBenchmarkCase",
    "OCRBenchmarkFailure",
    "OCRBenchmarkFeatures",
    "OCRBenchmarkReport",
    "OCRBenchmarkResult",
    "OCRBenchmarkRunner",
    "OCRBenchmarkTags",
    "OCRSliceSummary",
    "load_ocr_benchmark_manifest",
    "run_ocr_benchmark",
]
