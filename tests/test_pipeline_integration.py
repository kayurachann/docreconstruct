from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from docreconstruct import BBox, Document, Element, ElementType, Page, reconstruct
from docreconstruct.pipeline import analyze, export


def test_image_ingestion_is_lossless_and_does_not_invent_text(tmp_path: Path) -> None:
    source = tmp_path / "scan.png"
    Image.new("RGB", (64, 32), "white").save(source)

    document = analyze(source)

    assert len(document.pages) == 1
    assert document.pages[0].width == 64
    assert document.pages[0].elements[0].type is ElementType.IMAGE
    assert document.pages[0].elements[0].text is None
    assert document.metadata["pipeline"]["providers"] == ["source_image"]

    html_path = export(document, tmp_path / "scan.html")
    html = html_path.read_text(encoding="utf-8")
    assert "data:image/png;base64," in html
    assert "dr-page" in html


def test_json_pipeline_preserves_exact_text_and_records_plan(tmp_path: Path) -> None:
    exact_text = "Total Revenue: $12,850,000 & unchanged"
    source_document = Document(
        id="example",
        pages=[
            Page(
                id="page-1",
                number=1,
                width=600,
                height=800,
                elements=[
                    Element(
                        id="heading-1",
                        type=ElementType.HEADING,
                        bbox=BBox(x0=40, y0=30, x1=560, y1=80),
                        text=exact_text,
                    )
                ],
            )
        ],
    )
    source = tmp_path / "document.json"
    source.write_text(source_document.model_dump_json(indent=2), encoding="utf-8")
    output = tmp_path / "document.html"

    reconstructed = reconstruct(source, output=output, output_format="html")

    assert reconstructed.pages[0].elements[0].text == exact_text
    assert reconstructed.metadata["reconstruction_plan"]["target"] == "html"
    rendered = output.read_text(encoding="utf-8")
    assert "Total Revenue: $12,850,000 &amp; unchanged" in rendered


def test_auto_export_falls_back_only_when_renderer_is_not_registered(tmp_path: Path) -> None:
    document = Document(id="empty", pages=[])
    destination = export(document, tmp_path / "result", output_format="auto")

    assert destination.is_file()
    assert destination.suffix in {".docx", ".html"}


def test_auto_export_respects_an_explicit_destination_extension(tmp_path: Path) -> None:
    document = Document(id="empty", pages=[])
    destination = export(document, tmp_path / "result.json", output_format="auto")

    assert destination.suffix == ".json"
    assert '"schema_version"' in destination.read_text(encoding="utf-8")


def test_saved_ocr_sidecar_uses_original_image_dimensions(tmp_path: Path) -> None:
    source = tmp_path / "scan.png"
    Image.new("RGB", (100, 200), "white").save(source)
    sidecar = tmp_path / "scan.png.paddleocr.json"
    sidecar.write_text(
        json.dumps(
            {
                "rec_texts": ["Exact OCR"],
                "rec_boxes": [[5, 6, 55, 20]],
                "rec_scores": [0.97],
            }
        ),
        encoding="utf-8",
    )

    document = analyze(source, engines=["paddleocr"])

    assert document.pages[0].width == 100
    assert document.pages[0].height == 200
    assert document.pages[0].elements[0].text == "Exact OCR"
    assert document.pages[0].elements[0].provenance.engine == "paddleocr"


def test_native_pdf_runs_through_source_analysis_ir_and_html(tmp_path: Path) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    source = tmp_path / "native.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=320, height=240)
    page.insert_text((30, 50), "Exact native PDF text", fontsize=14)
    pdf.save(source)
    pdf.close()

    document = analyze(source, engines="auto")
    output = export(document, tmp_path / "native.html")

    assert document.metadata["pipeline"]["providers"] == ["native_pdf"]
    assert document.pages[0].source_type.value == "native"
    assert any(element.text == "Exact native PDF text" for element in document.pages[0].elements)
    assert "Exact native PDF text" in output.read_text(encoding="utf-8")
