from __future__ import annotations

import json
import zipfile
from pathlib import Path
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
from docreconstruct.reconstruction.hybrid_job import (
    OnlineOCRRequest,
    run_hybrid_job,
)


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    content = tmp_path / "content.md"
    content.write_text("Authoritative editable wording.\n", encoding="utf-8")
    layout = tmp_path / "layout.png"
    image = Image.new("RGB", (600, 849), "white")
    ImageDraw.Draw(image).rectangle((70, 96, 430, 122), fill="black")
    image.save(layout)
    return content, layout


def _evidence_document() -> Document:
    return Document(
        id="online-evidence",
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
                        text="Authoritative editable wording.",
                        confidence=0.97,
                        provenance=Provenance(engine="fixture_cloud"),
                    )
                ],
            )
        ],
        metadata={"provider": "fixture_cloud"},
    )


def _fake_extractor(captured: dict[str, Any]) -> Any:
    def fake_extract(source: Path, **kwargs: Any) -> ExtractionResult:
        captured["source"] = source
        captured.update(kwargs)
        generated = Path(kwargs["output"])
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text("WRONG hosted wording must not become authority.\n", encoding="utf-8")
        evidence_directory = Path(kwargs["evidence_directory"])
        evidence_directory.mkdir(parents=True, exist_ok=True)
        sidecar = evidence_directory / "layout.fixture_cloud.canonical.json"
        document = _evidence_document()
        sidecar.write_text(document.model_dump_json(), encoding="utf-8")
        import hashlib

        source_sha = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        evidence_sha = hashlib.sha256(sidecar.read_bytes()).hexdigest()
        manifest = ExtractionRunManifest(
            source=str(source),
            source_sha256=source_sha,
            mode=ExtractionMode.CLOUD,
            cloud_authorized=True,
            requested_providers=["fixture_cloud"],
            selected_providers=["fixture_cloud"],
            successful_providers=["fixture_cloud"],
            attempts=[
                ProviderAttempt(
                    provider="fixture_cloud",
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


def test_online_hybrid_uses_generated_json_but_never_generated_wording(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docreconstruct.reconstruction.hybrid_job as job_module

    content, layout = _sources(tmp_path)
    output = tmp_path / "editable.docx"
    qa_report = tmp_path / "editable.qa.json"
    artifacts = tmp_path / "ocr-artifacts"
    captured: dict[str, Any] = {}

    monkeypatch.setattr(job_module, "extract_to_markdown", _fake_extractor(captured))
    result = run_hybrid_job(
        content,
        layout,
        output=output,
        online_ocr=OnlineOCRRequest(
            providers=("fixture_cloud",),
            allow_cloud=True,
            artifacts_directory=artifacts,
        ),
        qa_report=qa_report,
    )

    assert result.validation.passed
    assert result.extraction is not None
    assert result.extraction_report == artifacts / "extraction.run.json"
    assert result.generated_markdown == artifacts / "layout.online-ocr.md"
    assert captured["require_geometry"] is True
    assert captured["allow_cloud"] is True
    assert captured["cache_directory"] == artifacts / "cache"
    assert qa_report.is_file()
    assert json.loads(qa_report.read_text(encoding="utf-8"))["passed"] is True
    with zipfile.ZipFile(output) as package:
        document_xml = package.read("word/document.xml").decode("utf-8")
    assert "Authoritative editable wording." in document_xml
    assert "WRONG hosted wording" not in document_xml


def test_hybrid_cli_runs_online_ocr_evidence_render_and_qa_in_one_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docreconstruct.reconstruction.hybrid_job as job_module

    content, layout = _sources(tmp_path)
    output = tmp_path / "cli-editable.docx"
    report = tmp_path / "cli-editable.qa.json"
    artifacts = tmp_path / "cli-ocr"
    captured: dict[str, Any] = {}
    monkeypatch.setattr(job_module, "extract_to_markdown", _fake_extractor(captured))

    result = CliRunner().invoke(
        cli,
        [
            "hybrid",
            str(content),
            str(layout),
            "--online-ocr",
            "--allow-cloud",
            "--ocr-provider",
            "fixture_cloud",
            "--ocr-artifacts-dir",
            str(artifacts),
            "--qa-report",
            str(report),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output.is_file()
    assert report.is_file()
    assert (artifacts / "extraction.run.json").is_file()
    assert (artifacts / "evidence" / "layout.fixture_cloud.canonical.json").is_file()
    assert captured["providers"] == ("fixture_cloud",)
    assert captured["require_geometry"] is True
    assert "online OCR evidence: fixture_cloud; provider run" in result.output
    assert "QA gates: 100.00%" in result.output


def test_online_ocr_cloud_policy_requires_explicit_upload_consent(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="allow_cloud must be true"):
        OnlineOCRRequest(
            mode=ExtractionMode.CLOUD,
            artifacts_directory=tmp_path / "artifacts",
        )


def test_hybrid_cli_rejects_online_upload_without_explicit_consent(tmp_path: Path) -> None:
    content, layout = _sources(tmp_path)
    output = tmp_path / "must-not-exist.docx"

    result = CliRunner().invoke(
        cli,
        [
            "hybrid",
            str(content),
            str(layout),
            "--online-ocr",
            "--ocr-provider",
            "mistral_ocr",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 2
    assert "allow_cloud must be true" in result.output
    assert not output.exists()


def test_offline_hybrid_job_never_runs_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docreconstruct.reconstruction.hybrid_job as job_module

    content, layout = _sources(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("offline hybrid job must not call OCR extraction")

    monkeypatch.setattr(job_module, "extract_to_markdown", forbidden)
    result = run_hybrid_job(content, layout, output=tmp_path / "offline.docx")

    assert result.extraction is None
    assert result.validation.passed
    assert result.evidence == ()
