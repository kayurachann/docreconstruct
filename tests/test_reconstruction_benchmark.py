from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from docreconstruct.cli import cli
from docreconstruct.evaluation.reconstruction_benchmark import (
    ReconstructionBenchmarkCase,
    ReconstructionBenchmarkRunner,
    ReconstructionJobOptions,
    load_reconstruction_benchmark_manifest,
    run_reconstruction_benchmark,
)
from docreconstruct.evaluation.visual import VISUAL_METRIC_VERSION


@dataclass
class _Dumpable:
    payload: dict[str, Any]

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return self.payload


def _fixture_job(
    calls: list[dict[str, Any]],
    *,
    score: float = 0.75,
    fidelity: float | None = None,
    bad_reconstruction_sha: bool = False,
    fail_failed_content: bool = True,
    visual_metric_versions: dict[str, str] | None = None,
) -> Any:
    def run(content: Path, layout: Path, **kwargs: Any) -> Any:
        output = Path(kwargs["output"])
        assert not output.exists(), "every benchmark candidate must start fresh"
        calls.append({"content": content, "layout": layout, **kwargs})
        content_stem = Path(content).stem
        if fail_failed_content and content_stem.startswith("failed"):
            raise RuntimeError("intentional reconstruction failure")
        output.write_bytes(b"deterministic fixture candidate")
        output_sha = hashlib.sha256(output.read_bytes()).hexdigest()
        reconstruction = _Dumpable(
            {
                "output": {
                    "path": str(output),
                    "sha256": "0" * 64 if bad_reconstruction_sha else output_sha,
                },
                "render_input_sha256": "1" * 64,
            }
        )
        reconstruction.output = SimpleNamespace(path=str(output))  # type: ignore[attr-defined]
        metrics: dict[str, Any] = {
            "render_input_artifact_sha256": "1" * 64,
            "render_plan_sha256": "1" * 64,
        }
        if fidelity is not None:
            metrics.update(
                {
                    "rendered_visual": {
                        "score": fidelity,
                        "metric_version": (visual_metric_versions or {}).get(
                            content_stem, VISUAL_METRIC_VERSION
                        ),
                    },
                    "render_backend": {
                        "status": "rendered",
                        "used_backend": "libreoffice",
                    },
                }
            )
        validation = _Dumpable(
            {
                "passed": score == 1.0,
                "score": score,
                "candidate_sha256": output_sha,
                "metrics": metrics,
            }
        )
        validation.passed = score == 1.0  # type: ignore[attr-defined]
        validation.score = score  # type: ignore[attr-defined]
        return SimpleNamespace(
            reconstruction=reconstruction,
            validation=validation,
            phase_seconds={
                "prepare.scan": 0.02,
                "render.docx": 0.03,
                "qa.native": 0.01,
                "job.total": 0.06,
            },
        )

    return run


def _dataset(tmp_path: Path) -> Path:
    for stem in ("passing", "failed"):
        (tmp_path / f"{stem}.png").write_bytes(f"layout:{stem}".encode())
        (tmp_path / f"{stem}.md").write_text(f"Reviewed {stem}.\n", encoding="utf-8")
        (tmp_path / f"{stem}.json").write_text(
            json.dumps({"id": stem, "pages": [], "schema_version": "0.1"}),
            encoding="utf-8",
        )
    payload = {
        "schema_version": "0.1",
        "seed": 23,
        "configuration": {"suite": "offline-fixture"},
        "job_options": {"allow_remote_assets": False},
        "cases": [
            {
                "id": "z-failure",
                "original_layout": "failed.png",
                "reviewed_markdown": "failed.md",
                "evidence": [{"path": "failed.json", "provider": "json"}],
                "tags": {
                    "language": "vi",
                    "document_type": "exam",
                    "degradation": "blur",
                    "content_kind": "formula",
                },
            },
            {
                "id": "a-success",
                "original_layout": "passing.png",
                "reviewed_markdown": "passing.md",
                "evidence": ["passing.json"],
                "evidence_provider_hints": ["json"],
                "tags": {
                    "languages": ["vi"],
                    "document_types": ["exam"],
                    "degradations": ["clean"],
                    "content_kinds": ["printed"],
                },
                "metadata": {"accessToken": "must-not-leak"},
            },
        ],
    }
    path = tmp_path / "reconstruction-benchmark.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_three_source_benchmark_generates_fresh_candidates_and_counts_failures_as_zero(
    tmp_path: Path,
) -> None:
    manifest = _dataset(tmp_path)
    first_calls: list[dict[str, Any]] = []
    second_calls: list[dict[str, Any]] = []

    first = run_reconstruction_benchmark(
        manifest,
        output_dir=tmp_path / "first-output",
        job_runner=_fixture_job(first_calls),
    )
    second = run_reconstruction_benchmark(
        tmp_path,
        output_dir=tmp_path / "second-output",
        job_runner=_fixture_job(second_calls),
    )

    assert [result.case_id for result in first.results] == ["a-success", "z-failure"]
    assert first.successful_cases == 1
    assert first.failed_cases == 1
    assert first.mean_quality_score is None
    assert first.quality_coverage == 0.0
    assert first.mean_validation_gate_score == pytest.approx(0.75)
    assert first.validation_gate_coverage == pytest.approx(0.5)
    assert first.operational_success_rate == pytest.approx(0.5)
    assert first.run_fingerprint == second.run_fingerprint
    assert [result.fingerprint for result in first.results] == [
        result.fingerprint for result in second.results
    ]
    passing, failed = first.results
    assert passing.quality_score is None
    assert passing.quality_complete is False
    assert passing.validation_gate_score == pytest.approx(0.75)
    assert passing.operational_score == 1.0
    assert (
        passing.candidate_sha256 == hashlib.sha256(b"deterministic fixture candidate").hexdigest()
    )
    assert passing.render_input_sha256 == "1" * 64
    assert passing.phase_seconds["prepare.scan"] == pytest.approx(0.02)
    assert failed.quality_score is None
    assert failed.operational_score == 0.0
    assert failed.failure is not None
    assert failed.failure.phase == "reconstruction"
    assert failed.failure.error_type == "RuntimeError"
    assert first.slice_means["language"]["vi"].mean_quality_score is None
    assert first.slice_means["degradation"]["blur"].mean_quality_score is None
    assert first.slice_means["degradation"]["blur"].operational_success_rate == 0.0
    assert first.phase_seconds["prepare.scan"] == pytest.approx(0.02)
    assert "must-not-leak" not in first.to_json()
    assert str(tmp_path) not in first.to_json()
    assert "intentional reconstruction failure" not in first.to_json()

    successful_call = first_calls[0]
    assert successful_call["evidence_provider_hints"] == ("json",)
    assert successful_call["allow_remote_assets"] is False
    assert Path(successful_call["output"]).name == "candidate.docx"
    assert Path(successful_call["qa_report"]).name == "qa.json"


def test_manifest_requires_all_three_sources_and_validates_provider_hints(
    tmp_path: Path,
) -> None:
    manifest = _dataset(tmp_path)
    cases, loaded = load_reconstruction_benchmark_manifest(manifest)

    assert loaded["seed"] == 23
    assert cases[0].evidence_provider_hints == ("json",)
    assert cases[0].original_layout.is_absolute()
    assert cases[1].evidence_provider_hints == ("json",)

    with pytest.raises(ValueError, match="needs positioned evidence"):
        ReconstructionBenchmarkCase(
            "missing-evidence",
            tmp_path / "passing.png",
            tmp_path / "passing.md",
            (),
        )

    bad = json.loads(manifest.read_text(encoding="utf-8"))
    bad["cases"][0]["evidence_provider_hints"] = ["json"]
    (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot combine embedded providers"):
        load_reconstruction_benchmark_manifest(tmp_path / "bad.json")


def test_rendered_mode_is_explicit_and_passed_to_the_hybrid_job(
    tmp_path: Path,
) -> None:
    manifest = _dataset(tmp_path)
    cases, _ = load_reconstruction_benchmark_manifest(manifest)
    renderer = tmp_path / "soffice.exe"
    renderer.write_bytes(b"fixture renderer")
    calls: list[dict[str, Any]] = []

    report = ReconstructionBenchmarkRunner(
        output_dir=tmp_path / "rendered-output",
        render_backend="libreoffice",
        renderer_path=renderer,
        minimum_visual_score=0.9,
        save_render_artifacts=True,
        job_runner=_fixture_job(calls, score=1.0, fidelity=0.88),
    ).run([cases[1]])

    assert report.successful_cases == 1
    assert report.accepted_cases == 1
    assert report.mean_quality_score == pytest.approx(0.88)
    assert report.quality_coverage == 1.0
    assert report.results[0].quality_profile == (
        f"rendered_visual|backend=libreoffice|metric={VISUAL_METRIC_VERSION}"
    )
    assert calls[0]["render_backend"] == "libreoffice"
    assert calls[0]["renderer_path"] == renderer.resolve()
    assert calls[0]["minimum_visual_score"] == pytest.approx(0.9)
    assert Path(calls[0]["render_output_dir"]).name == "rendered"


def test_failed_rendered_cases_contribute_zero_to_comparable_fidelity(
    tmp_path: Path,
) -> None:
    manifest = _dataset(tmp_path)
    cases, _ = load_reconstruction_benchmark_manifest(manifest)
    renderer = tmp_path / "soffice.exe"
    renderer.write_bytes(b"fixture renderer")

    report = ReconstructionBenchmarkRunner(
        output_dir=tmp_path / "rendered-with-failure",
        render_backend="libreoffice",
        renderer_path=renderer,
        job_runner=_fixture_job([], score=1.0, fidelity=0.8),
    ).run(cases)

    assert report.quality_complete_cases == 2
    assert report.mean_quality_score == pytest.approx(0.4)
    assert report.results[1].failure is not None
    assert report.results[1].quality_score == 0.0


def test_mixed_visual_metric_versions_are_not_combined_into_primary_aggregate(
    tmp_path: Path,
) -> None:
    manifest = _dataset(tmp_path)
    cases, _ = load_reconstruction_benchmark_manifest(manifest)
    renderer = tmp_path / "soffice.exe"
    renderer.write_bytes(b"fixture renderer")
    metric_versions = {"passing": "visual-test-v1", "failed": "visual-test-v2"}

    report = ReconstructionBenchmarkRunner(
        output_dir=tmp_path / "mixed-metric-versions",
        render_backend="libreoffice",
        renderer_path=renderer,
        job_runner=_fixture_job(
            [],
            score=1.0,
            fidelity=0.8,
            fail_failed_content=False,
            visual_metric_versions=metric_versions,
        ),
    ).run(cases)

    expected_profiles = {
        f"rendered_visual|backend=libreoffice|metric={version}"
        for version in metric_versions.values()
    }
    assert report.successful_cases == 2
    assert report.quality_complete_cases == 2
    assert report.mean_quality_score is None
    assert set(report.quality_profiles) == expected_profiles
    assert all(summary.total_cases == 1 for summary in report.quality_profiles.values())
    assert all(
        summary.mean_quality_score == pytest.approx(0.8)
        for summary in report.quality_profiles.values()
    )
    serialized = json.loads(report.to_json())
    assert serialized["summary"]["mean_quality_score"] is None
    assert set(serialized["summary"]["quality_profiles"]) == expected_profiles


def test_reconstruction_benchmark_cli_writes_report_and_returns_three_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docreconstruct.evaluation.reconstruction_benchmark as benchmark_module

    manifest = _dataset(tmp_path)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(benchmark_module, "run_hybrid_job", _fixture_job(calls))
    report_path = tmp_path / "report.json"

    result = CliRunner().invoke(
        cli,
        [
            "benchmark-reconstruction",
            str(manifest),
            "--output",
            str(report_path),
            "--output-dir",
            str(tmp_path / "cli-output"),
        ],
    )

    assert result.exit_code == 3, result.output
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["failed_cases"] == 1
    assert payload["summary"]["mean_quality_score"] is None
    assert payload["summary"]["mean_validation_gate_score"] == pytest.approx(0.75)
    assert "incomplete" in result.output


def test_reconstruction_benchmark_rejects_duplicate_ids(tmp_path: Path) -> None:
    manifest = _dataset(tmp_path)
    cases, _ = load_reconstruction_benchmark_manifest(manifest)
    runner = ReconstructionBenchmarkRunner(
        output_dir=tmp_path / "outputs",
        job_runner=_fixture_job([]),
    )

    with pytest.raises(ValueError, match="IDs must be unique"):
        runner.run([cases[0], cases[0]])


def test_runtime_policy_is_final_authority_for_remote_assets_and_backend(
    tmp_path: Path,
) -> None:
    manifest = _dataset(tmp_path)
    cases, _ = load_reconstruction_benchmark_manifest(manifest)
    base = cases[1]
    remote_case = replace(
        base,
        job_options=ReconstructionJobOptions(allow_remote_assets=True),
    )

    disabled_calls: list[dict[str, Any]] = []
    ReconstructionBenchmarkRunner(
        output_dir=tmp_path / "remote-disabled",
        allow_remote_assets=False,
        job_runner=_fixture_job(disabled_calls),
    ).run([remote_case])
    assert disabled_calls[0]["allow_remote_assets"] is False

    enabled_calls: list[dict[str, Any]] = []
    ReconstructionBenchmarkRunner(
        output_dir=tmp_path / "remote-enabled",
        allow_remote_assets=True,
        job_runner=_fixture_job(enabled_calls),
    ).run([remote_case])
    assert enabled_calls[0]["allow_remote_assets"] is True

    escalating_case = replace(
        base,
        job_options=ReconstructionJobOptions(render_backend="libreoffice"),
    )
    with pytest.raises(ValueError, match="cannot enable rendered QA"):
        ReconstructionBenchmarkRunner(
            output_dir=tmp_path / "backend-escalation",
            render_backend="native",
            job_runner=_fixture_job([]),
            fail_fast=True,
        ).run([escalating_case])


def test_candidate_integrity_must_match_reconstruction_and_validation(
    tmp_path: Path,
) -> None:
    manifest = _dataset(tmp_path)
    cases, _ = load_reconstruction_benchmark_manifest(manifest)
    report = ReconstructionBenchmarkRunner(
        output_dir=tmp_path / "integrity",
        job_runner=_fixture_job([], bad_reconstruction_sha=True),
    ).run([cases[1]])

    result = report.results[0]
    assert result.failure is not None
    assert result.failure.error_type == "RuntimeError"
    assert result.operational_success is False
    assert result.validation is None
    assert "does not match" not in report.to_json()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update({"schema_version": "9.9"}), "unsupported"),
        (lambda payload: payload.update({"cases": []}), "at least one case"),
        (lambda payload: payload.update({"unexpected": True}), "unknown"),
        (
            lambda payload: payload["cases"][0].update({"unexpected": True}),
            "unknown",
        ),
    ],
)
def test_manifest_rejects_unsupported_empty_and_unknown_fields(
    tmp_path: Path,
    mutation: Any,
    message: str,
) -> None:
    manifest = _dataset(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    mutation(payload)
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_reconstruction_benchmark_manifest(manifest)


def test_manifest_rejects_unresolved_unknown_and_ambiguous_provider_hints(
    tmp_path: Path,
) -> None:
    manifest = _dataset(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    passing = payload["cases"][1]

    passing["evidence_provider_hints"] = {"not-an-input.json": "json"}
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="not an evidence input"):
        load_reconstruction_benchmark_manifest(manifest)

    passing["evidence_provider_hints"] = ["not-a-provider"]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown sidecar provider"):
        load_reconstruction_benchmark_manifest(manifest)

    (tmp_path / "passing.json").write_text(
        json.dumps(
            {
                "document": {"id": "ambiguous", "pages": []},
                "natural_text": "also resembles olmOCR",
            }
        ),
        encoding="utf-8",
    )
    passing.pop("evidence_provider_hints")
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="ambiguous evidence provider"):
        load_reconstruction_benchmark_manifest(manifest)


def test_report_redacts_paths_urls_snippets_and_failure_messages(tmp_path: Path) -> None:
    manifest = _dataset(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["cases"][1]["metadata"].update(
        {
            "snippet": "private OCR text",
            "download": "https://example.test/file?signature=secret",
            "local_note": str(tmp_path / "private.txt"),
            "posix_note": "/srv/docreconstruct/private/input.pdf",
            "media_type": (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            "pipeline_note": "OCR/parser fallback remains enabled",
        }
    )
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    report = run_reconstruction_benchmark(
        manifest,
        output_dir=tmp_path / "privacy",
        job_runner=_fixture_job([]),
    )
    serialized = report.to_json()

    assert str(tmp_path) not in serialized
    assert "private OCR text" not in serialized
    assert "signature=secret" not in serialized
    assert "intentional reconstruction failure" not in serialized
    assert "message_sha256" in serialized

    payload = json.loads(serialized)
    metadata = next(
        result["metadata"] for result in payload["results"] if result["case_id"] == "a-success"
    )
    assert metadata["download"] == "https://example.test/file"
    assert metadata["local_note"] == "<local-path-redacted>"
    assert metadata["posix_note"] == "<local-path-redacted>"
    assert (
        metadata["media_type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert metadata["pipeline_note"] == "OCR/parser fallback remains enabled"
