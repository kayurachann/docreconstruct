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


def test_benchmark_reports_failures_and_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``benchmark`` printed "Wrote ..." and exited 0 even when every case failed.

    Its three sibling commands all print a succeeded/failed line and exit 3, so
    a CI job gating on this one silently passed a fully broken run.
    """

    from docreconstruct import evaluation

    dataset = tmp_path / "manifest.json"
    dataset.write_text('{"cases": []}', encoding="utf-8")
    output = tmp_path / "report.json"

    def fake_run(source: Path, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            successful_cases=0,
            failed_cases=3,
            mean_score=None,
            to_dict=lambda: {"results": []},
        )

    monkeypatch.setattr(evaluation, "run_benchmark", fake_run)
    result = runner.invoke(cli, ["benchmark", str(dataset), "--output", str(output)])

    assert result.exit_code == 3, result.output
    assert "0 succeeded, 3 failed" in result.output
    assert "mean score: unavailable" in result.output


@pytest.mark.parametrize(
    ("exif_orientation", "expected"),
    [(1, (1200, 800)), (3, (1200, 800)), (5, (800, 1200)), (6, (800, 1200)), (8, (800, 1200))],
)
def test_exif_quarter_turns_are_reflected_in_the_page_frame(
    tmp_path: Path, exif_orientation: int, expected: tuple[int, int]
) -> None:
    """``image.size`` is the stored raster, not what a provider receives.

    preprocessing.image applies exif_transpose before any provider sees the
    pixels, so an EXIF 6 photo stored 1200x800 arrives as 800x1200. Reporting
    the stored size handed every downstream consumer a transposed page frame
    and the wrong orientation label.
    """

    source = tmp_path / f"exif{exif_orientation}.jpg"
    image = Image.new("RGB", (1200, 800), "white")
    exif = image.getexif()
    exif[274] = exif_orientation
    image.save(source, "JPEG", exif=exif)

    page = analyze_source(source).pages[0]

    assert (page.width, page.height) == expected
    assert page.orientation == ("landscape" if expected[0] > expected[1] else "portrait")


def test_exif_transposed_page_frame_matches_the_transposed_raster(tmp_path: Path) -> None:
    from PIL import ImageOps

    source = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (1200, 800), "white")
    exif = image.getexif()
    exif[274] = 6
    image.save(source, "JPEG", exif=exif)

    page = analyze_source(source).pages[0]
    with Image.open(source) as raw:
        transposed = ImageOps.exif_transpose(raw)
        actual = transposed.size if transposed is not None else raw.size

    assert (page.width, page.height) == actual
    assert page.rotation == 90.0


@pytest.mark.parametrize(
    ("output_name", "output_format"),
    [("report.docx", "html"), ("report.html", "docx"), ("report.md", "json")],
)
def test_conflicting_output_name_and_format_are_rejected(
    tmp_path: Path, output_name: str, output_format: str
) -> None:
    """The extension lost to --output-format, silently and without a warning.

    `reconstruct scan.png -o report.docx --output-format html` exited 0 having
    written HTML into a file Word cannot open.
    """

    source = tmp_path / "scan.png"
    Image.new("RGB", (200, 120), "white").save(source)
    destination = tmp_path / output_name

    result = runner.invoke(
        cli,
        ["reconstruct", str(source), "-o", str(destination), "--output-format", output_format],
    )

    assert result.exit_code == 2, result.output
    assert "conflicts with" in result.output
    assert not destination.exists()


@pytest.mark.parametrize(
    ("output_name", "output_format"),
    [
        ("report.html", "html"),
        # Aliases must agree rather than trip the guard.
        ("report.htm", "html"),
        ("report.md", "markdown"),
        # An unrecognized suffix stays the caller's business.
        ("report.out", "html"),
    ],
)
def test_agreeing_or_unknown_output_names_are_accepted(
    tmp_path: Path, output_name: str, output_format: str
) -> None:
    source = tmp_path / "scan.png"
    Image.new("RGB", (200, 120), "white").save(source)
    destination = tmp_path / output_name

    result = runner.invoke(
        cli,
        ["reconstruct", str(source), "-o", str(destination), "--output-format", output_format],
    )

    assert result.exit_code == 0, result.output
    assert destination.exists()
