"""Align native Markdown tables to ruled regions in a source scan."""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from docreconstruct.reconstruction.asset_matching import AssetMatch
from docreconstruct.reconstruction.markdown_content import MarkdownContent
from docreconstruct.reconstruction.scan_layout import (
    PixelBox,
    ScanDocumentLayout,
    ScanRegionKind,
)


class TableMatch(BaseModel):
    """A native Markdown table aligned to source-page geometry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str
    page_number: int = Field(ge=1)
    bbox: PixelBox
    confidence: float = Field(ge=0.0, le=1.0)


def match_markdown_tables(
    content: MarkdownContent,
    layout: ScanDocumentLayout,
    asset_matches: Sequence[AssetMatch] | None = None,
) -> list[TableMatch]:
    """Match tables monotonically using grid rules, order, and group context.

    Consecutive tables in one semantic group may share a wide detected region;
    this occurs when side-by-side grids are joined by projection analysis.
    """

    image_boxes: dict[int, list[PixelBox]] = {}
    for match in asset_matches or []:
        image_boxes.setdefault(match.page_number, []).append(match.bbox)

    def overlaps_image(page_number: int, bbox: PixelBox) -> bool:
        for image in image_boxes.get(page_number, []):
            width = max(0, min(bbox.x1, image.x1) - max(bbox.x0, image.x0))
            height = max(0, min(bbox.y1, image.y1) - max(bbox.y0, image.y0))
            if width * height / max(1, min(bbox.area, image.area)) >= 0.55:
                return True
        return False

    candidates = [
        (page.number, region)
        for page in layout.pages
        for region in page.regions
        if region.kind is ScanRegionKind.TABLE
        and not overlaps_image(page.number, region.bbox)
        and int(region.metadata.get("vertical_rules", 0)) > 0
        and (
            int(region.metadata.get("horizontal_rules", 0)) >= 2
            or int(region.metadata.get("vertical_rules", 0)) >= 3
        )
    ]
    tables = content.table_blocks
    if not candidates or not tables:
        return []
    matches: list[TableMatch] = []
    candidate_index = 0
    previous_group: str | None = None
    previous_columns = 0
    for block in tables:
        rows = len(block.table_rows)
        columns = max((len(row) for row in block.table_rows), default=1)
        required_vertical = max(2, math.ceil((columns + 1) * 0.45))
        required_horizontal = 2 if rows >= 2 else 1
        while candidate_index < len(candidates):
            candidate = candidates[candidate_index][1]
            if (
                int(candidate.metadata.get("vertical_rules", 0)) >= required_vertical
                and int(candidate.metadata.get("horizontal_rules", 0)) >= required_horizontal
            ):
                break
            candidate_index += 1
        if candidate_index >= len(candidates):
            break
        previous_candidate = candidates[candidate_index - 1] if candidate_index else None
        previous_region = previous_candidate[1] if previous_candidate else None
        can_share = (
            previous_region is not None
            and block.group_id is not None
            and block.group_id == previous_group
            and float(previous_region.metadata.get("aspect_ratio", 1.0)) >= 4.5
            and int(previous_region.metadata.get("vertical_rules", 0))
            >= max(columns + 1, previous_columns + 1)
        )
        if can_share:
            page_number, candidate = candidates[candidate_index - 1]
        else:
            page_number, candidate = candidates[candidate_index]
            candidate_index += 1
        vertical_rules = int(candidate.metadata.get("vertical_rules", 0))
        horizontal_rules = int(candidate.metadata.get("horizontal_rules", 0))
        row_fit = (
            0.65 if horizontal_rules == 0 else 1.0 / (1.0 + abs(horizontal_rules - (rows + 1)))
        )
        column_fit = 1.0 / (1.0 + abs(vertical_rules - (columns + 1)))
        confidence = max(0.35, min(0.92, 0.45 + 0.22 * row_fit + 0.25 * column_fit))
        matches.append(
            TableMatch(
                block_id=block.id,
                page_number=page_number,
                bbox=candidate.bbox,
                confidence=confidence,
            )
        )
        previous_group = block.group_id
        previous_columns = columns
    return matches


__all__ = ["TableMatch", "match_markdown_tables"]
