"""Public value objects for source-only benchmark configuration and results."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from ._common import executable_identity, stable_digest

_SYSTEM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_FORBIDDEN_CANDIDATE_PLACEHOLDERS = (
    "{ground_truth}",
    "{dataset_json}",
    "{annotations}",
)


class SourceRunStatus(StrEnum):
    """Mutually exclusive outcome of one isolated candidate process."""

    SUCCESS = "success"
    TIMEOUT = "timeout"
    OOM = "oom"
    CRASH = "crash"
    NONZERO_EXIT = "nonzero_exit"
    MISSING_OUTPUT = "missing_output"
    EMPTY_OUTPUT = "empty_output"
    INVALID_OUTPUT = "invalid_output"


@dataclass(frozen=True, slots=True)
class SourceBenchmarkSystem:
    """Pinned external candidate command.

    Candidate commands may use ``{input}``, ``{output}``, ``{case_id}``,
    ``{input_name}``, ``{source_name}``, and ``{work_dir}``. They never receive
    annotations.
    """

    name: str
    version: str
    revision: str
    command: tuple[str, ...]
    cwd: Path | None = None
    timeout_seconds: float = 900.0
    environment: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _SYSTEM_NAME.fullmatch(self.name):
            raise ValueError(
                "system.name must start with an alphanumeric character and contain only "
                "letters, digits, '.', '_' or '-'"
            )
        if not self.version.strip() or not self.revision.strip():
            raise ValueError("system.version and system.revision must be pinned")
        if not self.command:
            raise ValueError(f"system {self.name!r} command must not be empty")
        command_text = "\n".join(self.command)
        if "{input}" not in command_text or "{output}" not in command_text:
            raise ValueError(f"system {self.name!r} command must contain {{input}} and {{output}}")
        if any(token in command_text for token in _FORBIDDEN_CANDIDATE_PLACEHOLDERS):
            raise ValueError(f"system {self.name!r} candidate command may not receive ground truth")
        if self.timeout_seconds <= 0:
            raise ValueError("system.timeout_seconds must be greater than zero")
        if self.cwd is not None and not self.cwd.is_dir():
            raise ValueError(f"system.cwd is not a directory: {self.cwd}")

    @property
    def identity(self) -> dict[str, Any]:
        environment_digest = stable_digest(self.environment) if self.environment else None
        executable, executable_sha256 = executable_identity(self.command[0])
        return {
            "name": self.name,
            "version": self.version,
            "revision": self.revision,
            "command": list(self.command),
            "timeout_seconds": self.timeout_seconds,
            "environment_names": sorted(self.environment),
            "environment_sha256": environment_digest,
            "executable": executable,
            "executable_sha256": executable_sha256,
        }


@dataclass(frozen=True, slots=True)
class OfficialEvaluator:
    """Pinned external official evaluator command."""

    name: str
    version: str
    revision: str
    command: tuple[str, ...]
    cwd: Path | None = None
    timeout_seconds: float = 3600.0
    environment: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.version.strip() or not self.revision.strip():
            raise ValueError("official_evaluator name, version, and revision must be pinned")
        if not self.command:
            raise ValueError("official_evaluator.command must not be empty")
        joined = "\n".join(self.command)
        for placeholder in ("{ground_truth}", "{predictions}", "{result_dir}"):
            if placeholder not in joined:
                raise ValueError(f"official_evaluator.command must contain {placeholder}")
        if self.timeout_seconds <= 0:
            raise ValueError("official_evaluator.timeout_seconds must be greater than zero")
        if self.cwd is not None and not self.cwd.is_dir():
            raise ValueError(f"official_evaluator.cwd is not a directory: {self.cwd}")

    @property
    def identity(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "revision": self.revision,
            "command": list(self.command),
            "timeout_seconds": self.timeout_seconds,
            "environment_names": sorted(self.environment),
            "environment_sha256": (stable_digest(self.environment) if self.environment else None),
        }


@dataclass(frozen=True, slots=True)
class SourceBenchmarkCase:
    """One source page selected from an OmniDocBench-style annotation JSON."""

    index: int
    case_id: str
    input_name: str
    source_name: str
    source: Path
    source_sha256: str
    source_bytes: int
    prediction_name: str
    subset: str | None = None

    def __post_init__(self) -> None:
        if not self.source.is_file():
            raise ValueError(f"source file does not exist: {self.source}")
        if Path(self.prediction_name).name != self.prediction_name:
            raise ValueError("prediction_name must be a filename")
        if not self.prediction_name.casefold().endswith(".md"):
            raise ValueError("prediction_name must end in .md")


@dataclass(frozen=True, slots=True)
class SourceBenchmarkManifest:
    """Validated source benchmark configuration."""

    path: Path
    dataset_json: Path
    images_dir: Path
    source_suffix: str | None
    dataset_revision: str
    expected_dataset_sha256: str | None
    output_dir: Path
    subset: str
    max_output_bytes: int
    systems: tuple[SourceBenchmarkSystem, ...]
    official_evaluator: OfficialEvaluator | None = None


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    timed_out: bool
    exit_code: int | None
    duration_seconds: float
    stdout_sha256: str
    stdout_bytes: int
    stderr_sha256: str
    stderr_bytes: int
    stderr_tail: str
    peak_rss_bytes: int | None


@dataclass(frozen=True, slots=True)
class SourceRunRecord:
    """Public, path-redacted result for one system/case pair."""

    system: str
    case_id: str
    case_index: int
    input_name: str
    source_name: str
    source_sha256: str
    source_bytes: int
    prediction: str
    fingerprint: str
    status: SourceRunStatus
    duration_seconds: float
    exit_code: int | None
    output_sha256: str
    output_bytes: int
    stdout_sha256: str
    stdout_bytes: int
    stderr_sha256: str
    stderr_bytes: int
    peak_rss_bytes: int | None
    reused: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status is SourceRunStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "case_id": self.case_id,
            "case_index": self.case_index,
            "input_name": self.input_name,
            "source_name": self.source_name,
            "source_sha256": self.source_sha256,
            "source_bytes": self.source_bytes,
            "prediction": self.prediction,
            "fingerprint": self.fingerprint,
            "status": self.status.value,
            "duration_seconds": self.duration_seconds,
            "exit_code": self.exit_code,
            "output_sha256": self.output_sha256,
            "output_bytes": self.output_bytes,
            "stdout_sha256": self.stdout_sha256,
            "stdout_bytes": self.stdout_bytes,
            "stderr_sha256": self.stderr_sha256,
            "stderr_bytes": self.stderr_bytes,
            "peak_rss_bytes": self.peak_rss_bytes,
            "reused": self.reused,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, reused: bool) -> SourceRunRecord:
        return cls(
            system=str(value["system"]),
            case_id=str(value["case_id"]),
            case_index=int(value["case_index"]),
            input_name=str(value["input_name"]),
            source_name=str(value.get("source_name", value["input_name"])),
            source_sha256=str(value["source_sha256"]),
            source_bytes=int(value["source_bytes"]),
            prediction=str(value["prediction"]),
            fingerprint=str(value["fingerprint"]),
            status=SourceRunStatus(str(value["status"])),
            duration_seconds=float(value["duration_seconds"]),
            exit_code=(None if value.get("exit_code") is None else int(value["exit_code"])),
            output_sha256=str(value["output_sha256"]),
            output_bytes=int(value["output_bytes"]),
            stdout_sha256=str(value["stdout_sha256"]),
            stdout_bytes=int(value["stdout_bytes"]),
            stderr_sha256=str(value["stderr_sha256"]),
            stderr_bytes=int(value["stderr_bytes"]),
            peak_rss_bytes=(
                None if value.get("peak_rss_bytes") is None else int(value["peak_rss_bytes"])
            ),
            reused=reused,
        )


@dataclass(frozen=True, slots=True)
class EvaluatorRecord:
    system: str
    status: SourceRunStatus
    duration_seconds: float
    exit_code: int | None
    result_dir: str
    stdout_sha256: str
    stdout_bytes: int
    stderr_sha256: str
    stderr_bytes: int
    peak_rss_bytes: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "status": self.status.value,
            "duration_seconds": self.duration_seconds,
            "exit_code": self.exit_code,
            "result_dir": self.result_dir,
            "stdout_sha256": self.stdout_sha256,
            "stdout_bytes": self.stdout_bytes,
            "stderr_sha256": self.stderr_sha256,
            "stderr_bytes": self.stderr_bytes,
            "peak_rss_bytes": self.peak_rss_bytes,
        }


@dataclass(frozen=True, slots=True)
class SourceSystemSummary:
    system: str
    total_cases: int
    successful_cases: int
    failed_cases: int
    status_counts: dict[str, int]
    operational_success_rate: float
    reused_cases: int
    total_seconds: float
    mean_seconds_all: float | None
    mean_seconds_successful: float | None
    p50_seconds_successful: float | None
    p95_seconds_successful: float | None
    peak_rss_bytes_max: int | None
    peak_rss_bytes_mean: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "total_cases": self.total_cases,
            "successful_cases": self.successful_cases,
            "failed_cases": self.failed_cases,
            "status_counts": self.status_counts,
            "operational_success_rate": self.operational_success_rate,
            "reused_cases": self.reused_cases,
            "total_seconds": self.total_seconds,
            "mean_seconds_all": self.mean_seconds_all,
            "mean_seconds_successful": self.mean_seconds_successful,
            "p50_seconds_successful": self.p50_seconds_successful,
            "p95_seconds_successful": self.p95_seconds_successful,
            "peak_rss_bytes_max": self.peak_rss_bytes_max,
            "peak_rss_bytes_mean": self.peak_rss_bytes_mean,
        }


@dataclass(frozen=True, slots=True)
class SourceBenchmarkReport:
    schema_version: str
    run_fingerprint: str
    dataset_revision: str
    dataset_sha256: str
    dataset_bytes: int
    selection_sha256: str
    subset: str
    shard_index: int
    shard_count: int
    selected_cases: int
    systems: tuple[dict[str, Any], ...]
    official_evaluator: dict[str, Any] | None
    results: tuple[SourceRunRecord, ...]
    summaries: tuple[SourceSystemSummary, ...]
    evaluator_results: tuple[EvaluatorRecord, ...]

    @property
    def failed_cases(self) -> int:
        return sum(not result.succeeded for result in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_fingerprint": self.run_fingerprint,
            "dataset_revision": self.dataset_revision,
            "dataset_sha256": self.dataset_sha256,
            "dataset_bytes": self.dataset_bytes,
            "selection_sha256": self.selection_sha256,
            "subset": self.subset,
            "shard": {"index": self.shard_index, "count": self.shard_count},
            "selected_cases": self.selected_cases,
            "systems": list(self.systems),
            "official_evaluator": self.official_evaluator,
            "summaries": [summary.to_dict() for summary in self.summaries],
            "evaluator_results": [result.to_dict() for result in self.evaluator_results],
            "results": [result.to_dict() for result in self.results],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
