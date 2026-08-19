from __future__ import annotations

from pathlib import Path

from PIL import Image

from docreconstruct.reconstruction.asset_matching import AssetMatch
from docreconstruct.reconstruction.hybrid_planner import (
    apply_page_vertical_fit_budget,
    build_hybrid_layout_plan,
    build_page_vertical_fit_budget,
    visual_text_row_groups,
    visual_text_rows,
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
    ScanTextLine,
    _detect_header_layout,
)

_ROW_BOXES = [
    (356, 32, 705, 100),
    (356, 132, 853, 193),
    (356, 228, 704, 289),
    (356, 313, 747, 381),
    (355, 401, 722, 448),
    (90, 465, 157, 484),
    (91, 499, 698, 576),
    (92, 603, 370, 672),
    (113, 691, 742, 741),
    (320, 760, 829, 807),
    (89, 828, 518, 897),
    (89, 921, 156, 940),
    (91, 955, 439, 978),
    (89, 999, 477, 1041),
    (89, 1066, 958, 1145),
    (353, 1163, 713, 1210),
    (353, 1230, 855, 1278),
    (353, 1298, 857, 1343),
]

_RAW_ROW_BOXES = [
    (422, 32, 703, 59),
    (380, 61, 551, 70),
    (356, 72, 705, 84),
    (380, 89, 571, 100),
    (381, 132, 853, 164),
    (356, 162, 853, 193),
    (422, 228, 700, 247),
    (380, 254, 436, 259),
    (356, 261, 704, 273),
    (380, 278, 569, 289),
    (500, 313, 541, 317),
    (356, 324, 747, 366),
    (380, 368, 702, 381),
    (380, 401, 708, 421),
    (355, 419, 722, 448),
    (90, 465, 157, 484),
    (138, 499, 486, 519),
    (91, 517, 685, 546),
    (91, 544, 698, 576),
    (155, 603, 196, 607),
    (155, 611, 317, 629),
    (92, 630, 370, 672),
    (113, 691, 741, 712),
    (113, 710, 742, 741),
    (350, 760, 812, 780),
    (320, 778, 829, 807),
    (205, 828, 246, 833),
    (148, 835, 503, 856),
    (89, 854, 518, 884),
    (113, 882, 505, 897),
    (89, 921, 156, 940),
    (91, 955, 439, 978),
    (89, 999, 477, 1030),
    (134, 1031, 297, 1041),
    (124, 1066, 923, 1096),
    (89, 1094, 958, 1124),
    (89, 1122, 958, 1138),
    (414, 1141, 722, 1145),
    (378, 1163, 713, 1183),
    (353, 1181, 713, 1210),
    (376, 1230, 854, 1250),
    (353, 1248, 855, 1278),
    (353, 1298, 857, 1330),
    (418, 1328, 857, 1343),
]


def _scan_layout(tmp_path: Path) -> ScanDocumentLayout:
    lines = [
        ScanTextLine(
            bbox=PixelBox(x0=x0, y0=y0, x1=x1, y1=y1),
            segments=[PixelBox(x0=x0, y0=y0, x1=x1, y1=y1)],
            ink_density=0.12,
        )
        for x0, y0, x1, y1 in _RAW_ROW_BOXES
    ]
    return ScanDocumentLayout(
        source=str(tmp_path / "layout.png"),
        pages=[
            ScanPageLayout(
                number=1,
                width=980,
                height=1400,
                pdf_width=595.28,
                pdf_height=841.89,
                content_bbox=PixelBox(x0=86, y0=27, x1=958, y1=1347),
                line_pitch=22.5,
                text_lines=lines,
                image=Image.new("RGB", (980, 1400), "white"),
            )
        ],
    )


def _three_column_page(*, include_suffix: bool = False) -> ScanPageLayout:
    prefix = [
        PixelBox(x0=70, y0=28, x1=530, y1=42),
        PixelBox(x0=110, y0=58, x1=490, y1=72),
    ]
    columns = [(40, 175), (220, 355), (400, 535)]
    body_lines = []
    for y0 in (120, 145, 170, 195):
        segments = [PixelBox(x0=x0, y0=y0, x1=x1, y1=y0 + 14) for x0, x1 in columns]
        body_lines.append(
            ScanTextLine(
                bbox=PixelBox(x0=40, y0=y0, x1=535, y1=y0 + 14),
                segments=segments,
                ink_density=0.1,
            )
        )
    lines = [
        *[ScanTextLine(bbox=box, segments=[box], ink_density=0.1) for box in prefix],
        *body_lines,
        # A rule fragment wholly inside a gutter is detector noise, not a
        # readable source row in either neighboring column.
        ScanTextLine(
            bbox=PixelBox(x0=188, y0=170, x1=207, y1=172),
            segments=[PixelBox(x0=188, y0=170, x1=207, y1=172)],
            ink_density=0.1,
        ),
    ]
    if include_suffix:
        suffix = PixelBox(x0=60, y0=250, x1=180, y1=264)
        lines.append(ScanTextLine(bbox=suffix, segments=[suffix], ink_density=0.1))
    return ScanPageLayout(
        number=1,
        width=580,
        height=310,
        pdf_width=595,
        pdf_height=318,
        content_bbox=PixelBox(x0=30, y0=20, x1=550, y1=285),
        line_pitch=20,
        text_lines=lines,
        metadata={
            "column_count": 3,
            "column_boxes": [
                [40, 105, 175, 235],
                [220, 105, 355, 235],
                [400, 105, 535, 235],
            ],
            "column_content_bottoms": [220, 220, 220],
        },
        image=Image.new("RGB", (580, 310), "white"),
    )


def test_all_editable_blocks_receive_monotonic_source_row_geometry(tmp_path: Path) -> None:
    markdown = tmp_path / "calculus.md"
    markdown.write_text(
        """$$\\begin{aligned}&=a\\\\&=b\\\\&=c\\\\&=d\\\\&=e\\end{aligned}$$

方法二

$$x$$

由此可知 $x=1$。

$$\\begin{aligned}&=f\\\\&=g\\end{aligned}$$

$$y$$

方法三

由泰勒公式可知 $e^x=1+x$。

从而有 $x=1$。

$$\\begin{aligned}&=h\\\\&=i\\\\&=j\\\\&=k\\end{aligned}$$
""",
        encoding="utf-8",
    )
    content = parse_markdown_content(markdown)
    layout = _scan_layout(tmp_path)

    assert visual_text_rows(layout.pages[0]) == [
        PixelBox(x0=x0, y0=y0, x1=x1, y1=y1) for x0, y0, x1, y1 in _ROW_BOXES
    ]

    plan = build_hybrid_layout_plan(content, layout, [], [])

    placements = plan.pages[0].placements
    assert len(placements) == 10
    assert [len(placement.source_rows) for placement in placements] == [
        5,
        1,
        1,
        1,
        2,
        1,
        1,
        1,
        1,
        4,
    ]
    assert sum(len(placement.source_rows) for placement in placements) == 18
    assert all(placement.source_bbox is not None for placement in placements)
    assert placements[0].source_bbox == PixelBox(x0=355, y0=32, x1=853, y1=448)
    assert placements[-1].source_bbox == PixelBox(x0=89, y0=1066, x1=958, y1=1343)
    assert [row for placement in placements for row in placement.source_rows] == (
        visual_text_rows(layout.pages[0])
    )

    page = layout.pages[0]
    vertical_scale = page.pdf_width / page.width
    printable_height = page.content_bbox.height * vertical_scale
    font_size = max(8.6, min(12.0, page.line_pitch * vertical_scale * 0.76))
    budget = build_page_vertical_fit_budget(
        page,
        placements,
        printable_height_points=printable_height,
        font_size_points=font_size,
    )

    assert budget.calibrated
    assert budget.fits
    assert 0 < budget.block_gap_scale < 1
    assert budget.row_gap_scale == 1
    assert budget.native_line_box_allowance == font_size * 0.33 * 18
    assert budget.estimated_footprint <= printable_height - budget.headroom
    fitted = apply_page_vertical_fit_budget(page, placements, budget)
    assert len(fitted) == 10
    assert sum(len(placement.source_rows) for placement in fitted) == 18
    assert fitted[0].source_gap_before == placements[0].source_gap_before
    assert sum(int(placement.source_gap_before or 0) for placement in fitted[1:]) < sum(
        int(placement.source_gap_before or 0) for placement in placements[1:]
    )
    assert [placement.source_rows for placement in fitted] == [
        placement.source_rows for placement in placements
    ]
    assert [placement.source_bbox for placement in fitted] == [
        placement.source_bbox for placement in placements
    ]

    tight_budget = build_page_vertical_fit_budget(
        page,
        placements,
        printable_height_points=700,
        font_size_points=font_size,
        headroom_points=0,
    )
    assert tight_budget.fits
    assert tight_budget.block_gap_scale == 0
    assert 0 < tight_budget.row_gap_scale < 1
    tightly_fitted = apply_page_vertical_fit_budget(page, placements, tight_budget)
    assert sum(len(placement.source_rows) for placement in tightly_fitted) == 18
    assert [row.height for placement in tightly_fitted for row in placement.source_rows] == [
        row.height for placement in placements for row in placement.source_rows
    ]
    assert tightly_fitted[-1].source_bbox is not None
    assert placements[-1].source_bbox is not None
    assert tightly_fitted[-1].source_bbox.height < placements[-1].source_bbox.height


def test_centered_formula_fragments_are_not_a_split_masthead() -> None:
    content = PixelBox(x0=86, y0=27, x1=958, y1=1347)
    lines = [
        ScanTextLine(
            bbox=PixelBox(x0=356, y0=y0, x1=853, y1=y0 + 30),
            segments=[
                PixelBox(x0=356, y0=y0, x1=445, y1=y0 + 30),
                PixelBox(x0=650, y0=y0, x1=853, y1=y0 + 30),
            ],
            ink_density=0.10,
        )
        for y0 in (32, 132, 228)
    ]

    metadata = _detect_header_layout(lines, content, 22.5)

    assert metadata == {"header_column_count": 1}


def test_adjacent_dense_baselines_are_not_collapsed_by_overlapping_boxes(
    tmp_path: Path,
) -> None:
    lines = [
        ScanTextLine(
            bbox=PixelBox(x0=50, y0=30 + index * 28, x1=550, y1=65 + index * 28),
            segments=[PixelBox(x0=50, y0=30 + index * 28, x1=550, y1=65 + index * 28)],
            ink_density=0.1,
        )
        for index in range(8)
    ]
    page = ScanPageLayout(
        number=1,
        width=600,
        height=400,
        pdf_width=595,
        pdf_height=397,
        content_bbox=PixelBox(x0=40, y0=20, x1=560, y1=300),
        line_pitch=30,
        text_lines=lines,
        image=Image.new("RGB", (600, 400), "white"),
    )

    assert visual_text_rows(page) == [line.bbox for line in lines]


def test_compact_option_group_shares_visual_rows_without_losing_geometry(
    tmp_path: Path,
) -> None:
    markdown = tmp_path / "options.md"
    markdown.write_text(
        "Câu 1. Chọn phương án đúng.\n\n"
        "A. $\\frac{x+1}{2}=0$.\n\n"
        "B. $x=1$.\n\n"
        "C. $x=2$.\n\n"
        "D. $\\frac{x-1}{2}=0$.\n",
        encoding="utf-8",
    )
    lines = [
        ScanTextLine(
            bbox=PixelBox(x0=45, y0=30 + index * 38, x1=555, y1=58 + index * 38),
            segments=[PixelBox(x0=45, y0=30 + index * 38, x1=555, y1=58 + index * 38)],
            ink_density=0.1,
        )
        for index in range(5)
    ]
    page = ScanPageLayout(
        number=1,
        width=600,
        height=280,
        pdf_width=595,
        pdf_height=278,
        content_bbox=PixelBox(x0=40, y0=20, x1=560, y1=230),
        line_pitch=30,
        text_lines=lines,
        image=Image.new("RGB", (600, 280), "white"),
    )
    content = parse_markdown_content(markdown)
    layout = ScanDocumentLayout(source=str(tmp_path / "layout.png"), pages=[page])

    plan = build_hybrid_layout_plan(content, layout, [], [])
    placements = plan.pages[0].placements

    assert all(placement.source_bbox is not None for placement in placements)
    option_rows = [placement.source_rows for placement in placements[1:]]
    assert option_rows and all(rows == option_rows[0] for rows in option_rows)
    assert {
        (row.x0, row.y0, row.x1, row.y1)
        for placement in placements
        for row in placement.source_rows
    } == {(line.bbox.x0, line.bbox.y0, line.bbox.x1, line.bbox.y1) for line in lines}


def test_text_geometry_never_crosses_an_intervening_figure_anchor(tmp_path: Path) -> None:
    blocks = [
        MarkdownBlock(
            id="md-1",
            index=0,
            kind=MarkdownBlockKind.PARAGRAPH,
            text="Editable introduction above the source figure on two visual rows.",
        ),
        MarkdownBlock(
            id="md-2",
            index=1,
            kind=MarkdownBlockKind.IMAGE,
            source="https://assets.invalid/figure.png",
        ),
        MarkdownBlock(
            id="md-3",
            index=2,
            kind=MarkdownBlockKind.PARAGRAPH,
            text="Editable continuation below the source figure on two visual rows.",
        ),
    ]
    rows = [
        PixelBox(x0=50, y0=90, x1=540, y1=115),
        PixelBox(x0=50, y0=130, x1=520, y1=155),
        PixelBox(x0=50, y0=390, x1=545, y1=415),
        PixelBox(x0=50, y0=430, x1=500, y1=455),
    ]
    page = ScanPageLayout(
        number=1,
        width=600,
        height=800,
        pdf_width=595,
        pdf_height=793,
        content_bbox=PixelBox(x0=40, y0=30, x1=560, y1=760),
        line_pitch=40,
        text_lines=[ScanTextLine(bbox=row, segments=[row], ink_density=0.1) for row in rows],
        image=Image.new("RGB", (600, 800), "white"),
    )
    content = MarkdownContent(source=str(tmp_path / "content.md"), blocks=blocks)
    layout = ScanDocumentLayout(source=str(tmp_path / "layout.png"), pages=[page])
    figure = PixelBox(x0=180, y0=190, x1=430, y1=350)

    plan = build_hybrid_layout_plan(
        content,
        layout,
        [
            AssetMatch(
                block_id="md-2",
                source=blocks[1].source or "",
                page_number=1,
                bbox=figure,
                score=1.0,
                resolved=False,
            )
        ],
        [],
    )

    before, anchor, after = plan.pages[0].placements
    assert before.source_bbox is not None and before.source_bbox.y1 < figure.y0
    assert anchor.source_bbox == figure
    assert after.source_bbox is not None and after.source_bbox.y0 > figure.y1


def test_three_column_rows_follow_column_major_reading_order() -> None:
    page = _three_column_page(include_suffix=True)

    groups = visual_text_row_groups(page)

    assert [len(group) for group in groups] == [2, 4, 4, 4, 1]
    assert [(row.x0, row.x1) for row in groups[0]] == [(70, 530), (110, 490)]
    assert all(row.x0 == 40 and row.x1 == 175 for row in groups[1])
    assert all(row.x0 == 220 and row.x1 == 355 for row in groups[2])
    assert all(row.x0 == 400 and row.x1 == 535 for row in groups[3])
    assert groups[4] == [PixelBox(x0=60, y0=250, x1=180, y1=264)]
    assert not any(row.x0 == 188 for row in visual_text_rows(page))


def test_tall_post_masthead_headline_remains_a_spanning_prefix() -> None:
    page = _three_column_page()
    headline = PixelBox(x0=48, y0=108, x1=350, y1=136)
    page.text_lines.insert(
        2,
        ScanTextLine(bbox=headline, segments=[headline], ink_density=0.16),
    )

    groups = visual_text_row_groups(page)

    assert headline in groups[0]
    assert all(headline not in group for group in groups[1:])


def test_ungrouped_markdown_maps_wholly_inside_three_source_columns(
    tmp_path: Path,
) -> None:
    blocks = [
        MarkdownBlock(
            id="md-1",
            index=0,
            kind=MarkdownBlockKind.HEADING,
            text="Full width newspaper headline",
            level=1,
        ),
        MarkdownBlock(
            id="md-2",
            index=1,
            kind=MarkdownBlockKind.HEADING,
            text="Full width standfirst",
            level=4,
        ),
        *[
            MarkdownBlock(
                id=f"md-{index + 3}",
                index=index + 2,
                kind=MarkdownBlockKind.PARAGRAPH,
                text=f"Column text block {index + 1} has two rows.",
            )
            for index in range(6)
        ],
    ]
    content = MarkdownContent(source=str(tmp_path / "newspaper.md"), blocks=blocks)
    page = _three_column_page()
    layout = ScanDocumentLayout(source=str(tmp_path / "newspaper.png"), pages=[page])

    plan = build_hybrid_layout_plan(content, layout, [], [])

    placements = plan.pages[0].placements
    assert all(placement.source_bbox is not None for placement in placements)
    assert [len(placement.source_rows) for placement in placements[:2]] == [1, 1]
    column_boxes = [
        PixelBox(x0=40, y0=105, x1=175, y1=235),
        PixelBox(x0=220, y0=105, x1=355, y1=235),
        PixelBox(x0=400, y0=105, x1=535, y1=235),
    ]
    owners = []
    for placement in placements[2:]:
        matching = [
            index
            for index, column in enumerate(column_boxes)
            if placement.source_bbox is not None
            and placement.source_bbox.x0 >= column.x0
            and placement.source_bbox.x1 <= column.x1
        ]
        assert len(matching) == 1
        assert all(
            column_boxes[matching[0]].x0 <= row.x0 < row.x1 <= column_boxes[matching[0]].x1
            for row in placement.source_rows
        )
        owners.append(matching[0])
    assert owners == [0, 0, 1, 1, 2, 2]


def test_surplus_column_noise_does_not_disable_available_block_geometry(
    tmp_path: Path,
) -> None:
    rows = [PixelBox(x0=40, y0=110 + index * 20, x1=170, y1=124 + index * 20) for index in range(8)]
    page = ScanPageLayout(
        number=1,
        width=580,
        height=310,
        pdf_width=595,
        pdf_height=318,
        content_bbox=PixelBox(x0=30, y0=20, x1=550, y1=285),
        line_pitch=20,
        text_lines=[ScanTextLine(bbox=row, segments=[row], ink_density=0.1) for row in rows],
        metadata={
            "column_count": 3,
            "column_boxes": [
                [40, 100, 175, 285],
                [220, 100, 355, 285],
                [400, 100, 535, 285],
            ],
            "column_content_bottoms": [280, 280, 280],
        },
        image=Image.new("RGB", (580, 310), "white"),
    )
    content = MarkdownContent(
        source=str(tmp_path / "noise.md"),
        blocks=[
            MarkdownBlock(
                id="md-1",
                index=0,
                kind=MarkdownBlockKind.PARAGRAPH,
                text="Short editable text.",
            )
        ],
    )

    plan = build_hybrid_layout_plan(
        content,
        ScanDocumentLayout(source=str(tmp_path / "noise.png"), pages=[page]),
        [],
        [],
    )

    placement = plan.pages[0].placements[0]
    assert placement.source_bbox is not None
    assert placement.source_bbox.x0 >= 40 and placement.source_bbox.x1 <= 175
    assert 1 <= len(placement.source_rows) < len(visual_text_rows(page))
