"""Project-native structural QA for Markdown/scan hybrid DOCX artifacts.

The validator deliberately does not invoke an Office process or rasterize the
candidate.  It measures the guarantees that can be established directly from
the source plan and OOXML package, and names the visual properties that remain
unmeasured when rendered QA is disabled.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import statistics
import zipfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field

from docreconstruct.evidence import (
    ProviderHints,
    SidecarEvidenceBundle,
    load_sidecar_evidence,
)
from docreconstruct.providers import ProviderContext
from docreconstruct.reconstruction.asset_matching import match_markdown_assets
from docreconstruct.reconstruction.evidence_matching import (
    EvidenceMatch,
    match_sidecar_evidence,
)
from docreconstruct.reconstruction.hybrid_planner import (
    HybridLayoutPlan,
    build_hybrid_layout_plan,
    equation_layout_units,
    source_row_reading_order,
    visual_text_rows,
)
from docreconstruct.reconstruction.markdown_content import (
    MarkdownBlockKind,
    MarkdownContent,
    parse_markdown_content,
)
from docreconstruct.reconstruction.math_omml import (
    build_omml,
    equation_row_count,
    latex_visible_text,
    unsupported_latex_commands,
)
from docreconstruct.reconstruction.scan_layout import (
    PixelBox,
    ScanDocumentLayout,
    analyze_scan_source,
)
from docreconstruct.reconstruction.table_matching import match_markdown_tables

_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MATH = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_DRAWING = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CJK_PATTERN = re.compile(
    r"[\u1100-\u11ff\u3040-\u30ff\u3130-\u318f\u3400-\u4dbf"
    r"\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]"
)
_INLINE_MATH = re.compile(r"\$([^$]+)\$")
_CONTROL_LEAK = re.compile(r"\\(?:begin|end|big|left|right)|\\\\")
_NARY_SOURCE = re.compile(r"\\(?P<command>oint|int|sum|prod)(?![A-Za-z])")
_INTEGRAL_SOURCE = re.compile(r"\\(?:oint|int)(?![A-Za-z])(?P<limits>\s*\\limits(?![A-Za-z]))?")
_NATIVE_DELIMITER_SOURCE = re.compile(r"\\(?:left|bigl|Bigl|biggl|Biggl)\b")
_BODY_COLUMNS_CAPTION = re.compile(r"^docreconstruct:body-columns-(?P<count>\d+)$")
_BODY_FOREGROUND_MIN_RATIO = 0.30
_SOURCE_VISUAL_SLOT_MIN_COVERAGE = 0.85
_NARY_SYMBOLS = {"int": "∫", "oint": "∮", "sum": "∑", "prod": "∏"}
_MATH_CONTROL_PROPERTIES = {
    "accPr",
    "barPr",
    "borderBoxPr",
    "boxPr",
    "dPr",
    "eqArrPr",
    "fPr",
    "funcPr",
    "groupChrPr",
    "limLowPr",
    "limUppPr",
    "mPr",
    "naryPr",
    "phantPr",
    "radPr",
    "sPrePr",
    "sSubPr",
    "sSubSupPr",
    "sSupPr",
}
_MATH_FONT = "Cambria Math"
_MATH_FONT_ATTRIBUTES = ("ascii", "hAnsi", "cs")


class HybridValidationGate(BaseModel):
    """One deterministic acceptance condition and its observed values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    passed: bool
    expected: Any = None
    actual: Any = None
    detail: str | None = None


class HybridValidationReport(BaseModel):
    """Serializable report returned by :func:`validate_hybrid`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str
    layout: str
    candidate: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    layout_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    passed_gates: int = Field(ge=0)
    measured_gates: int = Field(ge=1)
    gates: list[HybridValidationGate]
    metrics: dict[str, Any] = Field(default_factory=dict)
    unmeasured: list[str] = Field(default_factory=list)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _normalized(value: str) -> str:
    return re.sub(r"[\s\ufeff\u200b]+", " ", value).strip()


def _text_evidence(value: str) -> dict[str, str | int]:
    return {
        "characters": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
    }


def _projection_difference(expected: str, actual: str) -> dict[str, Any] | None:
    """Return a compact deterministic diagnostic for the first text mismatch."""

    limit = min(len(expected), len(actual))
    index = next((offset for offset in range(limit) if expected[offset] != actual[offset]), limit)
    if index == len(expected) == len(actual):
        return None
    start = max(0, index - 36)
    return {
        "index": index,
        "expected": expected[start : index + 36],
        "actual": actual[start : index + 36],
        "expected_codepoint": ord(expected[index]) if index < len(expected) else None,
        "actual_codepoint": ord(actual[index]) if index < len(actual) else None,
    }


def _inline_projection(value: str) -> str:
    value = re.sub(r"<eq>(.*?)</eq>", r"$\1$", value)
    return _INLINE_MATH.sub(lambda match: latex_visible_text(match.group(1)), value)


def _markdown_projection(content: MarkdownContent) -> str:
    parts: list[str] = []
    for block in content.blocks:
        if block.kind in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.RULE}:
            continue
        if block.kind is MarkdownBlockKind.TABLE:
            parts.extend(" ".join(cell for cell in row if cell) for row in block.table_rows)
        elif block.kind is MarkdownBlockKind.EQUATION:
            parts.append(latex_visible_text(block.text))
        elif block.text:
            parts.append(_inline_projection(block.text))
    return _normalized("\n".join(parts))


def _docx_projection(root: ElementTree.Element) -> str:
    paragraphs: list[str] = []
    for paragraph in root.iter(_WORD + "p"):
        value = "".join(
            node.text or "" for node in paragraph.iter() if node.tag in {_WORD + "t", _MATH + "t"}
        )
        if value:
            paragraphs.append(value)
    return _normalized("\n".join(paragraphs))


def _render_content_bbox(page: Any) -> PixelBox:
    value = page.metadata.get("render_content_bbox")
    if isinstance(value, dict):
        try:
            return PixelBox.model_validate(value)
        except ValueError:
            pass
    return page.content_bbox


def _math_sources(content: MarkdownContent) -> tuple[list[str], list[str]]:
    expressions: list[str] = []
    display: list[str] = []
    for block in content.blocks:
        if block.kind is MarkdownBlockKind.EQUATION:
            expressions.append(block.text)
            display.append(block.text)
        elif block.kind is not MarkdownBlockKind.IMAGE:
            expressions.extend(match.group(1) for match in _INLINE_MATH.finditer(block.text))
    return expressions, display


def _math_signature(node: Any) -> tuple[Any, ...]:
    attributes = tuple(sorted((_local_name(key), value) for key, value in node.attrib.items()))
    text = node.text or "" if _local_name(node.tag) == "t" else ""
    presentation_only = {
        _WORD + "rPr",
        _MATH + "ctrlPr",
        _MATH + "eqArrPr",
        _MATH + "limLoc",
    }
    return (
        _local_name(node.tag),
        attributes,
        text,
        tuple(_math_signature(child) for child in node if child.tag not in presentation_only),
    )


def _display_math_nodes(root: ElementTree.Element) -> list[ElementTree.Element]:
    result: list[ElementTree.Element] = []
    for paragraph in root.iter(_WORD + "p"):
        math_nodes = list(paragraph.iter(_MATH + "oMath"))
        if not math_nodes:
            continue
        ordinary_text = "".join(node.text or "" for node in paragraph.iter(_WORD + "t"))
        if not ordinary_text.strip():
            result.extend(math_nodes)
    return result


def _display_row_count(nodes: list[ElementTree.Element]) -> int:
    rows = 0
    for equation in nodes:
        arrays = list(equation.iter(_MATH + "eqArr"))
        if not arrays:
            rows += 1
            continue
        rows += sum(sum(child.tag == _MATH + "e" for child in list(array)) for array in arrays)
    return rows


def _expected_margins(layout: ScanDocumentLayout) -> tuple[list[float], list[float]]:
    horizontal_scales = [page.pdf_width / page.width for page in layout.pages]
    vertical_scales = [
        page.pdf_height / page.height
        if page.metadata.get("source_kind") == "image"
        else horizontal_scale
        for page, horizontal_scale in zip(layout.pages, horizontal_scales, strict=True)
    ]
    render_frames = [_render_content_bbox(page) for page in layout.pages]
    left = [frame.x0 * scale for frame, scale in zip(render_frames, horizontal_scales, strict=True)]
    right = [
        (page.width - frame.x1) * scale
        for page, frame, scale in zip(
            layout.pages,
            render_frames,
            horizontal_scales,
            strict=True,
        )
    ]
    top: list[float] = []
    bottom: list[float] = []
    for page, horizontal_scale, vertical_scale in zip(
        layout.pages,
        horizontal_scales,
        vertical_scales,
        strict=True,
    ):
        offset = (
            0.0
            if page.metadata.get("source_kind") == "image"
            else max(0.0, (page.pdf_height - page.height * horizontal_scale) / 2)
        )
        top.append(offset + page.content_bbox.y0 * vertical_scale)
        bottom.append(page.pdf_height - (offset + page.content_bbox.y1 * vertical_scale))
    dense_bottoms = sorted(bottom)[: max(1, math.ceil(len(bottom) * 0.8))]
    shared = [
        statistics.median(left),
        statistics.median(top),
        statistics.median(right),
        statistics.median(dense_bottoms),
    ]
    page_bottoms = [min(shared[3], value) for value in bottom]
    return shared, page_bottoms


def _section_geometry(
    root: ElementTree.Element,
    layout: ScanDocumentLayout,
    plan: HybridLayoutPlan,
    content: MarkdownContent,
) -> tuple[bool, list[dict[str, Any]]]:
    sections = list(root.iter(_WORD + "sectPr"))
    shared, page_bottoms = _expected_margins(layout)
    result: list[dict[str, Any]] = []
    valid = len(sections) == len(layout.pages)
    blocks = {block.id: block for block in content.blocks}
    for index, (section, page) in enumerate(zip(sections, layout.pages, strict=False)):
        size = section.find(_WORD + "pgSz")
        margins = section.find(_WORD + "pgMar")
        expected_top = shared[1]
        if index < len(plan.pages):
            page_plan = plan.pages[index]
            first_block_index = min(
                (placement.block_index for placement in page_plan.placements),
                default=0,
            )
            leading_image_tops = [
                placement.source_bbox.y0
                for placement in page_plan.placements
                if placement.source_bbox is not None
                and placement.block_index <= first_block_index + 2
                and blocks[placement.block_id].kind is MarkdownBlockKind.IMAGE
            ]
            if leading_image_tops:
                horizontal_scale = page.pdf_width / page.width
                vertical_scale = (
                    page.pdf_height / page.height
                    if page.metadata.get("source_kind") == "image"
                    else horizontal_scale
                )
                source_offset = (
                    0.0
                    if page.metadata.get("source_kind") == "image"
                    else max(0.0, (page.pdf_height - page.height * horizontal_scale) / 2)
                )
                expected_top = min(
                    expected_top,
                    source_offset + min(leading_image_tops) * vertical_scale,
                )
        expected = {
            "width": round(page.pdf_width * 20),
            "height": round(page.pdf_height * 20),
            "left": round(shared[0] * 20),
            "top": round(expected_top * 20),
            "right": round(shared[2] * 20),
            "bottom": round(page_bottoms[index] * 20),
        }
        actual = {
            "width": int(size.get(_WORD + "w", "-1")) if size is not None else -1,
            "height": int(size.get(_WORD + "h", "-1")) if size is not None else -1,
            "left": int(margins.get(_WORD + "left", "-1")) if margins is not None else -1,
            "top": int(margins.get(_WORD + "top", "-1")) if margins is not None else -1,
            "right": int(margins.get(_WORD + "right", "-1")) if margins is not None else -1,
            "bottom": int(margins.get(_WORD + "bottom", "-1")) if margins is not None else -1,
        }
        matched = all(abs(actual[key] - expected[key]) <= 2 for key in expected)
        valid = valid and matched
        result.append({"page": index + 1, "expected_twips": expected, "actual_twips": actual})
    return valid, result


def _cjk_font_coverage(root: ElementTree.Element) -> tuple[int, int]:
    total = 0
    mapped = 0
    for run in root.iter(_WORD + "r"):
        text = "".join(node.text or "" for node in run.iter(_WORD + "t"))
        if not _CJK_PATTERN.search(text):
            continue
        total += 1
        properties = run.find(_WORD + "rPr")
        fonts = properties.find(_WORD + "rFonts") if properties is not None else None
        language = properties.find(_WORD + "lang") if properties is not None else None
        if (
            fonts is not None
            and fonts.get(_WORD + "eastAsia")
            and language is not None
            and language.get(_WORD + "eastAsia")
        ):
            mapped += 1
    return total, mapped


def _full_page_drawings(root: ElementTree.Element, layout: ScanDocumentLayout) -> int:
    if not layout.pages:
        return 0
    maximum_width = max(page.pdf_width for page in layout.pages) * 20 * 635
    maximum_height = max(page.pdf_height for page in layout.pages) * 20 * 635
    count = 0
    for extent in root.iter(_DRAWING + "extent"):
        width = int(extent.get("cx", "0"))
        height = int(extent.get("cy", "0"))
        if width >= maximum_width * 0.80 and height >= maximum_height * 0.80:
            count += 1
    return count


def _native_body_column_metrics(
    root: ElementTree.Element,
    layout: ScanDocumentLayout,
) -> dict[str, Any]:
    """Compare source multi-column evidence with tagged native Word tables.

    The renderer tags each editable body-column table with a caption of the
    form ``docreconstruct:body-columns-N``. OOXML traversal preserves page
    order, so an exact ordered comparison also rejects missing, extra, and
    mismatched native column structures without starting an Office renderer.
    """

    source_counts: list[int] = []
    for page in layout.pages:
        value = page.metadata.get("column_count", 1)
        try:
            count = int(value)
        except (TypeError, ValueError):
            count = 1
        source_counts.append(max(1, count))

    tagged_tables: list[tuple[int, ElementTree.Element]] = []
    for table in root.iter(_WORD + "tbl"):
        caption = table.find(f"{_WORD}tblPr/{_WORD}tblCaption")
        if caption is None:
            continue
        match = _BODY_COLUMNS_CAPTION.fullmatch(caption.get(_WORD + "val", ""))
        if match is not None:
            tagged_tables.append((max(1, int(match.group("count"))), table))
    rendered_counts = [count for count, _table in tagged_tables]

    table_reports: list[dict[str, Any]] = []
    for count, table in tagged_tables:
        rows = table.findall(_WORD + "tr")
        cells = rows[0].findall(_WORD + "tc") if len(rows) == 1 else []
        expected_cells = count * 2 - 1
        payloads: list[bool] = []
        character_counts: list[int] = []
        for cell in cells:
            text = _normalized(
                "".join(
                    node.text or ""
                    for node in cell.iter()
                    if node.tag in {_WORD + "t", _MATH + "t"}
                )
            )
            structural_payload = any(
                node is not cell and node.tag in {_WORD + "drawing", _MATH + "oMath", _WORD + "tbl"}
                for node in cell.iter()
            )
            character_counts.append(len(text))
            payloads.append(bool(text) or structural_payload)
        content_payloads = payloads[::2]
        gutter_payloads = payloads[1::2]
        populated_content_cells = sum(content_payloads)
        nonempty_gutter_cells = sum(gutter_payloads)
        unsplittable_rows = sum(
            row.find(f"{_WORD}trPr/{_WORD}cantSplit") is not None for row in rows
        )
        framed_paragraphs = sum(
            paragraph.find(f"{_WORD}pPr/{_WORD}framePr") is not None
            for paragraph in table.iter(_WORD + "p")
        )
        shape_matches = len(rows) == 1 and len(cells) == expected_cells
        payload_matches = (
            shape_matches and populated_content_cells == count and nonempty_gutter_cells == 0
        )
        flow_safe = unsplittable_rows == 0 and framed_paragraphs == 0
        table_reports.append(
            {
                "column_count": count,
                "row_count": len(rows),
                "expected_cell_count": expected_cells,
                "actual_cell_count": len(cells),
                "cell_character_counts": character_counts,
                "populated_content_cells": populated_content_cells,
                "nonempty_gutter_cells": nonempty_gutter_cells,
                "unsplittable_rows": unsplittable_rows,
                "framed_paragraphs": framed_paragraphs,
                "payload_matches": payload_matches,
                "flow_safe": flow_safe,
            }
        )

    expected_counts = [count for count in source_counts if count > 1]
    if expected_counts:
        matched = sum(
            expected == actual
            for expected, actual in zip(expected_counts, rendered_counts, strict=False)
        )
        coverage = matched / len(expected_counts)
    else:
        coverage = 1.0 if not rendered_counts else 0.0
    passed = rendered_counts == expected_counts
    expected_content_cells = sum(expected_counts)
    populated_content_cells = sum(
        int(report["populated_content_cells"]) for report in table_reports
    )
    expected_gutter_cells = sum(max(0, count - 1) for count in expected_counts)
    empty_gutter_cells = sum(
        max(0, int(report["actual_cell_count"]) // 2) - int(report["nonempty_gutter_cells"])
        for report in table_reports
    )
    payload_coverage = (
        populated_content_cells / expected_content_cells
        if expected_content_cells
        else 1.0
        if not table_reports
        else 0.0
    )
    gutter_purity = (
        empty_gutter_cells / expected_gutter_cells
        if expected_gutter_cells
        else 1.0
        if not table_reports
        else 0.0
    )
    payload_matches = passed and all(bool(report["payload_matches"]) for report in table_reports)
    flow_safe = passed and all(bool(report["flow_safe"]) for report in table_reports)
    return {
        "source_body_column_counts": source_counts,
        "rendered_body_column_counts": rendered_counts,
        "body_column_coverage": round(coverage, 6),
        "body_columns_match": passed,
        "body_column_table_reports": table_reports,
        "body_column_payload_coverage": round(min(1.0, payload_coverage), 6),
        "body_column_gutter_purity": round(min(1.0, gutter_purity), 6),
        "body_column_payload_matches": payload_matches,
        "body_column_flow_safe": flow_safe,
        "body_column_unsplittable_rows": sum(
            int(report["unsplittable_rows"]) for report in table_reports
        ),
        "body_column_framed_paragraphs": sum(
            int(report["framed_paragraphs"]) for report in table_reports
        ),
    }


def _source_body_column_boxes(page: Any) -> list[PixelBox]:
    """Return validated source body columns for rendered foreground QA."""

    value = page.metadata.get("column_count", 1)
    try:
        count = int(value)
    except (TypeError, ValueError):
        return []
    raw_boxes = page.metadata.get("column_boxes")
    if count <= 1 or not isinstance(raw_boxes, list) or len(raw_boxes) != count:
        return []
    boxes: list[PixelBox] = []
    for value in raw_boxes:
        try:
            if isinstance(value, dict):
                box = PixelBox.model_validate(value)
            elif isinstance(value, list) and len(value) == 4:
                box = PixelBox(
                    x0=round(float(value[0])),
                    y0=round(float(value[1])),
                    x1=round(float(value[2])),
                    y1=round(float(value[3])),
                )
            else:
                return []
        except (TypeError, ValueError):
            return []
        boxes.append(box)
    boxes.sort(key=lambda box: box.x0)
    return boxes


def _body_foreground_metrics(
    layout: ScanDocumentLayout,
    rendered_pages: tuple[bytes, ...],
) -> dict[str, Any]:
    """Measure conservative ink-mass retention inside detected body columns."""

    from docreconstruct.evaluation.visual import (
        _document_foreground,
        _load_image,
        _pillow,
    )

    api = _pillow()
    reports: list[dict[str, Any]] = []
    for page, rendered in zip(layout.pages, rendered_pages, strict=False):
        boxes = _source_body_column_boxes(page)
        if not boxes:
            continue
        reference = _load_image(page.image, api)
        candidate = _load_image(io.BytesIO(rendered), api)
        if candidate.size != reference.size:
            candidate = candidate.resize(reference.size, api["Image"].Resampling.LANCZOS)
        reference_map = _document_foreground(reference, api).convert("L")
        candidate_map = _document_foreground(candidate, api).convert("L")
        columns: list[dict[str, Any]] = []
        for index, box in enumerate(boxes, start=1):
            crop = (box.x0, box.y0, box.x1, box.y1)
            source_ink = sum(reference_map.crop(crop).histogram()[:128])
            candidate_ink = sum(candidate_map.crop(crop).histogram()[:128])
            active = source_ink >= max(64, round(box.area * 0.001))
            maximum = max(source_ink, candidate_ink)
            ratio = 1.0 if maximum == 0 else min(source_ink, candidate_ink) / maximum
            columns.append(
                {
                    "column": index,
                    "source_ink_pixels": source_ink,
                    "candidate_ink_pixels": candidate_ink,
                    "active": active,
                    "ink_mass_ratio": round(ratio, 6),
                }
            )
        active_columns = [column for column in columns if column["active"]]
        page_ratio = min(
            (float(column["ink_mass_ratio"]) for column in active_columns),
            default=1.0,
        )
        reports.append(
            {
                "page": page.number,
                "minimum_active_column_ratio": round(page_ratio, 6),
                "columns": columns,
                "passed": page_ratio >= _BODY_FOREGROUND_MIN_RATIO,
            }
        )
    return {
        "threshold": _BODY_FOREGROUND_MIN_RATIO,
        "measured_pages": len(reports),
        "minimum_ratio": min(
            (float(report["minimum_active_column_ratio"]) for report in reports),
            default=1.0,
        ),
        "pages": reports,
        "passed": all(bool(report["passed"]) for report in reports),
    }


def _layout_utilization(
    content: MarkdownContent,
    layout: ScanDocumentLayout,
    plan_geometry: dict[str, Any],
) -> list[dict[str, Any]]:
    if not layout.pages:
        return []
    total_capacity = sum(page.content_bbox.height / page.line_pitch for page in layout.pages)
    equation_units = sum(
        equation_layout_units(block.text)
        for block in content.blocks
        if block.kind is MarkdownBlockKind.EQUATION
    )
    other_units = sum(
        max(1.0, block.text.count("\n") + 1)
        for block in content.blocks
        if block.kind not in {MarkdownBlockKind.EQUATION, MarkdownBlockKind.IMAGE}
    )
    fallback_utilization = (equation_units + other_units) / max(1.0, total_capacity)
    geometry_coverage = float(plan_geometry.get("source_geometry_coverage", 0.0))
    planned_utilization = float(plan_geometry.get("mapped_vertical_span_ratio", 0.0))
    return [
        {
            "planning_mode": (
                "source_geometry" if geometry_coverage == 1.0 else "content_estimate"
            ),
            "estimated_content_units": round(equation_units + other_units, 4),
            "source_capacity_units": round(total_capacity, 4),
            "fallback_estimated_utilization": round(fallback_utilization, 6),
            "planned_visual_utilization": round(planned_utilization, 6),
        }
    ]


def _overlap_ratio(left: PixelBox, right: PixelBox) -> float:
    width = max(0, min(left.x1, right.x1) - max(left.x0, right.x0))
    height = max(0, min(left.y1, right.y1) - max(left.y0, right.y0))
    return width * height / max(1, min(left.area, right.area))


def _validation_plan(
    content: MarkdownContent,
    layout: ScanDocumentLayout,
    evidence_matches: Sequence[EvidenceMatch] | None = None,
) -> HybridLayoutPlan:
    """Rebuild the exact deterministic plan without fetching remote assets."""

    asset_matches = match_markdown_assets(content, layout, allow_remote=False)
    table_matches = match_markdown_tables(content, layout, asset_matches)
    if evidence_matches is None:
        return build_hybrid_layout_plan(content, layout, asset_matches, table_matches)
    return build_hybrid_layout_plan(
        content,
        layout,
        asset_matches,
        table_matches,
        evidence_matches=evidence_matches,
    )


def _validation_evidence_context(
    layout_path: Path,
    layout: ScanDocumentLayout,
) -> ProviderContext:
    """Give saved adapters the layout authority without assuming multi-page size."""

    updates: dict[str, Any] = {
        "source": str(layout_path),
        "metadata": {"authority": "layout", "offline_sidecar": True},
    }
    if len(layout.pages) == 1:
        updates["page_width"] = float(layout.pages[0].width)
        updates["page_height"] = float(layout.pages[0].height)
    return ProviderContext.model_validate(updates)


def _evidence_path_fingerprints(paths: Sequence[Path]) -> list[dict[str, Any]]:
    """Fingerprint sidecars for reports without turning QA into a second loader."""

    fingerprints: list[dict[str, Any]] = []
    for path in paths:
        record: dict[str, Any] = {"path": str(path), "sha256": None, "size": None}
        try:
            if path.is_file():
                record["sha256"] = _sha256(path)
                record["size"] = path.stat().st_size
        except OSError as exc:
            record["fingerprint_error"] = f"{type(exc).__name__}: {exc}"
        fingerprints.append(record)
    return fingerprints


def _unique_strings(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _evidence_validation_metrics(
    paths: Sequence[Path],
    bundle: SidecarEvidenceBundle | None,
    matches: Sequence[EvidenceMatch],
) -> dict[str, Any]:
    """Return audit-safe evidence metrics without retaining raw provider JSON."""

    items = bundle.items if bundle is not None else ()
    providers = sorted(
        {item.provider for item in items if item.document is not None and item.provider is not None}
    )
    ambiguous = [
        {
            "path": str(item.path),
            "selected_provider": item.provider,
            "confidence": item.detection.confidence,
            "reason": item.detection.reason,
            "candidates": [
                {
                    "provider": candidate.provider,
                    "confidence": candidate.confidence,
                    "reason": candidate.reason,
                }
                for candidate in item.detection.candidates[:3]
            ],
        }
        for item in items
        if item.detection.ambiguous
    ]
    bundle_warnings = list(bundle.warnings) if bundle is not None else []
    match_warnings = [
        f"{match.block_id}: {warning}" for match in matches for warning in match.warnings
    ]
    match_details = [
        {
            "block_id": match.block_id,
            "block_index": match.block_index,
            "page_number": match.page_number,
            "source_bbox": match.source_bbox.model_dump(mode="json"),
            "source_rows": [row.model_dump(mode="json") for row in match.source_rows],
            "match_score": match.match_score,
            "confidence": match.confidence,
            "providers": list(match.providers),
            "element_ids": list(match.element_ids),
            "geometry_source": match.geometry_source,
            "conflict": match.conflict,
            "warnings": list(match.warnings),
        }
        for match in matches
    ]
    return {
        "evidence_inputs": len(paths),
        "evidence_documents": len(bundle.documents) if bundle is not None else 0,
        "evidence_providers": providers,
        "evidence_matched_blocks": len({match.block_id for match in matches}),
        "evidence_geometry_matches": sum(match.source_bbox.area > 0 for match in matches),
        "evidence_conflicts": sum(match.conflict for match in matches),
        "evidence_warnings": _unique_strings([*bundle_warnings, *match_warnings]),
        "evidence_errors": list(bundle.errors) if bundle is not None else [],
        "evidence_ambiguous_detections": ambiguous,
        "evidence_fingerprints": _evidence_path_fingerprints(paths),
        "evidence_matches": match_details,
    }


def _plan_geometry_metrics(
    content: MarkdownContent,
    layout: ScanDocumentLayout,
    plan: HybridLayoutPlan,
) -> dict[str, Any]:
    """Measure source-row use independently from the emitted DOCX package."""

    blocks = {block.id: block for block in content.blocks}
    pages = {page.number: page for page in plan.pages}
    total_slots = 0
    mapped_slots = 0
    total_placements = 0
    placements_with_geometry = 0
    mapped_source_rows = 0
    anchor_order_violations: list[dict[str, Any]] = []
    maximum_row_gap_ratio = 0.0
    spans: list[dict[str, Any]] = []
    for source_page in layout.pages:
        page_plan = pages.get(source_page.number)
        placements = list(page_plan.placements) if page_plan is not None else []
        total_placements += len(placements)
        placements_with_geometry += sum(
            placement.source_bbox is not None for placement in placements
        )
        anchors = [
            placement.source_bbox
            for placement in placements
            if placement.source_bbox is not None
            and blocks[placement.block_id].kind
            in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}
        ]
        slots = visual_text_rows(source_page, anchors)
        placement_rows = [row for placement in placements for row in placement.source_rows]
        mapped_source_rows += len(placement_rows)
        anchored = [
            placement
            for placement in placements
            if placement.source_bbox is not None
            and blocks[placement.block_id].kind
            in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}
        ]
        tolerance = source_page.line_pitch * 0.30
        for placement in placements:
            if placement.source_bbox is None or placement in anchored:
                continue
            block_kind = blocks[placement.block_id].kind
            if block_kind in {
                MarkdownBlockKind.PARAGRAPH,
                MarkdownBlockKind.HEADING,
                MarkdownBlockKind.LIST_ITEM,
                MarkdownBlockKind.CODE,
            }:
                ordered_rows = source_row_reading_order(source_page, placement.source_rows)
                for previous, following in zip(ordered_rows, ordered_rows[1:], strict=False):
                    maximum_row_gap_ratio = max(
                        maximum_row_gap_ratio,
                        max(0.0, following.y0 - previous.y1) / source_page.line_pitch,
                    )
            for anchor in anchored:
                anchor_box = anchor.source_bbox
                if anchor_box is None:
                    continue
                before_crossing = (
                    placement.block_index < anchor.block_index
                    and placement.source_bbox.y1 > anchor_box.y0 + tolerance
                )
                after_crossing = (
                    placement.block_index > anchor.block_index
                    and placement.source_bbox.y0 < anchor_box.y1 - tolerance
                )
                if before_crossing or after_crossing:
                    anchor_order_violations.append(
                        {
                            "page": source_page.number,
                            "block_id": placement.block_id,
                            "anchor_id": anchor.block_id,
                        }
                    )
        covered = {
            index
            for index, slot in enumerate(slots)
            if any(_overlap_ratio(slot, row) >= 0.90 for row in placement_rows)
        }
        total_slots += len(slots)
        mapped_slots += len(covered)
        source_top: int | None
        source_bottom: int | None
        mapped_top: int | None
        mapped_bottom: int | None
        if slots:
            source_top = min(slot.y0 for slot in slots)
            source_bottom = max(slot.y1 for slot in slots)
            source_span = max(1, source_bottom - source_top)
            if placement_rows:
                mapped_top = min(row.y0 for row in placement_rows)
                mapped_bottom = max(row.y1 for row in placement_rows)
                overlap = max(
                    0,
                    min(source_bottom, mapped_bottom) - max(source_top, mapped_top),
                )
                span_ratio = overlap / source_span
            else:
                mapped_top = mapped_bottom = None
                span_ratio = 0.0
        else:
            source_top = source_bottom = None
            mapped_top = mapped_bottom = None
            span_ratio = 1.0
        spans.append(
            {
                "page": source_page.number,
                "source_top": source_top,
                "source_bottom": source_bottom,
                "mapped_top": mapped_top,
                "mapped_bottom": mapped_bottom,
                "ratio": round(span_ratio, 6),
                "source_slots": len(slots),
                "mapped_slots": len(covered),
            }
        )
    slot_coverage = mapped_slots / total_slots if total_slots else 1.0
    geometry_coverage = placements_with_geometry / total_placements if total_placements else 1.0
    span_ratio = min(
        (float(item["ratio"]) for item in spans if item["source_slots"]),
        default=1.0,
    )
    return {
        "source_visual_slots": total_slots,
        "mapped_visual_slots": mapped_slots,
        "unmapped_visual_slots": max(0, total_slots - mapped_slots),
        "source_visual_slot_coverage": round(slot_coverage, 6),
        "planned_placements": total_placements,
        "placements_with_source_geometry": placements_with_geometry,
        "source_geometry_coverage": round(geometry_coverage, 6),
        "mapped_source_rows": mapped_source_rows,
        "source_anchor_order_violations": anchor_order_violations,
        "maximum_source_row_gap_ratio": round(maximum_row_gap_ratio, 6),
        "mapped_vertical_span": spans,
        "mapped_vertical_span_ratio": round(span_ratio, 6),
    }


def _source_math_expectations(content: MarkdownContent) -> dict[str, Any]:
    nary_symbols: Counter[str] = Counter()
    integral_modes = {
        "display": Counter[str](),
        "inline": Counter[str](),
    }
    native_delimiters = 0
    for block in content.blocks:
        if block.kind is MarkdownBlockKind.EQUATION:
            sources = [(block.text, True)]
        elif block.kind is MarkdownBlockKind.IMAGE:
            sources = []
        else:
            sources = [(match.group(1), False) for match in _INLINE_MATH.finditer(block.text)]
        for source, is_display in sources:
            commands = [match.group("command") for match in _NARY_SOURCE.finditer(source)]
            nary_symbols.update(_NARY_SYMBOLS[command] for command in commands)
            context = "display" if is_display else "inline"
            for integral in _INTEGRAL_SOURCE.finditer(source):
                mode = "undOvr" if integral.group("limits") else "subSup"
                integral_modes[context][mode] += 1
            native_delimiters += len(_NATIVE_DELIMITER_SOURCE.findall(source))
    return {
        "nary_symbols": dict(sorted(nary_symbols.items())),
        "nary_count": sum(nary_symbols.values()),
        "integral_limit_modes": {
            context: dict(sorted(counts.items())) for context, counts in integral_modes.items()
        },
        "native_delimiters": native_delimiters,
    }


def _expected_layout_furniture(
    content: MarkdownContent,
    layout: ScanDocumentLayout,
    plan: HybridLayoutPlan,
) -> tuple[int, list[str]]:
    blocks = {block.id: block for block in content.blocks}
    expected_mastheads = 0
    expected_footers: list[str] = []
    for page, page_plan in zip(layout.pages, plan.pages, strict=False):
        page_blocks = [blocks[placement.block_id] for placement in page_plan.placements]
        section_index = next(
            (
                index
                for index, block in enumerate(page_blocks)
                if block.metadata.get("role") == "section_heading"
            ),
            None,
        )
        if (
            page.metadata.get("header_column_count") == 2
            and page.metadata.get("column_count") == 1
            and section_index is not None
            and section_index >= 4
        ):
            expected_mastheads += 1
        threshold = page.content_bbox.y0 + page.content_bbox.height * 0.95
        footer: list[str] = []
        for placement in reversed(page_plan.placements):
            block = blocks[placement.block_id]
            box = placement.source_bbox
            if (
                box is None
                or box.y0 < threshold
                or block.kind not in {MarkdownBlockKind.PARAGRAPH, MarkdownBlockKind.HEADING}
                or len(block.text) > 120
            ):
                break
            footer.append(block.text)
        expected_footers.extend(reversed(footer))
    return expected_mastheads, expected_footers


def _body_flow_metrics(
    root: ElementTree.Element,
    layout: ScanDocumentLayout,
) -> dict[str, Any]:
    body = root.find(_WORD + "body")
    if body is None:
        return {
            "ordinary_line_spacing_outliers": [],
            "narrow_body_paragraphs": [],
        }
    sections = list(root.iter(_WORD + "sectPr"))
    widths: list[int] = []
    for section in sections:
        size = section.find(_WORD + "pgSz")
        margins = section.find(_WORD + "pgMar")
        if size is None or margins is None:
            continue
        widths.append(
            int(size.get(_WORD + "w", "0"))
            - int(margins.get(_WORD + "left", "0"))
            - int(margins.get(_WORD + "right", "0"))
        )
    content_width = min(widths, default=1)
    pitch_twips = round(
        statistics.median(
            page.line_pitch * (page.pdf_height / page.height) * 20 for page in layout.pages
        )
    )
    spacing_outliers: list[dict[str, Any]] = []
    narrow: list[dict[str, Any]] = []

    def heading_like(paragraph: ElementTree.Element) -> bool:
        properties = paragraph.find(_WORD + "pPr")
        style = properties.find(_WORD + "pStyle") if properties is not None else None
        style_name = style.get(_WORD + "val", "").casefold() if style is not None else ""
        if style_name.startswith("heading") or style_name in {"title", "subtitle"}:
            return True
        text_runs = [
            run
            for run in paragraph.iter(_WORD + "r")
            if any((node.text or "").strip() for node in run.iter(_WORD + "t"))
        ]
        if not text_runs:
            return False
        for run in text_runs:
            bold = run.find(f"{_WORD}rPr/{_WORD}b")
            if bold is None or bold.get(_WORD + "val", "1").casefold() in {
                "0",
                "false",
                "off",
            }:
                return False
        return True

    for index, paragraph in enumerate(child for child in body if child.tag == _WORD + "p"):
        text = _normalized("".join(node.text or "" for node in paragraph.iter(_WORD + "t")))
        if not text or next(paragraph.iter(_MATH + "oMath"), None) is not None:
            continue
        if heading_like(paragraph):
            continue
        spacing = paragraph.find(f"{_WORD}pPr/{_WORD}spacing")
        line = int(spacing.get(_WORD + "line", "0")) if spacing is not None else 0
        if line > pitch_twips * 1.80:
            spacing_outliers.append({"paragraph": index, "line_twips": line, "text": text[:80]})
        indent = paragraph.find(f"{_WORD}pPr/{_WORD}ind")
        left = int(indent.get(_WORD + "left", "0")) if indent is not None else 0
        right = int(indent.get(_WORD + "right", "0")) if indent is not None else 0
        remaining = max(0, content_width - left - right)
        if remaining < content_width * 0.25:
            narrow.append(
                {
                    "paragraph": index,
                    "width_ratio": round(remaining / max(1, content_width), 6),
                    "text": text[:80],
                }
            )
    return {
        "ordinary_line_spacing_outliers": spacing_outliers,
        "narrow_body_paragraphs": narrow,
    }


def _has_math_payload(element: ElementTree.Element | None) -> bool:
    if element is None:
        return False
    if any((node.text or "").strip() for node in element.iter(_MATH + "t")):
        return True
    structural = {"d", "f", "func", "limLow", "limUpp", "nary", "rad", "sSub", "sSup"}
    return any(
        _local_name(node.tag) in structural for node in element.iter() if node is not element
    )


def _positive_integer(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _math_format_status(
    properties: ElementTree.Element | None,
) -> tuple[bool, bool, str | None, int | None]:
    """Return explicit font/size coverage and the agreed base values."""

    if properties is None:
        return False, False, None, None
    fonts = properties.find(_WORD + "rFonts")
    font_values = (
        [fonts.get(_WORD + attribute) for attribute in _MATH_FONT_ATTRIBUTES]
        if fonts is not None
        else []
    )
    explicit_fonts = len(font_values) == len(_MATH_FONT_ATTRIBUTES) and all(font_values)
    font_name = font_values[0] if explicit_fonts and len(set(font_values)) == 1 else None
    font_valid = font_name == _MATH_FONT

    size = properties.find(_WORD + "sz")
    complex_size = properties.find(_WORD + "szCs")
    half_points = _positive_integer(size.get(_WORD + "val") if size is not None else None)
    complex_half_points = _positive_integer(
        complex_size.get(_WORD + "val") if complex_size is not None else None
    )
    size_valid = half_points is not None and half_points == complex_half_points
    return font_valid, size_valid, font_name, half_points if size_valid else None


def _math_typography_metrics(
    root: ElementTree.Element,
    display_ids: set[int],
) -> dict[str, Any]:
    """Audit explicit base typography without simulating Word math layout.

    Script structures deliberately retain the same explicit base size as their
    equation.  Word applies its native sub/superscript scaling from that base;
    encoding a smaller direct size would make the result renderer-dependent.
    """

    total_runs = 0
    sized_runs = 0
    fonted_runs = 0
    formatted_runs = 0
    total_controls = 0
    sized_controls = 0
    fonted_controls = 0
    formatted_controls = 0
    total_display_marks = 0
    formatted_display_marks = 0
    sizes: Counter[int] = Counter()
    fonts: Counter[str] = Counter()
    control_properties: Counter[str] = Counter()
    formatted_control_properties: Counter[str] = Counter()
    uniform_equations = 0
    equation_reports: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    equations = list(root.iter(_MATH + "oMath"))
    display_marks: dict[int, ElementTree.Element | None] = {}
    for paragraph in root.iter(_WORD + "p"):
        paragraph_properties = paragraph.find(f"{_WORD}pPr/{_WORD}rPr")
        for wrapper in paragraph.iter(_MATH + "oMathPara"):
            for equation in wrapper.iter(_MATH + "oMath"):
                display_marks[id(equation)] = paragraph_properties

    for equation_index, equation in enumerate(equations, start=1):
        runs = [
            run
            for run in equation.iter(_MATH + "r")
            if any((node.text or "") != "" for node in run.iter(_MATH + "t"))
        ]
        controls = [
            node for node in equation.iter() if _local_name(node.tag) in _MATH_CONTROL_PROPERTIES
        ]
        equation_sizes: set[int] = set()
        equation_fonts: set[str] = set()
        is_display = id(equation) in display_ids
        requires_display_mark = id(equation) in display_marks
        run_font_coverage = 0
        run_size_coverage = 0
        run_format_coverage = 0
        for run in runs:
            font_valid, size_valid, font_name, half_points = _math_format_status(
                run.find(_WORD + "rPr")
            )
            run_font_coverage += font_valid
            run_size_coverage += size_valid
            run_format_coverage += font_valid and size_valid
            if font_name is not None:
                equation_fonts.add(font_name)
                fonts[font_name] += 1
            if half_points is not None:
                equation_sizes.add(half_points)
                sizes[half_points] += 1

        control_font_coverage = 0
        control_size_coverage = 0
        control_format_coverage = 0
        for control_property in controls:
            property_name = _local_name(control_property.tag)
            control_properties[property_name] += 1
            control = control_property.find(_MATH + "ctrlPr")
            properties = control.find(_WORD + "rPr") if control is not None else None
            font_valid, size_valid, font_name, half_points = _math_format_status(properties)
            control_font_coverage += font_valid
            control_size_coverage += size_valid
            control_format_coverage += font_valid and size_valid
            if font_valid and size_valid:
                formatted_control_properties[property_name] += 1
            if font_name is not None:
                equation_fonts.add(font_name)
                fonts[font_name] += 1
            if half_points is not None:
                equation_sizes.add(half_points)
                sizes[half_points] += 1

        display_mark_formatted = True
        if requires_display_mark:
            total_display_marks += 1
            mark_font_valid, mark_size_valid, mark_font_name, mark_half_points = (
                _math_format_status(display_marks.get(id(equation)))
            )
            display_mark_formatted = mark_font_valid and mark_size_valid
            formatted_display_marks += display_mark_formatted
            if mark_font_name is not None:
                equation_fonts.add(mark_font_name)
                fonts[mark_font_name] += 1
            if mark_half_points is not None:
                equation_sizes.add(mark_half_points)
                sizes[mark_half_points] += 1

        total_runs += len(runs)
        sized_runs += run_size_coverage
        fonted_runs += run_font_coverage
        formatted_runs += run_format_coverage
        total_controls += len(controls)
        sized_controls += control_size_coverage
        fonted_controls += control_font_coverage
        formatted_controls += control_format_coverage
        uniform = bool(runs) and all(
            (
                run_font_coverage == len(runs),
                run_size_coverage == len(runs),
                control_font_coverage == len(controls),
                control_size_coverage == len(controls),
                display_mark_formatted,
                equation_fonts == {_MATH_FONT},
                len(equation_sizes) == 1,
            )
        )
        uniform_equations += uniform
        report = {
            "equation": equation_index,
            "context": "display" if is_display else "inline",
            "math_runs": len(runs),
            "formatted_math_runs": run_format_coverage,
            "math_controls": len(controls),
            "formatted_math_controls": control_format_coverage,
            "display_paragraph_mark_formatted": (
                display_mark_formatted if requires_display_mark else None
            ),
            "base_fonts": sorted(equation_fonts),
            "base_half_point_sizes": sorted(equation_sizes),
            "uniform": uniform,
        }
        equation_reports.append(report)
        if not uniform:
            mismatches.append(report)

    equation_count = len(equations)
    return {
        "math_runs": total_runs,
        "sized_math_runs": sized_runs,
        "fonted_math_runs": fonted_runs,
        "formatted_math_runs": formatted_runs,
        "math_size_coverage": round(sized_runs / total_runs, 6) if total_runs else 1.0,
        "math_run_size_coverage": round(sized_runs / total_runs, 6) if total_runs else 1.0,
        "math_run_font_coverage": round(fonted_runs / total_runs, 6) if total_runs else 1.0,
        "math_run_format_coverage": (round(formatted_runs / total_runs, 6) if total_runs else 1.0),
        "math_half_point_sizes": {str(key): value for key, value in sorted(sizes.items())},
        "math_font_names": dict(sorted(fonts.items())),
        "math_controls": total_controls,
        "sized_math_controls": sized_controls,
        "fonted_math_controls": fonted_controls,
        "formatted_math_controls": formatted_controls,
        "math_control_size_coverage": (
            round(sized_controls / total_controls, 6) if total_controls else 1.0
        ),
        "math_control_font_coverage": (
            round(fonted_controls / total_controls, 6) if total_controls else 1.0
        ),
        "math_control_format_coverage": (
            round(formatted_controls / total_controls, 6) if total_controls else 1.0
        ),
        "math_control_property_counts": dict(sorted(control_properties.items())),
        "formatted_math_control_property_counts": dict(
            sorted(formatted_control_properties.items())
        ),
        "math_typography_equations": equation_count,
        "math_display_paragraph_marks": total_display_marks,
        "formatted_math_display_paragraph_marks": formatted_display_marks,
        "math_display_paragraph_mark_format_coverage": (
            round(formatted_display_marks / total_display_marks, 6) if total_display_marks else 1.0
        ),
        "math_typography_uniform_equations": uniform_equations,
        "math_typography_equation_coverage": (
            round(uniform_equations / equation_count, 6) if equation_count else 1.0
        ),
        "math_base_fonts": sorted(fonts),
        "math_base_half_point_sizes": sorted(sizes),
        "math_typography_equation_reports": equation_reports,
        "math_typography_mismatches": mismatches,
    }


def _math_ooxml_metrics(
    root: ElementTree.Element,
    display_math: list[ElementTree.Element],
) -> dict[str, Any]:
    display_ids = {id(node) for node in display_math}
    typography = _math_typography_metrics(root, display_ids)
    nary_symbols: Counter[str] = Counter()
    nary_with_operand = 0
    integral_modes = {
        "display": Counter[str](),
        "inline": Counter[str](),
    }
    for equation in root.iter(_MATH + "oMath"):
        context = "display" if id(equation) in display_ids else "inline"
        for nary in equation.iter(_MATH + "nary"):
            character = nary.find(f"{_MATH}naryPr/{_MATH}chr")
            symbol = character.get(_MATH + "val") if character is not None else None
            if symbol:
                nary_symbols[symbol] += 1
            if _has_math_payload(nary.find(_MATH + "e")):
                nary_with_operand += 1
            if symbol not in {"∫", "∮"}:
                continue
            mode = nary.find(f"{_MATH}naryPr/{_MATH}limLoc")
            value = mode.get(_MATH + "val") if mode is not None else None
            integral_modes[context][value or "missing"] += 1

    delimiters = list(root.iter(_MATH + "d"))
    complete_delimiters = 0
    for delimiter in delimiters:
        properties = delimiter.find(_MATH + "dPr")
        beginning = properties.find(_MATH + "begChr") if properties is not None else None
        ending = properties.find(_MATH + "endChr") if properties is not None else None
        grow = properties.find(_MATH + "grow") if properties is not None else None
        if (
            beginning is not None
            and ending is not None
            and grow is not None
            and grow.get(_MATH + "val") == "1"
            and _has_math_payload(delimiter.find(_MATH + "e"))
        ):
            complete_delimiters += 1

    display_paragraphs = []
    line_rules: Counter[str] = Counter()
    exact_display_spacing = 0
    for paragraph in root.iter(_WORD + "p"):
        math_nodes = list(paragraph.iter(_MATH + "oMath"))
        ordinary = "".join(node.text or "" for node in paragraph.iter(_WORD + "t"))
        if not math_nodes or ordinary.strip():
            continue
        display_paragraphs.append(paragraph)
        spacing = paragraph.find(f"{_WORD}pPr/{_WORD}spacing")
        rule = spacing.get(_WORD + "lineRule") if spacing is not None else None
        normalized_rule = rule or "unspecified"
        line_rules[normalized_rule] += 1
        exact_display_spacing += normalized_rule.lower() == "exact"

    return {
        "display_oMathPara": len(list(root.iter(_MATH + "oMathPara"))),
        "display_paragraphs": len(display_paragraphs),
        "display_line_rules": dict(sorted(line_rules.items())),
        "display_exact_spacing": exact_display_spacing,
        **typography,
        "nary_symbols": dict(sorted(nary_symbols.items())),
        "nary_count": sum(nary_symbols.values()),
        "nary_with_operand": nary_with_operand,
        "integral_limit_modes": {
            context: dict(sorted(counts.items())) for context, counts in integral_modes.items()
        },
        "native_delimiters": len(delimiters),
        "complete_native_delimiters": complete_delimiters,
    }


def validate_hybrid(
    content: str | Path,
    layout: str | Path,
    candidate: str | Path,
    *,
    evidence: Sequence[str | Path] = (),
    evidence_provider_hints: ProviderHints | None = None,
    strict_evidence: bool = True,
    render_backend: str = "native",
    renderer_path: str | Path | None = None,
    minimum_visual_score: float | None = None,
    render_output_dir: str | Path | None = None,
) -> HybridValidationReport:
    """Validate hybrid OOXML and optionally render it through a project backend."""

    if minimum_visual_score is not None and not 0.0 <= minimum_visual_score <= 1.0:
        raise ValueError("minimum_visual_score must be between 0 and 1")
    if render_backend.strip().casefold() == "native" and minimum_visual_score is not None:
        raise ValueError("minimum_visual_score requires auto or libreoffice render_backend")

    content_path = Path(content).expanduser().resolve()
    layout_path = Path(layout).expanduser().resolve()
    candidate_path = Path(candidate).expanduser().resolve()
    markdown = parse_markdown_content(content_path)
    scan = analyze_scan_source(layout_path)
    evidence_paths: tuple[Path, ...]
    if isinstance(evidence, (str, Path)):
        evidence_paths = (Path(evidence).expanduser().resolve(),)
    else:
        evidence_paths = tuple(Path(path).expanduser().resolve() for path in evidence)
    evidence_bundle: SidecarEvidenceBundle | None = None
    evidence_matches: list[EvidenceMatch] = []
    if evidence_paths:
        evidence_bundle = load_sidecar_evidence(
            evidence_paths,
            provider_hints=evidence_provider_hints,
            context=_validation_evidence_context(layout_path, scan),
            strict=strict_evidence,
        )
        evidence_matches = match_sidecar_evidence(markdown, scan, evidence_bundle)
        plan = _validation_plan(markdown, scan, evidence_matches)
    else:
        plan = _validation_plan(markdown, scan)
    evidence_metrics = _evidence_validation_metrics(
        evidence_paths,
        evidence_bundle,
        evidence_matches,
    )
    plan_geometry = _plan_geometry_metrics(markdown, scan, plan)
    try:
        with zipfile.ZipFile(candidate_path) as package:
            corrupt_member = package.testzip()
            names = set(package.namelist())
            required = {"[Content_Types].xml", "word/document.xml"}
            if corrupt_member is not None or not required <= names:
                raise ValueError(f"not a complete DOCX package: {candidate_path}")
            root = ElementTree.fromstring(package.read("word/document.xml"))
            footer_roots = [
                ElementTree.fromstring(package.read(name))
                for name in sorted(names)
                if name.startswith("word/footer") and name.endswith(".xml")
            ]
            relationships = (
                ElementTree.fromstring(package.read("word/_rels/document.xml.rels"))
                if "word/_rels/document.xml.rels" in names
                else None
            )
            media = sorted(name for name in names if name.startswith("word/media/"))
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError, KeyError) as exc:
        raise ValueError(f"not a readable DOCX package: {candidate_path}") from exc

    expressions, display_expressions = _math_sources(markdown)
    actual_math = list(root.iter(_MATH + "oMath"))
    display_math = _display_math_nodes(root)
    expected_signatures = [_math_signature(build_omml(source)) for source in expressions]
    actual_signatures = [_math_signature(node) for node in actual_math]
    unsupported = sorted(
        {
            command
            for expression in expressions
            for command in unsupported_latex_commands(expression)
        }
    )
    math_text = "".join(node.text or "" for node in root.iter(_MATH + "t"))
    leaked_controls = sorted(set(_CONTROL_LEAK.findall(math_text)))
    expected_rows = sum(equation_row_count(expression) for expression in display_expressions)
    actual_rows = _display_row_count(display_math)
    source_math_expectations = _source_math_expectations(markdown)
    math_ooxml = _math_ooxml_metrics(root, display_math)
    geometry_matches, geometry = _section_geometry(root, scan, plan, markdown)
    sections = list(root.iter(_WORD + "sectPr"))
    drawings = len(list(root.iter(_WORD + "drawing")))
    full_page_drawings = _full_page_drawings(root, scan)
    external_relationships = 0
    if relationships is not None:
        external_relationships = sum(
            node.get("TargetMode") == "External"
            for node in relationships.iter(f"{{{_RELATIONSHIPS}}}Relationship")
        )
    cjk_runs, cjk_mapped_runs = _cjk_font_coverage(root)
    expected_mastheads, expected_footers = _expected_layout_furniture(markdown, scan, plan)
    footer_projection = "\n".join(_docx_projection(footer) for footer in footer_roots)
    candidate_projection = _normalized(
        "\n".join(value for value in (_docx_projection(root), footer_projection) if value)
    )
    source_projection = _markdown_projection(markdown)
    source_bag = Counter(re.findall(r"\S+", source_projection))
    candidate_bag = Counter(re.findall(r"\S+", candidate_projection))
    furniture_reorders_content = bool(expected_mastheads or expected_footers)
    content_projection_matches = (
        source_bag == candidate_bag
        if furniture_reorders_content
        else source_projection == candidate_projection
    )
    tagged_mastheads = sum(
        node.get(_WORD + "val") == "docreconstruct:split-masthead"
        for node in root.iter(_WORD + "tblCaption")
    )
    footer_text = _normalized(footer_projection)
    body_text = _docx_projection(root)
    footer_blocks_native = all(
        _normalized(text) in footer_text and _normalized(text) not in body_text
        for text in expected_footers
    )
    flow = _body_flow_metrics(root, scan)
    body_columns = _native_body_column_metrics(root, scan)
    from docreconstruct.evaluation.document_rendering import render_docx_pages

    render_result = render_docx_pages(
        candidate_path,
        backend=render_backend,
        executable=renderer_path,
        target_sizes=[(page.width, page.height) for page in scan.pages],
    )
    visual_metrics = None
    body_foreground = None
    render_artifacts: list[dict[str, str]] = []
    if render_result.rendered:
        from docreconstruct.evaluation.visual import evaluate_visual, visual_diff

        visual_metrics = evaluate_visual(
            [page.image for page in scan.pages],
            list(render_result.pages),
            normalize_illumination=True,
        )
        body_foreground = _body_foreground_metrics(scan, render_result.pages)
        if render_output_dir is not None:
            artifact_directory = Path(render_output_dir).expanduser().resolve()
            artifact_directory.mkdir(parents=True, exist_ok=True)
            page_count = max(len(scan.pages), len(render_result.pages))
            for index in range(page_count):
                artifact: dict[str, str] = {"page": str(index + 1)}
                source_page = scan.pages[index] if index < len(scan.pages) else None
                candidate_page = (
                    render_result.pages[index] if index < len(render_result.pages) else None
                )
                if source_page is not None:
                    source_path = artifact_directory / f"source-page-{index + 1}.png"
                    source_page.image.save(source_path, format="PNG")
                    artifact["source"] = str(source_path)
                if candidate_page is not None:
                    candidate_path_png = artifact_directory / f"candidate-page-{index + 1}.png"
                    candidate_path_png.write_bytes(candidate_page)
                    artifact["candidate"] = str(candidate_path_png)
                if source_page is not None and candidate_page is not None:
                    difference_path = artifact_directory / f"difference-page-{index + 1}.png"
                    visual_diff(source_page.image, candidate_page, difference_path)
                    artifact["difference"] = str(difference_path)
                render_artifacts.append(artifact)

    gates = [
        HybridValidationGate(name="valid_ooxml_package", passed=True, expected=True, actual=True),
        HybridValidationGate(
            name="native_content_projection",
            passed=content_projection_matches,
            expected=_text_evidence(source_projection),
            actual=_text_evidence(candidate_projection),
            detail=(
                "Exact stream order is required unless native masthead/footer furniture "
                "legitimately changes OOXML traversal order; those cases retain an exact "
                "visible-token multiset and are checked by dedicated layout gates."
            ),
        ),
        HybridValidationGate(
            name="native_office_math_count",
            passed=len(actual_math) == len(expressions),
            expected=len(expressions),
            actual=len(actual_math),
        ),
        HybridValidationGate(
            name="office_math_structure",
            passed=actual_signatures == expected_signatures,
            expected="source-derived OMML signatures",
            actual="exact" if actual_signatures == expected_signatures else "different",
        ),
        HybridValidationGate(
            name="display_math_paragraphs",
            passed=math_ooxml["display_oMathPara"] == len(display_expressions),
            expected=len(display_expressions),
            actual=math_ooxml["display_oMathPara"],
            detail="Native display equations must use m:oMathPara, not inline-only m:oMath.",
        ),
        HybridValidationGate(
            name="math_size_coverage",
            passed=(
                math_ooxml["sized_math_runs"] == math_ooxml["math_runs"]
                and (not expressions or math_ooxml["math_runs"] > 0)
            ),
            expected=math_ooxml["math_runs"],
            actual=math_ooxml["sized_math_runs"],
            detail="Every native math text run must carry an explicit positive Word size.",
        ),
        HybridValidationGate(
            name="math_typography_uniformity",
            passed=(
                math_ooxml["math_run_font_coverage"] == 1.0
                and math_ooxml["math_run_size_coverage"] == 1.0
                and math_ooxml["math_control_format_coverage"] == 1.0
                and math_ooxml["math_display_paragraph_mark_format_coverage"] == 1.0
                and math_ooxml["math_typography_uniform_equations"]
                == math_ooxml["math_typography_equations"]
                and (not expressions or math_ooxml["math_typography_equations"] > 0)
            ),
            expected={
                "base_font": _MATH_FONT,
                "run_font_coverage": 1.0,
                "run_size_coverage": 1.0,
                "control_format_coverage": 1.0,
                "display_paragraph_mark_format_coverage": 1.0,
                "uniform_equations": math_ooxml["math_typography_equations"],
            },
            actual={
                "base_fonts": math_ooxml["math_base_fonts"],
                "base_half_point_sizes": math_ooxml["math_base_half_point_sizes"],
                "run_font_coverage": math_ooxml["math_run_font_coverage"],
                "run_size_coverage": math_ooxml["math_run_size_coverage"],
                "control_format_coverage": math_ooxml["math_control_format_coverage"],
                "display_paragraph_mark_format_coverage": math_ooxml[
                    "math_display_paragraph_mark_format_coverage"
                ],
                "uniform_equations": math_ooxml["math_typography_uniform_equations"],
            },
            detail=(
                "Each equation must use one explicit Cambria Math base size across "
                "nonempty runs, every supported m:ctrlPr, and the display paragraph "
                "mark fallback. Word may then apply native script scaling without "
                "direct-size drift."
            ),
        ),
        HybridValidationGate(
            name="nary_operand_coverage",
            passed=(
                math_ooxml["nary_symbols"] == source_math_expectations["nary_symbols"]
                and math_ooxml["nary_with_operand"] == source_math_expectations["nary_count"]
            ),
            expected={
                "symbols": source_math_expectations["nary_symbols"],
                "with_operand": source_math_expectations["nary_count"],
            },
            actual={
                "symbols": math_ooxml["nary_symbols"],
                "with_operand": math_ooxml["nary_with_operand"],
            },
            detail="Integral, sum, and product operands are checked from OOXML directly.",
        ),
        HybridValidationGate(
            name="integral_limit_modes",
            passed=(
                math_ooxml["integral_limit_modes"]
                == source_math_expectations["integral_limit_modes"]
            ),
            expected=source_math_expectations["integral_limit_modes"],
            actual=math_ooxml["integral_limit_modes"],
            detail=(
                "Plain integrals use side sub/sup limits; only explicit LaTeX "
                "\\limits requests under/over placement."
            ),
        ),
        HybridValidationGate(
            name="native_delimiter_expectations",
            passed=(
                math_ooxml["native_delimiters"] >= source_math_expectations["native_delimiters"]
                and math_ooxml["complete_native_delimiters"] == math_ooxml["native_delimiters"]
            ),
            expected={
                "minimum": source_math_expectations["native_delimiters"],
                "complete": "all",
            },
            actual={
                "count": math_ooxml["native_delimiters"],
                "complete": math_ooxml["complete_native_delimiters"],
            },
            detail="Stretchy source delimiter pairs must remain native m:d structures.",
        ),
        HybridValidationGate(
            name="display_spacing_not_exact",
            passed=math_ooxml["display_exact_spacing"] == 0,
            expected=0,
            actual=math_ooxml["display_exact_spacing"],
            detail="Exact Word line spacing can clip fractions, integrals, and limits.",
        ),
        HybridValidationGate(
            name="display_equation_rows",
            passed=actual_rows == expected_rows,
            expected=expected_rows,
            actual=actual_rows,
        ),
        HybridValidationGate(
            name="supported_math_controls",
            passed=not unsupported and not leaked_controls,
            expected={"unsupported": [], "leaked": []},
            actual={"unsupported": unsupported, "leaked": leaked_controls},
        ),
        HybridValidationGate(
            name="native_tables",
            passed=len(list(root.iter(_WORD + "tbl"))) >= len(markdown.table_blocks),
            expected=f">={len(markdown.table_blocks)}",
            actual=len(list(root.iter(_WORD + "tbl"))),
        ),
        HybridValidationGate(
            name="native_split_mastheads",
            passed=tagged_mastheads == expected_mastheads,
            expected=expected_mastheads,
            actual=tagged_mastheads,
            detail="Every detected two-zone masthead must be a tagged native two-column table.",
        ),
        HybridValidationGate(
            name="native_body_columns",
            passed=body_columns["body_columns_match"],
            expected=[count for count in body_columns["source_body_column_counts"] if count > 1],
            actual=body_columns["rendered_body_column_counts"],
            detail=(
                "Every source multi-column page must emit one matching editable "
                "Word table tagged docreconstruct:body-columns-N."
            ),
        ),
        HybridValidationGate(
            name="native_body_column_payload",
            passed=body_columns["body_column_payload_matches"],
            expected={
                "content_cells": "all populated",
                "gutter_cells": "all empty",
                "shape": "2N-1 cells in one row",
            },
            actual={
                "payload_coverage": body_columns["body_column_payload_coverage"],
                "gutter_purity": body_columns["body_column_gutter_purity"],
                "tables": body_columns["body_column_table_reports"],
            },
            detail=(
                "A body-column caption is not sufficient: each editable source column "
                "must have payload and each explicit gutter cell must remain empty."
            ),
        ),
        HybridValidationGate(
            name="native_body_column_flow_safety",
            passed=body_columns["body_column_flow_safe"],
            expected={"unsplittable_rows": 0, "framed_paragraphs": 0},
            actual={
                "unsplittable_rows": body_columns["body_column_unsplittable_rows"],
                "framed_paragraphs": body_columns["body_column_framed_paragraphs"],
            },
            detail=(
                "Editable column flows must be allowed to paginate and must not use "
                "absolute paragraph frames that can move the body off its source page."
            ),
        ),
        HybridValidationGate(
            name="native_source_footers",
            passed=footer_blocks_native,
            expected=expected_footers,
            actual=footer_text,
            detail="Bottom page furniture must live in a full-width Word footer, not body flow.",
        ),
        HybridValidationGate(
            name="planned_page_sections",
            passed=len(sections) == len(scan.pages),
            expected=len(scan.pages),
            actual=len(sections),
        ),
        HybridValidationGate(
            name="source_visual_slot_coverage",
            passed=(
                plan_geometry["source_visual_slot_coverage"] >= _SOURCE_VISUAL_SLOT_MIN_COVERAGE
            ),
            expected=f">={_SOURCE_VISUAL_SLOT_MIN_COVERAGE:.2f}",
            actual=plan_geometry["source_visual_slot_coverage"],
            detail=(
                "The editable plan must cover most OCR-free source rows. Residual rows "
                "remain explicit because they can be decoration, handwriting, or source "
                "text omitted from the authoritative Markdown and must never be invented."
            ),
        ),
        HybridValidationGate(
            name="source_geometry_placements",
            passed=plan_geometry["placements_with_source_geometry"]
            == plan_geometry["planned_placements"],
            expected=plan_geometry["planned_placements"],
            actual=plan_geometry["placements_with_source_geometry"],
            detail="Every planned editable block must retain a source geometry anchor.",
        ),
        HybridValidationGate(
            name="mapped_vertical_span",
            passed=plan_geometry["mapped_vertical_span_ratio"] >= 0.95,
            expected=">=0.95",
            actual=plan_geometry["mapped_vertical_span_ratio"],
            detail="Mapped source rows must span the page's visual content vertically.",
        ),
        HybridValidationGate(
            name="source_anchor_order",
            passed=not plan_geometry["source_anchor_order_violations"],
            expected=[],
            actual=plan_geometry["source_anchor_order_violations"],
            detail="Editable text rows may not cross an intervening image or table anchor.",
        ),
        HybridValidationGate(
            name="source_row_gap_sanity",
            passed=plan_geometry["maximum_source_row_gap_ratio"] <= 1.80,
            expected="<=1.80 source pitches",
            actual=plan_geometry["maximum_source_row_gap_ratio"],
            detail="One editable paragraph may not bridge a large raster gap.",
        ),
        HybridValidationGate(
            name="native_body_flow_sanity",
            passed=(
                not flow["ordinary_line_spacing_outliers"] and not flow["narrow_body_paragraphs"]
            ),
            expected={"line_spacing_outliers": [], "narrow_paragraphs": []},
            actual={
                "line_spacing_outliers": flow["ordinary_line_spacing_outliers"],
                "narrow_paragraphs": flow["narrow_body_paragraphs"],
            },
            detail="Ordinary editable body text must retain sane leading and usable width.",
        ),
        HybridValidationGate(
            name="page_geometry",
            passed=geometry_matches,
            expected="source-derived size and margins",
            actual="exact within 2 twips" if geometry_matches else "different",
        ),
        HybridValidationGate(
            name="no_full_page_scan",
            passed=full_page_drawings == 0,
            expected=0,
            actual=full_page_drawings,
        ),
        HybridValidationGate(
            name="no_external_relationships",
            passed=external_relationships == 0,
            expected=0,
            actual=external_relationships,
        ),
        HybridValidationGate(
            name="cjk_font_mapping",
            passed=cjk_runs == cjk_mapped_runs,
            expected=cjk_runs,
            actual=cjk_mapped_runs,
        ),
    ]
    if evidence_paths:
        evidence_errors = evidence_metrics["evidence_errors"]
        ambiguous_detections = evidence_metrics["evidence_ambiguous_detections"]
        geometry_match_count = int(evidence_metrics["evidence_geometry_matches"])
        alignment_passed = (
            geometry_match_count > 0
            and not evidence_errors
            and (not strict_evidence or not ambiguous_detections)
        )
        gates.append(
            HybridValidationGate(
                name="evidence_alignment_used",
                passed=alignment_passed,
                expected={
                    "minimum_geometry_matches": 1,
                    "loader_errors": [],
                    "ambiguous_auto_detection": [] if strict_evidence else "warning_only",
                },
                actual={
                    "geometry_matches": geometry_match_count,
                    "loader_errors": evidence_errors,
                    "ambiguous_auto_detection": ambiguous_detections,
                },
                detail=(
                    "Saved JSON must normalize offline and align at least one Markdown "
                    "block to source geometry. Strict mode also rejects ambiguous "
                    "automatic provider detection."
                ),
            )
        )
    requested_backend = render_backend.strip().casefold()
    if requested_backend == "libreoffice" or minimum_visual_score is not None:
        gates.append(
            HybridValidationGate(
                name="render_backend_available",
                passed=render_result.rendered,
                expected="rendered",
                actual=render_result.status,
                detail=render_result.diagnostic,
            )
        )
    if render_result.rendered:
        gates.append(
            HybridValidationGate(
                name="rendered_page_count",
                passed=len(render_result.pages) == len(scan.pages),
                expected=len(scan.pages),
                actual=len(render_result.pages),
                detail="Rendered pagination must exactly match the source page count.",
            )
        )
        expected_page_sizes = [
            (float(page.pdf_width), float(page.pdf_height)) for page in scan.pages
        ]
        actual_page_sizes = list(render_result.page_sizes_points)
        physical_sizes_match = len(expected_page_sizes) == len(actual_page_sizes) and all(
            abs(expected_width - actual_width) <= 1.0
            and abs(expected_height - actual_height) <= 1.0
            for (expected_width, expected_height), (actual_width, actual_height) in zip(
                expected_page_sizes,
                actual_page_sizes,
                strict=True,
            )
        )
        gates.append(
            HybridValidationGate(
                name="rendered_physical_page_size",
                passed=physical_sizes_match,
                expected=[list(size) for size in expected_page_sizes],
                actual=[list(size) for size in actual_page_sizes],
                detail=(
                    "Physical PDF page boxes must match source-derived page geometry "
                    "within one point before pixel resampling."
                ),
            )
        )
        if body_foreground is not None and body_foreground["measured_pages"]:
            gates.append(
                HybridValidationGate(
                    name="rendered_body_foreground_coverage",
                    passed=body_foreground["passed"],
                    expected=f">={_BODY_FOREGROUND_MIN_RATIO:.2f} per active column",
                    actual=body_foreground["minimum_ratio"],
                    detail=(
                        "Foreground-normalized ink mass in each detected body column "
                        "must retain a conservative fraction of the source."
                    ),
                )
            )
    if minimum_visual_score is not None:
        gates.append(
            HybridValidationGate(
                name="rendered_visual_similarity",
                passed=(
                    visual_metrics is not None and visual_metrics.score >= minimum_visual_score
                ),
                expected=f">={minimum_visual_score:.6f}",
                actual=visual_metrics.score if visual_metrics is not None else None,
                detail="Foreground-normalized DOCX page render compared with source layout pixels.",
            )
        )
    passed_gates = sum(gate.passed for gate in gates)
    metrics: dict[str, Any] = {
        "source_blocks": len(markdown.blocks),
        "content_projection_difference": _projection_difference(
            source_projection,
            candidate_projection,
        ),
        "source_display_equations": len(display_expressions),
        "source_inline_equations": len(expressions) - len(display_expressions),
        "source_display_rows": expected_rows,
        "native_paragraphs": len(list(root.iter(_WORD + "p"))),
        "native_tables": len(list(root.iter(_WORD + "tbl"))),
        "expected_split_mastheads": expected_mastheads,
        "native_split_mastheads": tagged_mastheads,
        **body_columns,
        "expected_source_footers": expected_footers,
        "native_footer_text": footer_text,
        "native_office_math": len(actual_math),
        "native_equation_arrays": len(list(root.iter(_MATH + "eqArr"))),
        "native_display_rows": actual_rows,
        **math_ooxml,
        "drawings": drawings,
        "media_parts": len(media),
        "full_page_drawings": full_page_drawings,
        "cjk_runs": cjk_runs,
        "cjk_font_mapped_runs": cjk_mapped_runs,
        "source_pages": len(scan.pages),
        "docx_sections": len(sections),
        "source_columns": [page.metadata.get("column_count", 1) for page in scan.pages],
        "source_line_geometry": [
            {
                "page": page.number,
                "content_bbox": page.content_bbox.model_dump(mode="json"),
                "line_pitch": page.line_pitch,
                "line_bands": [list(band) for band in page.line_bands],
                "text_line_bboxes": [line.bbox.model_dump(mode="json") for line in page.text_lines],
            }
            for page in scan.pages
        ],
        "page_geometry": geometry,
        "layout_budget": _layout_utilization(markdown, scan, plan_geometry),
        "render_backend": render_result.provenance(),
        "rendered_page_count": len(render_result.pages) if render_result.rendered else None,
        "rendered_visual": visual_metrics.to_dict() if visual_metrics is not None else None,
        "rendered_body_foreground": body_foreground,
        "render_artifacts": render_artifacts,
        **evidence_metrics,
        **flow,
        **plan_geometry,
    }
    return HybridValidationReport(
        content=str(content_path),
        layout=str(layout_path),
        candidate=str(candidate_path),
        content_sha256=_sha256(content_path),
        layout_sha256=_sha256(layout_path),
        candidate_sha256=_sha256(candidate_path),
        passed=passed_gates == len(gates),
        score=passed_gates / len(gates),
        passed_gates=passed_gates,
        measured_gates=len(gates),
        gates=gates,
        metrics=metrics,
        unmeasured=(
            (
                ["office_font_substitution"]
                + (
                    ["rendered_body_foreground_coverage"]
                    if body_foreground is not None and not body_foreground["measured_pages"]
                    else []
                )
            )
            if visual_metrics is not None
            else [
                "rendered_pixel_similarity",
                "office_font_substitution",
                "office_line_wrapping",
                "renderer_confirmed_pagination",
            ]
        ),
    )


__all__ = ["HybridValidationGate", "HybridValidationReport", "validate_hybrid"]
