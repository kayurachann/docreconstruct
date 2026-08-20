from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import docreconstruct.evaluation.hybrid_validation as hybrid_validation
from docreconstruct.evaluation import (
    RENDER_DIFF_METRIC_VERSION,
    DocumentRenderResult,
    RenderDiffDiagnostic,
    RenderDiffKind,
    RenderDiffReport,
    RenderedObjectRegion,
    RenderPixelBox,
    localize_page_render_diff,
    validate_hybrid,
)
from docreconstruct.reconstruction import reconstruct_hybrid


def _page() -> Image.Image:
    return Image.new("RGB", (400, 500), "white")


def _paragraph(draw: ImageDraw.ImageDraw, *, top: int, right: int) -> None:
    for y in range(top, top + 51, 10):
        draw.rectangle((40, y, right, y + 3), fill="black")


def _table(draw: ImageDraw.ImageDraw, *, left: int, top: int) -> None:
    draw.rectangle((left, top, left + 250, top + 150), outline="black", width=3)
    for offset in (80, 170):
        draw.line((left + offset, top, left + offset, top + 150), fill="black", width=2)
    for offset in (50, 100):
        draw.line((left, top + offset, left + 250, top + offset), fill="black", width=2)


def _object(object_id: str, box: tuple[int, int, int, int]) -> RenderedObjectRegion:
    return RenderedObjectRegion(
        object_id=object_id,
        bbox=RenderPixelBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
    )


def _diagnostics(
    report: RenderDiffReport,
    kind: RenderDiffKind,
) -> list[RenderDiffDiagnostic]:
    return [diagnostic for diagnostic in report.diagnostics if diagnostic.kind is kind]


def test_missing_paragraph_is_localized_and_mapped_to_ir_id() -> None:
    reference = _page()
    reference_draw = ImageDraw.Draw(reference)
    _paragraph(reference_draw, top=60, right=280)
    _paragraph(reference_draw, top=250, right=330)
    candidate = _page()
    _paragraph(ImageDraw.Draw(candidate), top=60, right=280)

    report = localize_page_render_diff(
        reference,
        candidate,
        reference_regions=[_object("paragraph-2", (35, 245, 340, 310))],
    )

    missing = _diagnostics(report, RenderDiffKind.MISSING_REGION)
    assert missing
    assert any("paragraph-2" in diagnostic.object_ids for diagnostic in missing)
    assert report.metric_version == RENDER_DIFF_METRIC_VERSION
    assert report.diagnostic_counts["missing_region"] == len(missing)
    assert all(
        0.0 <= diagnostic.normalized_bbox.x0 < diagnostic.normalized_bbox.x1 <= 1.0
        for diagnostic in missing
    )


def test_shifted_table_is_one_displacement_with_shared_object_evidence() -> None:
    reference = _page()
    candidate = _page()
    _table(ImageDraw.Draw(reference), left=50, top=100)
    _table(ImageDraw.Draw(candidate), left=80, top=135)

    report = localize_page_render_diff(
        reference,
        candidate,
        reference_regions=[_object("table-1", (50, 100, 301, 251))],
        candidate_regions=[_object("table-1", (80, 135, 331, 286))],
    )

    displaced = _diagnostics(report, RenderDiffKind.DISPLACED_REGION)
    assert len(displaced) == 1
    assert displaced[0].object_ids == ("table-1",)
    assert "shared_object_id" in displaced[0].evidence
    assert displaced[0].scores.shape_similarity > 0.95


def test_extra_image_is_not_forced_into_an_unrelated_match() -> None:
    reference = _page()
    candidate = _page()
    draw = ImageDraw.Draw(candidate)
    draw.rectangle((80, 120, 250, 260), fill="black")
    draw.rectangle((95, 135, 235, 245), fill="white")

    report = localize_page_render_diff(
        reference,
        candidate,
        candidate_regions=[_object("image-extra", (75, 115, 256, 266))],
    )

    extra = _diagnostics(report, RenderDiffKind.EXTRA_REGION)
    assert len(extra) == 1
    assert extra[0].object_ids == ("image-extra",)
    assert extra[0].scores.candidate_difference_fraction == 1.0


def test_page_overflow_requires_and_reports_geometric_boundary_evidence() -> None:
    reference = _page()
    candidate = _page()
    ImageDraw.Draw(reference).rectangle((100, 380, 300, 450), fill="black")
    ImageDraw.Draw(candidate).rectangle((100, 470, 300, 499), fill="black")

    report = localize_page_render_diff(
        reference,
        candidate,
        reference_regions=[_object("footer-table", (100, 380, 301, 451))],
        candidate_regions=[_object("footer-table", (100, 470, 301, 550))],
    )

    clipping = _diagnostics(report, RenderDiffKind.CLIPPING_OVERFLOW)
    assert len(clipping) == 1
    assert clipping[0].object_ids == ("footer-table",)
    assert clipping[0].bbox.y1 == 500
    assert clipping[0].normalized_bbox.y1 == 1.0
    assert clipping[0].evidence == ("candidate_object_bbox_outside_page",)


def test_scaled_region_is_classified_as_size_mismatch() -> None:
    reference = _page()
    candidate = _page()
    ImageDraw.Draw(reference).rectangle((100, 100, 299, 299), outline="black", width=8)
    ImageDraw.Draw(candidate).rectangle((125, 125, 274, 274), outline="black", width=8)

    report = localize_page_render_diff(
        reference,
        candidate,
        reference_regions=[_object("chart-1", (100, 100, 300, 300))],
        candidate_regions=[_object("chart-1", (125, 125, 275, 275))],
    )

    mismatches = _diagnostics(report, RenderDiffKind.SIZE_MISMATCH)
    assert len(mismatches) == 1
    assert mismatches[0].object_ids == ("chart-1",)
    assert mismatches[0].scores.area_similarity < 0.80


def test_report_is_repeatable_and_region_input_order_is_irrelevant() -> None:
    reference = _page()
    candidate = _page()
    draw_reference = ImageDraw.Draw(reference)
    draw_candidate = ImageDraw.Draw(candidate)
    draw_reference.rectangle((20, 20, 100, 100), outline="black", width=4)
    draw_reference.rectangle((220, 300, 350, 420), outline="black", width=4)
    draw_candidate.rectangle((30, 35, 110, 115), outline="black", width=4)
    draw_candidate.rectangle((210, 280, 340, 400), outline="black", width=4)
    references = [_object("alpha", (20, 20, 101, 101)), _object("beta", (220, 300, 351, 421))]
    candidates = [_object("alpha", (30, 35, 111, 116)), _object("beta", (210, 280, 341, 401))]

    forward = localize_page_render_diff(
        reference,
        candidate,
        reference_regions=references,
        candidate_regions=candidates,
    )
    reversed_input = localize_page_render_diff(
        reference,
        candidate,
        reference_regions=list(reversed(references)),
        candidate_regions=list(reversed(candidates)),
    )
    repeated = localize_page_render_diff(
        reference,
        candidate,
        reference_regions=references,
        candidate_regions=candidates,
    )

    assert forward.to_dict() == reversed_input.to_dict() == repeated.to_dict()
    assert forward.fingerprint == reversed_input.fingerprint == repeated.fingerprint


def test_hybrid_render_diff_is_verified_diagnostic_not_acceptance_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    output = tmp_path / "candidate.docx"
    reconstruct_hybrid(markdown, layout, output=output)
    scan = hybrid_validation.analyze_scan_source(layout)

    def exact_render(*args: object, **kwargs: object) -> DocumentRenderResult:
        return DocumentRenderResult(
            requested_backend="libreoffice",
            used_backend="libreoffice",
            status="rendered",
            pages=(layout.read_bytes(),),
            executable="project-test-backend",
            page_sizes_points=tuple(
                (float(page.pdf_width), float(page.pdf_height)) for page in scan.pages
            ),
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

    localization = report.metrics["rendered_diff_localization"]
    assert report.passed
    assert report.metrics["rendered_diff_localization_status"] == "measured"
    assert localization["metric_version"] == RENDER_DIFF_METRIC_VERSION
    assert localization["diagnostics"] == []
    assert all(gate.name != "render_diff_localization" for gate in report.gates)
