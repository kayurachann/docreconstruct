from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest
from PIL import Image, ImageDraw

from docreconstruct.evaluation.hybrid_validation import (
    HybridValidationGate,
    HybridValidationReport,
    validate_hybrid,
)
from docreconstruct.ir import BBox, Document, Element, ElementType, Page
from docreconstruct.reconstruction import reconstruct_hybrid
from docreconstruct.reconstruction.evidence_matching import (
    EvidenceMatch,
    match_sidecar_evidence,
)
from docreconstruct.reconstruction.hybrid_docx import render_hybrid_docx
from docreconstruct.reconstruction.hybrid_planner import (
    HybridBlockPlacement,
    HybridPagePlan,
    build_hybrid_layout_plan,
)
from docreconstruct.reconstruction.markdown_content import (
    MarkdownBlock,
    MarkdownBlockKind,
    MarkdownContent,
    parse_markdown_content,
)
from docreconstruct.reconstruction.scan_layout import (
    PixelBox,
    ScanDocumentLayout,
    ScanPageLayout,
    analyze_scan_source,
)

_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _two_page_scan_pdf(path: Path) -> tuple[tuple[tuple[int, int, int, int], ...], ...]:
    pymupdf = pytest.importorskip("pymupdf")
    page_boxes = (
        ((60, 80, 340, 100), (60, 160, 300, 180), (60, 240, 300, 260)),
        ((60, 100, 300, 120), (60, 200, 300, 220)),
    )
    document = pymupdf.open()
    try:
        for boxes in page_boxes:
            image = Image.new("RGB", (400, 600), "white")
            draw = ImageDraw.Draw(image)
            for box in boxes:
                draw.rectangle(box, fill="black")
            stream = io.BytesIO()
            image.save(stream, format="PNG")
            page = document.new_page(width=400, height=600)
            page.insert_image(page.rect, stream=stream.getvalue())
        document.save(path)
    finally:
        document.close()
    return page_boxes


def _section_text(payload: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(payload)) as package:
        root = ElementTree.fromstring(package.read("word/document.xml"))
    body = root.find(_WORD + "body")
    assert body is not None
    sections = [""]
    for child in body:
        sections[-1] += "".join(node.text or "" for node in child.iter(_WORD + "t"))
        if child.find(".//" + _WORD + "sectPr") is not None:
            sections.append("")
    # The final body-level sectPr describes the current section rather than
    # starting another one.
    return sections[:-1] if sections and not sections[-1] else sections


def _gate(report: HybridValidationReport, name: str) -> HybridValidationGate:
    return next(gate for gate in report.gates if gate.name == name)


def test_multi_page_pdf_json_group_keeps_blocks_on_their_source_pages(
    tmp_path: Path,
) -> None:
    markdown_path = tmp_path / "content.md"
    markdown_path.write_text(
        "Question 1: A prompt continuing across pages.\n\n"
        "A. First choice\n\n"
        "B. Second choice\n\n"
        "C. Third choice\n\n"
        "D. Fourth choice\n",
        encoding="utf-8",
    )
    content = parse_markdown_content(markdown_path)
    assert len({block.group_id for block in content.blocks}) == 1

    layout_path = tmp_path / "layout.pdf"
    page_boxes = _two_page_scan_pdf(layout_path)
    page_block_indices = ((0, 1, 2), (3, 4))
    evidence_document = Document(
        id="two-page-evidence",
        pages=[
            Page(
                id=f"page-{page_number}",
                number=page_number,
                width=400,
                height=600,
                elements=[
                    Element(
                        id=f"element-{block_index + 1}",
                        type=ElementType.PARAGRAPH,
                        bbox=BBox.from_sequence(page_boxes[page_number - 1][element_index]),
                        text=content.blocks[block_index].text,
                        reading_order=element_index,
                        confidence=0.99,
                    )
                    for element_index, block_index in enumerate(page_block_indices[page_number - 1])
                ],
            )
            for page_number in (1, 2)
        ],
        metadata={"provider": "json"},
    )
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(evidence_document.model_dump_json(), encoding="utf-8")
    output = tmp_path / "result.docx"

    result = reconstruct_hybrid(
        markdown_path,
        layout_path,
        evidence=evidence_path,
        evidence_provider_hints="json",
        output=output,
    )

    assert result.evidence_summary is not None
    assert result.evidence_summary.matched_blocks == 5
    scan = analyze_scan_source(layout_path)
    matches = match_sidecar_evidence(content, scan, evidence_document)
    plan = build_hybrid_layout_plan(content, scan, [], [], evidence_matches=matches)
    assert [[item.block_index for item in page.placements] for page in plan.pages] == [
        [0, 1, 2],
        [3, 4],
    ]
    section_text = _section_text(output.read_bytes())
    assert len(section_text) == 2
    assert "First choice" in section_text[0]
    assert "Third choice" not in section_text[0]
    assert "Third choice" in section_text[1]

    report = validate_hybrid(
        markdown_path,
        layout_path,
        output,
        evidence=[evidence_path],
        evidence_provider_hints="json",
    )
    assert report.metrics["source_pages"] == 2
    assert report.metrics["docx_sections"] == 2
    assert _gate(report, "planned_page_sections").passed
    assert _gate(report, "evidence_alignment_used").passed


def test_source_page_without_markdown_content_remains_an_empty_word_section() -> None:
    block = MarkdownBlock(
        id="md-1",
        index=0,
        kind=MarkdownBlockKind.PARAGRAPH,
        text="Editable content begins on source page two.",
    )
    content = MarkdownContent(source="content.md", blocks=[block])
    pages = [
        ScanPageLayout(
            number=number,
            width=400,
            height=600,
            pdf_width=400,
            pdf_height=600,
            content_bbox=PixelBox(x0=20, y0=20, x1=380, y1=580),
            line_pitch=20,
            image=Image.new("RGB", (400, 600), "white"),
            metadata={"source_kind": "pdf"},
        )
        for number in (1, 2)
    ]
    layout = ScanDocumentLayout(source="layout.pdf", pages=pages)
    row = PixelBox(x0=50, y0=100, x1=350, y1=120)
    evidence = EvidenceMatch(
        block_id=block.id,
        block_index=block.index,
        page_number=2,
        source_bbox=row,
        source_rows=[row],
        match_score=1.0,
        confidence=1.0,
    )

    plan = build_hybrid_layout_plan(content, layout, [], [], evidence_matches=[evidence])

    assert [page.placements for page in plan.pages[:1]] == [[]]
    assert [item.block_id for item in plan.pages[1].placements] == [block.id]
    section_text = _section_text(render_hybrid_docx(content, layout, plan, []))
    assert section_text == ["", block.text]

    unanchored = build_hybrid_layout_plan(content, layout, [], [])
    assert [item.block_id for item in unanchored.pages[0].placements] == [block.id]
    assert unanchored.pages[1].placements == []


def test_page_plan_rejects_a_block_bound_to_a_different_source_page() -> None:
    with pytest.raises(ValueError, match="bound to another page"):
        HybridPagePlan(
            number=1,
            pdf_width=400,
            pdf_height=600,
            raster_width=400,
            raster_height=600,
            content_bbox=PixelBox(x0=20, y0=20, x1=380, y1=580),
            line_pitch=20,
            placements=[
                HybridBlockPlacement(
                    block_id="md-1",
                    block_index=0,
                    page_number=2,
                )
            ],
        )
