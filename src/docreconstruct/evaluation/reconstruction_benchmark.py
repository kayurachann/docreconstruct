"""End-to-end three-source reconstruction benchmarks.

Every case starts from the original layout, reviewed Markdown, and one or more
saved positioned-evidence sidecars.  The candidate is always created inside a
fresh case directory by :func:`run_hybrid_job`; manifests cannot point at a
precomputed DOCX and therefore cannot accidentally benchmark a stale artifact.

Pipeline exceptions are observations, not missing data.  A failed case records
both quality and operational scores as zero so aggregate and slice scores cannot
improve merely because a difficult case failed to produce a candidate.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from docreconstruct.evaluation.visual import VISUAL_METRIC_VERSION
from docreconstruct.evidence import ProviderHints, load_sidecar_evidence
from docreconstruct.providers._hosted import safe_raw
from docreconstruct.renderers.json import to_jsonable

if TYPE_CHECKING:
    from docreconstruct.reconstruction.hybrid_job import HybridJobResult

HybridJobCallable = Callable[..., "HybridJobResult"]
_SLICE_FIELDS = {
    "language": "languages",
    "script": "scripts",
    "document_type": "document_types",
    "degradation": "degradations",
    "content_kind": "content_kinds",
}
_RENDER_BACKENDS = {"native", "auto", "libreoffice"}
_MANIFEST_SCHEMA_VERSION = "0.1"
_REPORT_SCHEMA_VERSION = "0.2"
_ROOT_FIELDS = {
    "schema_version",
    "seed",
    "configuration",
    "job_options",
    "reconstruction",
    "cases",
}
_CASE_FIELDS = {
    "id",
    "case_id",
    "original_layout",
    "reviewed_markdown",
    "evidence",
    "evidence_provider_hints",
    "job_options",
    "tags",
    "metadata",
}
_PRIVATE_VALUE_KEYS = {
    "content",
    "diagnostic",
    "detail",
    "error",
    "markdown",
    "message",
    "raw",
    "snippet",
    "text",
}
_PATH_VALUE_KEYS = {
    "candidate",
    "content_path",
    "executable",
    "layout",
    "layout_path",
    "path",
    "run_directory",
    "source",
}
_ABSOLUTE_PATH_IN_TEXT = re.compile(
    r"(?i)(?:"
    r"(?<![\w])[a-z]:[\\/][^\r\n;]+"
    r"|(?<![\w/])/(?!/)[^\r\n;]+"
    r")"
)


def run_hybrid_job(*args: Any, **kwargs: Any) -> HybridJobResult:
    """Import the job lazily to avoid the evaluation-package startup cycle."""

    from docreconstruct.reconstruction.hybrid_job import run_hybrid_job as execute

    return execute(*args, **kwargs)


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


def _normalized_label(value: str) -> str:
    """Return one stable, human-readable slice key."""

    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _normalized_strings(value: Any, *, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values: Sequence[Any] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        raise ValueError(f"{label} must be a string or array of strings")
    normalized: set[str] = set()
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} entries must be non-empty strings")
        normalized.add(_normalized_label(item))
    return tuple(sorted(normalized))


def _privacy_safe_value(value: Any, *, key: str | None = None) -> Any:
    """Bound and redact report payloads while retaining numeric audit evidence."""

    normalized_key = (key or "").casefold().replace("-", "_")
    if normalized_key in _PATH_VALUE_KEYS or normalized_key.endswith("_path"):
        return "<local-path-redacted>" if value is not None else None
    if normalized_key in _PRIVATE_VALUE_KEYS or any(
        marker in normalized_key for marker in ("snippet", "password", "secret", "token")
    ):
        if value is None:
            return None
        encoded = str(value).encode("utf-8", errors="replace")
        return {
            "redacted": True,
            "sha256": _sha256_bytes(encoded),
            "characters": len(str(value)),
        }
    if isinstance(value, Mapping):
        return {
            str(nested_key): _privacy_safe_value(nested, key=str(nested_key))
            for nested_key, nested in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_privacy_safe_value(item) for item in value]
    if isinstance(value, str):
        redacted = safe_raw(value)
        assert isinstance(redacted, str)
        redacted = _ABSOLUTE_PATH_IN_TEXT.sub("<local-path-redacted>", redacted)
        if len(redacted) > 512:
            return {
                "redacted": True,
                "sha256": _sha256_bytes(redacted.encode("utf-8")),
                "characters": len(redacted),
            }
        return redacted
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _privacy_safe_value(str(value), key=key)


def _privacy_safe_validation(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep validation decisions and metrics without authority paths or snippets."""

    allowed = {
        "passed",
        "score",
        "passed_gates",
        "measured_gates",
        "candidate_sha256",
        "metrics",
        "unmeasured",
    }
    safe = {key: value[key] for key in allowed if key in value}
    gates = value.get("gates")
    if isinstance(gates, Sequence) and not isinstance(gates, (str, bytes, bytearray)):
        safe["gates"] = [
            {
                key: gate[key]
                for key in ("name", "passed")
                if isinstance(gate, Mapping) and key in gate
            }
            for gate in gates
            if isinstance(gate, Mapping)
        ]
    sanitized = _privacy_safe_value(safe)
    assert isinstance(sanitized, dict)
    return sanitized


def _privacy_safe_reconstruction(value: Mapping[str, Any]) -> dict[str, Any]:
    """Retain hashes/policies/summaries while removing local source locations."""

    sanitized = _privacy_safe_value(value)
    assert isinstance(sanitized, dict)
    return sanitized


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


def _optional_score(value: Any, *, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number between 0 and 1")
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError(f"{label} must be a number between 0 and 1")
    return score


def _normalized_backend(value: str, *, label: str = "render_backend") -> str:
    backend = str(value).strip().casefold()
    if backend not in _RENDER_BACKENDS:
        choices = ", ".join(sorted(_RENDER_BACKENDS))
        raise ValueError(f"{label} must be one of: {choices}")
    return backend


@dataclass(frozen=True, slots=True)
class ReconstructionJobOptions:
    """Case-level switches passed to the offline hybrid job.

    ``renderer_path`` is intentionally absent: an input manifest may select a
    backend, but the executable remains an explicit runtime/CLI choice.
    """

    strict_evidence: bool = True
    allow_remote_assets: bool = False
    render_backend: str | None = None
    minimum_visual_score: float | None = None
    save_render_artifacts: bool = False

    def __post_init__(self) -> None:
        if self.render_backend is not None:
            object.__setattr__(
                self,
                "render_backend",
                _normalized_backend(self.render_backend, label="job_options.render_backend"),
            )
        score = _optional_score(
            self.minimum_visual_score,
            label="job_options.minimum_visual_score",
        )
        object.__setattr__(self, "minimum_visual_score", score)
        if self.render_backend == "native" and score is not None:
            raise ValueError(
                "job_options.minimum_visual_score requires auto or libreoffice render_backend"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strict_evidence": self.strict_evidence,
            "allow_remote_assets": self.allow_remote_assets,
            "render_backend": self.render_backend,
            "minimum_visual_score": self.minimum_visual_score,
            "save_render_artifacts": self.save_render_artifacts,
        }


@dataclass(frozen=True, slots=True)
class ReconstructionBenchmarkTags:
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

    def to_dict(self) -> dict[str, list[str]]:
        return {
            field_name: list(getattr(self, field_name)) for field_name in _SLICE_FIELDS.values()
        }


def _coerce_job_options(
    value: ReconstructionJobOptions | Mapping[str, Any],
) -> ReconstructionJobOptions:
    if isinstance(value, ReconstructionJobOptions):
        return value
    data = _mapping(value, label="job_options")
    allowed = {
        "strict_evidence",
        "allow_remote_assets",
        "render_backend",
        "minimum_visual_score",
        "save_render_artifacts",
    }
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"unknown reconstruction job option(s): {', '.join(unknown)}")
    return ReconstructionJobOptions(
        strict_evidence=_boolean(
            data.get("strict_evidence"),
            label="job_options.strict_evidence",
            default=True,
        ),
        allow_remote_assets=_boolean(
            data.get("allow_remote_assets"),
            label="job_options.allow_remote_assets",
            default=False,
        ),
        render_backend=data.get("render_backend"),
        minimum_visual_score=_optional_score(
            data.get("minimum_visual_score"),
            label="job_options.minimum_visual_score",
        ),
        save_render_artifacts=_boolean(
            data.get("save_render_artifacts"),
            label="job_options.save_render_artifacts",
            default=False,
        ),
    )


def _coerce_tags(
    value: ReconstructionBenchmarkTags | Mapping[str, Any],
) -> ReconstructionBenchmarkTags:
    if isinstance(value, ReconstructionBenchmarkTags):
        return value
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
        raise ValueError(f"unknown reconstruction benchmark tag(s): {', '.join(unknown)}")
    return ReconstructionBenchmarkTags(
        **{
            field_name: _normalized_strings(data.get(field_name), label=f"tags.{field_name}")
            for field_name in _SLICE_FIELDS.values()
        }
    )


def _positional_hints(
    evidence: Sequence[Path],
    hints: ProviderHints | None,
) -> tuple[str | None, ...]:
    """Canonicalize all accepted hint forms to one value per evidence file."""

    if hints is None:
        return (None,) * len(evidence)
    if isinstance(hints, Mapping):
        values = {str(key): value for key, value in hints.items()}
        positional: list[str | None] = []
        for path in evidence:
            provider = next(
                (
                    values[key]
                    for key in (str(path), str(path.absolute()), path.name)
                    if key in values
                ),
                None,
            )
            positional.append(provider)
        hints_sequence: Sequence[str | None] = positional
    elif isinstance(hints, str):
        hints_sequence = (hints,)
    else:
        hints_sequence = tuple(hints)
    if len(hints_sequence) != len(evidence):
        raise ValueError(
            f"expected {len(evidence)} positional provider hint(s), received {len(hints_sequence)}"
        )
    normalized: list[str | None] = []
    for value in hints_sequence:
        if value is None:
            normalized.append(None)
        elif not isinstance(value, str) or not value.strip():
            raise ValueError("evidence provider hints must be non-empty strings or null")
        else:
            normalized.append(value.strip())
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class ReconstructionBenchmarkCase:
    """One complete Markdown + layout + positioned-evidence reconstruction case."""

    case_id: str
    original_layout: Path
    reviewed_markdown: Path
    evidence: tuple[Path, ...]
    evidence_provider_hints: ProviderHints | None = None
    job_options: ReconstructionJobOptions = field(default_factory=ReconstructionJobOptions)
    tags: ReconstructionBenchmarkTags = field(default_factory=ReconstructionBenchmarkTags)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        case_id = str(self.case_id).strip()
        if not case_id:
            raise ValueError("reconstruction benchmark case_id must not be empty")
        object.__setattr__(self, "case_id", case_id)
        layout = Path(self.original_layout).expanduser().resolve()
        markdown = Path(self.reviewed_markdown).expanduser().resolve()
        if markdown.suffix.casefold() not in {".md", ".markdown"}:
            raise ValueError(f"reviewed_markdown for {case_id!r} must be Markdown")
        evidence = tuple(Path(path).expanduser().resolve() for path in self.evidence)
        if not evidence:
            raise ValueError(f"reconstruction benchmark case {case_id!r} needs positioned evidence")
        if len(evidence) != len(set(evidence)):
            raise ValueError(f"evidence paths for {case_id!r} must be unique")
        object.__setattr__(self, "original_layout", layout)
        object.__setattr__(self, "reviewed_markdown", markdown)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(
            self,
            "evidence_provider_hints",
            _positional_hints(evidence, self.evidence_provider_hints),
        )
        object.__setattr__(self, "job_options", _coerce_job_options(self.job_options))
        object.__setattr__(self, "tags", _coerce_tags(self.tags))
        sanitized_metadata = safe_raw(dict(self.metadata))
        assert isinstance(sanitized_metadata, dict)
        object.__setattr__(self, "metadata", sanitized_metadata)

    @property
    def provider_hints(self) -> tuple[str | None, ...] | None:
        values = cast(tuple[str | None, ...], self.evidence_provider_hints)
        return values if any(value is not None for value in values) else None

    def fingerprint(
        self,
        *,
        render_backend: str,
        renderer_sha256: str | None = None,
        minimum_visual_score: float | None = None,
        allow_remote_assets: bool = False,
        save_render_artifacts: bool = False,
    ) -> str:
        """Fingerprint authorities and effective execution policy, never paths/timings."""

        payload = {
            "schema": "reconstruction-case/0.2",
            "layout_sha256": _path_digest(self.original_layout),
            "markdown_sha256": _path_digest(self.reviewed_markdown),
            "evidence": [
                {
                    "sha256": _path_digest(path),
                    "provider_hint": hint,
                }
                for path, hint in zip(
                    self.evidence,
                    tuple(self.evidence_provider_hints or ()),
                    strict=True,
                )
            ],
            "execution_policy": {
                "strict_evidence": self.job_options.strict_evidence,
                "allow_remote_assets": allow_remote_assets,
                "render_backend": render_backend,
                "minimum_visual_score": minimum_visual_score,
                "save_render_artifacts": save_render_artifacts,
                "renderer_sha256": renderer_sha256,
                "visual_metric_version": VISUAL_METRIC_VERSION,
            },
            "tags": self.tags.to_dict(),
        }
        return _stable_digest(payload)


@dataclass(frozen=True, slots=True)
class ReconstructionBenchmarkFailure:
    phase: str
    error_type: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "phase": self.phase,
            "error_type": self.error_type,
            "message": "<redacted>",
            "message_sha256": _sha256_bytes(self.message.encode("utf-8", errors="replace")),
        }


def _phase_seconds(value: Mapping[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for name, duration in value.items():
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            continue
        duration_value = float(duration)
        if math.isfinite(duration_value) and duration_value >= 0.0:
            result[str(name)] = duration_value
    return dict(sorted(result.items()))


@dataclass(frozen=True, slots=True)
class ReconstructionBenchmarkResult:
    case_id: str
    fingerprint: str
    quality_score: float | None
    quality_complete: bool
    quality_profile: str | None
    rendered_fidelity_score: float | None
    validation_gate_score: float | None
    operational_score: float
    accepted: bool
    candidate: str | None
    candidate_sha256: str | None
    render_input_sha256: str | None
    validation: dict[str, Any] | None
    reconstruction: dict[str, Any] | None
    render_backend: str
    phase_seconds: dict[str, float]
    tags: ReconstructionBenchmarkTags
    metadata: dict[str, Any]
    failure: ReconstructionBenchmarkFailure | None = None

    def __post_init__(self) -> None:
        quality = _optional_score(self.quality_score, label="quality_score")
        rendered = _optional_score(
            self.rendered_fidelity_score,
            label="rendered_fidelity_score",
        )
        validation = _optional_score(
            self.validation_gate_score,
            label="validation_gate_score",
        )
        operational = _optional_score(self.operational_score, label="operational_score")
        assert operational is not None
        object.__setattr__(self, "quality_score", quality)
        object.__setattr__(self, "rendered_fidelity_score", rendered)
        object.__setattr__(self, "validation_gate_score", validation)
        object.__setattr__(self, "operational_score", operational)
        object.__setattr__(self, "phase_seconds", _phase_seconds(self.phase_seconds))
        if self.quality_complete:
            if quality is None or rendered is None or not self.quality_profile:
                raise ValueError(
                    "complete benchmark quality requires rendered fidelity and a profile"
                )
            if quality != rendered:
                raise ValueError("quality_score must equal the rendered fidelity score")
        elif quality is not None or rendered is not None:
            raise ValueError("incomplete benchmark quality must not expose a partial score")
        if self.failure is not None and (operational != 0.0 or self.accepted):
            raise ValueError("failed reconstruction benchmark results must be rejected")
        if self.failure is not None and self.quality_complete and quality != 0.0:
            raise ValueError("a failed rendered benchmark must contribute zero fidelity")

    @property
    def score(self) -> float | None:
        return self.quality_score

    @property
    def succeeded(self) -> bool:
        return self.failure is None and self.operational_score == 1.0

    @property
    def operational_success(self) -> bool:
        return self.succeeded

    @property
    def native_conformance_score(self) -> float | None:
        """Conditional gate conformance, explicitly not end-to-end fidelity."""

        return self.validation_gate_score

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "fingerprint": self.fingerprint,
            "score": self.score,
            "quality_score": self.quality_score,
            "quality_complete": self.quality_complete,
            "quality_profile": self.quality_profile,
            "rendered_fidelity_score": self.rendered_fidelity_score,
            "validation_gate_score": self.validation_gate_score,
            "native_conformance_score": self.native_conformance_score,
            "operational_score": self.operational_score,
            "operational_success": self.operational_success,
            "accepted": self.accepted,
            "candidate": self.candidate,
            "candidate_sha256": self.candidate_sha256,
            "render_input_sha256": self.render_input_sha256,
            "validation": to_jsonable(self.validation),
            "reconstruction": to_jsonable(self.reconstruction),
            "render_backend": self.render_backend,
            "phase_seconds": dict(sorted(self.phase_seconds.items())),
            "tags": self.tags.to_dict(),
            "metadata": _privacy_safe_value(self.metadata),
            "failure": self.failure.to_dict() if self.failure else None,
        }


@dataclass(frozen=True, slots=True)
class ReconstructionSliceSummary:
    total_cases: int
    successful_cases: int
    accepted_cases: int
    mean_quality_score: float | None
    quality_complete_cases: int
    quality_profiles: tuple[str, ...]
    mean_validation_gate_score: float | None
    validation_gate_cases: int
    operational_success_rate: float | None

    @property
    def failed_cases(self) -> int:
        return self.total_cases - self.successful_cases

    @property
    def quality_score(self) -> float | None:
        return self.mean_quality_score

    @property
    def operational_score(self) -> float | None:
        return self.operational_success_rate

    @property
    def quality_coverage(self) -> float | None:
        return self.quality_complete_cases / self.total_cases if self.total_cases else None

    @property
    def validation_gate_coverage(self) -> float | None:
        return self.validation_gate_cases / self.total_cases if self.total_cases else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "successful_cases": self.successful_cases,
            "failed_cases": self.failed_cases,
            "accepted_cases": self.accepted_cases,
            "mean_quality_score": self.mean_quality_score,
            "quality_complete_cases": self.quality_complete_cases,
            "quality_coverage": self.quality_coverage,
            "quality_profiles": list(self.quality_profiles),
            "mean_validation_gate_score": self.mean_validation_gate_score,
            "validation_gate_cases": self.validation_gate_cases,
            "validation_gate_coverage": self.validation_gate_coverage,
            "operational_success_rate": self.operational_success_rate,
        }


def _slice_summary(
    results: Sequence[ReconstructionBenchmarkResult],
) -> ReconstructionSliceSummary:
    count = len(results)
    complete = [result for result in results if result.quality_complete]
    profiles = tuple(
        sorted({result.quality_profile for result in complete if result.quality_profile})
    )
    comparable = len(complete) == count and len(profiles) == 1
    validation_scores = [
        result.validation_gate_score
        for result in results
        if result.validation_gate_score is not None
    ]
    return ReconstructionSliceSummary(
        total_cases=count,
        successful_cases=sum(result.succeeded for result in results),
        accepted_cases=sum(result.accepted for result in results),
        mean_quality_score=(
            sum(cast(float, result.quality_score) for result in complete) / count
            if count and comparable
            else None
        ),
        quality_complete_cases=len(complete),
        quality_profiles=profiles,
        mean_validation_gate_score=(
            sum(validation_scores) / len(validation_scores) if validation_scores else None
        ),
        validation_gate_cases=len(validation_scores),
        operational_success_rate=(
            sum(result.operational_score for result in results) / count if count else None
        ),
    )


@dataclass(frozen=True, slots=True)
class ReconstructionBenchmarkReport:
    seed: int
    results: tuple[ReconstructionBenchmarkResult, ...]
    configuration: dict[str, Any] = field(default_factory=dict)
    run_directory: str | None = None
    schema_version: str = _REPORT_SCHEMA_VERSION

    @property
    def successful_cases(self) -> int:
        return sum(result.succeeded for result in self.results)

    @property
    def failed_cases(self) -> int:
        return len(self.results) - self.successful_cases

    @property
    def accepted_cases(self) -> int:
        return sum(result.accepted for result in self.results)

    @property
    def mean_quality_score(self) -> float | None:
        return _slice_summary(self.results).mean_quality_score

    @property
    def quality_complete_cases(self) -> int:
        return sum(result.quality_complete for result in self.results)

    @property
    def quality_coverage(self) -> float | None:
        count = len(self.results)
        return self.quality_complete_cases / count if count else None

    @property
    def quality_profiles(self) -> dict[str, ReconstructionSliceSummary]:
        groups: dict[str, list[ReconstructionBenchmarkResult]] = {}
        for result in self.results:
            if result.quality_profile:
                groups.setdefault(result.quality_profile, []).append(result)
        return {
            profile: _slice_summary(groups[profile]) for profile in sorted(groups, key=str.casefold)
        }

    @property
    def mean_validation_gate_score(self) -> float | None:
        return _slice_summary(self.results).mean_validation_gate_score

    @property
    def validation_gate_coverage(self) -> float | None:
        return _slice_summary(self.results).validation_gate_coverage

    @property
    def operational_success_rate(self) -> float | None:
        count = len(self.results)
        return sum(result.operational_score for result in self.results) / count if count else None

    @property
    def quality_score(self) -> float | None:
        return self.mean_quality_score

    @property
    def mean_score(self) -> float | None:
        return self.mean_quality_score

    @property
    def operational_score(self) -> float | None:
        return self.operational_success_rate

    @property
    def slice_means(self) -> dict[str, dict[str, ReconstructionSliceSummary]]:
        slices: dict[str, dict[str, ReconstructionSliceSummary]] = {}
        for dimension, field_name in _SLICE_FIELDS.items():
            members: dict[str, list[ReconstructionBenchmarkResult]] = {}
            for result in self.results:
                for value in getattr(result.tags, field_name):
                    members.setdefault(value, []).append(result)
            slices[dimension] = {
                value: _slice_summary(members[value]) for value in sorted(members, key=str.casefold)
            }
        return slices

    @property
    def phase_seconds(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for result in self.results:
            for phase, duration in result.phase_seconds.items():
                totals[phase] = totals.get(phase, 0.0) + duration
        return dict(sorted(totals.items()))

    @property
    def phase_statistics(self) -> dict[str, dict[str, float | int]]:
        observations: dict[str, list[float]] = {}
        for result in self.results:
            for phase, duration in result.phase_seconds.items():
                observations.setdefault(phase, []).append(duration)
        summaries: dict[str, dict[str, float | int]] = {}
        for phase, values in observations.items():
            ordered = sorted(values)
            count = len(ordered)
            p50 = ordered[min(count - 1, math.ceil(0.50 * count) - 1)]
            p95 = ordered[min(count - 1, math.ceil(0.95 * count) - 1)]
            summaries[phase] = {
                "count": count,
                "total": sum(ordered),
                "mean": sum(ordered) / count,
                "p50": p50,
                "p95": p95,
                "maximum": ordered[-1],
            }
        return dict(sorted(summaries.items()))

    @property
    def run_fingerprint(self) -> str:
        payload = {
            "schema": f"reconstruction-report/{self.schema_version}",
            "configuration": self.configuration,
            "results": [
                {
                    "case_id": result.case_id,
                    "fingerprint": result.fingerprint,
                    "candidate_sha256": result.candidate_sha256,
                    "render_input_sha256": result.render_input_sha256,
                    "quality_score": result.quality_score,
                    "quality_complete": result.quality_complete,
                    "quality_profile": result.quality_profile,
                    "validation_gate_score": result.validation_gate_score,
                    "operational_score": result.operational_score,
                    "accepted": result.accepted,
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
            "seed": self.seed,
            "configuration": _privacy_safe_value(self.configuration),
            "run_directory": self.run_directory,
            "summary": {
                "total_cases": len(self.results),
                "successful_cases": self.successful_cases,
                "failed_cases": self.failed_cases,
                "accepted_cases": self.accepted_cases,
                "mean_quality_score": self.mean_quality_score,
                "quality_complete_cases": self.quality_complete_cases,
                "quality_coverage": self.quality_coverage,
                "quality_profiles": {
                    profile: summary.to_dict() for profile, summary in self.quality_profiles.items()
                },
                "mean_validation_gate_score": self.mean_validation_gate_score,
                "validation_gate_coverage": self.validation_gate_coverage,
                "operational_success_rate": self.operational_success_rate,
                "phase_seconds": self.phase_seconds,
                "phase_statistics": self.phase_statistics,
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


def _safe_name(case_id: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", case_id).strip("-.")
    return name[:80] or "case"


def _mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _candidate_integrity(
    *,
    candidate_sha256: str,
    reconstruction: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> str | None:
    """Verify every independently reported artifact/render-input identity."""

    output = _mapping_value(reconstruction.get("output"))
    reconstruction_sha = output.get("sha256")
    validation_sha = validation.get("candidate_sha256")
    for label, observed in (
        ("reconstruction output", reconstruction_sha),
        ("validation candidate", validation_sha),
    ):
        if observed != candidate_sha256:
            raise RuntimeError(f"{label} SHA-256 does not match the freshly generated candidate")

    reconstruction_render_input = reconstruction.get("render_input_sha256")
    validation_metrics = _mapping_value(validation.get("metrics"))
    artifact_render_input = validation_metrics.get("render_input_artifact_sha256")
    prepared_render_plan = validation_metrics.get("render_plan_sha256")
    if reconstruction_render_input is None or artifact_render_input is None:
        raise RuntimeError("candidate is missing an independently verified render-input identity")
    if reconstruction_render_input != artifact_render_input:
        raise RuntimeError(
            "embedded and independently verified render-input SHA-256 identities disagree"
        )
    if prepared_render_plan is not None and prepared_render_plan != reconstruction_render_input:
        raise RuntimeError(
            "prepared render plan and embedded render-input SHA-256 identities disagree"
        )
    render_input = reconstruction_render_input
    if not isinstance(render_input, str) or not re.fullmatch(r"[0-9a-f]{64}", render_input):
        raise RuntimeError("render-input SHA-256 identity is invalid")
    return render_input


def _rendered_quality(
    validation: Mapping[str, Any],
) -> tuple[float | None, str | None]:
    """Return comparable rendered fidelity only when its full profile is present."""

    metrics = _mapping_value(validation.get("metrics"))
    visual = _mapping_value(metrics.get("rendered_visual"))
    provenance = _mapping_value(metrics.get("render_backend"))
    score = _optional_score(visual.get("score"), label="rendered visual score")
    metric_version = visual.get("metric_version")
    used_backend = provenance.get("used_backend")
    status = provenance.get("status")
    if score is None:
        return None, None
    if status != "rendered" or not isinstance(used_backend, str) or not used_backend.strip():
        return None, None
    if not isinstance(metric_version, str) or not metric_version.strip():
        return None, None
    profile = (
        f"rendered_visual|backend={used_backend.strip().casefold()}|metric={metric_version.strip()}"
    )
    return score, profile


def _failed_quality_profile(render_backend: str) -> str | None:
    if render_backend == "native":
        return None
    backend = render_backend if render_backend == "libreoffice" else f"unresolved({render_backend})"
    return f"rendered_visual|backend={backend}|metric={VISUAL_METRIC_VERSION}"


class ReconstructionBenchmarkRunner:
    """Generate and validate a fresh DOCX for every three-source case."""

    def __init__(
        self,
        *,
        output_dir: str | Path,
        seed: int = 0,
        render_backend: str = "native",
        renderer_path: str | Path | None = None,
        minimum_visual_score: float | None = None,
        save_render_artifacts: bool = False,
        allow_remote_assets: bool = False,
        configuration: Mapping[str, Any] | None = None,
        job_runner: HybridJobCallable | None = None,
        fail_fast: bool = False,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.seed = int(seed)
        self.render_backend = _normalized_backend(render_backend)
        self.renderer_path = (
            Path(renderer_path).expanduser().resolve() if renderer_path is not None else None
        )
        if self.renderer_path is not None and self.render_backend == "native":
            raise ValueError("renderer_path requires auto or libreoffice render_backend")
        if self.renderer_path is not None and not self.renderer_path.is_file():
            raise FileNotFoundError(self.renderer_path)
        self.minimum_visual_score = _optional_score(
            minimum_visual_score,
            label="minimum_visual_score",
        )
        if self.minimum_visual_score is not None and self.render_backend == "native":
            raise ValueError("minimum_visual_score requires auto or libreoffice render_backend")
        self.save_render_artifacts = bool(save_render_artifacts)
        self.allow_remote_assets = bool(allow_remote_assets)
        sanitized_configuration = safe_raw(dict(configuration or {}))
        assert isinstance(sanitized_configuration, dict)
        self.configuration = sanitized_configuration
        self.job_runner = job_runner or run_hybrid_job
        self.fail_fast = fail_fast

    def _execution(
        self,
        case: ReconstructionBenchmarkCase,
    ) -> tuple[str, float | None, bool, bool]:
        options = case.job_options
        requested_backend = options.render_backend
        if self.render_backend == "native" and requested_backend not in {None, "native"}:
            raise ValueError(
                f"case {case.case_id!r} cannot enable rendered QA when runtime QA is native"
            )
        # Runtime selection remains the final authority. A manifest may
        # de-escalate to native, but cannot select a stronger/different
        # executable backend than the caller explicitly allowed.
        backend = "native" if requested_backend == "native" else self.render_backend
        minimum = (
            options.minimum_visual_score
            if options.minimum_visual_score is not None
            else self.minimum_visual_score
        )
        if minimum is not None and backend == "native":
            raise ValueError(f"case {case.case_id!r} minimum_visual_score requires rendered QA")
        save_artifacts = options.save_render_artifacts or self.save_render_artifacts
        allow_remote_assets = self.allow_remote_assets and options.allow_remote_assets
        return backend, minimum, save_artifacts, allow_remote_assets

    def run(
        self,
        cases: Iterable[ReconstructionBenchmarkCase],
    ) -> ReconstructionBenchmarkReport:
        ordered = sorted(cases, key=lambda case: case.case_id)
        identifiers = [case.case_id for case in ordered]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("reconstruction benchmark case IDs must be unique")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        run_directory = Path(tempfile.mkdtemp(prefix="run-", dir=self.output_dir))
        renderer_sha256 = (
            _path_digest(self.renderer_path) if self.renderer_path is not None else None
        )
        results: list[ReconstructionBenchmarkResult] = []
        for index, case in enumerate(ordered, start=1):
            started = time.perf_counter()
            case_directory = run_directory / f"{index:04d}-{_safe_name(case.case_id)}"
            case_directory.mkdir(parents=False, exist_ok=False)
            candidate = case_directory / "candidate.docx"
            qa_report = case_directory / "qa.json"
            phase = "configuration"
            quality_score: float | None = None
            rendered_fidelity_score: float | None = None
            validation_gate_score: float | None = None
            quality_complete = False
            quality_profile: str | None = None
            operational_score = 0.0
            accepted = False
            candidate_sha256: str | None = None
            render_input_sha256: str | None = None
            validation: dict[str, Any] | None = None
            reconstruction: dict[str, Any] | None = None
            failure: ReconstructionBenchmarkFailure | None = None
            timings: dict[str, float] = {}
            backend = self.render_backend
            minimum: float | None = self.minimum_visual_score
            save_artifacts = self.save_render_artifacts
            allow_remote_assets = False
            try:
                backend, minimum, save_artifacts, allow_remote_assets = self._execution(case)
                fingerprint = case.fingerprint(
                    render_backend=backend,
                    renderer_sha256=renderer_sha256,
                    minimum_visual_score=minimum,
                    allow_remote_assets=allow_remote_assets,
                    save_render_artifacts=save_artifacts,
                )
                phase = "reconstruction"
                job = self.job_runner(
                    case.reviewed_markdown,
                    case.original_layout,
                    evidence=case.evidence,
                    evidence_provider_hints=case.provider_hints,
                    strict_evidence=case.job_options.strict_evidence,
                    output=candidate,
                    allow_remote_assets=allow_remote_assets,
                    render_backend=backend,
                    renderer_path=self.renderer_path,
                    minimum_visual_score=minimum,
                    render_output_dir=(case_directory / "rendered" if save_artifacts else None),
                    qa_report=qa_report,
                )
                timings.update(_phase_seconds(job.phase_seconds))
                if not candidate.is_file():
                    raise RuntimeError("run_hybrid_job did not create the requested candidate")
                produced = Path(job.reconstruction.output.path).expanduser().resolve()
                if produced != candidate.resolve():
                    raise RuntimeError(
                        "run_hybrid_job returned a candidate outside its fresh case output"
                    )
                candidate_sha256 = _sha256_file(candidate)
                raw_validation = job.validation.model_dump(mode="json")
                raw_reconstruction = job.reconstruction.model_dump(mode="json")
                phase = "integrity"
                render_input_sha256 = _candidate_integrity(
                    candidate_sha256=candidate_sha256,
                    reconstruction=raw_reconstruction,
                    validation=raw_validation,
                )
                phase = "evaluation"
                validation_gate_score = _optional_score(
                    job.validation.score,
                    label="hybrid validation score",
                )
                rendered_fidelity_score, quality_profile = _rendered_quality(raw_validation)
                if rendered_fidelity_score is not None and quality_profile is not None:
                    quality_score = rendered_fidelity_score
                    quality_complete = True
                validation = _privacy_safe_validation(raw_validation)
                reconstruction = _privacy_safe_reconstruction(raw_reconstruction)
                phase = "complete"
                operational_score = 1.0
                accepted = bool(job.validation.passed)
            except Exception as exc:
                if self.fail_fast:
                    raise
                fingerprint = case.fingerprint(
                    render_backend=backend,
                    renderer_sha256=renderer_sha256,
                    minimum_visual_score=minimum,
                    allow_remote_assets=allow_remote_assets,
                    save_render_artifacts=save_artifacts,
                )
                if candidate.is_file():
                    candidate_sha256 = _sha256_file(candidate)
                quality_profile = _failed_quality_profile(backend)
                if quality_profile is not None:
                    quality_score = 0.0
                    rendered_fidelity_score = 0.0
                    quality_complete = True
                else:
                    quality_score = None
                    rendered_fidelity_score = None
                    quality_complete = False
                validation_gate_score = None
                validation = None
                reconstruction = None
                failure = ReconstructionBenchmarkFailure(
                    phase=phase,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            timings["benchmark.total"] = time.perf_counter() - started
            results.append(
                ReconstructionBenchmarkResult(
                    case_id=case.case_id,
                    fingerprint=fingerprint,
                    quality_score=quality_score,
                    quality_complete=quality_complete,
                    quality_profile=quality_profile,
                    rendered_fidelity_score=rendered_fidelity_score,
                    validation_gate_score=validation_gate_score,
                    operational_score=operational_score,
                    accepted=accepted,
                    candidate=(
                        f"{case_directory.name}/candidate.docx" if candidate.is_file() else None
                    ),
                    candidate_sha256=candidate_sha256,
                    render_input_sha256=render_input_sha256,
                    validation=validation,
                    reconstruction=reconstruction,
                    render_backend=backend,
                    phase_seconds=_phase_seconds(timings),
                    tags=case.tags,
                    metadata=dict(case.metadata),
                    failure=failure,
                )
            )
        return ReconstructionBenchmarkReport(
            seed=self.seed,
            results=tuple(results),
            configuration=self.configuration,
            run_directory=run_directory.name,
        )


def _merge_options(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    return {**dict(base), **dict(override)}


def _manifest_path(dataset: str | Path) -> Path:
    path = Path(dataset).expanduser().resolve()
    if not path.is_dir():
        return path
    preferred = path / "reconstruction-benchmark.json"
    return preferred if preferred.is_file() else path / "manifest.json"


def _relative_path(value: Any, *, base: Path, label: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError(f"{label} must be a non-empty path")
    path = Path(value)
    return path if path.is_absolute() else base / path


def _evidence_entries(
    value: Any,
    *,
    base: Path,
    case_id: str,
) -> tuple[tuple[Path, ...], tuple[str | None, ...]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"reconstruction benchmark case {case_id!r} evidence must be an array")
    paths: list[Path] = []
    hints: list[str | None] = []
    for index, raw in enumerate(value):
        provider: str | None = None
        if isinstance(raw, Mapping):
            entry = _mapping(raw, label=f"evidence[{index}]")
            unknown = sorted(set(entry) - {"path", "provider"})
            if unknown:
                raise ValueError(f"unknown evidence field(s) for {case_id!r}: {', '.join(unknown)}")
            if "path" not in entry:
                raise ValueError(f"evidence[{index}] for {case_id!r} needs path")
            raw_path = entry["path"]
            raw_provider = entry.get("provider")
            if raw_provider is not None:
                if not isinstance(raw_provider, str) or not raw_provider.strip():
                    raise ValueError(f"evidence[{index}].provider must be a non-empty string")
                provider = raw_provider.strip()
        else:
            raw_path = raw
        paths.append(_relative_path(raw_path, base=base, label=f"evidence[{index}].path"))
        hints.append(provider)
    return tuple(paths), tuple(hints)


def _manifest_hints(
    value: Any,
    *,
    evidence: Sequence[Path],
    base: Path,
) -> ProviderHints | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        positions = {path.resolve(): index for index, path in enumerate(evidence)}
        hints_by_index: list[str | None] = [None] * len(evidence)
        seen: set[Path] = set()
        for raw_path, provider in value.items():
            if not isinstance(provider, str) or not provider.strip():
                raise ValueError("evidence_provider_hints values must be non-empty strings")
            path = Path(str(raw_path))
            resolved = (path if path.is_absolute() else base / path).resolve()
            if resolved not in positions:
                raise ValueError(
                    "evidence_provider_hints contains a path that is not an evidence input"
                )
            if resolved in seen:
                raise ValueError("duplicate evidence provider hint path")
            seen.add(resolved)
            hints_by_index[positions[resolved]] = provider.strip()
        return tuple(hints_by_index)
    if isinstance(value, str):
        return value
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        raise ValueError("evidence_provider_hints must be a string, array, or object")
    result: list[str | None] = []
    for provider in value:
        if provider is None:
            result.append(None)
        elif not isinstance(provider, str) or not provider.strip():
            raise ValueError("evidence_provider_hints entries must be strings or null")
        else:
            result.append(provider.strip())
    if len(result) != len(evidence):
        raise ValueError(
            f"expected {len(evidence)} positional provider hint(s), received {len(result)}"
        )
    return tuple(result)


def _preflight_case(case: ReconstructionBenchmarkCase) -> None:
    """Reject incomplete authorities and unresolved/ambiguous saved evidence."""

    for label, path in (
        ("original_layout", case.original_layout),
        ("reviewed_markdown", case.reviewed_markdown),
        *(("evidence", path) for path in case.evidence),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} input is not a file: {path}")
    bundle = load_sidecar_evidence(
        case.evidence,
        provider_hints=case.provider_hints,
        strict=True,
    )
    for item in bundle.items:
        if item.detection.ambiguous:
            candidates = ", ".join(
                candidate.provider for candidate in item.detection.candidates[:2]
            )
            raise ValueError(
                f"ambiguous evidence provider detection for {item.path.name}: {candidates}; "
                "add an explicit provider hint"
            )


def load_reconstruction_benchmark_manifest(
    dataset: str | Path,
) -> tuple[list[ReconstructionBenchmarkCase], dict[str, Any]]:
    """Load a three-source benchmark manifest without running reconstruction."""

    manifest_path = _manifest_path(dataset)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"reconstruction benchmark manifest not found: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("reconstruction benchmark manifest must be an object")
    manifest = payload
    unknown_root = sorted(set(manifest) - _ROOT_FIELDS)
    if unknown_root:
        raise ValueError(
            f"unknown reconstruction benchmark manifest field(s): {', '.join(unknown_root)}"
        )
    schema_version = manifest.get("schema_version")
    if schema_version != _MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            "unsupported reconstruction benchmark schema_version; "
            f"expected {_MANIFEST_SCHEMA_VERSION!r}"
        )
    if "job_options" in manifest and "reconstruction" in manifest:
        raise ValueError("manifest cannot specify both job_options and reconstruction")
    entries: Any = manifest.get("cases")
    if not isinstance(entries, list):
        raise ValueError("reconstruction benchmark manifest must contain a `cases` array")
    if not entries:
        raise ValueError("reconstruction benchmark manifest must contain at least one case")
    default_options = _mapping(
        manifest.get("job_options", manifest.get("reconstruction")),
        label="job_options",
    )
    base = manifest_path.parent
    cases: list[ReconstructionBenchmarkCase] = []
    for index, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, Mapping):
            raise ValueError(f"reconstruction benchmark case {index} must be an object")
        entry = dict(raw_entry)
        unknown_case = sorted(set(entry) - _CASE_FIELDS)
        if unknown_case:
            raise ValueError(
                f"unknown reconstruction benchmark case field(s): {', '.join(unknown_case)}"
            )
        if "id" in entry and "case_id" in entry:
            raise ValueError("benchmark case cannot specify both id and case_id")
        case_id = str(entry.get("id", entry.get("case_id", f"case-{index + 1:04d}")))
        if "original_layout" not in entry or "reviewed_markdown" not in entry:
            raise ValueError(
                f"reconstruction benchmark case {case_id!r} needs "
                "original_layout and reviewed_markdown"
            )
        if "evidence" not in entry:
            raise ValueError(f"reconstruction benchmark case {case_id!r} needs positioned evidence")
        evidence, embedded_hints = _evidence_entries(
            entry["evidence"],
            base=base,
            case_id=case_id,
        )
        explicit_hints = entry.get("evidence_provider_hints")
        if explicit_hints is not None and any(hint is not None for hint in embedded_hints):
            raise ValueError(
                f"case {case_id!r} cannot combine embedded providers with evidence_provider_hints"
            )
        hints = (
            _manifest_hints(explicit_hints, evidence=evidence, base=base)
            if explicit_hints is not None
            else embedded_hints
        )
        options = _merge_options(
            default_options,
            _mapping(entry.get("job_options"), label="job_options"),
        )
        case = ReconstructionBenchmarkCase(
            case_id=case_id,
            original_layout=_relative_path(
                entry["original_layout"],
                base=base,
                label="original_layout",
            ),
            reviewed_markdown=_relative_path(
                entry["reviewed_markdown"],
                base=base,
                label="reviewed_markdown",
            ),
            evidence=evidence,
            evidence_provider_hints=hints,
            job_options=_coerce_job_options(options),
            tags=_coerce_tags(entry.get("tags") or {}),
            metadata=_mapping(entry.get("metadata"), label="metadata"),
        )
        _preflight_case(case)
        cases.append(case)
    identifiers = [case.case_id for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("reconstruction benchmark case IDs must be unique")
    return cases, manifest


def run_reconstruction_benchmark(
    dataset: str | Path | Iterable[ReconstructionBenchmarkCase],
    *,
    output_dir: str | Path | None = None,
    output_path: str | Path | None = None,
    seed: int | None = None,
    render_backend: str = "native",
    renderer_path: str | Path | None = None,
    minimum_visual_score: float | None = None,
    save_render_artifacts: bool = False,
    allow_remote_assets: bool = False,
    job_runner: HybridJobCallable | None = None,
    fail_fast: bool = False,
) -> ReconstructionBenchmarkReport:
    """Load/run three-source cases and optionally write one JSON report."""

    manifest: dict[str, Any] = {}
    if isinstance(dataset, (str, Path)):
        cases, manifest = load_reconstruction_benchmark_manifest(dataset)
        manifest_path = _manifest_path(dataset)
        default_output_dir = manifest_path.parent / "reconstruction-benchmark-output"
    else:
        cases = list(dataset)
        default_output_dir = Path.cwd() / "reconstruction-benchmark-output"
    configured_seed = manifest.get("seed", 0) if seed is None else seed
    if isinstance(configured_seed, bool) or not isinstance(configured_seed, int):
        raise ValueError("seed must be an integer")
    configuration = _mapping(manifest.get("configuration"), label="configuration")
    runner = ReconstructionBenchmarkRunner(
        output_dir=output_dir or default_output_dir,
        seed=configured_seed,
        render_backend=render_backend,
        renderer_path=renderer_path,
        minimum_visual_score=minimum_visual_score,
        save_render_artifacts=save_render_artifacts,
        allow_remote_assets=allow_remote_assets,
        configuration=configuration,
        job_runner=job_runner,
        fail_fast=fail_fast,
    )
    report = runner.run(cases)
    if output_path is not None:
        report.write(output_path)
    return report


__all__ = [
    "HybridJobCallable",
    "ReconstructionBenchmarkCase",
    "ReconstructionBenchmarkFailure",
    "ReconstructionBenchmarkReport",
    "ReconstructionBenchmarkResult",
    "ReconstructionBenchmarkRunner",
    "ReconstructionBenchmarkTags",
    "ReconstructionJobOptions",
    "ReconstructionSliceSummary",
    "load_reconstruction_benchmark_manifest",
    "run_reconstruction_benchmark",
]
