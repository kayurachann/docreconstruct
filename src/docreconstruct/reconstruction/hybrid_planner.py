"""Deterministic alignment of Markdown blocks to raster scan pages."""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Sequence
from functools import cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from docreconstruct.ir import ElementStyle
from docreconstruct.reconstruction.asset_matching import AssetMatch
from docreconstruct.reconstruction.evidence_matching import EvidenceMatch
from docreconstruct.reconstruction.markdown_content import (
    MarkdownBlock,
    MarkdownBlockKind,
    MarkdownContent,
)
from docreconstruct.reconstruction.math_omml import equation_row_count, latex_visible_text
from docreconstruct.reconstruction.scan_layout import (
    PixelBox,
    ScanDocumentLayout,
    ScanPageLayout,
    ScanTextLine,
)
from docreconstruct.reconstruction.table_matching import TableMatch


class HybridBlockPlacement(BaseModel):
    """Assignment of one editable content block to one source page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str
    block_index: int = Field(ge=0)
    page_number: int = Field(ge=1)
    source_bbox: PixelBox | None = None
    source_rows: list[PixelBox] = Field(default_factory=list)
    source_gap_before: int | None = Field(default=None, ge=0)
    match_score: float | None = None
    geometry_source: str = "content_estimate"
    evidence_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_providers: tuple[str, ...] = ()
    evidence_element_ids: tuple[str, ...] = ()
    evidence_style: ElementStyle | None = None
    evidence_conflict: bool = False
    evidence_warnings: list[str] = Field(default_factory=list)


class HybridPagePlan(BaseModel):
    """Page-sized render plan with source geometry retained."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    number: int = Field(ge=1)
    pdf_width: float = Field(gt=0)
    pdf_height: float = Field(gt=0)
    raster_width: int = Field(gt=0)
    raster_height: int = Field(gt=0)
    content_bbox: PixelBox
    line_pitch: float = Field(gt=0)
    placements: list[HybridBlockPlacement]

    @model_validator(mode="after")
    def placements_must_belong_to_this_page(self) -> HybridPagePlan:
        misplaced = [
            placement.block_id
            for placement in self.placements
            if placement.page_number != self.number
        ]
        if misplaced:
            raise ValueError(
                f"page {self.number} contains placement(s) bound to another page: "
                + ", ".join(misplaced)
            )
        return self


class HybridLayoutPlan(BaseModel):
    """Renderer-neutral plan shared by DOCX/XLSX/PPTX exporters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content_source: str
    layout_source: str
    pages: list[HybridPagePlan]
    warnings: list[str] = Field(default_factory=list)


class VerticalFitBudget(BaseModel):
    """Deterministic allocation of a single-column page's vertical whitespace.

    Scan geometry measures glyph ink, while Word lays the same content inside
    native line boxes. The allowance below models that renderer-owned leading
    without changing source ink targets or the document font size. When a
    dense page would otherwise overflow, inter-block whitespace is compressed
    before spacing inside a multi-row block.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    calibrated: bool
    printable_height: float = Field(gt=0)
    headroom: float = Field(ge=0)
    fixed_ink_height: float = Field(ge=0)
    native_line_box_allowance: float = Field(ge=0)
    leading_gap_height: float = Field(ge=0)
    block_gap_height: float = Field(ge=0)
    row_gap_height: float = Field(ge=0)
    block_gap_scale: float = Field(ge=0, le=1)
    row_gap_scale: float = Field(ge=0, le=1)
    native_leading_scale: float = Field(ge=0, le=1)
    font_size_scale: float = Field(gt=0, le=1)
    line_height_scale: float = Field(gt=0, le=1)
    geometry_coverage: float = Field(ge=0, le=1)
    estimated_line_count: float = Field(ge=0)
    source_glyph_height: float = Field(ge=0)
    estimated_footprint: float = Field(ge=0)
    fits: bool


def contains_tall_inline_math(text: str) -> bool:
    """Return whether inline Office Math needs a taller native line box."""

    return bool(
        re.search(
            r"\\(?:frac|dfrac|tfrac|int|oint|sum|prod|sqrt|lim|overbrace|underbrace)\b",
            text,
        )
    )


def _page_vertical_scale(page: ScanPageLayout) -> float:
    if page.metadata.get("source_kind") == "image":
        return page.pdf_height / page.height
    return page.pdf_width / page.width


def _forms_horizontal_visual_row(boxes: Sequence[PixelBox]) -> bool:
    """Return whether source visuals occupy one side-by-side physical row."""

    if len(boxes) < 2:
        return False
    ordered = sorted(boxes, key=lambda box: box.x0)
    if any(left.x1 > right.x0 for left, right in zip(ordered, ordered[1:], strict=False)):
        return False
    common_overlap = max(0, min(box.y1 for box in ordered) - max(box.y0 for box in ordered))
    return common_overlap / max(1, min(box.height for box in ordered)) >= 0.55


def _unique_source_rows(page: ScanPageLayout, rows: Sequence[PixelBox]) -> list[PixelBox]:
    ordered = source_row_reading_order(page, rows)
    unique: list[PixelBox] = []
    seen: set[tuple[int, int, int, int]] = set()
    for row in ordered:
        key = (row.x0, row.y0, row.x1, row.y1)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def build_page_vertical_fit_budget(
    page: ScanPageLayout,
    placements: Sequence[HybridBlockPlacement],
    *,
    printable_height_points: float,
    font_size_points: float,
    blocks: Sequence[MarkdownBlock] | None = None,
    line_height_points: float | None = None,
    headroom_points: float | None = None,
    # Raster rows already include most ascender/descender ink.  Reserving one
    # additional third-em per native line matches Word/LibreOffice line boxes
    # without discarding the source's inter-block whitespace.
    native_leading_em: float = 0.33,
) -> VerticalFitBudget:
    """Allocate elastic scan whitespace inside one native Word page.

    The first source gap is a page-positioning offset and remains fixed.
    Later gaps are ink-to-ink measurements, so they are the first elastic
    component used to accommodate Word's native line-box leading. Row gaps
    are retained at full size unless block-gap compression alone cannot fit the
    page. Multi-column flows are deliberately left untouched because their
    vertical coordinates reset between independent columns.
    """

    if printable_height_points <= 0:
        raise ValueError("printable_height_points must be greater than zero")
    if font_size_points <= 0:
        raise ValueError("font_size_points must be greater than zero")
    if line_height_points is not None and line_height_points <= 0:
        raise ValueError("line_height_points must be greater than zero")
    if native_leading_em < 0:
        raise ValueError("native_leading_em must not be negative")
    if headroom_points is not None and headroom_points < 0:
        raise ValueError("headroom_points must not be negative")

    headroom = (
        min(12.0, max(6.0, printable_height_points * 0.0125))
        if headroom_points is None
        else min(printable_height_points, headroom_points)
    )
    ordered = sorted(placements, key=lambda item: item.block_index)
    scale = _page_vertical_scale(page)

    def uncalibrated() -> VerticalFitBudget:
        return VerticalFitBudget(
            calibrated=False,
            printable_height=printable_height_points,
            headroom=headroom,
            fixed_ink_height=0.0,
            native_line_box_allowance=0.0,
            leading_gap_height=0.0,
            block_gap_height=0.0,
            row_gap_height=0.0,
            block_gap_scale=1.0,
            row_gap_scale=1.0,
            native_leading_scale=1.0,
            font_size_scale=1.0,
            line_height_scale=1.0,
            geometry_coverage=0.0,
            estimated_line_count=0.0,
            source_glyph_height=0.0,
            estimated_footprint=0.0,
            fits=True,
        )

    if page.metadata.get("column_count", 1) != 1 or not ordered:
        return uncalibrated()
    block_by_id = {block.id: block for block in blocks or ()}
    partial_geometry = any(
        placement.source_bbox is None or placement.source_gap_before is None
        for placement in ordered
    )
    if partial_geometry and (
        not block_by_id or any(placement.block_id not in block_by_id for placement in ordered)
    ):
        return uncalibrated()
    # Markdown providers commonly serialize a side figure after the prose it
    # accompanies even though the figure starts higher on the physical page.
    # That is a valid two-dimensional reading order, not a backwards text
    # flow.  Preserve the monotonic safety check for native text while letting
    # images/tables contribute their fixed height independently below.
    flow_boxes = [
        placement.source_bbox
        for placement in ordered
        if placement.source_bbox is not None
        and (
            (block := block_by_id.get(placement.block_id)) is None
            or block.kind not in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}
        )
    ]

    def is_meaningful_backward_flow(current: PixelBox, following: PixelBox) -> bool:
        if following.y0 >= current.y0:
            return False
        backward = current.y0 - following.y0
        overlap = max(0, min(current.y1, following.y1) - max(current.y0, following.y0))
        overlap_ratio = overlap / max(1, min(current.height, following.height))
        return backward > max(2.0, page.line_pitch * 0.12) and overlap_ratio < 0.55

    if any(
        is_meaningful_backward_flow(current, following)
        for current, following in zip(flow_boxes, flow_boxes[1:], strict=False)
    ):
        return uncalibrated()

    geometry_count = sum(placement.source_bbox is not None for placement in ordered)
    geometry_coverage = geometry_count / len(ordered)
    ordinary_ink_heights = [
        line.bbox.height * scale
        for line in page.text_lines
        if page.line_pitch * 0.45 <= line.bbox.height <= page.line_pitch * 1.25
        and line.bbox.width >= page.content_bbox.width * 0.05
    ]
    source_glyph_height = (
        statistics.median(ordinary_ink_heights) if ordinary_ink_heights else font_size_points
    )

    characters_per_line = max(
        24.0,
        page.content_bbox.width / max(1.0, page.line_pitch * 0.42),
    )

    def rendered_units(placement: HybridBlockPlacement) -> float:
        block = block_by_id.get(placement.block_id)
        if block is None:
            return float(max(1, len(_unique_source_rows(page, placement.source_rows))))
        if block.kind is MarkdownBlockKind.IMAGE and placement.source_bbox is not None:
            return max(2.0, placement.source_bbox.height / page.line_pitch)
        return _block_weight(
            block,
            characters_per_line=characters_per_line,
            image_match=None,
            line_pitch=page.line_pitch,
        )

    placement_units = {placement.block_id: rendered_units(placement) for placement in ordered}
    estimated_line_count = sum(
        units
        for block_id, units in placement_units.items()
        if (block := block_by_id.get(block_id)) is None
        or block.kind not in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}
    )

    # A side figure and its editable question text occupy one native table
    # row in the renderer.  Their vertical footprints overlap rather than add.
    # Mirror that two-dimensional geometry here so the budget does not charge
    # a full image height on top of the prose it accompanies.
    side_visual_ink: dict[str, float] = {}
    group_ids = {block.group_id for block in block_by_id.values() if block.group_id is not None}
    for group_id in group_ids:
        grouped = [
            placement
            for placement in ordered
            if (block := block_by_id.get(placement.block_id)) is not None
            and block.group_id == group_id
            and placement.source_bbox is not None
        ]
        visuals = [
            placement
            for placement in grouped
            if block_by_id[placement.block_id].kind
            in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}
        ]
        flow = [placement for placement in grouped if placement not in visuals]
        if not visuals:
            continue
        visual_boxes = [box for placement in visuals if (box := placement.source_bbox) is not None]
        visual_union = PixelBox(
            x0=min(box.x0 for box in visual_boxes),
            y0=min(box.y0 for box in visual_boxes),
            x1=max(box.x1 for box in visual_boxes),
            y1=max(box.y1 for box in visual_boxes),
        )
        if _forms_horizontal_visual_row(visual_boxes):
            # Separate source crops in one native table row share a vertical
            # footprint. Charge the tallest crop, never their sum. If that
            # row is itself beside editable prose, only its excess remains.
            row_height = max(box.height for box in visual_boxes)
            excess = float(row_height)
            flow_boxes = [box for placement in flow if (box := placement.source_bbox) is not None]
            left_fraction = (visual_union.x0 - page.content_bbox.x0) / max(
                1, page.content_bbox.width
            )
            is_side_row = (
                bool(flow_boxes)
                and left_fraction >= 0.34
                and visual_union.width <= page.content_bbox.width * 0.58
            )
            if is_side_row:
                flow_union = PixelBox(
                    x0=min(box.x0 for box in flow_boxes),
                    y0=min(box.y0 for box in flow_boxes),
                    x1=max(box.x1 for box in flow_boxes),
                    y1=max(box.y1 for box in flow_boxes),
                )
                overlap = max(
                    max(
                        0,
                        min(box.y1, flow_union.y1) - max(box.y0, flow_union.y0),
                    )
                    for box in visual_boxes
                )
                excess = max(0, row_height - min(row_height, overlap))
            side_visual_ink[visuals[0].block_id] = excess * scale
            side_visual_ink.update({placement.block_id: 0.0 for placement in visuals[1:]})
            continue
        if not flow:
            continue
        flow_boxes = [box for placement in flow if (box := placement.source_bbox) is not None]
        left_fraction = (visual_union.x0 - page.content_bbox.x0) / max(1, page.content_bbox.width)
        if left_fraction < 0.34 or visual_union.width > page.content_bbox.width * 0.58:
            continue
        flow_union = PixelBox(
            x0=min(box.x0 for box in flow_boxes),
            y0=min(box.y0 for box in flow_boxes),
            x1=max(box.x1 for box in flow_boxes),
            y1=max(box.y1 for box in flow_boxes),
        )
        overlap = max(
            0,
            min(visual_union.y1, flow_union.y1) - max(visual_union.y0, flow_union.y0),
        )
        if overlap <= 0:
            continue
        excess = max(0, visual_union.height - overlap) * scale
        side_visual_ink[visuals[0].block_id] = excess
        side_visual_ink.update({placement.block_id: 0.0 for placement in visuals[1:]})

    fixed_ink = 0.0
    row_gaps = 0.0
    seen_rows: set[tuple[int, int, int, int]] = set()
    for placement in ordered:
        block = block_by_id.get(placement.block_id)
        if block is not None and block.kind in {
            MarkdownBlockKind.IMAGE,
            MarkdownBlockKind.TABLE,
        }:
            if placement.block_id in side_visual_ink:
                fixed_ink += side_visual_ink[placement.block_id]
            elif placement.source_bbox is not None:
                fixed_ink += placement.source_bbox.height * scale
            else:
                fixed_ink += placement_units[placement.block_id] * source_glyph_height
            continue
        rows = _unique_source_rows(page, placement.source_rows)
        if rows:
            unique_rows = []
            for row in rows:
                key = (row.x0, row.y0, row.x1, row.y1)
                if key in seen_rows:
                    continue
                seen_rows.add(key)
                unique_rows.append(row)
            coarse_row = any(row.height > page.line_pitch * 1.50 for row in unique_rows)
            if (
                coarse_row
                and block is not None
                and not contains_tall_inline_math(block.text)
                and block.kind
                in {
                    MarkdownBlockKind.PARAGRAPH,
                    MarkdownBlockKind.OPTION,
                    MarkdownBlockKind.LIST_ITEM,
                    MarkdownBlockKind.CODE,
                }
            ):
                # Scan segmentation can merge several baselines around a
                # nearby diagram into one 3–5 pitch pseudo-row.  The renderer
                # caps such evidence to ordinary native leading, so estimate
                # its real editable rows from Markdown at the scan-derived
                # glyph height rather than treating the union as immutable ink.
                ordinary_rows = [row for row in unique_rows if row.height <= page.line_pitch * 1.50]
                fixed_ink += sum(row.height for row in ordinary_rows) * scale
                missing_units = max(
                    0.0,
                    placement_units[placement.block_id] - len(ordinary_rows),
                )
                fixed_ink += missing_units * source_glyph_height
            else:
                fixed_ink += sum(row.height for row in unique_rows) * scale
                row_gaps += (
                    sum(
                        max(0, following.y0 - previous.y1)
                        for previous, following in zip(unique_rows, unique_rows[1:], strict=False)
                    )
                    * scale
                )
        else:
            fixed_ink += placement_units[placement.block_id] * source_glyph_height

    leading_gap = float(ordered[0].source_gap_before or 0) * scale
    block_gaps = sum(float(placement.source_gap_before or 0) * scale for placement in ordered[1:])
    # Word owns one native line box for every editable source row, not merely
    # one for every Markdown block.  Counting blocks substantially
    # underestimates aligned equations and wrapped paragraphs: a five-row
    # equation is still one paragraph but consumes five native math rows.
    # The source boxes above measure glyph ink, so this per-row allowance is
    # the renderer leading that must be reserved before retaining scan gaps.
    source_line_count = float(
        sum(
            max(1, len(_unique_source_rows(page, placement.source_rows)))
            for placement in ordered
            if (block := block_by_id.get(placement.block_id)) is None
            or block.kind not in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}
        )
    )
    native_line_count = estimated_line_count if block_by_id else source_line_count
    nominal_line_height = (
        line_height_points
        if line_height_points is not None
        else font_size_points * (1.0 + native_leading_em)
    )
    if line_height_points is not None:
        measured_native_allowance = (
            max(0.0, nominal_line_height - source_glyph_height) * native_line_count
        )
        # Estimated Markdown rows are useful when source geometry is partial,
        # but a small measured leading multiplied by those estimates must not
        # reserve less than the conservative leading for actual mapped rows.
        # Otherwise the difference is incorrectly released into paragraph
        # gaps and can push an atomic equation onto another page.
        source_native_floor = font_size_points * native_leading_em * source_line_count
        native_allowance = max(measured_native_allowance, source_native_floor)
    else:
        native_allowance = font_size_points * native_leading_em * native_line_count
    fixed_total = fixed_ink + native_allowance + leading_gap
    elastic_target = max(0.0, printable_height_points - headroom - fixed_total)
    raw_elastic = block_gaps + row_gaps

    block_scale = 1.0
    row_scale = 1.0
    if raw_elastic > elastic_target + 1e-6:
        if elastic_target >= row_gaps:
            block_scale = (
                min(1.0, max(0.0, (elastic_target - row_gaps) / block_gaps))
                if block_gaps > 0
                else 1.0
            )
        else:
            block_scale = 0.0
            row_scale = min(1.0, max(0.0, elastic_target / row_gaps)) if row_gaps > 0 else 1.0

    estimate = fixed_total + block_gaps * block_scale + row_gaps * row_scale
    target = printable_height_points - headroom
    native_leading_scale = 1.0
    if estimate > target + 1e-6 and native_allowance > 0:
        overflow = estimate - target
        native_leading_scale = max(0.0, 1.0 - overflow / native_allowance)
        estimate -= native_allowance * (1.0 - native_leading_scale)

    option_placements = [
        placement
        for placement in ordered
        if (block := block_by_id.get(placement.block_id)) is not None
        and block.kind is MarkdownBlockKind.OPTION
    ]
    editable_placements = [
        placement
        for placement in ordered
        if (block := block_by_id.get(placement.block_id)) is None
        or block.kind not in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}
    ]
    source_owned_option_sheet = (
        geometry_coverage >= 0.98
        and len(option_placements) >= 8
        and native_line_count >= 32
        and len(option_placements) / max(1, len(editable_placements)) >= 0.5
        and all(
            placement.source_bbox is not None and placement.source_rows
            for placement in option_placements
        )
        and sum(
            placement.geometry_source == "scan_inferred_group_option"
            for placement in option_placements
        )
        / len(option_placements)
        >= 0.75
    )
    option_container_line_scale = 1.0
    if (
        source_owned_option_sheet
        and native_leading_scale < 1.0
        and native_allowance > 0
        and fixed_ink + leading_gap <= target + 1e-6
    ):
        # Dense answer sheets render each physical option row through a native
        # borderless table.  The source-sized table row already owns the
        # container/pagination floor; retaining an additional Markdown line
        # allowance for every A-D paragraph double-counts that height in
        # Word/LibreOffice.  Once normal gap compression has proved the page
        # is dense, use the measured scan glyph box as its line-height floor.
        estimate -= native_allowance * native_leading_scale
        native_leading_scale = 0.0
        # Word-compatible renderers keep a question prompt with its following
        # borderless option table near a page boundary.  Reserve the prompt
        # plus the tallest observed option grid, then amortize that atomic
        # container over the mapped native rows.  This adjusts line boxes only;
        # editable font sizes remain unchanged.
        rows_by_group: dict[str, set[tuple[int, int]]] = {}
        for placement in option_placements:
            block = block_by_id[placement.block_id]
            group_id = block.group_id or placement.block_id
            box = placement.source_bbox
            if box is not None:
                rows_by_group.setdefault(group_id, set()).add((box.y0, box.y1))
        tallest_option_grid = max(
            (len(rows) for rows in rows_by_group.values()),
            default=1,
        )
        pagination_reserve_rows = float(1 + min(4, tallest_option_grid))
        option_container_line_scale = native_line_count / (
            native_line_count + pagination_reserve_rows
        )
        estimate = max(
            0.0,
            estimate - source_glyph_height * pagination_reserve_rows,
        )

    font_size_scale = 1.0
    if estimate > target + 1e-6 and source_glyph_height < font_size_points:
        # The scan's ordinary glyph ink is the only permitted font-size floor.
        # No document-specific point size is introduced here.
        font_size_scale = max(1e-6, source_glyph_height / font_size_points)
        scalable_ink = fixed_ink
        estimate -= scalable_ink * (1.0 - font_size_scale)

    calibrated_line_height = (
        source_glyph_height
        + max(
            0.0,
            nominal_line_height - source_glyph_height,
        )
        * native_leading_scale
    ) * option_container_line_scale
    line_height_scale = min(1.0, calibrated_line_height / nominal_line_height)
    return VerticalFitBudget(
        calibrated=True,
        printable_height=printable_height_points,
        headroom=headroom,
        fixed_ink_height=fixed_ink,
        native_line_box_allowance=native_allowance,
        leading_gap_height=leading_gap,
        block_gap_height=block_gaps,
        row_gap_height=row_gaps,
        block_gap_scale=block_scale,
        row_gap_scale=row_scale,
        native_leading_scale=native_leading_scale,
        font_size_scale=font_size_scale,
        line_height_scale=line_height_scale,
        geometry_coverage=geometry_coverage,
        estimated_line_count=native_line_count,
        source_glyph_height=source_glyph_height,
        estimated_footprint=estimate,
        fits=estimate <= target + 1e-6,
    )


def apply_page_vertical_fit_budget(
    page: ScanPageLayout,
    placements: Sequence[HybridBlockPlacement],
    budget: VerticalFitBudget,
) -> list[HybridBlockPlacement]:
    """Return placement copies with only elastic vertical gaps adjusted."""

    if not budget.calibrated or (budget.block_gap_scale >= 1.0 and budget.row_gap_scale >= 1.0):
        return list(placements)

    ordered = sorted(placements, key=lambda item: item.block_index)
    first_id = ordered[0].block_id if ordered else None
    adjusted: dict[str, HybridBlockPlacement] = {}
    for placement in ordered:
        update: dict[str, object] = {}
        if placement.block_id != first_id and placement.source_gap_before is not None:
            update["source_gap_before"] = round(
                placement.source_gap_before * budget.block_gap_scale
            )
        rows = _unique_source_rows(page, placement.source_rows)
        if budget.row_gap_scale < 1.0 and len(rows) > 1:
            compressed = [rows[0]]
            for source_index, row in enumerate(rows[1:], start=1):
                previous_source = rows[source_index - 1]
                previous_adjusted = compressed[-1]
                gap = max(0, row.y0 - previous_source.y1)
                y0 = previous_adjusted.y1 + round(gap * budget.row_gap_scale)
                compressed.append(PixelBox(x0=row.x0, y0=y0, x1=row.x1, y1=y0 + row.height))
            update["source_rows"] = compressed
            if placement.source_bbox is not None:
                delta = compressed[-1].y1 - rows[-1].y1
                update["source_bbox"] = placement.source_bbox.model_copy(
                    update={
                        "y1": max(
                            placement.source_bbox.y0 + 1,
                            placement.source_bbox.y1 + delta,
                        )
                    }
                )
        adjusted[placement.block_id] = placement.model_copy(update=update)
    return [adjusted.get(placement.block_id, placement) for placement in placements]


def equation_layout_units(latex: str) -> float:
    """Estimate vertical source-line units for one editable display equation."""

    rows = equation_row_count(latex)
    tall = bool(re.search(r"\\(?:frac|dfrac|tfrac|int|sum|prod|sqrt)\b", latex))
    per_row = 1.62 if tall else 1.18
    return max(1.25, rows * per_row)


def _visible_text(text: str) -> str:
    return text.replace("<eq>", "").replace("</eq>", "").replace("$", "").replace("\\%", "%")


def _project_inline_math(text: str) -> str:
    return re.sub(r"\$([^$]+)\$", lambda match: latex_visible_text(match.group(1)), text)


def _merge_visual_rows(rows: list[PixelBox], line_pitch: float) -> list[PixelBox]:
    """Merge glyph fragments that describe the same visual baseline."""

    center_tolerance = max(3.0, line_pitch * 0.45)
    merged: list[PixelBox] = []
    for box in sorted(rows, key=lambda item: (item.y0, item.x0)):
        previous_center = (merged[-1].y0 + merged[-1].y1) / 2.0 if merged else float("-inf")
        center = (box.y0 + box.y1) / 2.0
        if merged and abs(center - previous_center) <= center_tolerance:
            previous = merged[-1]
            merged[-1] = PixelBox(
                x0=min(previous.x0, box.x0),
                y0=min(previous.y0, box.y0),
                x1=max(previous.x1, box.x1),
                y1=max(previous.y1, box.y1),
            )
        else:
            merged.append(box)
    return merged


# A numerator, its bar and its denominator are stacked inside one horizontal
# span, so genuine fragments always share some of it: the narrowest genuine
# overlap observed across the corpus is 25% of the smaller box, and most are
# 97-100%. A heading swallowed by the equation beside it shared none at all.
# The threshold only has to exclude that degenerate case, so it sits well below
# the genuine minimum rather than being tuned to any particular document.
_FRAGMENT_STACK_OVERLAP = 0.12


def _merge_single_column_row_fragments(
    rows: list[PixelBox],
    line_pitch: float,
    *,
    fragmented_page: bool | None = None,
) -> list[PixelBox]:
    """Merge vertically adjacent glyph fragments into logical source slots.

    Tall display equations are frequently detected as separate numerator,
    fraction-bar, and denominator rows.  Their fragments form a transitive
    chain separated by no more than a small fraction of the measured baseline
    pitch.  Uniform prose baselines remain distinct: a positive-gap merge also
    requires component-sized ink and materially different horizontal extents,
    while deeply overlapping line boxes are treated as separate baselines.
    """

    maximum_gap = max(2.0, line_pitch * 0.35)
    maximum_overlap = max(2.0, line_pitch * 0.15)
    component_height = line_pitch * 0.90
    minimum_edge_shift = max(3.0, line_pitch * 0.35)
    if fragmented_page is None:
        vertical_span = max(box.y1 for box in rows) - min(box.y0 for box in rows)
        baseline_capacity = max(1.0, vertical_span / max(1.0, line_pitch))
        # A page whose detector already reports roughly one band per baseline
        # is ordinary dense text: small overlaps there are dilation, not
        # evidence of a fraction/equation component. Sparse pages with many
        # vertically split formula fragments need the overlap merge.
        fragmented_page = len(rows) / baseline_capacity < 0.82
    merged: list[PixelBox] = []
    previous_fragment: PixelBox | None = None
    for box in sorted(rows, key=lambda item: (item.y0, item.x0)):
        if not merged:
            merged.append(box)
            previous_fragment = box
            continue
        current = merged[-1]
        assert previous_fragment is not None
        gap = box.y0 - current.y1
        horizontal_change = max(
            abs(box.x0 - previous_fragment.x0),
            abs(box.x1 - previous_fragment.x1),
        )
        width_ratio = min(box.width, previous_fragment.width) / max(
            box.width,
            previous_fragment.width,
        )
        component_shape = horizontal_change >= minimum_edge_shift or width_ratio <= 0.92
        # A numerator, its fraction bar and its denominator are stacked inside
        # the same horizontal span, so genuine fragments overlap almost
        # completely: across the showcase corpus every real merge shares 97-100%
        # of the narrower box. Two pieces of ink that barely share horizontal
        # extent are separate baselines, and joining them swallowed a short
        # left-margin heading into the display equation beside it.
        horizontal_overlap = max(
            0.0,
            min(box.x1, previous_fragment.x1) - max(box.x0, previous_fragment.x0),
        )
        narrower_width = max(1.0, min(box.width, previous_fragment.width))
        stacked = horizontal_overlap / narrower_width >= _FRAGMENT_STACK_OVERLAP
        component_pair = (
            min(box.height, previous_fragment.height) <= component_height
            and component_shape
            and stacked
        )
        # Scan line detectors commonly dilate adjacent prose baselines until
        # their boxes overlap by one or two pixels.  Treating that overlap as
        # fraction evidence creates transitive mega-rows.  Permit overlap-only
        # merging on a demonstrably fragmented page; dense baseline streams
        # still require an independent component shape and a positive gap.
        overlap_component = fragmented_page and -maximum_overlap <= gap <= 0
        if (0 <= gap <= maximum_gap and component_pair) or overlap_component:
            merged[-1] = PixelBox(
                x0=min(current.x0, box.x0),
                y0=min(current.y0, box.y0),
                x1=max(current.x1, box.x1),
                y1=max(current.y1, box.y1),
            )
        else:
            merged.append(box)
        previous_fragment = box
    return merged


def _page_column_boxes(page: ScanPageLayout) -> list[PixelBox]:
    """Return validated left-to-right column boxes from scan metadata."""

    raw_count = page.metadata.get("column_count")
    raw_boxes = page.metadata.get("column_boxes")
    if not isinstance(raw_count, int) or raw_count < 2 or not isinstance(raw_boxes, list):
        return []
    boxes: list[PixelBox] = []
    for raw_box in raw_boxes:
        if not isinstance(raw_box, list) or len(raw_box) != 4:
            return []
        try:
            box = PixelBox(x0=raw_box[0], y0=raw_box[1], x1=raw_box[2], y1=raw_box[3])
        except (TypeError, ValueError):
            return []
        boxes.append(box)
    if len(boxes) != raw_count or not 2 <= len(boxes) <= 4:
        return []
    boxes.sort(key=lambda item: item.x0)
    if any(left.x1 > right.x0 for left, right in zip(boxes, boxes[1:], strict=False)):
        return []
    return boxes


def _line_is_excluded(box: PixelBox, anchors: list[PixelBox]) -> bool:
    return any(
        (
            max(0, min(box.y1, anchor.y1) - max(box.y0, anchor.y0)) / max(1, box.height) >= 0.55
            and max(0, min(box.x1, anchor.x1) - max(box.x0, anchor.x0)) / max(1, box.width) >= 0.55
        )
        for anchor in anchors
    )


def source_row_reading_order(
    page: ScanPageLayout,
    rows: Sequence[PixelBox],
) -> list[PixelBox]:
    """Order source rows by page flow, including newspaper column transitions."""

    columns = _page_column_boxes(page)
    if not columns:
        return sorted(rows, key=lambda box: (box.y0, box.x0))

    return sorted(
        rows,
        key=lambda box: (_source_flow_band(page, box, columns), box.y0, box.x0),
    )


def _source_flow_band(
    page: ScanPageLayout,
    box: PixelBox,
    columns: Sequence[PixelBox] | None = None,
) -> int:
    """Return the monotonic reading-order band that owns a source box."""

    resolved_columns = list(columns) if columns is not None else _page_column_boxes(page)
    if not resolved_columns:
        return 0
    body_top = min(column.y0 for column in resolved_columns)
    body_bottom = max(column.y1 for column in resolved_columns)
    center_y = (box.y0 + box.y1) / 2.0
    if center_y < body_top - page.line_pitch * 0.25:
        return -1
    overlaps = [
        max(0, min(box.x1, column.x1) - max(box.x0, column.x0)) for column in resolved_columns
    ]
    if max(overlaps, default=0) > 0:
        return max(range(len(resolved_columns)), key=overlaps.__getitem__)
    if center_y > body_bottom + page.line_pitch * 0.25:
        return len(resolved_columns)
    center_x = (box.x0 + box.x1) / 2.0
    return min(
        range(len(resolved_columns)),
        key=lambda index: abs(
            center_x - (resolved_columns[index].x0 + resolved_columns[index].x1) / 2.0
        ),
    )


def visual_text_row_groups(
    page: ScanPageLayout,
    anchors: list[PixelBox] | None = None,
) -> list[list[PixelBox]]:
    """Return OCR-free logical rows in source reading-order groups.

    Fraction bars and glyph components are often emitted as separate scan-line
    hypotheses.  Grouping nearby hypotheses by the measured page rhythm gives
    renderers and validators a common geometric unit without recognizing or
    changing the source text.  For detected newspaper layouts, a full-width
    prefix is followed by one group per body column in left-to-right order.
    Keeping those groups separate prevents a content block from borrowing rows
    across a gutter merely because the baselines share the same y coordinate.
    """

    excluded = anchors or []
    lines = sorted(page.text_lines, key=lambda item: (item.bbox.y0, item.bbox.x0))
    column_boxes = _page_column_boxes(page)
    if not column_boxes:
        all_segments = [segment for line in lines for segment in (line.segments or [line.bbox])]
        all_baseline_rows = _merge_visual_rows(all_segments, page.line_pitch)
        if not all_baseline_rows:
            return [[]]
        all_vertical_span = max(row.y1 for row in all_baseline_rows) - min(
            row.y0 for row in all_baseline_rows
        )
        all_baseline_capacity = max(1.0, all_vertical_span / max(1.0, page.line_pitch))
        fragmented_page = len(all_baseline_rows) / all_baseline_capacity < 0.82
        visible_segments = [
            segment
            for line in lines
            for segment in (line.segments or [line.bbox])
            if not _line_is_excluded(segment, excluded)
        ]
        if not visible_segments:
            return [[]]
        baseline_rows = _merge_visual_rows(visible_segments, page.line_pitch)
        return [
            _merge_single_column_row_fragments(
                baseline_rows,
                page.line_pitch,
                fragmented_page=fragmented_page,
            )
        ]

    tolerance = page.line_pitch * 0.25
    body_top = min(box.y0 for box in column_boxes)
    raw_bottoms = page.metadata.get("column_content_bottoms")
    bottoms = [box.y1 for box in column_boxes]
    if isinstance(raw_bottoms, list) and len(raw_bottoms) == len(column_boxes):
        for index, raw_bottom in enumerate(raw_bottoms):
            if isinstance(raw_bottom, int):
                bottoms[index] = min(column_boxes[index].y1, max(body_top, raw_bottom))
    maximum_bottom = max(bottoms)
    prefix: list[PixelBox] = []
    suffix: list[PixelBox] = []
    column_rows: list[list[PixelBox]] = [[] for _ in column_boxes]
    median_column_width = statistics.median(box.width for box in column_boxes)
    anchored_prefix_bottom = max(
        (anchor.y1 for anchor in excluded if anchor.y0 <= body_top + page.line_pitch),
        default=body_top,
    )
    spanning_deadline = max(body_top, anchored_prefix_bottom) + page.line_pitch * 4.0
    for line in lines:
        segments = [
            segment
            for segment in (line.segments or [line.bbox])
            if not _line_is_excluded(segment, excluded)
        ]
        if not segments:
            continue
        visible_bbox = PixelBox(
            x0=min(segment.x0 for segment in segments),
            y0=min(segment.y0 for segment in segments),
            x1=max(segment.x1 for segment in segments),
            y1=max(segment.y1 for segment in segments),
        )
        center = (visible_bbox.y0 + visible_bbox.y1) / 2.0
        if center < body_top - tolerance:
            prefix.append(visible_bbox)
            continue
        if center > maximum_bottom + tolerance:
            suffix.append(visible_bbox)
            continue
        # Newspaper headlines and standfirsts can span two body columns just
        # below a full-width masthead.  They are distinguished from ordinary
        # simultaneous column baselines by their tall ink envelope; width
        # alone is insufficient because a normal three-column row also spans
        # the page in the detector's union bbox.
        if (
            center <= spanning_deadline
            and visible_bbox.height >= page.line_pitch * 1.28
            and visible_bbox.width >= median_column_width * 1.45
        ):
            prefix.append(visible_bbox)
            continue
        for index, column in enumerate(column_boxes):
            if center > bottoms[index] + tolerance:
                continue
            clipped: list[PixelBox] = []
            for segment in segments:
                x0 = max(segment.x0, column.x0)
                x1 = min(segment.x1, column.x1)
                if x1 - x0 < 2:
                    continue
                clipped.append(
                    PixelBox(
                        x0=x0,
                        y0=segment.y0,
                        x1=x1,
                        y1=segment.y1,
                    )
                )
            if clipped:
                column_rows[index].extend(clipped)

    groups: list[list[PixelBox]] = []
    merged_prefix = _merge_visual_rows(prefix, page.line_pitch)
    if merged_prefix:
        groups.append(merged_prefix)
    detached_suffix: list[PixelBox] = []
    for rows in column_rows:
        merged = _merge_visual_rows(rows, page.line_pitch)
        if not merged:
            continue
        # A folio, handwritten identifier, or isolated bottom ornament often
        # extends one column's measured content bottom.  Detach only a short
        # trailing tail after a conspicuously large gap; ordinary section
        # spacing inside the column remains part of the body flow.
        for index in range(len(merged) - 1, 0, -1):
            gap = merged[index].y0 - merged[index - 1].y1
            tail_length = len(merged) - index
            if gap > page.line_pitch * 1.8 and tail_length <= max(2, math.ceil(len(merged) * 0.15)):
                detached_suffix.extend(merged[index:])
                merged = merged[:index]
                break
        if merged:
            groups.append(merged)
    suffix.extend(detached_suffix)
    merged_suffix = _merge_visual_rows(suffix, page.line_pitch)
    if merged_suffix:
        groups.append(merged_suffix)
    return groups


def visual_text_rows(
    page: ScanPageLayout,
    anchors: list[PixelBox] | None = None,
) -> list[PixelBox]:
    """Return logical source rows flattened in editable reading order."""

    return [row for group in visual_text_row_groups(page, anchors) for row in group]


def _snap_evidence_rows_to_scan(
    blocks: list[MarkdownBlock],
    placements: list[HybridBlockPlacement],
    page: ScanPageLayout,
) -> list[HybridBlockPlacement]:
    """Replace coarse provider boxes with OCR-free source line geometry.

    Provider JSON commonly stores one rectangle for a many-line paragraph.
    Keeping that rectangle as a single ``source_row`` makes native leading and
    QA coverage misleading.  The provider still decides which Markdown block
    owns the region; the original raster supplies the actual row rhythm.
    """

    visual_anchors = [
        placement.source_bbox
        for block, placement in zip(blocks, placements, strict=True)
        if placement.source_bbox is not None
        and block.kind in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}
    ]
    scan_rows = source_row_reading_order(page, visual_text_rows(page, visual_anchors))
    if not scan_rows:
        return placements

    def matches(row: PixelBox, region: PixelBox) -> bool:
        horizontal = max(0, min(row.x1, region.x1) - max(row.x0, region.x0))
        if horizontal / max(1, min(row.width, region.width)) < 0.30:
            return False
        vertical = max(0, min(row.y1, region.y1) - max(row.y0, region.y0))
        row_center = (row.y0 + row.y1) / 2.0
        return (
            vertical / max(1, row.height) >= 0.20
            or region.y0 - page.line_pitch * 0.20
            <= row_center
            <= region.y1 + page.line_pitch * 0.20
        )

    result: list[HybridBlockPlacement] = []
    for block, placement in zip(blocks, placements, strict=True):
        if (
            placement.source_bbox is None
            or not placement.evidence_providers
            or block.kind in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}
        ):
            result.append(placement)
            continue
        regions = placement.source_rows or [placement.source_bbox]
        snapped = [row for row in scan_rows if any(matches(row, region) for region in regions)]
        result.append(
            placement.model_copy(update={"source_rows": snapped}) if snapped else placement
        )
    return result


def _option_column_count(options: Sequence[MarkdownBlock]) -> int:
    longest = max((len(_project_inline_math(block.text)) for block in options), default=0)
    has_nary = any(re.search(r"\\(?:int|oint|sum|prod)\b", block.text) for block in options)
    if len(options) == 4 and has_nary:
        return 2 if longest <= 115 else 1
    if len(options) == 4 and longest <= 55:
        return 4
    if len(options) == 4 and longest <= 115:
        return 2
    return 1


def _merge_segments_to_columns(
    segments: Sequence[PixelBox],
    columns: int,
) -> list[PixelBox]:
    merged = sorted(segments, key=lambda box: box.x0)
    while len(merged) > columns:
        index = min(
            range(len(merged) - 1),
            key=lambda item: max(0, merged[item + 1].x0 - merged[item].x1),
        )
        left = merged[index]
        right = merged[index + 1]
        merged[index : index + 2] = [
            PixelBox(
                x0=min(left.x0, right.x0),
                y0=min(left.y0, right.y0),
                x1=max(left.x1, right.x1),
                y1=max(left.y1, right.y1),
            )
        ]
    return merged


def _option_region_ink(page: ScanPageLayout) -> Any:
    """Recreate the analyzer's illumination-normalized ink mask once per page.

    Global scan rows deliberately favor stable page-wide rhythm.  A dense exam
    page can therefore merge two compact answer baselines when a large heading
    inflated the global pitch.  Recomputing only the binary mask here is cheap;
    the coarse provider rectangle still owns the region and no text is read.
    """

    from docreconstruct.reconstruction.scan_layout import ink_mask

    return ink_mask(page.image.convert("L"))


def _localized_option_rows(
    page: ScanPageLayout,
    region: PixelBox,
    ink: Any | None,
) -> list[ScanTextLine]:
    """Return compact baselines whose left rail is protected from side notes.

    Answer labels normally start in the leading half of a question rectangle,
    even when a two-column row continues on its right.  Detecting baselines in
    that rail prevents handwritten working beside a vertical A--D list from
    masquerading as a second answer column.  Synthetic/no-raster callers retain
    the supplied scan-line fallback.
    """

    localized: list[ScanTextLine] = []
    if ink is not None:
        from docreconstruct.reconstruction.scan_layout import _detect_text_lines

        rail = PixelBox(
            x0=region.x0,
            y0=region.y0,
            x1=max(region.x0 + 1, min(region.x1, round(region.x0 + region.width * 0.52))),
            y1=region.y1,
        )
        local_pitch = max(18.0, min(26.0, page.line_pitch * 0.62))
        localized, _refined_pitch = _detect_text_lines(ink, rail, local_pitch)
        # Provider rectangles often stop a few pixels inside the following
        # baseline.  The region crop then turns that neighboring text into a
        # very short terminal "row".  It cannot carry reliable option
        # geometry and, because option grids are selected from the trailing
        # rows, would otherwise displace the real A--D row immediately above.
        minimum_edge_height = max(4.0, local_pitch * 0.32)
        localized = [
            line
            for line in localized
            if not (
                (line.bbox.y0 <= region.y0 + 1 or line.bbox.y1 >= region.y1 - 1)
                and line.bbox.height < minimum_edge_height
            )
        ]
    if localized:
        return localized
    return [
        line
        for line in page.text_lines
        if region.y0 <= (line.bbox.y0 + line.bbox.y1) / 2 <= region.y1
    ]


def _expand_option_rows(
    rows: Sequence[ScanTextLine],
    region: PixelBox,
    page: ScanPageLayout,
    ink: Any | None,
) -> list[tuple[PixelBox, list[PixelBox]]]:
    """Expand left-rail baselines to full-width row fragments.

    Midpoint boundaries keep neighboring glyph bands disjoint.  Returning the
    complete row box separately from its fragments lets every sibling on a
    horizontal answer row share one vertical-fit row while retaining its own
    source slot.
    """

    if not rows:
        return []
    ordered = sorted(rows, key=lambda line: (line.bbox.y0, line.bbox.x0))
    centers = [(line.bbox.y0 + line.bbox.y1) / 2.0 for line in ordered]
    expanded: list[tuple[PixelBox, list[PixelBox]]] = []
    if ink is not None and not bool(ink[region.y0 : region.y1, region.x0 : region.x1].any()):
        ink = None
    if ink is None:
        for line in ordered:
            segments = [
                segment
                for segment in (line.segments or [line.bbox])
                if segment.width >= max(3, page.line_pitch * 0.20)
            ]
            if segments:
                expanded.append((line.bbox, segments))
        return expanded

    from docreconstruct.reconstruction.scan_layout import _merge_runs, _require_numpy, _runs

    np = _require_numpy()
    for index, center in enumerate(centers):
        top = region.y0 if index == 0 else round((centers[index - 1] + center) / 2.0)
        bottom = (
            region.y1 if index + 1 == len(centers) else round((center + centers[index + 1]) / 2.0)
        )
        if bottom <= top:
            continue
        band = ink[top:bottom, region.x0 : region.x1]
        column_counts = np.count_nonzero(band, axis=0)
        threshold = max(1, round((bottom - top) * 0.055))
        raw_runs = _merge_runs(
            _runs(column_counts >= threshold),
            gap=max(3, round(region.width * 0.008)),
        )
        segments = [
            PixelBox(x0=region.x0 + start, y0=top, x1=region.x0 + end, y1=bottom)
            for start, end in raw_runs
            if end - start >= 3
        ]
        if not segments:
            continue
        row = PixelBox(
            x0=min(segment.x0 for segment in segments),
            y0=top,
            x1=max(segment.x1 for segment in segments),
            y1=bottom,
        )
        expanded.append((row, segments))
    return expanded


def _option_grid_slots(
    rows: Sequence[tuple[PixelBox, list[PixelBox]]],
    option_count: int,
    page: ScanPageLayout,
    region: PixelBox,
) -> tuple[list[PixelBox], list[PixelBox]] | None:
    """Choose the strongest physical A--D topology and return row-major slots.

    Four-across and two-column candidates must be demonstrated by substantial,
    separated ink in every trailing row.  A one-column list is the conservative
    fallback.  This makes right-margin handwriting insufficient evidence for a
    grid while preserving genuine 1x4 and 2x2 answer arrangements.
    """

    if option_count < 1:
        return None
    choices = [columns for columns in (4, 2, 1) if option_count % columns == 0]
    for columns in choices:
        rows_needed = option_count // columns
        if len(rows) <= rows_needed:
            continue
        selected = list(rows[-rows_needed:])
        slot_rows: list[list[PixelBox]] = []
        valid = True
        for _row, fragments in selected:
            merged = _merge_segments_to_columns(fragments, columns)
            if len(merged) != columns:
                valid = False
                break
            if columns > 1:
                # Short numeric choices can legitimately occupy only a small
                # fraction of a wide provider envelope.  The pitch floor plus
                # a 5.5% envelope share still rejects isolated handwriting
                # strokes while retaining compact one-line A--D grids.
                minimum_width = max(page.line_pitch * 1.10, region.width * 0.055)
                minimum_gutter = max(page.line_pitch * 0.45, region.width * 0.04)
                if (
                    min(slot.width for slot in merged) < minimum_width
                    or min(
                        right.x0 - left.x1 for left, right in zip(merged, merged[1:], strict=False)
                    )
                    < minimum_gutter
                ):
                    valid = False
                    break
            slot_rows.append(merged)
        if not valid:
            continue
        if columns > 1 and len(slot_rows) > 1:
            for column in range(columns):
                starts = [row[column].x0 for row in slot_rows]
                if max(starts) - min(starts) > region.width * 0.14:
                    valid = False
                    break
        if not valid:
            continue
        physical_rows = [
            PixelBox(
                x0=min(slot.x0 for slot in slots),
                y0=source_row.y0,
                x1=max(slot.x1 for slot in slots),
                y1=source_row.y1,
            )
            for (source_row, _fragments), slots in zip(selected, slot_rows, strict=True)
        ]
        slots = [slot for row in slot_rows for slot in row]
        if len(slots) == option_count:
            return slots, physical_rows
    return None


def _assign_group_option_geometry(
    blocks: list[MarkdownBlock],
    placements: list[HybridBlockPlacement],
    page: ScanPageLayout,
) -> list[HybridBlockPlacement]:
    """Split option columns out of a coarse same-group evidence region.

    OCR layout JSON often gives one rectangle to a question prompt and its
    A--D answer row, while Markdown intentionally exposes each answer as an
    editable block.  Raw scan segments can safely restore those answer slots
    when the prompt and every option share a structural group.  Candidate
    rows stay inside the provider rectangle and never overlap image/table
    anchors, so geometry cannot be borrowed from another question or across a
    visual object.
    """

    block_by_id = {block.id: block for block in blocks}
    placement_by_id = {placement.block_id: placement for placement in placements}
    visual_anchors = [
        placement.source_bbox
        for placement in placements
        if placement.source_bbox is not None
        and block_by_id[placement.block_id].kind
        in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}
    ]
    groups: dict[str, list[MarkdownBlock]] = {}
    for block in blocks:
        if block.group_id:
            groups.setdefault(block.group_id, []).append(block)

    ink = _option_region_ink(page) if groups else None
    assigned: dict[str, HybridBlockPlacement] = {}
    for group_blocks in groups.values():
        options = [block for block in group_blocks if block.kind is MarkdownBlockKind.OPTION]
        if not options or any(
            placement_by_id[block.id].source_bbox is not None for block in options
        ):
            continue
        owners = [
            block
            for block in group_blocks
            if block.starts_group
            and block.index < options[0].index
            and block.kind not in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}
            and placement_by_id[block.id].source_bbox is not None
            and placement_by_id[block.id].evidence_providers
        ]
        if len(owners) != 1:
            continue
        owner = owners[0]
        owner_placement = placement_by_id[owner.id]
        assert owner_placement.source_bbox is not None
        if any(
            block.kind in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}
            and owner.index < block.index < options[-1].index
            for block in group_blocks
        ):
            continue

        # Prefer the analyzer's stable page-wide baselines when they already
        # demonstrate a complete, well-separated option grid.  Besides being
        # cheaper, these exact source rows remain consistent with QA geometry.
        # Dense pages whose global pitch merged compact answers fail the grid
        # checks and fall through to localized left-rail detection below.
        global_rows: list[tuple[PixelBox, list[PixelBox]]] = []
        for line in page.text_lines:
            center = (line.bbox.y0 + line.bbox.y1) / 2.0
            if not (
                owner_placement.source_bbox.y0 <= center <= owner_placement.source_bbox.y1
            ) or _line_is_excluded(line.bbox, visual_anchors):
                continue
            fragments = [
                segment
                for segment in (line.segments or [line.bbox])
                if segment.width >= max(3, page.line_pitch * 0.20)
            ]
            if fragments:
                global_rows.append((line.bbox, fragments))
        candidate_rows = global_rows
        grid = _option_grid_slots(
            candidate_rows,
            len(options),
            page,
            owner_placement.source_bbox,
        )
        if grid is None:
            localized = [
                line
                for line in _localized_option_rows(page, owner_placement.source_bbox, ink)
                if not _line_is_excluded(line.bbox, visual_anchors)
            ]
            candidate_rows = _expand_option_rows(
                localized,
                owner_placement.source_bbox,
                page,
                ink,
            )
            grid = _option_grid_slots(
                candidate_rows,
                len(options),
                page,
                owner_placement.source_bbox,
            )
        if grid is None:
            continue
        slots, option_rows = grid
        prompt_rows = [row for row, _fragments in candidate_rows[: -len(option_rows)]]
        if not prompt_rows:
            continue

        assigned[owner.id] = owner_placement.model_copy(update={"source_rows": prompt_rows})
        columns = len(slots) // len(option_rows)
        for option_index, (option, slot) in enumerate(zip(options, slots, strict=True)):
            placement = placement_by_id[option.id]
            assigned[option.id] = placement.model_copy(
                update={
                    "source_bbox": slot,
                    # Horizontal siblings share one physical source row.  The
                    # vertical-fit budget consequently charges that row once.
                    "source_rows": [option_rows[option_index // columns]],
                    "match_score": 1.0,
                    "geometry_source": "scan_inferred_group_option",
                }
            )

    return [assigned.get(placement.block_id, placement) for placement in placements]


def _desired_row_count(block: MarkdownBlock, characters_per_line: float) -> int:
    if block.kind in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}:
        return 0
    if block.kind is MarkdownBlockKind.RULE:
        return 1
    if block.kind is MarkdownBlockKind.EQUATION:
        return equation_row_count(block.text)
    if block.kind is MarkdownBlockKind.CODE:
        return max(1, block.text.count("\n") + 1)
    if block.kind is MarkdownBlockKind.HEADING:
        return 1
    visible = _project_inline_math(block.text)
    return max(1, math.ceil(len(visible) / max(1.0, characters_per_line)))


def _assign_text_geometry(
    blocks: list[MarkdownBlock],
    placements: list[HybridBlockPlacement],
    page: ScanPageLayout,
) -> list[HybridBlockPlacement]:
    """Map editable blocks to source rows without recognizing or changing text."""

    anchored = [
        row
        for placement in placements
        if placement.source_bbox is not None
        for row in (placement.source_rows or [placement.source_bbox])
    ]
    row_groups = visual_text_row_groups(page, anchored)
    rows = [row for group in row_groups for row in group]
    column_boxes = _page_column_boxes(page)
    multi_column = bool(column_boxes)
    eligible = [
        (block, placement)
        for block, placement in zip(blocks, placements, strict=True)
        if placement.source_bbox is None
        and block.kind not in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}
    ]
    units: list[list[MarkdownBlock]] = []
    cursor = 0
    while cursor < len(eligible):
        block = eligible[cursor][0]
        if block.kind is not MarkdownBlockKind.OPTION:
            units.append([block])
            cursor += 1
            continue
        end = cursor + 1
        while end < len(eligible) and eligible[end][0].kind is MarkdownBlockKind.OPTION:
            end += 1
        units.append([item[0] for item in eligible[cursor:end]])
        cursor = end
    if not rows or not units:
        return placements
    characters_per_line = max(
        24.0,
        page.content_bbox.width / max(1.0, page.line_pitch * 0.42),
    )

    def desired_counts(
        segment_units: list[list[MarkdownBlock]],
        row_characters_per_line: float,
    ) -> list[int]:
        counts: list[int] = []
        for unit in segment_units:
            if unit[0].kind is not MarkdownBlockKind.OPTION:
                counts.append(_desired_row_count(unit[0], row_characters_per_line))
                continue
            columns = _option_column_count(unit)
            counts.append(max(1, math.ceil(len(unit) / columns)))
        return counts

    assigned: dict[str, tuple[list[PixelBox], int]] = {}

    def assign_segment(
        segment_units: list[list[MarkdownBlock]],
        segment_rows: list[PixelBox],
        *,
        previous_bottom: int,
        row_characters_per_line: float,
        consume_all_rows: bool,
    ) -> None:
        if not segment_units or not segment_rows:
            return
        counts = desired_counts(segment_units, row_characters_per_line)
        flexible = [
            index
            for index, unit in enumerate(segment_units)
            if unit[0].kind
            not in {MarkdownBlockKind.EQUATION, MarkdownBlockKind.HEADING, MarkdownBlockKind.CODE}
        ]
        # Extra visual rows within the same anchor-bounded interval may be real
        # wrapping and remain useful evidence.  They must never be borrowed
        # from the other side of an image/table anchor.
        expected = sum(counts)
        expansion_limit = len(segment_rows)
        if not consume_all_rows and len(segment_rows) > expected:
            # Column detectors can retain an isolated rule, folio, or handwritten
            # annotation as a row.  A modest wrapping correction is useful, but
            # consuming an arbitrarily large surplus would stretch editable text
            # across that noise and often into the following column.
            allowance = max(2, math.ceil(expected * 0.40))
            expansion_limit = min(len(segment_rows), expected + allowance)
        while sum(counts) < expansion_limit and flexible:
            index = max(
                flexible,
                key=lambda item: (
                    sum(len(_project_inline_math(block.text)) for block in segment_units[item])
                    / counts[item]
                ),
            )
            counts[index] += 1
        while sum(counts) > len(segment_rows):
            reducible = [index for index in flexible if counts[index] > 1]
            if not reducible and not consume_all_rows:
                reducible = [index for index, count in enumerate(counts) if count > 1]
            if not reducible:
                return
            index = min(
                reducible,
                key=lambda item: (
                    sum(len(_project_inline_math(block.text)) for block in segment_units[item])
                    / counts[item]
                ),
            )
            counts[index] -= 1
        if consume_all_rows and sum(counts) != len(segment_rows):
            return
        row_cursor = 0
        bottom = previous_bottom
        for unit, count in zip(segment_units, counts, strict=True):
            block_rows = segment_rows[row_cursor : row_cursor + count]
            row_cursor += count
            gap = max(0, block_rows[0].y0 - bottom)
            for member_index, block in enumerate(unit):
                assigned[block.id] = (block_rows, gap if member_index == 0 else 0)
            bottom = block_rows[-1].y1

    def group_characters_per_line(group: list[PixelBox]) -> float:
        if not multi_column:
            return characters_per_line
        for column in column_boxes:
            if all(row.x0 >= column.x0 and row.x1 <= column.x1 for row in group):
                return max(
                    18.0,
                    column.width / max(1.0, page.line_pitch * 0.42),
                )
        return characters_per_line

    def partition_units(
        segment_units: list[list[MarkdownBlock]],
        segment_groups: list[list[PixelBox]],
    ) -> list[tuple[list[list[MarkdownBlock]], list[PixelBox], float]]:
        """Split block units across source groups without crossing a gutter."""

        populated = [group for group in segment_groups if group]
        if not populated:
            return []
        if len(populated) == 1 or not multi_column:
            return [(segment_units, populated[0], group_characters_per_line(populated[0]))]
        group_characters = [group_characters_per_line(group) for group in populated]
        unit_count = len(segment_units)
        group_count = len(populated)

        @cache
        def solve(group_index: int, start: int) -> tuple[float, tuple[int, ...]]:
            if group_index == group_count:
                return (0.0, ()) if start == unit_count else (math.inf, ())
            rows_in_group = len(populated[group_index])
            best_cost = math.inf
            best_boundaries: tuple[int, ...] = ()
            for end in range(start, unit_count + 1):
                candidate_units = segment_units[start:end]
                if candidate_units:
                    counts = desired_counts(
                        candidate_units,
                        group_characters[group_index],
                    )
                    desired = sum(counts)
                    fit = ((desired - rows_in_group) / max(1, rows_in_group)) ** 2
                    if len(candidate_units) > rows_in_group:
                        fit += 100.0 + (len(candidate_units) - rows_in_group) * 10.0
                    if desired > rows_in_group * 1.6:
                        fit += (desired / max(1, rows_in_group) - 1.6) * 2.5
                    first = candidate_units[0][0]
                    if first.starts_group or first.kind is MarkdownBlockKind.HEADING:
                        fit -= 0.035
                else:
                    # Empty groups are legal: a false-positive column or an
                    # isolated source annotation must not disable all geometry.
                    fit = 0.75 + min(0.75, rows_in_group / 20.0)
                future, boundaries = solve(group_index + 1, end)
                total = fit + future
                if total < best_cost:
                    best_cost = total
                    best_boundaries = (end, *boundaries)
            return best_cost, best_boundaries

        _, boundaries = solve(0, 0)
        if len(boundaries) != group_count:
            return []
        result: list[tuple[list[list[MarkdownBlock]], list[PixelBox], float]] = []
        start = 0
        for group, row_characters, end in zip(
            populated,
            group_characters,
            boundaries,
            strict=True,
        ):
            result.append((segment_units[start:end], group, row_characters))
            start = end
        return result

    # Partition source rows by authoritative anchors *and* block order.  The
    # former implementation removed provider-owned text rows, then globally
    # reassigned the leftovers.  A later unanchored question could consequently
    # consume rows above an earlier JSON paragraph.  Text evidence is therefore
    # a boundary too; image/table anchors retain the same hard separation.
    anchors_by_index = sorted(
        (
            (block.index, placement.source_bbox)
            for block, placement in zip(blocks, placements, strict=True)
            if placement.source_bbox is not None
            and (
                block.kind in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}
                or placement.evidence_providers
            )
        ),
        key=lambda item: item[0],
    )
    ordered_rows = source_row_reading_order(page, rows)
    row_rank = {(row.x0, row.y0, row.x1, row.y1): rank for rank, row in enumerate(ordered_rows)}
    tolerance = page.line_pitch * 0.25

    def boundary_rank(anchor: PixelBox, *, after: bool) -> int:
        """Find an anchor boundary in monotonic source reading order."""

        anchor_band = _source_flow_band(page, anchor, column_boxes)
        cutoff = anchor.y1 - tolerance if after else anchor.y0 + tolerance
        for rank, row in enumerate(ordered_rows):
            row_band = _source_flow_band(page, row, column_boxes)
            if row_band < anchor_band:
                continue
            if row_band > anchor_band:
                return rank
            center = (row.y0 + row.y1) / 2.0
            if center >= cutoff:
                return rank
        return len(ordered_rows)

    unit_cursor = 0
    lower_rank = 0
    previous_anchor: PixelBox | None = None
    used_rows: set[tuple[int, int, int, int]] = set()
    for anchor_index, optional_anchor in [*anchors_by_index, (math.inf, None)]:
        segment_units: list[list[MarkdownBlock]] = []
        while unit_cursor < len(units) and units[unit_cursor][0].index < anchor_index:
            segment_units.append(units[unit_cursor])
            unit_cursor += 1
        upper_rank = (
            max(lower_rank, boundary_rank(optional_anchor, after=False))
            if optional_anchor is not None
            else len(ordered_rows)
        )
        segment_groups: list[list[PixelBox]] = []
        for group in row_groups:
            segment_rows = []
            for row in group:
                key = (row.x0, row.y0, row.x1, row.y1)
                if key in used_rows or row_rank[key] < lower_rank or row_rank[key] >= upper_rank:
                    continue
                segment_rows.append(row)
                used_rows.add(key)
            if segment_rows:
                segment_groups.append(segment_rows)
        assignments = partition_units(segment_units, segment_groups)
        for group_units, group_rows, row_characters in assignments:
            group_previous_bottom = page.content_bbox.y0
            if previous_anchor is not None:
                group_previous_bottom = previous_anchor.y1
            if previous_anchor is not None and _source_flow_band(
                page, previous_anchor, column_boxes
            ) != _source_flow_band(page, group_rows[0], column_boxes):
                group_previous_bottom = group_rows[0].y0
            assign_segment(
                group_units,
                group_rows,
                previous_bottom=group_previous_bottom,
                row_characters_per_line=row_characters,
                consume_all_rows=not multi_column,
            )
        if optional_anchor is not None:
            lower_rank = max(lower_rank, boundary_rank(optional_anchor, after=True))
            previous_anchor = optional_anchor

    result: list[HybridBlockPlacement] = []
    for placement in placements:
        geometry = assigned.get(placement.block_id)
        if geometry is None:
            result.append(placement)
            continue
        block_rows, gap = geometry
        bbox = PixelBox(
            x0=min(row.x0 for row in block_rows),
            y0=min(row.y0 for row in block_rows),
            x1=max(row.x1 for row in block_rows),
            y1=max(row.y1 for row in block_rows),
        )
        result.append(
            placement.model_copy(
                update={
                    "source_bbox": bbox,
                    "source_rows": block_rows,
                    "source_gap_before": gap,
                    "match_score": 1.0,
                    "geometry_source": "scan_inferred",
                }
            )
        )
    return result


def _fill_source_gaps(
    placements: list[HybridBlockPlacement],
    page: ScanPageLayout,
) -> list[HybridBlockPlacement]:
    """Derive missing vertical gaps after all scan and JSON anchors are known."""

    previous_bottom = page.content_bbox.y0
    result: list[HybridBlockPlacement] = []
    for placement in sorted(placements, key=lambda item: item.block_index):
        bbox = placement.source_bbox
        if bbox is None:
            result.append(placement)
            continue
        gap = placement.source_gap_before
        if gap is None:
            # A y reset is normal when reading order advances to the next
            # native newspaper column.  It is not a negative spacer.
            gap = max(0, bbox.y0 - previous_bottom)
        result.append(placement.model_copy(update={"source_gap_before": gap}))
        previous_bottom = bbox.y1
    return result


_PAGE_FRACTION_RE = re.compile(r"(?<!\d)(\d{1,4})\s*/\s*(\d{1,4})(?!\d)")
_URL_RE = re.compile(r"(?:https?://|www\.)", flags=re.IGNORECASE)


def _project_box_to_page(
    box: PixelBox,
    source_page: ScanPageLayout,
    target_page: ScanPageLayout,
) -> PixelBox:
    """Project a normalized provider box between two raster page grids."""

    x0 = max(0, min(target_page.width - 1, round(box.x0 * target_page.width / source_page.width)))
    y0 = max(
        0,
        min(target_page.height - 1, round(box.y0 * target_page.height / source_page.height)),
    )
    x1 = max(x0 + 1, min(target_page.width, round(box.x1 * target_page.width / source_page.width)))
    y1 = max(
        y0 + 1,
        min(target_page.height, round(box.y1 * target_page.height / source_page.height)),
    )
    return PixelBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _relocate_repeated_top_furniture(
    pages: list[HybridPagePlan],
    blocks: Sequence[MarkdownBlock],
    layout: ScanDocumentLayout,
) -> list[HybridPagePlan]:
    """Move a serialized next-page URL banner out of the preceding page.

    OCR Markdown commonly emits ``footer, next-page repeated header`` in that
    order while weak provider page metadata attaches both to the old page.
    Repetition, an immediately preceding current-page folio, and a source box
    in the top 12% jointly make the correction safe and language-neutral.
    """

    if len(pages) < 2:
        return pages
    block_by_id = {block.id: block for block in blocks}
    block_by_index = {block.index: block for block in blocks}
    normalized_counts: dict[str, int] = {}
    for block in blocks:
        normalized = re.sub(r"\s+", " ", block.text).strip().casefold()
        normalized_counts[normalized] = normalized_counts.get(normalized, 0) + 1

    relocated: dict[int, list[HybridBlockPlacement]] = {}
    removals: dict[int, set[str]] = {}
    for page_index, page_plan in enumerate(pages[:-1]):
        source_page = layout.pages[page_index]
        target_page = layout.pages[page_index + 1]
        ordered = sorted(page_plan.placements, key=lambda placement: placement.block_index)
        for tail_offset, placement in enumerate(reversed(ordered[-2:])):
            block = block_by_id[placement.block_id]
            box = placement.source_bbox
            normalized = re.sub(r"\s+", " ", block.text).strip().casefold()
            if (
                tail_offset > 0
                or box is None
                or block.kind not in {MarkdownBlockKind.PARAGRAPH, MarkdownBlockKind.HEADING}
                or len(block.text) > 180
                or _URL_RE.search(block.text) is None
                or normalized_counts.get(normalized, 0) < 2
                or box.y0
                > source_page.content_bbox.y0 + round(source_page.content_bbox.height * 0.12)
            ):
                continue
            previous = block_by_index.get(block.index - 1)
            if previous is None:
                continue
            fraction = _PAGE_FRACTION_RE.search(previous.text)
            if fraction is None:
                continue
            current, total = (int(value) for value in fraction.groups())
            if current != page_plan.number or total != len(pages):
                continue

            projected_box = _project_box_to_page(box, source_page, target_page)
            projected_rows = [
                _project_box_to_page(row, source_page, target_page) for row in placement.source_rows
            ]
            moved = placement.model_copy(
                update={
                    "page_number": page_plan.number + 1,
                    "source_bbox": projected_box,
                    "source_rows": projected_rows,
                    "source_gap_before": max(
                        0,
                        projected_box.y0 - target_page.content_bbox.y0,
                    ),
                }
            )
            removals.setdefault(page_plan.number, set()).add(placement.block_id)
            relocated.setdefault(page_plan.number + 1, []).append(moved)

    if not relocated:
        return pages

    result: list[HybridPagePlan] = []
    for page_plan in pages:
        placements = [
            placement
            for placement in page_plan.placements
            if placement.block_id not in removals.get(page_plan.number, set())
        ]
        placements.extend(relocated.get(page_plan.number, []))
        placements.sort(key=lambda placement: placement.block_index)
        moved_ids = {placement.block_id for placement in relocated.get(page_plan.number, [])}
        if moved_ids:
            moved_position = next(
                index
                for index, placement in enumerate(placements)
                if placement.block_id in moved_ids
            )
            if moved_position + 1 < len(placements):
                following = placements[moved_position + 1]
                moved = placements[moved_position]
                if moved.source_bbox is not None and following.source_bbox is not None:
                    placements[moved_position + 1] = following.model_copy(
                        update={
                            "source_gap_before": max(
                                0,
                                following.source_bbox.y0 - moved.source_bbox.y1,
                            )
                        }
                    )
        result.append(page_plan.model_copy(update={"placements": placements}))
    return result


def _block_weight(
    block: MarkdownBlock,
    *,
    characters_per_line: float,
    image_match: AssetMatch | EvidenceMatch | None,
    line_pitch: float,
) -> float:
    if block.kind is MarkdownBlockKind.IMAGE:
        return max(2.0, image_match.bbox.height / line_pitch) if image_match else 7.0
    if block.kind is MarkdownBlockKind.TABLE:
        rows = max(1, len(block.table_rows))
        return 0.5 + rows * 1.28
    if block.kind is MarkdownBlockKind.RULE:
        return 0.35
    if block.kind is MarkdownBlockKind.HEADING:
        return max(1.0, math.ceil(len(_visible_text(block.text)) / characters_per_line)) + 0.2
    if block.kind is MarkdownBlockKind.CODE:
        return max(1.0, block.text.count("\n") + 1) + 0.2
    if block.kind is MarkdownBlockKind.EQUATION:
        return equation_layout_units(block.text)
    length = len(_visible_text(block.text))
    lines = max(1, math.ceil(length / characters_per_line))
    if block.kind is MarkdownBlockKind.OPTION:
        # Short sibling options are rendered in compact native columns.  A
        # fractional line here models that without depending on a document type.
        return max(0.28, lines * (0.72 if length > 52 else 0.28))
    return lines + 0.10


def _preferred_image_geometry(
    asset: AssetMatch | None,
    evidence: EvidenceMatch | None,
) -> AssetMatch | EvidenceMatch | None:
    """Prefer provider-normalized geometry over filename-coordinate hints.

    Evidence matching has already projected provider coordinates into the
    selected scan page's pixel grid.  A Markdown asset match may instead be an
    offline filename hint expressed in the OCR export's smaller raster.  When
    both identify the same image block, the canonical evidence therefore owns
    page and crop geometry; the asset remains available to the renderer for
    its original bytes when it was successfully resolved.
    """

    return evidence or asset


def _segment_cost(
    blocks: list[MarkdownBlock],
    weights: list[float],
    cumulative: list[float],
    fixed_pages: dict[int, int],
    matches: dict[int, AssetMatch | TableMatch | EvidenceMatch],
    layout: ScanDocumentLayout,
    page_number: int,
    start: int,
    end: int,
    target: float,
) -> float:
    if start > end:
        return math.inf
    page_anchor_indices = [
        index for index, fixed_page in fixed_pages.items() if fixed_page == page_number
    ]
    if start == end:
        # A source PDF can contain an intentionally blank page, or a page whose
        # only pixels were omitted by the Markdown extractor.  Preserve that
        # physical page instead of stealing the next page's first editable
        # block merely to satisfy a non-empty partition.  The large finite
        # penalty keeps ordinary unanchored documents densely partitioned and
        # makes an empty page a fallback only when page anchors or cardinality
        # require it.
        # In the absence of any anchor, prefer a trailing empty page over a
        # leading one; normal reading order starts content on the first page.
        return math.inf if page_anchor_indices else 25.0 + 1.0 / page_number
    if page_anchor_indices and not (
        start <= min(page_anchor_indices) and max(page_anchor_indices) < end
    ):
        return math.inf
    if any(
        fixed_page != page_number
        for index, fixed_page in fixed_pages.items()
        if start <= index < end
    ):
        return math.inf
    used = cumulative[end] - cumulative[start]
    cost = ((used - target) / max(1.0, target)) ** 2
    page = layout.pages[page_number - 1]
    for index, match in matches.items():
        if match.page_number != page_number:
            continue
        predicted = (cumulative[index] - cumulative[start] + weights[index] / 2) / max(used, 1e-6)
        observed = ((match.bbox.y0 + match.bbox.y1) / 2 - page.content_bbox.y0) / max(
            1, page.content_bbox.height
        )
        cost += (predicted - observed) ** 2 * 0.85
        if isinstance(match, TableMatch):
            predicted_after = cumulative[end] - cumulative[index + 1]
            observed_after = (page.content_bbox.y1 - match.bbox.y1) / max(1.0, page.line_pitch)
            cost += ((predicted_after - observed_after) / max(4.0, observed_after)) ** 2 * 20.0
    if end < len(blocks):
        left_group = blocks[end - 1].group_id
        right_group = blocks[end].group_id
        if left_group and left_group == right_group:
            cost += 0.035
    return cost


def build_hybrid_layout_plan(
    content: MarkdownContent,
    layout: ScanDocumentLayout,
    asset_matches: list[AssetMatch],
    table_matches: list[TableMatch] | None = None,
    *,
    evidence_matches: Sequence[EvidenceMatch] | None = None,
) -> HybridLayoutPlan:
    """Align editable blocks to source pages with monotonic dynamic programming."""

    blocks = content.blocks
    page_count = len(layout.pages)
    if not blocks:
        raise ValueError("content contains no blocks")
    if page_count < 1:
        raise ValueError("layout contains no pages")
    match_by_id = {match.block_id: match for match in asset_matches}
    table_match_by_id = {match.block_id: match for match in table_matches or []}
    evidence_match_by_id = {match.block_id: match for match in evidence_matches or ()}
    image_geometry_by_id = {
        block.id: preferred
        for block in blocks
        if block.kind is MarkdownBlockKind.IMAGE
        and (
            preferred := _preferred_image_geometry(
                match_by_id.get(block.id), evidence_match_by_id.get(block.id)
            )
        )
        is not None
    }
    for block in blocks:
        evidence_match = evidence_match_by_id.get(block.id)
        if evidence_match is not None and evidence_match.block_index != block.index:
            raise ValueError(
                f"evidence block index mismatch for {block.id!r}: "
                f"{evidence_match.block_index} != {block.index}"
            )
    match_by_index: dict[int, AssetMatch | TableMatch | EvidenceMatch] = {}
    for block in blocks:
        evidence_match = evidence_match_by_id.get(block.id)
        if evidence_match is not None:
            match_by_index[block.index] = evidence_match
        image_geometry = image_geometry_by_id.get(block.id)
        if image_geometry is not None:
            match_by_index[block.index] = image_geometry
    fixed_pages = {index: match.page_number for index, match in match_by_index.items()}
    for block in blocks:
        table_match = table_match_by_id.get(block.id)
        if table_match:
            fixed_pages[block.index] = table_match.page_number
            match_by_index[block.index] = table_match
    asset_group_pages: dict[str, set[int]] = {}
    for block in blocks:
        match = image_geometry_by_id.get(block.id)
        if match and block.group_id:
            asset_group_pages.setdefault(block.group_id, set()).add(match.page_number)
    for block in blocks:
        anchored_pages = asset_group_pages.get(block.group_id or "", set())
        # A compact question/figure group with one matched source asset should
        # remain together.  Text evidence is intentionally not propagated to
        # every sibling: one recognized line does not prove that a long
        # article or question cannot continue onto the following source page.
        # Every JSON-evidenced block still keeps its own fixed page above.
        if (
            len(anchored_pages) == 1
            and block.id not in evidence_match_by_id
            and block.id not in table_match_by_id
        ):
            fixed_pages[block.index] = next(iter(anchored_pages))
    global_pitch = sorted(page.line_pitch for page in layout.pages)[page_count // 2]
    average_width = sum(page.content_bbox.width for page in layout.pages) / page_count
    characters_per_line = max(36.0, average_width / max(1.0, global_pitch * 0.42))
    weights = [
        _block_weight(
            block,
            characters_per_line=characters_per_line,
            image_match=image_geometry_by_id.get(block.id),
            line_pitch=global_pitch,
        )
        for block in blocks
    ]
    cumulative = [0.0]
    for weight in weights:
        cumulative.append(cumulative[-1] + weight)
    capacities = [page.content_bbox.height / global_pitch for page in layout.pages]
    normalization = cumulative[-1] / max(sum(capacities), 1e-6)
    targets = [capacity * normalization for capacity in capacities]

    @cache
    def solve(page_number: int, start: int) -> tuple[float, tuple[int, ...]]:
        if page_number == page_count:
            cost = _segment_cost(
                blocks,
                weights,
                cumulative,
                fixed_pages,
                match_by_index,
                layout,
                page_number,
                start,
                len(blocks),
                targets[page_number - 1],
            )
            return cost, (len(blocks),)
        best_cost = math.inf
        best_boundaries: tuple[int, ...] = ()
        # Every page considered every remaining block as its boundary, so the
        # search was quadratic in block count with no bound: a 30-page, 1800
        # block document spent a minute here. A page loaded past several times
        # its own height is never the cheaper split, but the scan cannot stop
        # before the page's own anchors are contained, or an anchored document
        # becomes infeasible. The test sits after the body so at least one `end`
        # beyond the cap is always evaluated, which keeps a single oversized
        # block, and an oversized anchored run, reachable.
        anchors = [index for index, page in fixed_pages.items() if page == page_number]
        floor_end = max(anchors) + 1 if anchors else start
        weight_cap = 3.0 * max(1.0, targets[page_number - 1])
        for end in range(start, len(blocks) + 1):
            current = _segment_cost(
                blocks,
                weights,
                cumulative,
                fixed_pages,
                match_by_index,
                layout,
                page_number,
                start,
                end,
                targets[page_number - 1],
            )
            if math.isfinite(current):
                future, future_boundaries = solve(page_number + 1, end)
                total = current + future
                if total < best_cost:
                    best_cost = total
                    best_boundaries = (end, *future_boundaries)
            if end > floor_end and (cumulative[end] - cumulative[start]) > weight_cap:
                break
        return best_cost, best_boundaries

    score, boundaries = solve(1, 0)
    if not math.isfinite(score) or len(boundaries) != page_count:
        raise ValueError(
            "Markdown blocks cannot be aligned monotonically to the matched PDF assets"
        )
    pages: list[HybridPagePlan] = []
    start = 0
    for source_page, end in zip(layout.pages, boundaries, strict=True):
        page_blocks = blocks[start:end]
        placements = []
        for block in page_blocks:
            table_match = table_match_by_id.get(block.id)
            evidence_match = evidence_match_by_id.get(block.id)
            image_geometry = image_geometry_by_id.get(block.id)
            geometry = table_match or image_geometry or evidence_match
            placements.append(
                HybridBlockPlacement(
                    block_id=block.id,
                    block_index=block.index,
                    page_number=source_page.number,
                    source_bbox=(geometry.bbox if geometry is not None else None),
                    source_rows=(
                        evidence_match.source_rows
                        if evidence_match is not None and geometry is evidence_match
                        else []
                    ),
                    match_score=(
                        table_match.confidence
                        if table_match
                        else image_geometry.score
                        if isinstance(image_geometry, AssetMatch)
                        else image_geometry.match_score
                        if isinstance(image_geometry, EvidenceMatch)
                        else evidence_match.match_score
                        if evidence_match
                        else None
                    ),
                    geometry_source=(
                        "source_table"
                        if table_match
                        else "source_asset"
                        if isinstance(image_geometry, AssetMatch)
                        else image_geometry.geometry_source
                        if isinstance(image_geometry, EvidenceMatch)
                        else evidence_match.geometry_source
                        if evidence_match
                        else "content_estimate"
                    ),
                    evidence_confidence=(
                        evidence_match.confidence if evidence_match is not None else None
                    ),
                    evidence_providers=(
                        evidence_match.providers if evidence_match is not None else ()
                    ),
                    evidence_element_ids=(
                        evidence_match.element_ids if evidence_match is not None else ()
                    ),
                    evidence_style=(evidence_match.style if evidence_match is not None else None),
                    evidence_conflict=(
                        evidence_match.conflict if evidence_match is not None else False
                    ),
                    evidence_warnings=(
                        evidence_match.warnings if evidence_match is not None else []
                    ),
                )
            )
        placements = _snap_evidence_rows_to_scan(page_blocks, placements, source_page)
        placements = _assign_group_option_geometry(page_blocks, placements, source_page)
        placements = _assign_text_geometry(page_blocks, placements, source_page)
        placements = _fill_source_gaps(placements, source_page)
        pages.append(
            HybridPagePlan(
                number=source_page.number,
                pdf_width=source_page.pdf_width,
                pdf_height=source_page.pdf_height,
                raster_width=source_page.width,
                raster_height=source_page.height,
                content_bbox=source_page.content_bbox,
                line_pitch=source_page.line_pitch,
                placements=placements,
            )
        )
        start = end
    pages = _relocate_repeated_top_furniture(pages, blocks, layout)
    warnings: list[str] = []
    unmatched = len(content.image_blocks) - len(asset_matches)
    if unmatched:
        warnings.append(f"{unmatched} Markdown image(s) could not be aligned to the layout PDF.")
    if evidence_matches:
        conflicts = sum(match.conflict for match in evidence_matches)
        if conflicts:
            warnings.append(f"{conflicts} Markdown block(s) retained JSON evidence disagreements.")
        warnings.extend(
            f"{match.block_id}: {warning}"
            for match in evidence_matches
            for warning in match.warnings
        )
    return HybridLayoutPlan(
        content_source=content.source,
        layout_source=layout.source,
        pages=pages,
        warnings=warnings,
    )


__all__ = [
    "HybridBlockPlacement",
    "HybridLayoutPlan",
    "HybridPagePlan",
    "VerticalFitBudget",
    "apply_page_vertical_fit_budget",
    "build_hybrid_layout_plan",
    "build_page_vertical_fit_budget",
    "contains_tall_inline_math",
    "equation_layout_units",
    "source_row_reading_order",
    "visual_text_row_groups",
    "visual_text_rows",
]
