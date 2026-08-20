from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from docreconstruct.ir import BBox
from docreconstruct.reconstruction.scan_layout import (
    PixelBox,
    ScanPageLayout,
    SourceToScanBand,
    SourceToScanMap,
    analyze_scan_image,
    project_source_box_to_scan,
)


def _page(
    *,
    width: int,
    height: int,
    metadata: dict[str, object] | None = None,
) -> ScanPageLayout:
    return ScanPageLayout(
        number=1,
        width=width,
        height=height,
        pdf_width=595.28,
        pdf_height=841.89,
        content_bbox=PixelBox(x0=0, y0=0, x1=width, y1=height),
        line_pitch=20.0,
        metadata=dict(metadata or {}),
        image=Image.new("RGB", (width, height), "white"),
    )


def _row_mesh() -> SourceToScanMap:
    return SourceToScanMap(
        source_width=1000,
        source_height=1000,
        target_width=500,
        target_height=1000,
        confidence=0.9,
        bands=[
            SourceToScanBand(
                source_y0=100,
                source_y1=500,
                source_left0=100,
                source_right0=900,
                source_left1=150,
                source_right1=850,
                target_y0=0,
                target_y1=500,
            ),
            SourceToScanBand(
                source_y0=500,
                source_y1=900,
                source_left0=150,
                source_right0=850,
                source_left1=200,
                source_right1=800,
                target_y0=500,
                target_y1=1000,
            ),
        ],
    )


def test_flat_and_normalized_boxes_scale_to_scan_pixels() -> None:
    page = _page(width=1000, height=2000, metadata={"rectified": False})

    scaled = project_source_box_to_scan(
        page,
        BBox(x0=10, y0=20, x1=110, y1=220),
        source_width=200,
        source_height=400,
    )
    normalized = project_source_box_to_scan(
        page,
        BBox(x0=0.1, y0=0.2, x1=0.9, y1=0.8),
        source_width=1,
        source_height=1,
    )

    assert scaled == PixelBox(x0=50, y0=100, x1=550, y1=1100)
    assert normalized == PixelBox(x0=100, y0=400, x1=900, y1=1600)


def test_rectified_row_mesh_projects_page_edges_and_interior() -> None:
    mapping = _row_mesh()
    page = _page(
        width=500,
        height=1000,
        metadata={
            "rectified": True,
            "original_width": 1000,
            "original_height": 1000,
            "source_to_scan_map": mapping.model_dump(mode="json"),
        },
    )

    whole_page = project_source_box_to_scan(
        page,
        BBox(x0=100, y0=100, x1=900, y1=900),
        source_width=1000,
        source_height=1000,
    )
    interior = project_source_box_to_scan(
        page,
        BBox(x0=400, y0=480, x1=600, y1=520),
        source_width=1000,
        source_height=1000,
    )
    resized_original = project_source_box_to_scan(
        page,
        BBox(x0=200, y0=240, x1=300, y1=260),
        source_width=500,
        source_height=500,
    )

    assert whole_page == PixelBox(x0=0, y0=0, x1=500, y1=1000)
    assert interior is not None
    assert 177 <= interior.x0 <= 179
    assert 321 <= interior.x1 <= 323
    assert interior.y0 == 475
    assert interior.y1 == 525
    assert resized_original == interior


def test_projection_clips_partial_boxes_and_rejects_unsafe_dimensions() -> None:
    page = _page(width=1000, height=2000, metadata={"rectified": False})

    clipped = project_source_box_to_scan(
        page,
        BBox(x0=-10, y0=-20, x1=50, y1=100),
        source_width=100,
        source_height=200,
    )
    mismatch = project_source_box_to_scan(
        page,
        BBox(x0=0, y0=0, x1=50, y1=50),
        source_width=100,
        source_height=100,
    )
    outside = project_source_box_to_scan(
        page,
        BBox(x0=110, y0=210, x1=120, y1=220),
        source_width=100,
        source_height=200,
    )

    assert clipped == PixelBox(x0=0, y0=0, x1=500, y1=1000)
    assert mismatch is None
    assert outside is None


def test_flat_image_analysis_keeps_existing_raster_and_records_dimensions(
    tmp_path: Path,
) -> None:
    source = tmp_path / "flat-a4.png"
    image = Image.new("RGB", (420, 594), "white")
    draw = ImageDraw.Draw(image)
    for y in range(80, 520, 28):
        draw.rectangle((55, y, 360, y + 4), fill="black")
    image.save(source)

    layout = analyze_scan_image(source)
    page = layout.pages[0]

    assert page.metadata["rectified"] is False
    assert page.width == 420
    assert page.height == 594
    assert page.metadata["original_width"] == 420
    assert page.metadata["original_height"] == 594
    assert "source_to_scan_map" not in page.metadata
    assert project_source_box_to_scan(
        page,
        BBox(x0=42, y0=59.4, x1=378, y1=534.6),
        source_width=420,
        source_height=594,
    ) == PixelBox(x0=42, y0=59, x1=378, y1=535)


@pytest.mark.parametrize(
    ("sheet_width", "sheet_height"),
    [(1650, 1275), (1275, 1650), (1754, 1240), (1240, 1754)],
)
def test_rectified_page_keeps_the_orientation_of_its_sheet(
    tmp_path: Path,
    sheet_width: int,
    sheet_height: int,
) -> None:
    """A landscape sheet must not be rectified into a portrait canvas.

    The candidate paper sizes were portrait-only, so the closest match was
    always portrait: a landscape sheet was mesh-stretched to a portrait canvas,
    distorting every glyph before line detection and reporting a portrait page
    size for a document that is physically wider than it is tall.
    """

    source = tmp_path / f"sheet-{sheet_width}x{sheet_height}.png"
    margin_x, margin_y = int(sheet_width * 0.15), int(sheet_height * 0.17)
    image = Image.new("RGB", (sheet_width + margin_x, sheet_height + margin_y), (35, 38, 42))
    draw = ImageDraw.Draw(image)
    left, top = margin_x // 2, margin_y // 2
    draw.rectangle((left, top, left + sheet_width, top + sheet_height), fill=(252, 252, 250))
    rows = max(8, sheet_height // 60)
    for index in range(rows):
        row_top = top + 60 + index * (sheet_height // (rows + 2))
        draw.rectangle(
            (left + 70, row_top, left + sheet_width - 70 - (index % 4) * 60, row_top + 18),
            fill=(20, 20, 20),
        )
    image.save(source)

    page = analyze_scan_image(source).pages[0]

    landscape_sheet = sheet_width > sheet_height
    assert (page.pdf_width > page.pdf_height) is landscape_sheet
    assert (page.width > page.height) is landscape_sheet


def test_photographed_page_records_compact_forward_row_mapping(tmp_path: Path) -> None:
    source = tmp_path / "photographed-page.png"
    image = Image.new("RGB", (900, 1200), (92, 67, 45))
    draw = ImageDraw.Draw(image)
    paper = [(170, 100), (760, 145), (820, 1080), (100, 1120)]
    draw.polygon(paper, fill=(245, 244, 238))
    for y in range(220, 1010, 38):
        fraction = (y - 100) / 1020
        left = round(170 + (100 - 170) * fraction)
        right = round(760 + (820 - 760) * fraction)
        draw.line((left + 75, y, right - 70, y + 8), fill=(32, 32, 32), width=4)
    image.save(source)

    page = analyze_scan_image(source).pages[0]

    assert page.metadata["rectified"] is True
    mapping = SourceToScanMap.model_validate(page.metadata["source_to_scan_map"])
    assert mapping.source_width == 900
    assert mapping.source_height == 1200
    assert mapping.target_width == page.width
    assert mapping.target_height == page.height
    assert 12 <= len(mapping.bands) <= 48
    assert len(mapping.model_dump_json()) < 25_000

    paper_projection = project_source_box_to_scan(
        page,
        BBox(x0=100, y0=100, x1=820, y1=1120),
        source_width=900,
        source_height=1200,
    )
    interior_projection = project_source_box_to_scan(
        page,
        BBox(x0=300, y0=400, x1=600, y1=700),
        source_width=900,
        source_height=1200,
    )
    assert paper_projection is not None
    assert paper_projection.width >= page.width * 0.98
    assert paper_projection.height >= page.height * 0.95
    assert interior_projection is not None
    assert 0 <= interior_projection.x0 < interior_projection.x1 <= page.width
    assert 0 <= interior_projection.y0 < interior_projection.y1 <= page.height
