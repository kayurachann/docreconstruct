"""Read-only adapter from the current hybrid planner to strict constraints."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypeVar

from docreconstruct.ir import BBox
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
from docreconstruct.reconstruction.scan_layout import PixelBox, ScanDocumentLayout, ScanPageLayout

from .canonical import stable_digest
from .models import (
    ColumnConstraint,
    ConstraintPlan,
    ConstraintPlanProvenance,
    HardConstraintKind,
    HardConstraintSet,
    Insets,
    ObjectConstraint,
    ObjectFlowMode,
    ObjectProvenance,
    PageConstraintPlan,
    Size,
    SoftConstraintKind,
)

if TYPE_CHECKING:
    from docreconstruct.reconstruction.hybrid import HybridPreparedRenderPlan

_RuleT = TypeVar("_RuleT", bound=StrEnum)


def _ordered_rules(values: Iterable[_RuleT]) -> tuple[_RuleT, ...]:
    return tuple(sorted(set(values), key=lambda value: value.value))


def _hybrid_plan_payload(plan: HybridLayoutPlan) -> dict[str, Any]:
    payload = plan.model_dump(mode="json")
    payload.pop("content_source", None)
    payload.pop("layout_source", None)
    payload["warnings"] = sorted(set(payload["warnings"]))
    payload["pages"] = sorted(payload["pages"], key=lambda page: page["number"])
    for page in payload["pages"]:
        page["placements"] = sorted(
            page["placements"],
            key=lambda item: (item["block_index"], item["block_id"]),
        )
        for placement in page["placements"]:
            placement["source_rows"] = sorted(
                placement["source_rows"],
                key=lambda box: (box["y0"], box["x0"], box["y1"], box["x1"]),
            )
            placement["evidence_providers"] = sorted(set(placement["evidence_providers"]))
            placement["evidence_element_ids"] = sorted(set(placement["evidence_element_ids"]))
            placement["evidence_warnings"] = sorted(set(placement["evidence_warnings"]))
    return payload


def _block_payload(block: MarkdownBlock) -> dict[str, Any]:
    return {
        "kind": block.kind.value,
        "text": block.text,
        "source": block.source,
        "level": block.level,
        "group_id": block.group_id,
        "starts_group": block.starts_group,
        "table_rows": block.table_rows,
        "metadata": block.metadata,
    }


def _point_box(box: PixelBox, page: HybridPagePlan) -> BBox:
    x_scale = page.pdf_width / page.raster_width
    y_scale = page.pdf_height / page.raster_height
    return BBox(
        x0=box.x0 * x_scale,
        y0=box.y0 * y_scale,
        x1=box.x1 * x_scale,
        y1=box.y1 * y_scale,
    )


def _page_margins(page: HybridPagePlan) -> Insets:
    frame = _point_box(page.content_bbox, page)
    return Insets(
        top=frame.y0,
        right=max(0.0, page.pdf_width - frame.x1),
        bottom=max(0.0, page.pdf_height - frame.y1),
        left=frame.x0,
    )


def _scan_page_by_number(
    source_layout: ScanDocumentLayout | None,
) -> dict[int, ScanPageLayout]:
    if source_layout is None:
        return {}
    result = {page.number: page for page in source_layout.pages}
    if len(result) != len(source_layout.pages):
        raise ValueError("source layout page numbers must be unique")
    return result


def _metadata_column_boxes(
    page: HybridPagePlan,
    source_page: ScanPageLayout | None,
) -> tuple[list[PixelBox], str]:
    if source_page is None:
        return [page.content_bbox], "content_bbox_fallback"
    if (
        source_page.width != page.raster_width
        or source_page.height != page.raster_height
        or not math.isclose(source_page.pdf_width, page.pdf_width, abs_tol=1e-6)
        or not math.isclose(source_page.pdf_height, page.pdf_height, abs_tol=1e-6)
    ):
        raise ValueError(f"source layout dimensions do not match hybrid page {page.number}")
    raw_count = source_page.metadata.get("column_count", 1)
    if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 1:
        raise ValueError(f"source page {page.number} has an invalid column count")
    if raw_count == 1:
        return [page.content_bbox], "content_bbox_fallback"
    raw_boxes = source_page.metadata.get("column_boxes")
    if not isinstance(raw_boxes, list) or len(raw_boxes) != raw_count:
        raise ValueError(f"source page {page.number} has incomplete column geometry")
    boxes: list[PixelBox] = []
    for raw in raw_boxes:
        if (
            not isinstance(raw, list)
            or len(raw) != 4
            or any(isinstance(value, bool) or not isinstance(value, int) for value in raw)
        ):
            raise ValueError(f"source page {page.number} has invalid column coordinates")
        box = PixelBox(x0=raw[0], y0=raw[1], x1=raw[2], y1=raw[3])
        if box.x1 > page.raster_width or box.y1 > page.raster_height:
            raise ValueError(f"source page {page.number} column lies outside the raster")
        boxes.append(box)
    boxes.sort(key=lambda box: (box.x0, box.y0, box.x1, box.y1))
    if any(left.x1 > right.x0 for left, right in zip(boxes, boxes[1:], strict=False)):
        raise ValueError(f"source page {page.number} columns overlap horizontally")
    return boxes, "scan_metadata"


def _column_index(box: BBox | None, columns: Sequence[BBox]) -> int:
    if box is None:
        return 0
    overlaps = [max(0.0, min(box.x1, column.x1) - max(box.x0, column.x0)) for column in columns]
    return min(
        range(len(columns)),
        key=lambda index: (
            -overlaps[index],
            abs(box.center_x - columns[index].center_x),
            index,
        ),
    )


def _flow_mode(kind: MarkdownBlockKind) -> ObjectFlowMode:
    if kind is MarkdownBlockKind.IMAGE:
        return ObjectFlowMode.INLINE_ASSET
    if kind is MarkdownBlockKind.TABLE:
        return ObjectFlowMode.NATIVE_TABLE
    if kind is MarkdownBlockKind.EQUATION:
        return ObjectFlowMode.NATIVE_MATH
    return ObjectFlowMode.BLOCK


def _soft_object_rules(kind: MarkdownBlockKind) -> tuple[SoftConstraintKind, ...]:
    common = {SoftConstraintKind.KEEP_WITH_NEXT, SoftConstraintKind.PAGE_BREAK_BEHAVIOR}
    if kind is MarkdownBlockKind.IMAGE:
        common.update({SoftConstraintKind.IMAGE_CROP, SoftConstraintKind.ANCHOR_OFFSET})
    elif kind is MarkdownBlockKind.TABLE:
        common.add(SoftConstraintKind.TABLE_WIDTH)
    else:
        common.update(
            {
                SoftConstraintKind.FONT_SIZE,
                SoftConstraintKind.LINE_SPACING,
                SoftConstraintKind.PARAGRAPH_SPACING,
            }
        )
    return _ordered_rules(common)


def _object_constraint(
    block: MarkdownBlock,
    placement: HybridBlockPlacement,
    page: HybridPagePlan,
    columns: Sequence[BBox],
    next_block: MarkdownBlock | None,
) -> ObjectConstraint:
    preferred = _point_box(placement.source_bbox, page) if placement.source_bbox else None
    content_width = _point_box(page.content_bbox, page).width
    preferred_width = preferred.width if preferred is not None else content_width
    min_width = preferred_width * 0.75 if preferred is not None else content_width * 0.25
    max_width = (
        min(page.pdf_width, preferred_width * 1.25) if preferred is not None else content_width
    )
    column_index = _column_index(preferred, columns)
    editable_required = block.kind is not MarkdownBlockKind.IMAGE
    hard_rules = {
        HardConstraintKind.AUTHORITY_HASH,
        HardConstraintKind.OBJECT_ID,
        HardConstraintKind.OBJECT_PROVENANCE,
        HardConstraintKind.NO_SOURCE_DELETION,
    }
    if editable_required:
        hard_rules.update(
            {
                HardConstraintKind.PRESERVE_NATIVE_EDITABILITY,
                HardConstraintKind.NO_RASTER_SUBSTITUTION,
            }
        )
    return ObjectConstraint(
        object_id=block.id,
        page_number=page.number,
        content_kind=block.kind.value,
        authority_content_sha256=stable_digest(_block_payload(block)),
        preferred_bbox=preferred,
        min_width=min_width,
        max_width=max_width,
        preferred_height=preferred.height if preferred is not None else None,
        flow_mode=_flow_mode(block.kind),
        keep_with_next=bool(
            block.group_id and next_block is not None and next_block.group_id == block.group_id
        ),
        editable_required=editable_required,
        column_id=f"page-{page.number}-column-{column_index + 1}",
        provenance=ObjectProvenance(
            block_index=block.index,
            geometry_source=placement.geometry_source,
            evidence_providers=tuple(sorted(set(placement.evidence_providers))),
            evidence_element_ids=tuple(sorted(set(placement.evidence_element_ids))),
        ),
        hard_constraints=_ordered_rules(hard_rules),
        soft_constraints=_soft_object_rules(block.kind),
    )


def _page_constraints(
    page: HybridPagePlan,
    block_by_id: dict[str, MarkdownBlock],
    source_page: ScanPageLayout | None,
) -> PageConstraintPlan:
    pixel_columns, column_provenance = _metadata_column_boxes(page, source_page)
    columns = [_point_box(box, page) for box in pixel_columns]
    ordered_placements = sorted(
        page.placements,
        key=lambda placement: (placement.block_index, placement.block_id),
    )
    objects = tuple(
        _object_constraint(
            block_by_id[placement.block_id],
            placement,
            page,
            columns,
            (
                block_by_id[ordered_placements[index + 1].block_id]
                if index + 1 < len(ordered_placements)
                else None
            ),
        )
        for index, placement in enumerate(ordered_placements)
    )
    object_ids_by_column: dict[str, list[str]] = {
        f"page-{page.number}-column-{index + 1}": [] for index in range(len(columns))
    }
    for item in objects:
        object_ids_by_column[item.column_id].append(item.object_id)
    column_constraints = []
    for index, preferred in enumerate(columns):
        column_id = f"page-{page.number}-column-{index + 1}"
        next_column = columns[index + 1] if index + 1 < len(columns) else None
        column_constraints.append(
            ColumnConstraint(
                column_id=column_id,
                preferred_bbox=preferred,
                min_width=preferred.width * 0.75,
                max_width=min(page.pdf_width, preferred.width * 1.25),
                preferred_gutter_after=(
                    max(0.0, next_column.x0 - preferred.x1) if next_column is not None else None
                ),
                object_ids=tuple(object_ids_by_column[column_id]),
                provenance=column_provenance,
                soft_constraints=(SoftConstraintKind.COLUMN_GUTTER,),
            )
        )
    return PageConstraintPlan(
        page_number=page.number,
        page_size=Size(width=page.pdf_width, height=page.pdf_height),
        margins=_page_margins(page),
        columns=tuple(column_constraints),
        objects=objects,
        hard_constraints=_ordered_rules(
            {HardConstraintKind.NO_FULL_PAGE_RASTER, HardConstraintKind.PAGE_SIZE}
        ),
        soft_constraints=_ordered_rules(
            {
                SoftConstraintKind.COLUMN_GUTTER,
                SoftConstraintKind.MARGIN,
                SoftConstraintKind.PAGE_BREAK_BEHAVIOR,
            }
        ),
    )


def adapt_hybrid_layout_plan(
    plan: HybridLayoutPlan,
    content: MarkdownContent,
    *,
    content_authority_sha256: str,
    layout_authority_sha256: str,
    evidence_authority_sha256: Sequence[str] = (),
    source_layout: ScanDocumentLayout | None = None,
    prepared_render_sha256: str | None = None,
) -> ConstraintPlan:
    """Map the current hybrid plan without mutating it or influencing rendering."""

    block_ids = [block.id for block in content.blocks]
    block_indices = [block.index for block in content.blocks]
    if len(block_ids) != len(set(block_ids)):
        raise ValueError("Markdown block IDs must be unique before constraint planning")
    if len(block_indices) != len(set(block_indices)):
        raise ValueError("Markdown block indices must be unique before constraint planning")
    block_by_id = {block.id: block for block in content.blocks}
    ordered_pages = sorted(plan.pages, key=lambda page: page.number)
    if tuple(page.number for page in ordered_pages) != tuple(range(1, len(ordered_pages) + 1)):
        raise ValueError("hybrid plan pages must be consecutive and start at one")
    placements = [placement for page in ordered_pages for placement in page.placements]
    placement_ids = [placement.block_id for placement in placements]
    if len(placement_ids) != len(set(placement_ids)):
        raise ValueError("hybrid plan placements must have unique block IDs")
    if set(placement_ids) != set(block_ids):
        missing = sorted(set(block_ids) - set(placement_ids))
        extra = sorted(set(placement_ids) - set(block_ids))
        raise ValueError(
            f"hybrid plan must preserve every Markdown block; missing={missing}, extra={extra}"
        )
    for placement in placements:
        if placement.block_index != block_by_id[placement.block_id].index:
            raise ValueError(f"hybrid placement index does not match block {placement.block_id!r}")

    scan_pages = _scan_page_by_number(source_layout)
    if source_layout is not None and set(scan_pages) != {page.number for page in ordered_pages}:
        raise ValueError("source layout pages must exactly match hybrid plan pages")
    pages = tuple(
        _page_constraints(page, block_by_id, scan_pages.get(page.number)) for page in ordered_pages
    )
    objects = sorted(
        (item for page in pages for item in page.objects),
        key=lambda item: (item.provenance.block_index, item.object_id),
    )
    object_ids = tuple(item.object_id for item in objects)
    provenance_payload = [
        {"object_id": item.object_id, "provenance": item.provenance.model_dump(mode="json")}
        for item in objects
    ]
    content_payload = [
        {
            "object_id": item.object_id,
            "authority_content_sha256": item.authority_content_sha256,
        }
        for item in objects
    ]
    page_payload = [page.model_dump(mode="json") for page in pages]
    provenance = ConstraintPlanProvenance(
        content_authority_sha256=content_authority_sha256,
        layout_authority_sha256=layout_authority_sha256,
        evidence_authority_sha256=tuple(sorted(set(evidence_authority_sha256))),
        hybrid_plan_sha256=stable_digest(_hybrid_plan_payload(plan)),
        prepared_render_sha256=prepared_render_sha256,
        mapping_sha256=stable_digest(page_payload),
    )
    hard = HardConstraintSet(
        content_authority_sha256=content_authority_sha256,
        layout_authority_sha256=layout_authority_sha256,
        required_object_ids=object_ids,
        object_content_sha256=stable_digest(content_payload),
        object_provenance_sha256=stable_digest(provenance_payload),
        page_count=len(pages),
        page_sizes=tuple(page.page_size for page in pages),
        rules=_ordered_rules(HardConstraintKind),
    )
    return ConstraintPlan(
        provenance=provenance,
        hard_constraints=hard,
        pages=pages,
        warnings=tuple(sorted(set(plan.warnings))),
    )


def adapt_prepared_hybrid_render_plan(prepared: HybridPreparedRenderPlan) -> ConstraintPlan:
    """Adapt the exact fingerprinted artifact already shared by renderer and QA."""

    manifest = prepared.sources.manifest
    if not isinstance(prepared.plan, HybridLayoutPlan):
        raise TypeError("prepared render plan does not contain a HybridLayoutPlan")
    return adapt_hybrid_layout_plan(
        prepared.plan,
        prepared.sources.markdown,
        content_authority_sha256=manifest.content.sha256,
        layout_authority_sha256=manifest.layout.sha256,
        evidence_authority_sha256=[item.sha256 for item in manifest.evidence],
        source_layout=prepared.sources.scan,
        prepared_render_sha256=prepared.sha256,
    )


__all__ = ["adapt_hybrid_layout_plan", "adapt_prepared_hybrid_render_plan"]
