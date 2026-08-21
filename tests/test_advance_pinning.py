"""Advance pinning, unclaimed-band restoration, and the rendered-fill metric."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from docx import Document
from docx.enum.text import WD_LINE_SPACING
from docx.shared import Pt

from docreconstruct.reconstruction.hybrid_docx import (
    _add_vertical_spacer,
    _document_flow_elements,
    _estimated_flow_height,
    _group_target_advance,
    _unclaimed_ink_bands,
)
from docreconstruct.reconstruction.hybrid_planner import HybridBlockPlacement
from docreconstruct.reconstruction.markdown_content import (
    MarkdownBlock,
    MarkdownBlockKind,
)
from docreconstruct.reconstruction.scan_layout import PixelBox


def test_estimated_flow_height_tracks_exact_line_boxes_and_spacers() -> None:
    document = Document()
    paragraph = document.add_paragraph("x" * 200)
    paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    paragraph.paragraph_format.line_spacing = Pt(12.0)
    paragraph.paragraph_format.space_before = Pt(3.0)
    _add_vertical_spacer(document, 40.0)

    flow = _document_flow_elements(document)
    height = _estimated_flow_height(
        flow,
        width_points=500.0,
        default_line_height=12.0,
        default_size=10.0,
    )
    tall = _estimated_flow_height(
        flow,
        width_points=500.0,
        default_line_height=12.0,
        default_size=10.0,
        tall=True,
    )
    # 200 characters at 10pt cannot fit one 500pt line; the spacer is exact.
    assert height > 12.0 + 3.0 + 40.0
    # The tall estimate rounds every wrap up and must never undershoot.
    assert tall >= height


def test_group_target_advance_maps_topmost_source_ink() -> None:
    block = MarkdownBlock(
        id="md-1",
        index=0,
        kind=MarkdownBlockKind.PARAGRAPH,
        text="body",
    )
    placement = HybridBlockPlacement(
        block_id="md-1",
        block_index=0,
        page_number=1,
        source_bbox=PixelBox(x0=10, y0=400, x1=500, y1=440),
    )
    target = _group_target_advance(
        [block],
        {"md-1": placement},
        page_offset=0.0,
        vertical_scale=0.375,
        top_margin=13.0,
    )
    assert target == pytest.approx(400 * 0.375 - 13.0)
    assert (
        _group_target_advance(
            [block],
            {},
            page_offset=0.0,
            vertical_scale=0.375,
            top_margin=13.0,
        )
        is None
    )


def _band_page(image: object) -> SimpleNamespace:
    return SimpleNamespace(
        content_bbox=PixelBox(x0=0, y0=0, x1=600, y1=800),
        image=image,
        line_pitch=24.0,
    )


def test_unclaimed_ink_bands_restores_only_unclaimed_solid_strips() -> None:
    pillow = pytest.importorskip("PIL.Image")
    image = pillow.new("L", (600, 800), 255)
    for top, bottom in ((100, 160), (500, 560)):
        for y in range(top, bottom):
            for x in range(0, 600, 1):
                image.putpixel((x, y), 0)
    claimed = HybridBlockPlacement(
        block_id="md-1",
        block_index=0,
        page_number=1,
        source_bbox=PixelBox(x0=0, y0=140, x1=600, y1=200),
        source_rows=[PixelBox(x0=0, y0=140, x1=600, y1=200)],
    )
    bands = _unclaimed_ink_bands(_band_page(image), [claimed])
    # The claimed text row trims the first strip (3px padding); the second
    # strip is untouched furniture.
    assert bands == [(100, 137), (500, 560)]


def test_unclaimed_ink_bands_requires_claimed_geometry() -> None:
    pillow = pytest.importorskip("PIL.Image")
    image = pillow.new("L", (600, 800), 255)
    for y in range(100, 160):
        for x in range(600):
            image.putpixel((x, y), 0)
    ungeometried = HybridBlockPlacement(
        block_id="md-1",
        block_index=0,
        page_number=1,
    )
    assert _unclaimed_ink_bands(_band_page(image), [ungeometried]) == []


def test_rendered_fill_metric_measures_vertical_ink_distribution() -> None:
    pillow = pytest.importorskip("PIL.Image")
    from docreconstruct.evaluation.hybrid_validation import _rendered_fill_metrics

    source = pillow.new("RGB", (200, 400), (255, 255, 255))
    for y in range(300, 340):
        for x in range(20, 180):
            source.putpixel((x, y), (0, 0, 0))
    candidate = pillow.new("RGB", (200, 400), (255, 255, 255))
    for y in range(60, 100):
        for x in range(20, 180):
            candidate.putpixel((x, y), (0, 0, 0))
    rendered = io.BytesIO()
    candidate.save(rendered, format="PNG")
    layout = SimpleNamespace(
        pages=[SimpleNamespace(number=1, image=source)],
    )
    metrics = _rendered_fill_metrics(layout, (rendered.getvalue(),))
    assert metrics["measured_pages"] == 1
    page = metrics["pages"][0]
    assert page["measured"] is True
    # The candidate concentrates its ink far above the source band.
    assert page["centroid_delta"] < -0.3
    assert page["within_tolerance"] is False
    assert metrics["within_tolerance"] is False
