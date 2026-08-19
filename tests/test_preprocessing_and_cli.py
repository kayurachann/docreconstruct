from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from typer.testing import CliRunner

from docreconstruct.cli import cli
from docreconstruct.preprocessing import SourceKind, analyze_source

runner = CliRunner()


def test_image_source_analysis(tmp_path: Path) -> None:
    source = tmp_path / "page.jpg"
    Image.new("RGB", (80, 120), "white").save(source)

    analysis = analyze_source(source)

    assert analysis.kind is SourceKind.SCANNED
    assert analysis.pages[0].width == 80
    assert analysis.pages[0].height == 120
    assert analysis.pages[0].orientation == "portrait"
    assert analysis.pages[0].rotation == 0
    assert analysis.recommended_provider == "paddleocr"


def test_cli_analyze_writes_canonical_json(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    output = tmp_path / "page.json"
    Image.new("RGB", (40, 20), "white").save(source)

    result = runner.invoke(cli, ["analyze", str(source), "--output", str(output)])

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "0.1"
    assert payload["pages"][0]["width"] == 40


def test_cli_engine_auto_uses_source_aware_defaults(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    Image.new("RGB", (32, 24), "white").save(source)

    result = runner.invoke(cli, ["analyze", str(source), "--engine", "auto"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["metadata"]["pipeline"]["providers"] == ["source_image"]


def test_cli_provider_recommend_explains_saved_distorted_handwriting(tmp_path: Path) -> None:
    source = tmp_path / "page.json"
    source.write_text("{}", encoding="utf-8")

    result = runner.invoke(
        cli,
        [
            "provider-recommend",
            str(source),
            "--execution",
            "saved",
            "--handwriting",
            "--formulas",
            "--tables",
            "--distorted-photo",
            "--dewarping",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload[0]["provider"] == "paddleocr"
    assert payload[0]["compatible"] is True


def test_cli_exposes_training_dataset_schema() -> None:
    result = runner.invoke(cli, ["schema", "--kind", "training-dataset"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["title"] == "DatasetManifest"
    assert "samples" in payload["properties"]


def test_cli_runs_ocr_benchmark_with_explicit_cloud_consent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docreconstruct import evaluation

    dataset = tmp_path / "ocr-benchmark.json"
    dataset.write_text('{"cases": []}', encoding="utf-8")
    output = tmp_path / "report.json"
    captured: dict[str, Any] = {}

    def fake_run(source: Path, **kwargs: Any) -> SimpleNamespace:
        captured["source"] = source
        captured.update(kwargs)
        Path(kwargs["output_path"]).write_text("{}\n", encoding="utf-8")
        return SimpleNamespace(successful_cases=2, failed_cases=0, mean_score=0.875)

    monkeypatch.setattr(evaluation, "run_ocr_benchmark", fake_run)
    result = runner.invoke(
        cli,
        [
            "benchmark-ocr",
            str(dataset),
            "--output",
            str(output),
            "--allow-cloud",
            "--record-timings",
        ],
    )

    assert result.exit_code == 0, result.output
    assert output.is_file()
    assert captured["source"] == dataset
    assert captured["allow_cloud"] is True
    assert captured["record_timings"] is True
    assert "2 succeeded, 0 failed" in result.output


def test_cli_compare_defaults_to_native_rendering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docreconstruct import evaluation

    reference = tmp_path / "reference.png"
    candidate = tmp_path / "candidate.png"
    Image.new("RGB", (8, 8), "white").save(reference)
    Image.new("RGB", (8, 8), "white").save(candidate)
    captured: dict[str, Any] = {}

    def fake_evaluate(*args: object, **kwargs: Any) -> dict[str, bool]:
        captured.update(kwargs)
        return {"passed": True}

    monkeypatch.setattr(evaluation, "evaluate", fake_evaluate)

    result = runner.invoke(cli, ["compare", str(reference), str(candidate)])

    assert result.exit_code == 0, result.output
    assert captured["render_backend"] == "native"
    assert captured["renderer_path"] is None


def test_cli_compare_passes_explicit_renderer_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docreconstruct import evaluation

    reference = tmp_path / "reference.png"
    candidate = tmp_path / "candidate.docx"
    renderer = tmp_path / "soffice.exe"
    Image.new("RGB", (8, 8), "white").save(reference)
    candidate.write_bytes(b"candidate")
    renderer.write_bytes(b"renderer")
    captured: dict[str, Any] = {}

    def fake_evaluate(*args: object, **kwargs: Any) -> dict[str, bool]:
        captured.update(kwargs)
        return {"passed": True}

    monkeypatch.setattr(evaluation, "evaluate", fake_evaluate)

    result = runner.invoke(
        cli,
        [
            "compare",
            str(reference),
            str(candidate),
            "--render-backend",
            "libreoffice",
            "--renderer-path",
            str(renderer),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["render_backend"] == "libreoffice"
    assert captured["renderer_path"] == renderer


def test_cli_compare_rejects_renderer_path_with_native_backend(tmp_path: Path) -> None:
    reference = tmp_path / "reference.png"
    candidate = tmp_path / "candidate.png"
    renderer = tmp_path / "soffice.exe"
    Image.new("RGB", (8, 8), "white").save(reference)
    Image.new("RGB", (8, 8), "white").save(candidate)
    renderer.write_bytes(b"renderer")

    result = runner.invoke(
        cli,
        [
            "compare",
            str(reference),
            str(candidate),
            "--renderer-path",
            str(renderer),
        ],
    )

    assert result.exit_code == 2
    assert "requires --render-backend auto or libreoffice" in result.output
