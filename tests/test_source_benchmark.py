from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from docreconstruct.cli import cli
from docreconstruct.evaluation import (
    SourceBenchmarkRunner,
    SourceRunStatus,
    load_omnidocbench_cases,
    load_source_benchmark_manifest,
    run_source_benchmark,
)
from docreconstruct.evaluation.source_benchmark.process import _windows_process_peak_rss_bytes


def test_source_benchmark_package_preserves_public_api() -> None:
    module = importlib.import_module("docreconstruct.evaluation.source_benchmark")
    expected = [
        "EvaluatorRecord",
        "OfficialEvaluator",
        "SOURCE_BENCHMARK_SCHEMA_VERSION",
        "SourceBenchmarkCase",
        "SourceBenchmarkManifest",
        "SourceBenchmarkReport",
        "SourceBenchmarkRunner",
        "SourceBenchmarkSystem",
        "SourceRunRecord",
        "SourceRunStatus",
        "SourceSystemSummary",
        "load_omnidocbench_cases",
        "load_source_benchmark_manifest",
        "run_source_benchmark",
    ]

    assert module.__all__ == expected
    assert module.SourceBenchmarkRunner is SourceBenchmarkRunner
    assert module.SourceRunStatus is SourceRunStatus
    assert module.load_omnidocbench_cases is load_omnidocbench_cases
    assert module.load_source_benchmark_manifest is load_source_benchmark_manifest
    assert module.run_source_benchmark is run_source_benchmark


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows guard regression")
def test_windows_rss_sampler_is_inert_on_other_platforms() -> None:
    assert _windows_process_peak_rss_bytes(1) is None


def _copy_command() -> list[str]:
    return [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; import sys; "
            "Path(sys.argv[2]).write_text("
            "Path(sys.argv[1]).read_text(encoding='utf-8'), encoding='utf-8')"
        ),
        "{input}",
        "{output}",
    ]


def _system(name: str, command: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "version": "fixture-1.0",
        "revision": "sha256:fixture",
        "command": command,
        "timeout_seconds": 2,
    }


def _write_manifest(
    tmp_path: Path,
    *,
    systems: list[dict[str, Any]] | None = None,
    subsets: tuple[str, ...] = ("default", "equation_hard", "layout_hard", "table_hard"),
    evaluator: bool = False,
) -> Path:
    images = tmp_path / "images"
    images.mkdir()
    annotations: list[dict[str, Any]] = []
    for index, subset in enumerate(subsets):
        name = f"page-{index}.png"
        (images / name).write_text(f"source {index}", encoding="utf-8")
        annotations.append(
            {
                "layout_dets": [],
                "page_info": {
                    "page_no": index,
                    "image_path": name,
                    "page_attribute": {"subset": subset},
                },
                "extra": {"relation": []},
            }
        )
    annotation_path = tmp_path / "OmniDocBench.json"
    annotation_path.write_text(json.dumps(annotations), encoding="utf-8")
    manifest: dict[str, Any] = {
        "schema_version": "0.1",
        "dataset": {
            "annotations": annotation_path.name,
            "images": images.name,
            "revision": "fixture-revision",
            "sha256": hashlib.sha256(annotation_path.read_bytes()).hexdigest(),
        },
        "output_dir": "benchmark-output",
        "systems": systems or [_system("copy", _copy_command())],
    }
    if evaluator:
        evaluator_code = (
            "import json, pathlib, sys; "
            "truth=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')); "
            "pred=list(pathlib.Path(sys.argv[2]).glob('*.md')); "
            "out=pathlib.Path(sys.argv[3]); out.mkdir(parents=True, exist_ok=True); "
            "(out/'counts.json').write_text(json.dumps([len(truth),len(pred)]))"
        )
        manifest["official_evaluator"] = {
            "name": "OmniDocBench-official",
            "version": "1.0",
            "revision": "193627ae9e97d89188468ed1ee3b7a856ff76044",
            "command": [
                sys.executable,
                "-c",
                evaluator_code,
                "{ground_truth}",
                "{predictions}",
                "{result_dir}",
            ],
        }
    path = tmp_path / "source-benchmark.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_hard_subset_uses_exact_inputs_official_names_and_evaluator(tmp_path: Path) -> None:
    empty_command = [
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('')",
        "{output}",
        "{input}",
    ]
    manifest = _write_manifest(
        tmp_path,
        systems=[_system("copy", _copy_command()), _system("empty", empty_command)],
        evaluator=True,
    )

    report = run_source_benchmark(manifest, subset="hard")

    assert report.selected_cases == 3
    assert len(report.results) == 6
    by_system = {summary.system: summary for summary in report.summaries}
    assert by_system["copy"].successful_cases == 3
    assert by_system["empty"].failed_cases == 3
    assert by_system["empty"].status_counts == {"empty_output": 3}
    assert by_system["empty"].operational_success_rate == 0.0
    output = tmp_path / "benchmark-output"
    assert sorted(path.name for path in (output / "copy" / "predictions").glob("*.md")) == [
        "page-1.md",
        "page-2.md",
        "page-3.md",
    ]
    assert all(path.stat().st_size == 0 for path in (output / "empty" / "predictions").glob("*.md"))
    assert json.loads((output / "copy" / "official-evaluation" / "counts.json").read_text()) == [
        3,
        3,
    ]
    assert json.loads((output / "empty" / "official-evaluation" / "counts.json").read_text()) == [
        3,
        3,
    ]
    assert all(result.status is SourceRunStatus.SUCCESS for result in report.evaluator_results)
    ledger = [json.loads(line) for line in (output / "ledger.jsonl").read_text().splitlines()]
    assert len(ledger) == 6
    assert "ground_truth" not in json.dumps(report.systems)
    assert all(len(result.source_sha256) == 64 for result in report.results)


def test_all_failure_classes_are_retained_as_empty_predictions(tmp_path: Path) -> None:
    def command(code: str) -> list[str]:
        return [sys.executable, "-c", code, "{input}", "{output}"]

    systems = [
        _system("timeout", command("import time; time.sleep(2)")),
        _system("oom", command("import sys; sys.stderr.write('CUDA out of memory');sys.exit(1)")),
        _system(
            "crash",
            command("import sys; sys.stderr.write('segmentation fault');sys.exit(139)"),
        ),
        _system("nonzero", command("import sys; sys.exit(7)")),
        _system("missing", command("pass")),
        _system(
            "empty",
            command("from pathlib import Path;import sys;Path(sys.argv[2]).write_bytes(b'')"),
        ),
        _system(
            "invalid",
            command("from pathlib import Path;import sys;Path(sys.argv[2]).write_bytes(b'\\xff')"),
        ),
        _system(
            "oversized",
            command("from pathlib import Path;import sys;Path(sys.argv[2]).write_bytes(b'x'*33)"),
        ),
    ]
    systems[0]["timeout_seconds"] = 0.05
    manifest = _write_manifest(tmp_path, systems=systems, subsets=("default",))
    raw = json.loads(manifest.read_text())
    raw["max_output_bytes"] = 32
    manifest.write_text(json.dumps(raw))

    report = run_source_benchmark(manifest, run_official_evaluator=False)

    assert {result.system: result.status for result in report.results} == {
        "timeout": SourceRunStatus.TIMEOUT,
        "oom": SourceRunStatus.OOM,
        "crash": SourceRunStatus.CRASH,
        "nonzero": SourceRunStatus.NONZERO_EXIT,
        "missing": SourceRunStatus.MISSING_OUTPUT,
        "empty": SourceRunStatus.EMPTY_OUTPUT,
        "invalid": SourceRunStatus.INVALID_OUTPUT,
        "oversized": SourceRunStatus.INVALID_OUTPUT,
    }
    assert report.failed_cases == 8
    assert any(result.peak_rss_bytes for result in report.results)
    assert any(summary.peak_rss_bytes_max for summary in report.summaries)
    for result in report.results:
        prediction = tmp_path / "benchmark-output" / result.prediction
        assert prediction.is_file()
        assert prediction.read_bytes() == b""
        assert result.output_bytes == 0


def test_atomic_checkpoint_resume_requires_exact_source_hash(tmp_path: Path) -> None:
    counter = tmp_path / "counter.txt"
    code = (
        "from pathlib import Path;import sys; "
        "counter=Path(sys.argv[3]); "
        "n=int(counter.read_text())+1 if counter.exists() else 1; "
        "counter.write_text(str(n)); "
        "Path(sys.argv[2]).write_text(Path(sys.argv[1]).read_text())"
    )
    system = _system(
        "counter",
        [sys.executable, "-c", code, "{input}", "{output}", str(counter)],
    )
    manifest_path = _write_manifest(tmp_path, systems=[system], subsets=("default",))

    first = run_source_benchmark(manifest_path)
    second = run_source_benchmark(manifest_path)

    assert counter.read_text() == "1"
    assert not first.results[0].reused
    assert second.results[0].reused
    assert first.run_fingerprint == second.run_fingerprint

    (tmp_path / "images" / "page-0.png").write_text("changed source", encoding="utf-8")
    third = run_source_benchmark(manifest_path)
    assert counter.read_text() == "2"
    assert not third.results[0].reused
    assert third.results[0].source_sha256 != first.results[0].source_sha256


def test_shards_are_disjoint_and_exhaustive(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    manifest = load_source_benchmark_manifest(manifest_path)
    shard_zero, _ = load_omnidocbench_cases(manifest, shard_index=0, shard_count=2)
    shard_one, _ = load_omnidocbench_cases(manifest, shard_index=1, shard_count=2)
    complete, _ = load_omnidocbench_cases(manifest)

    zero = {case.case_id for case in shard_zero}
    one = {case.case_id for case in shard_one}
    assert zero.isdisjoint(one)
    assert zero | one == {case.case_id for case in complete}

    run_source_benchmark(manifest_path, shard_index=0, shard_count=2)
    run_source_benchmark(manifest_path, shard_index=1, shard_count=2)
    output = tmp_path / "benchmark-output"
    assert (output / "shards" / "00000-of-00002" / "ledger.jsonl").is_file()
    assert (output / "shards" / "00001-of-00002" / "ledger.jsonl").is_file()
    assert len(list((output / "copy" / "predictions").glob("*.md"))) == 4


def test_source_suffix_uses_pixel_identical_pdf_lane_but_keeps_official_name(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest(tmp_path, subsets=("default",))
    (tmp_path / "images" / "page-0.pdf").write_text("compact PDF source", encoding="utf-8")
    raw = json.loads(manifest_path.read_text())
    raw["dataset"]["source_suffix"] = ".pdf"
    manifest_path.write_text(json.dumps(raw))

    report = run_source_benchmark(manifest_path)

    result = report.results[0]
    assert result.input_name == "page-0.png"
    assert result.source_name == "page-0.pdf"
    assert result.prediction.endswith("page-0.md")
    prediction = tmp_path / "benchmark-output" / result.prediction
    assert prediction.read_text(encoding="utf-8") == "compact PDF source"
    selection = json.loads(
        (tmp_path / "benchmark-output" / "_evaluation" / "selected-annotations.json").read_text()
    )
    assert selection[0]["page_info"]["image_path"] == "page-0.png"


def test_ground_truth_is_materialized_only_after_candidate_inference(tmp_path: Path) -> None:
    code = (
        "from pathlib import Path; import sys; "
        "source=Path(sys.argv[1]); output=Path(sys.argv[2]); "
        "selection=output.resolve().parents[2]/'_evaluation'/'selected-annotations.json'; "
        "output.write_text('leaked' if selection.exists() else source.read_text())"
    )
    manifest = _write_manifest(
        tmp_path,
        systems=[_system("probe", [sys.executable, "-c", code, "{input}", "{output}"])],
        subsets=("default",),
    )

    report = run_source_benchmark(manifest, run_official_evaluator=False)

    prediction = tmp_path / "benchmark-output" / report.results[0].prediction
    assert prediction.read_text(encoding="utf-8") == "source 0"
    assert (tmp_path / "benchmark-output" / "_evaluation" / "selected-annotations.json").is_file()


def test_manifest_rejects_ground_truth_candidate_and_prediction_collision(
    tmp_path: Path,
) -> None:
    invalid = _system("leaky", ["tool", "{input}", "{output}", "{ground_truth}"])
    path = _write_manifest(tmp_path, systems=[invalid], subsets=("default",))
    with pytest.raises(ValueError, match="may not receive ground truth"):
        load_source_benchmark_manifest(path)

    raw = json.loads(path.read_text())
    secret_system = _system("copy", _copy_command())
    secret_system["environment"] = {"OCR_API_KEY": "must-not-enter-report"}
    raw["systems"] = [secret_system]
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="looks secret-bearing"):
        load_source_benchmark_manifest(path)

    raw["systems"] = [_system("copy", _copy_command())]
    path.write_text(json.dumps(raw))
    annotations = json.loads((tmp_path / "OmniDocBench.json").read_text())
    duplicate = json.loads(json.dumps(annotations[0]))
    duplicate["page_info"]["image_path"] = "page-0.jpg"
    (tmp_path / "images" / "page-0.jpg").write_text("other")
    annotations.append(duplicate)
    (tmp_path / "OmniDocBench.json").write_text(json.dumps(annotations))
    raw["dataset"].pop("sha256")
    path.write_text(json.dumps(raw))
    manifest = load_source_benchmark_manifest(path)
    with pytest.raises(ValueError, match="filename collision"):
        load_omnidocbench_cases(manifest)


def test_cli_and_portable_schema(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, subsets=("default",))
    result = CliRunner().invoke(
        cli,
        ["benchmark-source", str(manifest), "--no-official-evaluator"],
    )

    assert result.exit_code == 0, result.output
    assert "1/1 succeeded" in result.output
    assert (tmp_path / "benchmark-output" / "source-benchmark-report.json").is_file()
    schema_path = Path(__file__).parents[1] / "schemas" / "source-benchmark.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["properties"]["schema_version"]["const"] == "0.1"


def test_annotation_hash_mismatch_stops_before_candidate_execution(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, subsets=("default",))
    raw = json.loads(manifest_path.read_text())
    raw["dataset"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(raw))
    manifest = load_source_benchmark_manifest(manifest_path)

    with pytest.raises(ValueError, match="SHA-256 does not match"):
        SourceBenchmarkRunner(manifest).run()
