from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest
from PIL import Image, ImageDraw
from typer.testing import CliRunner

from docreconstruct.cli import cli
from docreconstruct.ir import (
    BBox,
    Document,
    Element,
    ElementStyle,
    ElementType,
    Page,
    Provenance,
    TextAlignment,
)
from docreconstruct.reconstruction import reconstruct_hybrid
from docreconstruct.reconstruction.evidence_matching import match_sidecar_evidence
from docreconstruct.reconstruction.hybrid_docx import render_hybrid_docx
from docreconstruct.reconstruction.hybrid_planner import (
    build_hybrid_layout_plan,
    source_row_reading_order,
    visual_text_row_groups,
)
from docreconstruct.reconstruction.markdown_content import (
    MarkdownBlockKind,
    parse_markdown_content,
)
from docreconstruct.reconstruction.scan_layout import (
    PixelBox,
    ScanDocumentLayout,
    ScanPageLayout,
    ScanTextLine,
)

_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _scan(tmp_path: Path) -> ScanDocumentLayout:
    image = Image.new("RGB", (600, 849), "white")
    page = ScanPageLayout(
        number=1,
        width=600,
        height=849,
        pdf_width=595.28,
        pdf_height=841.89,
        content_bbox=PixelBox(x0=50, y0=70, x1=550, y1=780),
        line_pitch=28,
        text_lines=[
            ScanTextLine(
                bbox=PixelBox(x0=70, y0=100, x1=420, y1=120),
                segments=[],
                ink_density=0.2,
            ),
            ScanTextLine(
                bbox=PixelBox(x0=70, y0=180, x1=450, y1=200),
                segments=[],
                ink_density=0.2,
            ),
        ],
        image=image,
        metadata={"source_kind": "image", "rectified": False},
    )
    return ScanDocumentLayout(source=str(tmp_path / "original.png"), pages=[page])


def _document() -> Document:
    return Document(
        id="saved-ocr",
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
                        bbox=BBox(x0=70, y0=96, x1=420, y1=122),
                        text="First editable paragraph.",
                        reading_order=0,
                        confidence=0.96,
                        style=ElementStyle(
                            font_weight=700,
                            alignment=TextAlignment.CENTER,
                        ),
                        provenance=Provenance(engine="paddleocr"),
                    ),
                    Element(
                        id="line-2",
                        type=ElementType.PARAGRAPH,
                        bbox=BBox(x0=70, y0=176, x1=450, y1=202),
                        text="Second editable paragraph.",
                        reading_order=1,
                        confidence=0.94,
                        provenance=Provenance(engine="paddleocr"),
                    ),
                ],
            )
        ],
        metadata={"provider": "paddleocr"},
    )


def test_json_evidence_seeds_planner_geometry_and_native_style(tmp_path: Path) -> None:
    markdown = tmp_path / "content.md"
    markdown.write_text(
        "First editable paragraph.\n\nSecond editable paragraph.\n",
        encoding="utf-8",
    )
    content = parse_markdown_content(markdown)
    scan = _scan(tmp_path)

    matches = match_sidecar_evidence(content, scan, _document())
    plan = build_hybrid_layout_plan(content, scan, [], [], evidence_matches=matches)

    assert len(matches) == 2
    assert [placement.geometry_source for placement in plan.pages[0].placements] == [
        "json_consensus",
        "json_consensus",
    ]
    assert plan.pages[0].placements[0].evidence_providers == ("paddleocr",)
    assert plan.pages[0].placements[0].source_bbox == PixelBox(x0=70, y0=96, x1=420, y1=122)
    assert plan.pages[0].placements[0].source_rows == [PixelBox(x0=70, y0=100, x1=420, y1=120)]

    payload = render_hybrid_docx(content, scan, plan, [])
    with zipfile.ZipFile(io.BytesIO(payload)) as package:
        root = ElementTree.fromstring(package.read("word/document.xml"))
    first = next(root.iter(_WORD + "p"))
    justification = first.find(f"{_WORD}pPr/{_WORD}jc")
    assert justification is not None
    assert justification.get(_WORD + "val") == "center"
    assert first.find(f".//{_WORD}b") is not None


def test_column_local_evidence_anchor_preserves_other_column_rows(tmp_path: Path) -> None:
    columns = [
        PixelBox(x0=20, y0=80, x1=190, y1=300),
        PixelBox(x0=210, y0=80, x1=380, y1=300),
        PixelBox(x0=400, y0=80, x1=570, y1=300),
    ]
    segments = [PixelBox(x0=box.x0, y0=100, x1=box.x1, y1=112) for box in columns]
    page = ScanPageLayout(
        number=1,
        width=600,
        height=400,
        pdf_width=595.28,
        pdf_height=396.85,
        content_bbox=PixelBox(x0=20, y0=40, x1=570, y1=340),
        line_pitch=16,
        text_lines=[
            ScanTextLine(
                bbox=PixelBox(x0=20, y0=100, x1=570, y1=112),
                segments=segments,
                ink_density=0.2,
            )
        ],
        image=Image.new("RGB", (600, 400), "white"),
        metadata={
            "source_kind": "image",
            "rectified": False,
            "column_count": 3,
            "column_boxes": [[box.x0, box.y0, box.x1, box.y1] for box in columns],
        },
    )

    groups = visual_text_row_groups(page, [segments[0]])
    rows = [row for group in groups for row in group]

    assert rows == segments[1:]
    assert source_row_reading_order(page, list(reversed(segments))) == segments


def test_markdown_thematic_break_renders_as_native_rule_not_literal_text(
    tmp_path: Path,
) -> None:
    markdown = tmp_path / "rule.md"
    markdown.write_text("# Editable title\n\n---\n\nEditable body.\n", encoding="utf-8")
    content = parse_markdown_content(markdown)
    scan = _scan(tmp_path)
    plan = build_hybrid_layout_plan(content, scan, [], [])

    assert [block.kind for block in content.blocks] == [
        MarkdownBlockKind.HEADING,
        MarkdownBlockKind.RULE,
        MarkdownBlockKind.PARAGRAPH,
    ]
    payload = render_hybrid_docx(content, scan, plan, [])
    with zipfile.ZipFile(io.BytesIO(payload)) as package:
        root = ElementTree.fromstring(package.read("word/document.xml"))

    assert "---" not in "".join(node.text or "" for node in root.iter(_WORD + "t"))
    assert next(root.iter(_WORD + "pBdr"), None) is not None


def test_reconstruct_hybrid_uses_all_three_sources_and_reports_provenance(
    tmp_path: Path,
) -> None:
    pytest.importorskip("numpy")
    markdown = tmp_path / "content.md"
    markdown.write_text(
        "First editable paragraph.\n\nSecond editable paragraph.\n",
        encoding="utf-8",
    )
    layout = tmp_path / "original.png"
    image = Image.new("RGB", (600, 849), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 96, 420, 122), fill="black")
    draw.rectangle((70, 176, 450, 202), fill="black")
    image.save(layout)
    sidecar = tmp_path / "paddle.json"
    sidecar.write_text(_document().model_dump_json(), encoding="utf-8")
    output = tmp_path / "editable.docx"

    result = reconstruct_hybrid(
        markdown,
        layout,
        evidence=[sidecar],
        output=output,
    )

    assert output.is_file()
    assert [item.path for item in result.manifest.evidence] == [str(sidecar.resolve())]
    assert result.evidence_summary is not None
    assert result.evidence_summary.providers == ["paddleocr"]
    assert result.evidence_summary.matched_blocks == 2
    assert result.evidence_summary.geometry_matches == 2
    with zipfile.ZipFile(output) as package:
        document_xml = package.read("word/document.xml").decode("utf-8")
    assert "First editable paragraph." in document_xml
    assert "Second editable paragraph." in document_xml


def test_strict_three_source_mode_rejects_unrelated_json(tmp_path: Path) -> None:
    pytest.importorskip("numpy")
    markdown = tmp_path / "content.md"
    markdown.write_text("Authoritative wording", encoding="utf-8")
    layout = tmp_path / "original.png"
    Image.new("RGB", (600, 849), "white").save(layout)
    unrelated = _document().model_copy(
        update={
            "pages": [
                _document()
                .pages[0]
                .model_copy(
                    update={
                        "elements": [
                            _document()
                            .pages[0]
                            .elements[0]
                            .model_copy(update={"text": "Completely different document"})
                        ]
                    }
                )
            ]
        }
    )
    sidecar = tmp_path / "unrelated.json"
    sidecar.write_text(json.dumps(unrelated.model_dump(mode="json")), encoding="utf-8")

    with pytest.raises(ValueError, match="did not match any Markdown block"):
        reconstruct_hybrid(markdown, layout, evidence=[sidecar])


def test_hybrid_cli_accepts_repeatable_json_evidence_in_one_command(
    tmp_path: Path,
) -> None:
    pytest.importorskip("numpy")
    markdown = tmp_path / "content.md"
    markdown.write_text(
        "First editable paragraph.\n\nSecond editable paragraph.\n",
        encoding="utf-8",
    )
    layout = tmp_path / "original.png"
    image = Image.new("RGB", (600, 849), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 96, 420, 122), fill="black")
    draw.rectangle((70, 176, 450, 202), fill="black")
    image.save(layout)
    sidecar = tmp_path / "canonical.json"
    sidecar.write_text(_document().model_dump_json(), encoding="utf-8")
    output = tmp_path / "cli.docx"
    report = tmp_path / "cli.qa.json"

    result = CliRunner().invoke(
        cli,
        [
            "hybrid",
            str(markdown),
            str(layout),
            "--evidence",
            str(sidecar),
            "--evidence-provider",
            "json",
            "--output",
            str(output),
            "--qa-report",
            str(report),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output.is_file()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["metrics"]["evidence_inputs"] == 1
    assert payload["metrics"]["evidence_matched_blocks"] == 2
    assert "JSON evidence: 2 matched block(s)" in result.output
