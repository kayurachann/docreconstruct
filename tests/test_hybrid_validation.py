from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree

import pytest
from PIL import Image, ImageDraw
from typer.testing import CliRunner

import docreconstruct.evaluation.hybrid_validation as hybrid_validation
from docreconstruct.cli import cli
from docreconstruct.evaluation import (
    DocumentRenderResult,
    HybridValidationGate,
    HybridValidationReport,
    validate_hybrid,
)
from docreconstruct.evidence import SidecarEvidenceError
from docreconstruct.providers.mistral_ocr import MistralOCRProvider
from docreconstruct.reconstruction import reconstruct_hybrid
from docreconstruct.reconstruction.hybrid_planner import (
    HybridBlockPlacement,
    HybridLayoutPlan,
    HybridPagePlan,
)
from docreconstruct.reconstruction.markdown_content import (
    MarkdownBlock,
    MarkdownBlockKind,
    MarkdownContent,
)
from docreconstruct.reconstruction.scan_layout import (
    PixelBox,
    ScanDocumentLayout,
    ScanPageLayout,
)

_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MATH = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    markdown = tmp_path / "content.md"
    markdown.write_text(
        "方法二\n\n"
        "$$ \\begin{aligned}&=\\frac{x}{2}\\\\&=1\\end{aligned} $$\n\n"
        "由泰勒公式得 $ e^x=1+x $\n",
        encoding="utf-8",
    )
    layout = tmp_path / "layout.png"
    image = Image.new("RGB", (600, 840), "white")
    draw = ImageDraw.Draw(image)
    for top, left, right in (
        (70, 65, 180),
        (145, 120, 500),
        (215, 145, 460),
        (300, 65, 535),
    ):
        draw.rectangle((left, top, right, top + 10), fill="black")
    image.save(layout)
    return markdown, layout


def _physical_page_sizes(layout: Path) -> tuple[tuple[float, float], ...]:
    scan = hybrid_validation.analyze_scan_source(layout)
    return tuple((float(page.pdf_width), float(page.pdf_height)) for page in scan.pages)


def test_expected_furniture_reuses_multilingual_weak_bbox_footer_partition(
    tmp_path: Path,
) -> None:
    texts = [
        "Editable first page.",
        "Open: https://example.invalid/source",
        "Page 1/2",
        "Editable second page.",
        "Open: https://example.invalid/source",
        "Страница 2/2",
        "Repeated top banner.",
    ]
    blocks = [
        MarkdownBlock(
            id=f"md-{index}",
            index=index,
            kind=MarkdownBlockKind.PARAGRAPH,
            text=text,
        )
        for index, text in enumerate(texts)
    ]
    content = MarkdownContent(source=str(tmp_path / "content.md"), blocks=blocks)
    pages = [
        ScanPageLayout(
            number=number,
            width=600,
            height=800,
            pdf_width=595,
            pdf_height=793,
            content_bbox=PixelBox(x0=35, y0=20, x1=565, y1=780),
            line_pitch=30,
            image=Image.new("RGB", (600, 800), "white"),
            metadata={"source_kind": "image", "column_count": 1},
        )
        for number in (1, 2)
    ]
    layout = ScanDocumentLayout(source=str(tmp_path / "layout.png"), pages=pages)
    body_1 = PixelBox(x0=60, y0=100, x1=500, y1=124)
    link_1 = PixelBox(x0=180, y0=744, x1=500, y1=762)
    number_1 = PixelBox(x0=480, y0=764, x1=555, y1=780)
    body_2 = PixelBox(x0=60, y0=100, x1=500, y1=124)
    banner_2 = PixelBox(x0=160, y0=28, x1=500, y1=48)
    placements = [
        HybridBlockPlacement(
            block_id="md-0",
            block_index=0,
            page_number=1,
            source_bbox=body_1,
            source_rows=[body_1],
            source_gap_before=0,
        ),
        HybridBlockPlacement(
            block_id="md-1",
            block_index=1,
            page_number=1,
            source_bbox=link_1,
            source_rows=[link_1],
            source_gap_before=0,
        ),
        HybridBlockPlacement(
            block_id="md-2",
            block_index=2,
            page_number=1,
            source_bbox=number_1,
            source_rows=[number_1],
            source_gap_before=0,
        ),
        HybridBlockPlacement(
            block_id="md-3",
            block_index=3,
            page_number=2,
            source_bbox=body_2,
            source_rows=[body_2],
            source_gap_before=0,
        ),
        HybridBlockPlacement(block_id="md-4", block_index=4, page_number=2),
        HybridBlockPlacement(block_id="md-5", block_index=5, page_number=2),
        HybridBlockPlacement(
            block_id="md-6",
            block_index=6,
            page_number=2,
            source_bbox=banner_2,
            source_rows=[banner_2],
            source_gap_before=0,
        ),
    ]
    plan = HybridLayoutPlan(
        content_source=content.source,
        layout_source=layout.source,
        pages=[
            HybridPagePlan(
                number=page.number,
                pdf_width=page.pdf_width,
                pdf_height=page.pdf_height,
                raster_width=page.width,
                raster_height=page.height,
                content_bbox=page.content_bbox,
                line_pitch=page.line_pitch,
                placements=[
                    placement for placement in placements if placement.page_number == page.number
                ],
            )
            for page in pages
        ],
    )

    mastheads, footers = hybrid_validation._expected_layout_furniture(content, layout, plan)

    assert mastheads == 0
    assert footers == [
        "Open: https://example.invalid/source",
        "Page 1/2",
        "Open: https://example.invalid/source",
        "Страница 2/2",
    ]
    assert footers.count("Страница 2/2") == 1


def test_anchor_order_allows_same_group_side_visual_but_keeps_full_width_gate(
    tmp_path: Path,
) -> None:
    blocks = [
        MarkdownBlock(
            id="side-text",
            index=0,
            kind=MarkdownBlockKind.PARAGRAPH,
            text="Editable text beside a figure.",
            group_id="side-group",
        ),
        MarkdownBlock(
            id="side-image",
            index=1,
            kind=MarkdownBlockKind.IMAGE,
            text="Side image",
            group_id="side-group",
        ),
        MarkdownBlock(
            id="full-text",
            index=2,
            kind=MarkdownBlockKind.PARAGRAPH,
            text="Editable text that truly crosses a full-width anchor.",
            group_id="full-group",
        ),
        MarkdownBlock(
            id="full-image",
            index=3,
            kind=MarkdownBlockKind.IMAGE,
            text="Full image",
            group_id="full-group",
        ),
    ]
    content = MarkdownContent(source=str(tmp_path / "content.md"), blocks=blocks)
    # Coarse question geometry can extend into its right-side figure even
    # though the editable flow and figure share one native row.
    side_text = PixelBox(x0=50, y0=90, x1=450, y1=290)
    side_image = PixelBox(x0=400, y0=140, x1=550, y1=260)
    full_text = PixelBox(x0=50, y0=300, x1=550, y1=460)
    full_image = PixelBox(x0=45, y0=350, x1=555, y1=430)
    page = ScanPageLayout(
        number=1,
        width=600,
        height=800,
        pdf_width=595,
        pdf_height=793,
        content_bbox=PixelBox(x0=35, y0=20, x1=565, y1=780),
        line_pitch=30,
        image=Image.new("RGB", (600, 800), "white"),
        metadata={"source_kind": "pdf", "column_count": 1},
    )
    layout = ScanDocumentLayout(source=str(tmp_path / "layout.pdf"), pages=[page])
    boxes = [side_text, side_image, full_text, full_image]
    placements = [
        HybridBlockPlacement(
            block_id=block.id,
            block_index=block.index,
            page_number=1,
            source_bbox=box,
            source_rows=[] if block.kind is MarkdownBlockKind.IMAGE else [box],
            source_gap_before=0,
        )
        for block, box in zip(blocks, boxes, strict=True)
    ]
    plan = HybridLayoutPlan(
        content_source=content.source,
        layout_source=layout.source,
        pages=[
            HybridPagePlan(
                number=1,
                pdf_width=page.pdf_width,
                pdf_height=page.pdf_height,
                raster_width=page.width,
                raster_height=page.height,
                content_bbox=page.content_bbox,
                line_pitch=page.line_pitch,
                placements=placements,
            )
        ],
    )

    metrics = hybrid_validation._plan_geometry_metrics(content, layout, plan)

    assert metrics["source_anchor_order_violations"] == [
        {"page": 1, "block_id": "full-text", "anchor_id": "full-image"}
    ]


def _mistral_evidence(tmp_path: Path) -> Path:
    sidecar = tmp_path / "mistral-evidence.json"
    sidecar.write_text(
        json.dumps(
            {
                "model": "mistral-ocr-test",
                "pages": [
                    {
                        "index": 0,
                        "markdown": "方法二\n\n由泰勒公式得 $ e^x=1+x $",
                        "dimensions": {"width": 600, "height": 840},
                        "blocks": [
                            {
                                "id": "heading-1",
                                "type": "heading",
                                "markdown": "方法二",
                                "bbox": [65, 70, 180, 82],
                                "confidence": 0.99,
                            },
                            {
                                "id": "formula-1",
                                "type": "formula",
                                "markdown": r"\begin{aligned}&=\frac{x}{2}\\&=1\end{aligned}",
                                "latex": r"\begin{aligned}&=\frac{x}{2}\\&=1\end{aligned}",
                                "bbox": [120, 145, 500, 225],
                                "confidence": 0.97,
                            },
                            {
                                "id": "paragraph-1",
                                "type": "paragraph",
                                "markdown": "由泰勒公式得 $ e^x=1+x $",
                                "bbox": [65, 300, 535, 312],
                                "confidence": 0.98,
                            },
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return sidecar


def _gate(report: HybridValidationReport, name: str) -> HybridValidationGate:
    return next(gate for gate in report.gates if gate.name == name)


def test_docx_projection_preserves_explicit_break_and_tab_boundaries() -> None:
    root = ElementTree.fromstring(
        f"""
        <w:root xmlns:w="{_WORD[1:-1]}">
          <w:p><w:r>
            <w:t>First footer block</w:t><w:br/>
            <w:t>Second</w:t><w:tab/><w:t>block</w:t>
          </w:r></w:p>
        </w:root>
        """
    )

    assert hybrid_validation._docx_projection(root) == ("First footer block Second block")


def _advanced_math_fixture(tmp_path: Path) -> tuple[Path, Path]:
    markdown = tmp_path / "advanced-content.md"
    markdown.write_text(
        "$$ \\int_{0}^{x} e^{t^2}\\,dt="
        "\\left(\\frac{x}{2}\\right) $$\n\n"
        "由 $ \\int_{0}^{x} t\\,dt $ 可得结果\n",
        encoding="utf-8",
    )
    layout = tmp_path / "advanced-layout.png"
    image = Image.new("RGB", (600, 840), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((90, 110, 525, 124), fill="black")
    draw.rectangle((65, 360, 430, 374), fill="black")
    image.save(layout)
    return markdown, layout


def _mutate_document_xml(
    source: Path,
    destination: Path,
    mutate: Callable[[ElementTree.Element], None],
) -> None:
    with zipfile.ZipFile(source) as package, zipfile.ZipFile(destination, "w") as target:
        for member in package.infolist():
            payload = package.read(member.filename)
            if member.filename == "word/document.xml":
                root = ElementTree.fromstring(payload)
                mutate(root)
                payload = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(member, payload)


def _add_body_columns_caption(root: ElementTree.Element, count: int) -> None:
    body = root.find(_WORD + "body")
    assert body is not None
    table = ElementTree.Element(_WORD + "tbl")
    properties = ElementTree.SubElement(table, _WORD + "tblPr")
    caption = ElementTree.SubElement(properties, _WORD + "tblCaption")
    caption.set(_WORD + "val", f"docreconstruct:body-columns-{count}")
    row = ElementTree.SubElement(table, _WORD + "tr")
    cell = ElementTree.SubElement(row, _WORD + "tc")
    ElementTree.SubElement(cell, _WORD + "p")
    section = body.find(_WORD + "sectPr")
    body.insert(list(body).index(section) if section is not None else len(body), table)


def _add_populated_body_columns(
    root: ElementTree.Element,
    count: int,
    *,
    cant_split: bool = False,
    framed: bool = False,
) -> None:
    body = root.find(_WORD + "body")
    assert body is not None
    table = ElementTree.Element(_WORD + "tbl")
    properties = ElementTree.SubElement(table, _WORD + "tblPr")
    caption = ElementTree.SubElement(properties, _WORD + "tblCaption")
    caption.set(_WORD + "val", f"docreconstruct:body-columns-{count}")
    row = ElementTree.SubElement(table, _WORD + "tr")
    if cant_split:
        row_properties = ElementTree.SubElement(row, _WORD + "trPr")
        ElementTree.SubElement(row_properties, _WORD + "cantSplit")
    for index in range(count * 2 - 1):
        cell = ElementTree.SubElement(row, _WORD + "tc")
        paragraph = ElementTree.SubElement(cell, _WORD + "p")
        if framed and index == 0:
            paragraph_properties = ElementTree.SubElement(paragraph, _WORD + "pPr")
            ElementTree.SubElement(paragraph_properties, _WORD + "framePr")
        if index % 2 == 0:
            run = ElementTree.SubElement(paragraph, _WORD + "r")
            text = ElementTree.SubElement(run, _WORD + "t")
            text.text = f"Editable column {index // 2 + 1}"
    section = body.find(_WORD + "sectPr")
    body.insert(list(body).index(section) if section is not None else len(body), table)


def test_project_native_hybrid_validation_covers_cjk_math_and_geometry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markdown, layout = _fixture(tmp_path)
    output = tmp_path / "result.docx"
    reconstruct_hybrid(markdown, layout, output=output)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("native validation must not start an external renderer")

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("shutil.which", forbidden)

    report = validate_hybrid(markdown, layout, output)

    assert report.passed
    assert report.score == 1.0
    assert report.metrics["native_office_math"] == 2
    assert report.metrics["native_display_rows"] == 2
    assert report.metrics["native_equation_arrays"] == 1
    assert report.metrics["full_page_drawings"] == 0
    assert report.metrics["cjk_runs"] == report.metrics["cjk_font_mapped_runs"]
    assert report.metrics["source_visual_slot_coverage"] == 1.0
    assert report.metrics["source_geometry_coverage"] == 1.0
    assert report.metrics["mapped_vertical_span_ratio"] >= 0.95
    assert report.metrics["display_oMathPara"] == 1
    assert report.metrics["math_size_coverage"] == 1.0
    assert report.metrics["math_run_font_coverage"] == 1.0
    assert report.metrics["math_run_size_coverage"] == 1.0
    assert report.metrics["math_control_format_coverage"] == 1.0
    assert report.metrics["math_display_paragraph_mark_format_coverage"] == 1.0
    assert report.metrics["math_typography_equation_coverage"] == 1.0
    assert report.metrics["math_base_fonts"] == ["Cambria Math"]
    assert report.metrics["source_body_column_counts"] == [1]
    assert report.metrics["rendered_body_column_counts"] == []
    assert report.metrics["body_column_coverage"] == 1.0
    for name in (
        "source_visual_slot_coverage",
        "source_geometry_placements",
        "mapped_vertical_span",
        "display_math_paragraphs",
        "math_size_coverage",
        "math_typography_uniformity",
        "nary_operand_coverage",
        "integral_limit_modes",
        "native_delimiter_expectations",
        "display_spacing_not_exact",
        "native_body_columns",
    ):
        assert _gate(report, name).passed
    assert "rendered_pixel_similarity" in report.unmeasured


def test_validation_without_evidence_preserves_existing_gate_set_and_zero_metrics(
    tmp_path: Path,
) -> None:
    markdown, layout = _fixture(tmp_path)
    output = tmp_path / "without-evidence.docx"
    reconstruct_hybrid(markdown, layout, output=output)

    report = validate_hybrid(markdown, layout, output)

    assert report.passed
    assert "evidence_alignment_used" not in {gate.name for gate in report.gates}
    assert report.metrics["evidence_inputs"] == 0
    assert report.metrics["evidence_documents"] == 0
    assert report.metrics["evidence_providers"] == []
    assert report.metrics["evidence_matched_blocks"] == 0
    assert report.metrics["evidence_geometry_matches"] == 0
    assert report.metrics["evidence_fingerprints"] == []


def test_validation_rebuilds_the_evidence_aware_plan_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markdown, layout = _fixture(tmp_path)
    evidence = _mistral_evidence(tmp_path)
    output = tmp_path / "evidence-aware-validation.docx"
    reconstruct_hybrid(markdown, layout, output=output)
    original_planner = hybrid_validation.build_hybrid_layout_plan
    planned_evidence: list[object] = []

    def capture_plan(*args: object, **kwargs: object) -> object:
        planned_evidence.extend(kwargs.get("evidence_matches", []))
        return original_planner(*args, **kwargs)

    def forbidden_live_parse(*args: object, **kwargs: object) -> object:
        raise AssertionError("validation must not invoke hosted OCR or network parsing")

    monkeypatch.setattr(hybrid_validation, "build_hybrid_layout_plan", capture_plan)
    monkeypatch.setattr(MistralOCRProvider, "parse", forbidden_live_parse)

    report = validate_hybrid(markdown, layout, output, evidence=[evidence])

    assert planned_evidence
    assert _gate(report, "evidence_alignment_used").passed
    assert report.metrics["evidence_inputs"] == 1
    assert report.metrics["evidence_documents"] == 1
    assert report.metrics["evidence_providers"] == ["mistral_ocr"]
    assert report.metrics["evidence_matched_blocks"] >= 1
    assert report.metrics["evidence_geometry_matches"] >= 1
    assert report.metrics["evidence_errors"] == []
    assert report.metrics["evidence_ambiguous_detections"] == []
    assert report.metrics["evidence_fingerprints"] == [
        {
            "path": str(evidence.resolve()),
            "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            "size": evidence.stat().st_size,
        }
    ]
    assert all(
        detail["geometry_source"] == "json_consensus"
        for detail in report.metrics["evidence_matches"]
    )


def test_validation_rejects_wrong_sidecar_strictly_and_reports_it_non_strictly(
    tmp_path: Path,
) -> None:
    markdown, layout = _fixture(tmp_path)
    output = tmp_path / "wrong-evidence.docx"
    reconstruct_hybrid(markdown, layout, output=output)
    sidecar = tmp_path / "wrong.json"
    sidecar.write_text('{"unrelated": true}', encoding="utf-8")

    with pytest.raises(SidecarEvidenceError, match="could not identify JSON schema"):
        validate_hybrid(markdown, layout, output, evidence=[sidecar])

    report = validate_hybrid(
        markdown,
        layout,
        output,
        evidence=[sidecar],
        strict_evidence=False,
    )
    gate = _gate(report, "evidence_alignment_used")
    assert not gate.passed
    assert not report.passed
    assert report.metrics["evidence_documents"] == 0
    assert report.metrics["evidence_geometry_matches"] == 0
    assert report.metrics["evidence_errors"]


def test_strict_evidence_gate_rejects_ambiguous_auto_detection(tmp_path: Path) -> None:
    markdown, layout = _fixture(tmp_path)
    output = tmp_path / "ambiguous-evidence.docx"
    reconstruct_hybrid(markdown, layout, output=output)
    sidecar = tmp_path / "ambiguous.json"
    sidecar.write_text(
        json.dumps(
            {
                "natural_text": "方法二",
                "metadata": {"page_number": 1, "width": 600, "height": 840},
                "content_list": [
                    {
                        "page_idx": 0,
                        "type": "heading",
                        "text": "方法二",
                        "bbox": [65, 70, 180, 82],
                        "score": 0.99,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    strict_report = validate_hybrid(markdown, layout, output, evidence=[sidecar])
    permissive_report = validate_hybrid(
        markdown,
        layout,
        output,
        evidence=[sidecar],
        strict_evidence=False,
    )

    assert strict_report.metrics["evidence_ambiguous_detections"]
    assert strict_report.metrics["evidence_geometry_matches"] == 1
    assert not _gate(strict_report, "evidence_alignment_used").passed
    assert _gate(permissive_report, "evidence_alignment_used").passed


def test_hybrid_validation_requires_matching_tagged_native_body_columns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markdown, layout = _fixture(tmp_path)
    output = tmp_path / "body-columns-source.docx"
    reconstruct_hybrid(markdown, layout, output=output)
    original_analyze = hybrid_validation.analyze_scan_source

    def three_column_source(source: str | Path) -> object:
        scan = original_analyze(source)
        pages = [
            page.model_copy(
                update={
                    "metadata": {
                        **page.metadata,
                        "column_count": 3,
                    }
                }
            )
            for page in scan.pages
        ]
        return scan.model_copy(update={"pages": pages})

    monkeypatch.setattr(hybrid_validation, "analyze_scan_source", three_column_source)

    missing = validate_hybrid(markdown, layout, output)
    assert not _gate(missing, "native_body_columns").passed
    assert missing.metrics["source_body_column_counts"] == [3]
    assert missing.metrics["rendered_body_column_counts"] == []
    assert missing.metrics["body_column_coverage"] == 0.0

    mismatched = tmp_path / "body-columns-mismatched.docx"
    _mutate_document_xml(
        output,
        mismatched,
        lambda root: _add_body_columns_caption(root, 2),
    )
    mismatch_report = validate_hybrid(markdown, layout, mismatched)
    assert not _gate(mismatch_report, "native_body_columns").passed
    assert mismatch_report.metrics["rendered_body_column_counts"] == [2]
    assert mismatch_report.metrics["body_column_coverage"] == 0.0

    matching = tmp_path / "body-columns-matching.docx"
    _mutate_document_xml(
        output,
        matching,
        lambda root: _add_body_columns_caption(root, 3),
    )
    matching_report = validate_hybrid(markdown, layout, matching)
    assert _gate(matching_report, "native_body_columns").passed
    assert matching_report.metrics["rendered_body_column_counts"] == [3]
    assert matching_report.metrics["body_column_coverage"] == 1.0


def test_hybrid_validation_checks_body_column_payload_and_flow_safety(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markdown, layout = _fixture(tmp_path)
    output = tmp_path / "body-column-gates-source.docx"
    reconstruct_hybrid(markdown, layout, output=output)
    original_analyze = hybrid_validation.analyze_scan_source

    def three_column_source(source: str | Path) -> object:
        scan = original_analyze(source)
        pages = [
            page.model_copy(update={"metadata": {**page.metadata, "column_count": 3}})
            for page in scan.pages
        ]
        return scan.model_copy(update={"pages": pages})

    monkeypatch.setattr(hybrid_validation, "analyze_scan_source", three_column_source)

    safe = tmp_path / "body-column-safe.docx"
    _mutate_document_xml(
        output,
        safe,
        lambda root: _add_populated_body_columns(root, 3),
    )
    safe_report = validate_hybrid(markdown, layout, safe)
    assert _gate(safe_report, "native_body_columns").passed
    assert _gate(safe_report, "native_body_column_payload").passed
    assert _gate(safe_report, "native_body_column_flow_safety").passed
    assert safe_report.metrics["body_column_payload_coverage"] == 1.0
    assert safe_report.metrics["body_column_gutter_purity"] == 1.0

    empty = tmp_path / "body-column-empty.docx"
    _mutate_document_xml(
        output,
        empty,
        lambda root: _add_body_columns_caption(root, 3),
    )
    empty_report = validate_hybrid(markdown, layout, empty)
    assert _gate(empty_report, "native_body_columns").passed
    assert not _gate(empty_report, "native_body_column_payload").passed

    unsafe = tmp_path / "body-column-unsafe.docx"
    _mutate_document_xml(
        output,
        unsafe,
        lambda root: _add_populated_body_columns(
            root,
            3,
            cant_split=True,
            framed=True,
        ),
    )
    unsafe_report = validate_hybrid(markdown, layout, unsafe)
    assert _gate(unsafe_report, "native_body_column_payload").passed
    assert not _gate(unsafe_report, "native_body_column_flow_safety").passed
    assert unsafe_report.metrics["body_column_unsplittable_rows"] == 1
    assert unsafe_report.metrics["body_column_framed_paragraphs"] == 1


def test_hybrid_validation_can_enforce_project_rendered_visual_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markdown, layout = _fixture(tmp_path)
    output = tmp_path / "result.docx"
    reconstruct_hybrid(markdown, layout, output=output)
    rendered_page = layout.read_bytes()

    def exact_render(*args: object, **kwargs: object) -> DocumentRenderResult:
        return DocumentRenderResult(
            requested_backend="libreoffice",
            used_backend="libreoffice",
            status="rendered",
            pages=(rendered_page,),
            executable="project-test-backend",
            page_sizes_points=_physical_page_sizes(layout),
        )

    monkeypatch.setattr(
        "docreconstruct.evaluation.document_rendering.render_docx_pages",
        exact_render,
    )

    report = validate_hybrid(
        markdown,
        layout,
        output,
        render_backend="libreoffice",
        minimum_visual_score=0.99,
    )

    assert report.passed
    assert report.metrics["render_backend"]["status"] == "rendered"
    assert report.metrics["rendered_visual"]["dimension_similarity"] == 1.0
    assert report.metrics["rendered_visual"]["score"] == pytest.approx(1.0)
    assert _gate(report, "render_backend_available").passed
    assert _gate(report, "rendered_page_count").passed
    assert _gate(report, "rendered_visual_similarity").passed
    assert "rendered_pixel_similarity" not in report.unmeasured


def test_hybrid_validation_rejects_wrong_physical_pdf_page_box(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markdown, layout = _fixture(tmp_path)
    output = tmp_path / "wrong-page-size.docx"
    reconstruct_hybrid(markdown, layout, output=output)

    def wrong_size_render(*args: object, **kwargs: object) -> DocumentRenderResult:
        return DocumentRenderResult(
            requested_backend="libreoffice",
            used_backend="libreoffice",
            status="rendered",
            pages=(layout.read_bytes(),),
            executable="project-test-backend",
            page_sizes_points=((612.0, 792.0),),
        )

    monkeypatch.setattr(
        "docreconstruct.evaluation.document_rendering.render_docx_pages",
        wrong_size_render,
    )
    report = validate_hybrid(markdown, layout, output, render_backend="libreoffice")

    assert not report.passed
    assert not _gate(report, "rendered_physical_page_size").passed
    assert report.metrics["render_backend"]["page_sizes_points"] == [[612.0, 792.0]]


def test_rendered_page_count_gate_exports_overflow_candidate_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markdown, layout = _fixture(tmp_path)
    output = tmp_path / "overflow-result.docx"
    reconstruct_hybrid(markdown, layout, output=output)
    rendered_page = layout.read_bytes()
    extra_page = tmp_path / "extra-page.png"
    Image.new("RGB", (600, 840), "white").save(extra_page)

    def overflow_render(*args: object, **kwargs: object) -> DocumentRenderResult:
        return DocumentRenderResult(
            requested_backend="libreoffice",
            used_backend="libreoffice",
            status="rendered",
            pages=(rendered_page, extra_page.read_bytes()),
            executable="project-test-backend",
            page_sizes_points=(*_physical_page_sizes(layout), (595.28, 841.89)),
        )

    monkeypatch.setattr(
        "docreconstruct.evaluation.document_rendering.render_docx_pages",
        overflow_render,
    )
    artifact_directory = tmp_path / "overflow-render"
    report = validate_hybrid(
        markdown,
        layout,
        output,
        render_backend="libreoffice",
        render_output_dir=artifact_directory,
    )

    assert not report.passed
    assert not _gate(report, "rendered_page_count").passed
    assert report.metrics["rendered_page_count"] == 2
    overflow_artifact = report.metrics["render_artifacts"][1]
    assert overflow_artifact["page"] == "2"
    assert Path(overflow_artifact["candidate"]).is_file()
    assert "source" not in overflow_artifact
    assert "difference" not in overflow_artifact


def test_rendered_body_foreground_gate_rejects_header_only_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markdown, layout = _fixture(tmp_path)
    output = tmp_path / "foreground-result.docx"
    reconstruct_hybrid(markdown, layout, output=output)
    original_analyze = hybrid_validation.analyze_scan_source

    def three_column_source(source: str | Path) -> object:
        scan = original_analyze(source)
        pages = [
            page.model_copy(
                update={
                    "metadata": {
                        **page.metadata,
                        "column_count": 3,
                        "column_boxes": [
                            [0, 100, 190, 820],
                            [205, 100, 395, 820],
                            [410, 100, 600, 820],
                        ],
                    }
                }
            )
            for page in scan.pages
        ]
        return scan.model_copy(update={"pages": pages})

    monkeypatch.setattr(hybrid_validation, "analyze_scan_source", three_column_source)
    with Image.open(layout) as source_image:
        header_only = source_image.copy()
    ImageDraw.Draw(header_only).rectangle((0, 100, 600, 840), fill="white")
    header_only_path = tmp_path / "header-only.png"
    header_only.save(header_only_path)

    def header_only_render(*args: object, **kwargs: object) -> DocumentRenderResult:
        return DocumentRenderResult(
            requested_backend="libreoffice",
            used_backend="libreoffice",
            status="rendered",
            pages=(header_only_path.read_bytes(),),
            executable="project-test-backend",
            page_sizes_points=_physical_page_sizes(layout),
        )

    monkeypatch.setattr(
        "docreconstruct.evaluation.document_rendering.render_docx_pages",
        header_only_render,
    )
    report = validate_hybrid(
        markdown,
        layout,
        output,
        render_backend="libreoffice",
    )

    assert _gate(report, "rendered_page_count").passed
    assert not _gate(report, "rendered_body_foreground_coverage").passed
    assert report.metrics["rendered_body_foreground"]["minimum_ratio"] < 0.30


def test_hybrid_validation_detects_missing_native_math(tmp_path: Path) -> None:
    markdown, layout = _fixture(tmp_path)
    output = tmp_path / "result.docx"
    reconstruct_hybrid(markdown, layout, output=output)
    damaged = tmp_path / "damaged.docx"
    with zipfile.ZipFile(output) as source, zipfile.ZipFile(damaged, "w") as target:
        for member in source.infolist():
            payload = source.read(member.filename)
            if member.filename == "word/document.xml":
                payload = payload.replace(b"<m:oMath>", b"<m:removedMath>", 1)
                payload = payload.replace(b"</m:oMath>", b"</m:removedMath>", 1)
            target.writestr(member, payload)

    report = validate_hybrid(markdown, layout, damaged)

    assert not report.passed
    assert not _gate(report, "native_office_math_count").passed
    assert not _gate(report, "office_math_structure").passed


def test_hybrid_validation_rejects_full_page_scan_drawing(tmp_path: Path) -> None:
    docx = pytest.importorskip("docx")
    inches = pytest.importorskip("docx.shared").Inches
    markdown, layout = _fixture(tmp_path)
    scan = tmp_path / "scan.png"
    Image.new("RGB", (800, 1100), "white").save(scan)
    document = docx.Document()
    document.add_picture(str(scan), width=inches(7), height=inches(10))
    candidate = tmp_path / "flattened.docx"
    document.save(candidate)

    report = validate_hybrid(markdown, layout, candidate)

    assert not _gate(report, "no_full_page_scan").passed


def test_hybrid_validation_independently_rejects_damaged_math_ooxml(
    tmp_path: Path,
) -> None:
    markdown, layout = _advanced_math_fixture(tmp_path)
    output = tmp_path / "advanced-result.docx"
    reconstruct_hybrid(markdown, layout, output=output)
    baseline = validate_hybrid(markdown, layout, output)

    for name in (
        "display_math_paragraphs",
        "math_size_coverage",
        "math_typography_uniformity",
        "nary_operand_coverage",
        "integral_limit_modes",
        "native_delimiter_expectations",
        "display_spacing_not_exact",
    ):
        assert _gate(baseline, name).passed

    damaged = tmp_path / "advanced-damaged.docx"

    def damage(root: ElementTree.Element) -> None:
        math_paragraph = next(root.iter(_MATH + "oMathPara"))
        math_paragraph.tag = _MATH + "removedMathPara"

        math_run = next(root.iter(_MATH + "r"))
        properties = math_run.find(_WORD + "rPr")
        assert properties is not None
        size = properties.find(_WORD + "sz")
        assert size is not None
        properties.remove(size)

        first_nary = next(root.iter(_MATH + "nary"))
        operand = first_nary.find(_MATH + "e")
        assert operand is not None
        for child in list(operand):
            operand.remove(child)
        limit_mode = first_nary.find(f"{_MATH}naryPr/{_MATH}limLoc")
        assert limit_mode is not None
        limit_mode.set(_MATH + "val", "undOvr")

        delimiter = next(root.iter(_MATH + "d"))
        delimiter_properties = delimiter.find(_MATH + "dPr")
        assert delimiter_properties is not None
        grow = delimiter_properties.find(_MATH + "grow")
        assert grow is not None
        delimiter_properties.remove(grow)

        paragraph = next(
            item for item in root.iter(_WORD + "p") if item.find(f".//{_MATH}oMath") is not None
        )
        paragraph_properties = paragraph.find(_WORD + "pPr")
        assert paragraph_properties is not None
        spacing = paragraph_properties.find(_WORD + "spacing")
        if spacing is None:
            spacing = ElementTree.SubElement(paragraph_properties, _WORD + "spacing")
        spacing.set(_WORD + "lineRule", "exact")

    _mutate_document_xml(output, damaged, damage)
    report = validate_hybrid(markdown, layout, damaged)

    assert not report.passed
    for name in (
        "display_math_paragraphs",
        "math_size_coverage",
        "math_typography_uniformity",
        "nary_operand_coverage",
        "integral_limit_modes",
        "native_delimiter_expectations",
        "display_spacing_not_exact",
    ):
        assert not _gate(report, name).passed


def test_math_typography_gate_covers_runs_controls_scripts_and_display_mark(
    tmp_path: Path,
) -> None:
    markdown, layout = _advanced_math_fixture(tmp_path)
    output = tmp_path / "typography-result.docx"
    reconstruct_hybrid(markdown, layout, output=output)
    baseline = validate_hybrid(markdown, layout, output)

    assert _gate(baseline, "math_typography_uniformity").passed
    assert baseline.metrics["math_controls"] > 0
    assert baseline.metrics["formatted_math_controls"] == baseline.metrics["math_controls"]
    assert baseline.metrics["math_typography_mismatches"] == []
    assert all(
        len(equation["base_half_point_sizes"]) == 1
        for equation in baseline.metrics["math_typography_equation_reports"]
    )

    def remove_run_font(root: ElementTree.Element) -> None:
        run = next(
            item
            for item in root.iter(_MATH + "r")
            if any((text.text or "") for text in item.iter(_MATH + "t"))
        )
        properties = run.find(_WORD + "rPr")
        assert properties is not None
        fonts = properties.find(_WORD + "rFonts")
        assert fonts is not None
        properties.remove(fonts)

    def mismatch_script_base_size(root: ElementTree.Element) -> None:
        script = next(root.iter(_MATH + "sup"))
        run = next(
            item
            for item in script.iter(_MATH + "r")
            if any((text.text or "") for text in item.iter(_MATH + "t"))
        )
        properties = run.find(_WORD + "rPr")
        assert properties is not None
        size = properties.find(_WORD + "sz")
        complex_size = properties.find(_WORD + "szCs")
        assert size is not None and complex_size is not None
        changed = str(int(size.get(_WORD + "val", "0")) + 2)
        size.set(_WORD + "val", changed)
        complex_size.set(_WORD + "val", changed)

    def remove_control_format(root: ElementTree.Element) -> None:
        for properties in root.iter():
            control = properties.find(_MATH + "ctrlPr")
            if control is not None:
                properties.remove(control)
                return
        raise AssertionError("fixture must contain a native math control format")

    def mismatch_control_base_size(root: ElementTree.Element) -> None:
        control = next(root.iter(_MATH + "ctrlPr"))
        properties = control.find(_WORD + "rPr")
        assert properties is not None
        size = properties.find(_WORD + "sz")
        complex_size = properties.find(_WORD + "szCs")
        assert size is not None and complex_size is not None
        changed = str(int(size.get(_WORD + "val", "0")) + 2)
        size.set(_WORD + "val", changed)
        complex_size.set(_WORD + "val", changed)

    def mismatch_display_mark_size(root: ElementTree.Element) -> None:
        paragraph = next(
            item for item in root.iter(_WORD + "p") if item.find(f".//{_MATH}oMathPara") is not None
        )
        properties = paragraph.find(f"{_WORD}pPr/{_WORD}rPr")
        assert properties is not None
        size = properties.find(_WORD + "sz")
        complex_size = properties.find(_WORD + "szCs")
        assert size is not None and complex_size is not None
        changed = str(int(size.get(_WORD + "val", "0")) + 2)
        size.set(_WORD + "val", changed)
        complex_size.set(_WORD + "val", changed)

    mutations = {
        "missing-run-font": remove_run_font,
        "explicit-script-size-drift": mismatch_script_base_size,
        "missing-control-format": remove_control_format,
        "control-size-drift": mismatch_control_base_size,
        "display-mark-size-drift": mismatch_display_mark_size,
    }
    for name, mutate in mutations.items():
        damaged = tmp_path / f"typography-{name}.docx"
        _mutate_document_xml(output, damaged, mutate)
        report = validate_hybrid(markdown, layout, damaged)

        assert not _gate(report, "math_typography_uniformity").passed, name
        assert report.metrics["math_typography_mismatches"], name


def test_hybrid_cli_writes_docx_and_native_qa_report_in_one_command(tmp_path: Path) -> None:
    markdown, layout = _fixture(tmp_path)
    output = tmp_path / "result.docx"
    report = tmp_path / "result.qa.json"

    result = CliRunner().invoke(
        cli,
        [
            "hybrid",
            str(markdown),
            str(layout),
            "--output",
            str(output),
            "--qa-report",
            str(report),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output.is_file()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["score"] == 1.0
    assert "QA gates: 100.00%" in result.output
