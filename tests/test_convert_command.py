from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image, ImageDraw
from typer.testing import CliRunner

from docreconstruct.cli import cli
from docreconstruct.extraction import (
    ExtractionMode,
    ExtractionResult,
    ExtractionRunManifest,
    ProviderAttempt,
)
from docreconstruct.ir import BBox, Document, Element, ElementType, Page, Provenance

runner = CliRunner()


def _scan(tmp_path: Path) -> Path:
    source = tmp_path / "scan.png"
    image = Image.new("RGB", (600, 849), "white")
    ImageDraw.Draw(image).rectangle((70, 96, 430, 122), fill="black")
    image.save(source)
    return source


def _ocr_document() -> Document:
    return Document(
        id="convert-evidence",
        pages=[
            Page(
                id="page-1",
                number=1,
                width=600,
                height=849,
                elements=[
                    Element(
                        id="line-1",
                        type=ElementType.PARAGRAPH,
                        bbox=BBox(x0=70, y0=96, x1=430, y1=122),
                        text="Recognized scan wording.",
                        confidence=0.9,
                        provenance=Provenance(engine="tesseract_local"),
                    )
                ],
            )
        ],
        metadata={"provider": "tesseract_local"},
    )


def _fake_extractor(captured: dict[str, Any]) -> Any:
    def fake_extract(source: Path, **kwargs: Any) -> ExtractionResult:
        captured["source"] = Path(source)
        captured.update(kwargs)
        generated = Path(kwargs["output"])
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text("Recognized scan wording.\n", encoding="utf-8")
        evidence_directory = Path(kwargs["evidence_directory"])
        evidence_directory.mkdir(parents=True, exist_ok=True)
        sidecar = evidence_directory / "scan.tesseract_local.canonical.json"
        document = _ocr_document()
        sidecar.write_text(document.model_dump_json(), encoding="utf-8")
        source_sha = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        evidence_sha = hashlib.sha256(sidecar.read_bytes()).hexdigest()
        manifest = ExtractionRunManifest(
            source=str(source),
            source_sha256=source_sha,
            mode=ExtractionMode.LOCAL,
            cloud_authorized=False,
            requested_providers=["tesseract_local"],
            selected_providers=["tesseract_local"],
            successful_providers=["tesseract_local"],
            attempts=[
                ProviderAttempt(
                    provider="tesseract_local",
                    status="succeeded",
                    pages=1,
                    evidence_output=str(sidecar),
                    evidence_sha256=evidence_sha,
                )
            ],
            ensemble=False,
            document_id=document.id,
            output=str(generated),
            evidence_outputs=[str(sidecar)],
            evidence_sha256={str(sidecar): evidence_sha},
        )
        return ExtractionResult(
            document=document,
            output=generated,
            manifest=manifest,
            documents=(document,),
            evidence_outputs=(sidecar,),
        )

    return fake_extract


def test_convert_wires_detected_local_engine_into_extract_and_hybrid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docreconstruct.cli as cli_module
    import docreconstruct.extraction as extraction_module
    import docreconstruct.reconstruction.hybrid_job as job_module

    source = _scan(tmp_path)
    output = tmp_path / "out.docx"
    captured: dict[str, Any] = {}
    hybrid_calls: dict[str, Any] = {}

    monkeypatch.setattr(cli_module, "_installed_local_engines", lambda: ["tesseract_local"])
    monkeypatch.setattr(extraction_module, "extract_to_markdown", _fake_extractor(captured))

    def fake_run_hybrid_job(content: Path, layout: Path, **kwargs: Any) -> SimpleNamespace:
        hybrid_calls["content"] = Path(content)
        hybrid_calls["content_text"] = Path(content).read_text(encoding="utf-8")
        hybrid_calls["layout"] = Path(layout)
        hybrid_calls.update(kwargs)
        return SimpleNamespace(
            reconstruction=SimpleNamespace(output=SimpleNamespace(path=Path(kwargs["output"]))),
            validation=SimpleNamespace(score=1.0, passed_gates=12, measured_gates=12, passed=True),
        )

    monkeypatch.setattr(job_module, "run_hybrid_job", fake_run_hybrid_job)

    result = runner.invoke(cli, ["convert", str(source), str(output)])

    assert result.exit_code == 0, result.output
    assert captured["mode"] is ExtractionMode.LOCAL
    assert captured["providers"] == ["tesseract_local"]
    assert captured["require_geometry"] is True
    assert captured["allow_cloud"] is False
    assert captured["source"] == source.resolve()
    assert hybrid_calls["content"].suffix == ".md"
    assert hybrid_calls["content_text"] == "Recognized scan wording.\n"
    assert hybrid_calls["layout"] == source.resolve()
    assert Path(hybrid_calls["output"]) == output.resolve()
    evidence = tuple(Path(item) for item in hybrid_calls["evidence"])
    assert len(evidence) == 1
    assert evidence[0].name == "scan.tesseract_local.canonical.json"
    assert hybrid_calls["evidence_provider_hints"] == {str(evidence[0]): "json"}
    assert "QA gates: 100.00% (12/12 measured gates)" in result.output


def test_convert_without_local_engine_fails_with_guidance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docreconstruct.cli as cli_module

    monkeypatch.setattr(cli_module, "_installed_local_engines", lambda: [])
    source = _scan(tmp_path)

    result = runner.invoke(cli, ["convert", str(source)])

    assert result.exit_code == 2
    assert "Tesseract" in result.output
    assert "--ocr-provider" in result.output


def test_convert_hosted_override_requires_explicit_cloud_consent(tmp_path: Path) -> None:
    source = _scan(tmp_path)

    result = runner.invoke(cli, ["convert", str(source), "--ocr-provider", "mathpix"])

    assert result.exit_code == 2
    assert "--allow-cloud" in result.output


def test_convert_builds_valid_docx_and_keeps_intermediates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docreconstruct.cli as cli_module
    import docreconstruct.extraction as extraction_module

    monkeypatch.setattr(cli_module, "_installed_local_engines", lambda: ["tesseract_local"])
    captured: dict[str, Any] = {}
    monkeypatch.setattr(extraction_module, "extract_to_markdown", _fake_extractor(captured))
    source = _scan(tmp_path)
    output = tmp_path / "out.docx"

    result = runner.invoke(cli, ["convert", str(source), str(output), "--keep-intermediates"])

    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(output) as package:
        document_xml = package.read("word/document.xml").decode("utf-8")
    assert "Recognized scan wording." in document_xml
    workspace = tmp_path / "out.convert"
    assert (workspace / "scan.ocr.md").is_file()
    assert (workspace / "extraction.run.json").is_file()
    assert list((workspace / "evidence").glob("*.json"))
    assert "QA gates:" in result.output
    assert "Kept intermediates" in result.output


def test_convert_cleans_intermediates_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docreconstruct.cli as cli_module
    import docreconstruct.extraction as extraction_module

    monkeypatch.setattr(cli_module, "_installed_local_engines", lambda: ["tesseract_local"])
    captured: dict[str, Any] = {}
    monkeypatch.setattr(extraction_module, "extract_to_markdown", _fake_extractor(captured))
    source = _scan(tmp_path)
    output = tmp_path / "out.docx"

    result = runner.invoke(cli, ["convert", str(source), str(output)])

    assert result.exit_code == 0, result.output
    assert output.is_file()
    assert not (tmp_path / "out.convert").exists()
    assert not Path(captured["output"]).exists()
    assert "--keep-intermediates" in result.output


def test_convert_engine_plan_prefers_native_pdf_for_born_digital_pdfs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docreconstruct.cli as cli_module
    import docreconstruct.preprocessing as preprocessing_module
    from docreconstruct.preprocessing import SourceKind

    monkeypatch.setattr(cli_module, "_installed_local_engines", lambda: ["tesseract_local"])
    pdf = tmp_path / "document.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")

    def analysis(kind: SourceKind, characters: int) -> SimpleNamespace:
        return SimpleNamespace(
            kind=kind,
            pages=[SimpleNamespace(native_characters=characters)],
        )

    monkeypatch.setattr(
        preprocessing_module,
        "analyze_source",
        lambda source: analysis(SourceKind.NATIVE, 900),
    )
    engines, note = cli_module._convert_engine_plan(pdf)
    assert engines == ["native_pdf", "tesseract_local"]
    assert note is None

    monkeypatch.setattr(
        preprocessing_module,
        "analyze_source",
        lambda source: analysis(SourceKind.HYBRID, 900),
    )
    engines, note = cli_module._convert_engine_plan(pdf)
    assert engines == ["tesseract_local"]
    assert note is not None and "native_pdf" in note

    monkeypatch.setattr(
        preprocessing_module,
        "analyze_source",
        lambda source: analysis(SourceKind.SCANNED, 0),
    )
    engines, note = cli_module._convert_engine_plan(pdf)
    assert engines == ["tesseract_local"]
    assert note is None

    scan = _scan(tmp_path)
    engines, note = cli_module._convert_engine_plan(scan)
    assert engines == ["tesseract_local"]
    assert note is None


def test_convert_uses_native_text_layer_without_ocr_end_to_end(tmp_path: Path) -> None:
    import json

    pymupdf = pytest.importorskip("pymupdf")

    figure = tmp_path / "figure.png"
    Image.new("RGB", (80, 60), "navy").save(figure)
    source = tmp_path / "digital.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=612, height=792)
    page.insert_text((72, 96), "Born-digital wording stays exact.", fontsize=14)
    page.insert_text((72, 128), "No OCR engine touches this file.", fontsize=12)
    page.insert_image(pymupdf.Rect(72, 160, 232, 280), filename=str(figure))
    pdf.save(str(source))
    pdf.close()
    output = tmp_path / "digital.docx"

    result = runner.invoke(cli, ["convert", str(source), str(output), "--keep-intermediates"])

    assert result.exit_code == 0, result.output
    assert "OCR: native_pdf; mode: local" in result.output
    with zipfile.ZipFile(output) as package:
        document_xml = package.read("word/document.xml").decode("utf-8")
    assert "Born-digital wording stays exact." in document_xml
    workspace = tmp_path / "digital.convert"
    evidence_files = list((workspace / "evidence").glob("*.json"))
    assert evidence_files
    evidence = json.loads(evidence_files[0].read_text(encoding="utf-8"))
    image_elements = [
        element
        for page_payload in evidence["pages"]
        for element in page_payload["elements"]
        if element["type"] == "image"
    ]
    assert image_elements
    assert all(
        "bytes" not in (element.get("metadata", {}).get("image") or {})
        for element in image_elements
    )


@pytest.mark.parametrize(("strict", "expected_code"), [(False, 0), (True, 3)])
def test_convert_reports_failed_qa_gates_and_only_strict_mode_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strict: bool,
    expected_code: int,
) -> None:
    import docreconstruct.cli as cli_module
    import docreconstruct.extraction as extraction_module
    import docreconstruct.reconstruction.hybrid_job as job_module

    monkeypatch.setattr(cli_module, "_installed_local_engines", lambda: ["tesseract_local"])
    monkeypatch.setattr(extraction_module, "extract_to_markdown", _fake_extractor({}))

    def failing_run_hybrid_job(content: Path, layout: Path, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            reconstruction=SimpleNamespace(output=SimpleNamespace(path=Path(kwargs["output"]))),
            validation=SimpleNamespace(score=0.9, passed_gates=27, measured_gates=30, passed=False),
        )

    monkeypatch.setattr(job_module, "run_hybrid_job", failing_run_hybrid_job)
    source = _scan(tmp_path)
    arguments = ["convert", str(source), str(tmp_path / "out.docx")]
    if strict:
        arguments.append("--strict-qa")

    result = runner.invoke(cli, arguments)

    assert result.exit_code == expected_code, result.output
    assert "QA gates: 90.00% (27/30 measured gates)" in result.output
    assert "3 QA gate(s) failed" in result.output


def _tesseract_available() -> bool:
    try:
        from docreconstruct.providers.tesseract_local import _find_tesseract

        _find_tesseract(None)
    except Exception:  # noqa: BLE001 - absence, not failure
        return False
    return True


@pytest.mark.skipif(
    not _tesseract_available(),
    reason="local Tesseract executable is not installed",
)
def test_convert_runs_real_local_ocr_end_to_end(tmp_path: Path) -> None:
    showcase = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "showcases"
        / "math-exam"
        / "source-original.png"
    )
    output = tmp_path / "converted.docx"

    result = runner.invoke(cli, ["convert", str(showcase), str(output)])

    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(output) as package:
        assert "word/document.xml" in package.namelist()
    assert "QA gates:" in result.output
