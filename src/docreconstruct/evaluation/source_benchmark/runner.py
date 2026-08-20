"""Checkpointed source benchmark orchestration and report generation."""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ._common import (
    SOURCE_BENCHMARK_SCHEMA_VERSION,
    atomic_json,
    atomic_write,
    percentile,
    read_bounded,
    relative_public_path,
    sha256_bytes,
    sha256_file,
    stable_digest,
    stable_json,
)
from .corpus import load_omnidocbench_cases
from .manifest import load_source_benchmark_manifest
from .models import (
    EvaluatorRecord,
    OfficialEvaluator,
    SourceBenchmarkCase,
    SourceBenchmarkManifest,
    SourceBenchmarkReport,
    SourceBenchmarkSystem,
    SourceRunRecord,
    SourceRunStatus,
    SourceSystemSummary,
)
from .process import expand_command, os_error_outcome, process_failure_status, run_process


def _record_key(system: str, case_id: str) -> str:
    return stable_digest({"system": system, "case_id": case_id})[:32]


class SourceBenchmarkRunner:
    """Run pinned systems on one exact corpus with durable per-case checkpoints."""

    def __init__(
        self,
        manifest: SourceBenchmarkManifest,
        *,
        output_dir: str | Path | None = None,
        subset: str | None = None,
        shard_index: int = 0,
        shard_count: int = 1,
        resume: bool = True,
        run_official_evaluator: bool = True,
        systems: Sequence[str] | None = None,
    ) -> None:
        self.manifest = manifest
        self.output_dir = (
            Path(output_dir).expanduser().resolve()
            if output_dir is not None
            else manifest.output_dir
        )
        self.subset = subset or manifest.subset
        self.shard_index = shard_index
        self.shard_count = shard_count
        if shard_count <= 0:
            raise ValueError("shard_count must be greater than zero")
        if not 0 <= shard_index < shard_count:
            raise ValueError("shard_index must satisfy 0 <= index < shard_count")
        self.resume = resume
        self.run_official_evaluator = run_official_evaluator
        selected_names = {name.casefold() for name in systems or ()}
        self.systems = tuple(
            system
            for system in manifest.systems
            if not selected_names or system.name.casefold() in selected_names
        )
        if not self.systems:
            raise ValueError("no configured systems matched the selection")
        missing = selected_names - {system.name.casefold() for system in self.systems}
        if missing:
            raise ValueError(f"unknown system selection: {', '.join(sorted(missing))}")

    @property
    def artifact_dir(self) -> Path:
        """Shard-private ledgers/reports; predictions and checkpoints remain mergeable."""

        if self.shard_count == 1:
            return self.output_dir
        label = f"{self.shard_index:05d}-of-{self.shard_count:05d}"
        return self.output_dir / "shards" / label

    def _fingerprint(self, system: SourceBenchmarkSystem, case: SourceBenchmarkCase) -> str:
        return stable_digest(
            {
                "schema_version": SOURCE_BENCHMARK_SCHEMA_VERSION,
                "system": system.identity,
                "case_id": case.case_id,
                "source_name": case.source_name,
                "source_sha256": case.source_sha256,
                "source_bytes": case.source_bytes,
                "prediction_name": case.prediction_name,
                "max_output_bytes": self.manifest.max_output_bytes,
            }
        )

    def _resume_record(
        self,
        *,
        record_path: Path,
        prediction_path: Path,
        fingerprint: str,
    ) -> SourceRunRecord | None:
        if not self.resume or not record_path.is_file():
            return None
        try:
            raw = json.loads(record_path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping) or raw.get("fingerprint") != fingerprint:
                return None
            record = SourceRunRecord.from_dict(raw, reused=True)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None
        if record.succeeded:
            if not prediction_path.is_file():
                return None
            if prediction_path.stat().st_size != record.output_bytes:
                return None
            if sha256_file(prediction_path) != record.output_sha256:
                return None
        elif not prediction_path.is_file() or prediction_path.stat().st_size != 0:
            atomic_write(prediction_path, b"")
        return record

    def _run_case(
        self,
        system: SourceBenchmarkSystem,
        case: SourceBenchmarkCase,
    ) -> SourceRunRecord:
        predictions_dir = self.output_dir / system.name / "predictions"
        prediction_path = predictions_dir / case.prediction_name
        relative_prediction = relative_public_path(prediction_path, self.output_dir)
        key = _record_key(system.name, case.case_id)
        record_path = self.output_dir / "records" / system.name / f"{key}.json"
        fingerprint = self._fingerprint(system, case)
        resumed = self._resume_record(
            record_path=record_path,
            prediction_path=prediction_path,
            fingerprint=fingerprint,
        )
        if resumed is not None:
            return resumed
        logs = self.output_dir / "_private_logs" / system.name
        stdout_path = logs / f"{key}.stdout.log"
        stderr_path = logs / f"{key}.stderr.log"
        work_root = self.output_dir / "_work"
        work_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"{system.name}-{key}-", dir=work_root) as name:
            work_dir = Path(name).resolve()
            temporary_output = work_dir / case.prediction_name
            replacements = {
                "input": str(case.source),
                "output": str(temporary_output),
                "case_id": case.case_id,
                "input_name": case.input_name,
                "source_name": case.source_name,
                "work_dir": str(work_dir),
            }
            command = expand_command(system.command, replacements)
            try:
                outcome = run_process(
                    command,
                    cwd=system.cwd or work_dir,
                    environment=system.environment,
                    timeout_seconds=system.timeout_seconds,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                )
            except OSError as exc:
                outcome = os_error_outcome(exc, stderr_path)
            if outcome.timed_out or outcome.exit_code != 0:
                status = process_failure_status(outcome)
                output_payload = b""
            elif not temporary_output.is_file():
                status = SourceRunStatus.MISSING_OUTPUT
                output_payload = b""
            else:
                output_payload = read_bounded(
                    temporary_output,
                    self.manifest.max_output_bytes,
                )
                if not output_payload.strip():
                    status = SourceRunStatus.EMPTY_OUTPUT
                    output_payload = b""
                elif len(output_payload) > self.manifest.max_output_bytes:
                    status = SourceRunStatus.INVALID_OUTPUT
                    output_payload = b""
                else:
                    try:
                        output_payload.decode("utf-8", errors="strict")
                    except UnicodeDecodeError:
                        status = SourceRunStatus.INVALID_OUTPUT
                        output_payload = b""
                    else:
                        status = SourceRunStatus.SUCCESS
            atomic_write(prediction_path, output_payload)
        record = SourceRunRecord(
            system=system.name,
            case_id=case.case_id,
            case_index=case.index,
            input_name=case.input_name,
            source_name=case.source_name,
            source_sha256=case.source_sha256,
            source_bytes=case.source_bytes,
            prediction=relative_prediction,
            fingerprint=fingerprint,
            status=status,
            duration_seconds=outcome.duration_seconds,
            exit_code=outcome.exit_code,
            output_sha256=sha256_bytes(output_payload),
            output_bytes=len(output_payload),
            stdout_sha256=outcome.stdout_sha256,
            stdout_bytes=outcome.stdout_bytes,
            stderr_sha256=outcome.stderr_sha256,
            stderr_bytes=outcome.stderr_bytes,
            peak_rss_bytes=outcome.peak_rss_bytes,
        )
        atomic_json(record_path, record.to_dict())
        return record

    def _run_evaluator(
        self,
        evaluator: OfficialEvaluator,
        *,
        system: SourceBenchmarkSystem,
        selection_path: Path,
    ) -> EvaluatorRecord:
        predictions = self.output_dir / system.name / "predictions"
        result_dir = self.output_dir / system.name / "official-evaluation"
        log_name = system.name
        if self.shard_count > 1:
            shard_label = f"{self.shard_index:05d}-of-{self.shard_count:05d}"
            result_dir = result_dir / shard_label
            log_name = f"{system.name}-{shard_label}"
        result_dir.mkdir(parents=True, exist_ok=True)
        logs = self.output_dir / "_private_logs" / "official-evaluator"
        replacements = {
            "ground_truth": str(selection_path),
            "predictions": str(predictions),
            "result_dir": str(result_dir),
            "system": system.name,
        }
        stdout_path = logs / f"{log_name}.stdout.log"
        stderr_path = logs / f"{log_name}.stderr.log"
        try:
            outcome = run_process(
                expand_command(evaluator.command, replacements),
                cwd=evaluator.cwd or result_dir,
                environment=evaluator.environment,
                timeout_seconds=evaluator.timeout_seconds,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
        except OSError as exc:
            outcome = os_error_outcome(exc, stderr_path)
        status = (
            SourceRunStatus.SUCCESS
            if not outcome.timed_out and outcome.exit_code == 0
            else process_failure_status(outcome)
        )
        return EvaluatorRecord(
            system=system.name,
            status=status,
            duration_seconds=outcome.duration_seconds,
            exit_code=outcome.exit_code,
            result_dir=relative_public_path(result_dir, self.output_dir),
            stdout_sha256=outcome.stdout_sha256,
            stdout_bytes=outcome.stdout_bytes,
            stderr_sha256=outcome.stderr_sha256,
            stderr_bytes=outcome.stderr_bytes,
            peak_rss_bytes=outcome.peak_rss_bytes,
        )

    @staticmethod
    def _summary(system: str, records: Sequence[SourceRunRecord]) -> SourceSystemSummary:
        statuses = Counter(record.status.value for record in records)
        successful = [record for record in records if record.succeeded]
        all_durations = [record.duration_seconds for record in records]
        success_durations = [record.duration_seconds for record in successful]
        peak_rss_values = [
            record.peak_rss_bytes for record in records if record.peak_rss_bytes is not None
        ]
        total = len(records)
        return SourceSystemSummary(
            system=system,
            total_cases=total,
            successful_cases=len(successful),
            failed_cases=total - len(successful),
            status_counts=dict(sorted(statuses.items())),
            operational_success_rate=(len(successful) / total if total else 0.0),
            reused_cases=sum(record.reused for record in records),
            total_seconds=sum(all_durations),
            mean_seconds_all=(sum(all_durations) / total if total else None),
            mean_seconds_successful=(
                sum(success_durations) / len(success_durations) if success_durations else None
            ),
            p50_seconds_successful=percentile(success_durations, 0.5),
            p95_seconds_successful=percentile(success_durations, 0.95),
            peak_rss_bytes_max=max(peak_rss_values) if peak_rss_values else None,
            peak_rss_bytes_mean=(
                sum(peak_rss_values) / len(peak_rss_values) if peak_rss_values else None
            ),
        )

    def run(self) -> SourceBenchmarkReport:
        dataset_sha256 = sha256_file(self.manifest.dataset_json)
        if (
            self.manifest.expected_dataset_sha256 is not None
            and dataset_sha256 != self.manifest.expected_dataset_sha256
        ):
            raise ValueError(
                "dataset annotation SHA-256 does not match manifest: "
                f"expected {self.manifest.expected_dataset_sha256}, got {dataset_sha256}"
            )
        cases, annotations = load_omnidocbench_cases(
            self.manifest,
            subset=self.subset,
            shard_index=self.shard_index,
            shard_count=self.shard_count,
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        selection_payload = (json.dumps(annotations, ensure_ascii=False) + "\n").encode("utf-8")
        selection_path = self.artifact_dir / "_evaluation" / "selected-annotations.json"
        records: list[SourceRunRecord] = []
        for system in self.systems:
            for case in cases:
                records.append(self._run_case(system, case))
        ledger_lines = "".join(stable_json(record.to_dict()) + "\n" for record in records)
        atomic_write(self.artifact_dir / "ledger.jsonl", ledger_lines.encode("utf-8"))
        # Materialize ground truth only after every candidate process has exited. The
        # path is deliberately absent during inference, even from the benchmark output
        # tree, so an external parser cannot discover annotations by walking parents of
        # its isolated work directory.
        atomic_write(selection_path, selection_payload)
        evaluator_records: list[EvaluatorRecord] = []
        evaluator = self.manifest.official_evaluator
        if evaluator is not None and self.run_official_evaluator:
            for system in self.systems:
                evaluator_records.append(
                    self._run_evaluator(evaluator, system=system, selection_path=selection_path)
                )
        summaries = tuple(
            self._summary(
                system.name,
                [record for record in records if record.system == system.name],
            )
            for system in self.systems
        )
        run_fingerprint = stable_digest(
            {
                "schema_version": SOURCE_BENCHMARK_SCHEMA_VERSION,
                "dataset_revision": self.manifest.dataset_revision,
                "dataset_sha256": dataset_sha256,
                "selection_sha256": sha256_bytes(selection_payload),
                "subset": self.subset,
                "shard_index": self.shard_index,
                "shard_count": self.shard_count,
                "cases": [
                    {
                        "input_name": case.input_name,
                        "source_name": case.source_name,
                        "source_sha256": case.source_sha256,
                        "source_bytes": case.source_bytes,
                    }
                    for case in cases
                ],
                "systems": [system.identity for system in self.systems],
                "official_evaluator": evaluator.identity if evaluator else None,
            }
        )
        report = SourceBenchmarkReport(
            schema_version=SOURCE_BENCHMARK_SCHEMA_VERSION,
            run_fingerprint=run_fingerprint,
            dataset_revision=self.manifest.dataset_revision,
            dataset_sha256=dataset_sha256,
            dataset_bytes=self.manifest.dataset_json.stat().st_size,
            selection_sha256=sha256_bytes(selection_payload),
            subset=self.subset,
            shard_index=self.shard_index,
            shard_count=self.shard_count,
            selected_cases=len(cases),
            systems=tuple(system.identity for system in self.systems),
            official_evaluator=evaluator.identity if evaluator else None,
            results=tuple(records),
            summaries=summaries,
            evaluator_results=tuple(evaluator_records),
        )
        atomic_write(
            self.artifact_dir / "source-benchmark-report.json",
            (report.to_json() + "\n").encode("utf-8"),
        )
        return report


def run_source_benchmark(
    manifest: str | Path | SourceBenchmarkManifest,
    **kwargs: Any,
) -> SourceBenchmarkReport:
    """Convenience wrapper around :class:`SourceBenchmarkRunner`."""

    loaded = (
        manifest
        if isinstance(manifest, SourceBenchmarkManifest)
        else load_source_benchmark_manifest(manifest)
    )
    return SourceBenchmarkRunner(loaded, **kwargs).run()
