"""Strict, reproducible OmniDocBench oracle-to-canonical conversion.

This module is intentionally separate from source-only benchmarking.  It
consumes ground truth and therefore belongs only to the oracle reconstruction
lane.  The source raster is decoded before canonical IR is created: upstream
dimension metadata is not trusted when it contradicts the actual pixel grid.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from docreconstruct.ir import (
    BBox,
    Document,
    Element,
    ElementType,
    Page,
    Point,
    Provenance,
    SourceType,
)


class OmniDocBenchProjectionReason(StrEnum):
    """Stable outcomes of validating annotation and raster coordinates."""

    DIMENSIONS_MATCH = "dimensions_match"
    REPORTED_DIMENSIONS_TRANSPOSED = "reported_dimensions_transposed"
    REPORTED_DIMENSIONS_INCOMPATIBLE = "reported_dimensions_incompatible"
    ANNOTATION_OUT_OF_BOUNDS = "annotation_out_of_bounds"


class OmniDocBenchProjectionDiagnostic(BaseModel):
    """Auditable projection decision made before canonical conversion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: str = "projection_validation"
    image_name: str = Field(min_length=1)
    reason_code: OmniDocBenchProjectionReason
    reported_width: float = Field(gt=0)
    reported_height: float = Field(gt=0)
    raster_width: int = Field(gt=0)
    raster_height: int = Field(gt=0)
    canonical_width: int = Field(gt=0)
    canonical_height: int = Field(gt=0)
    annotation_count: int = Field(ge=0)
    retained_annotation_count: int = Field(ge=0)
    ignored_annotation_count: int = Field(ge=0)
    max_annotation_x: float = Field(ge=0)
    max_annotation_y: float = Field(ge=0)
    corrected: bool = False


class OmniDocBenchOracleConversionError(ValueError):
    """Raised when the oracle coordinate transform cannot be proved safely."""

    def __init__(self, diagnostic: OmniDocBenchProjectionDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(
            f"{diagnostic.reason_code}: {diagnostic.image_name} has reported "
            f"{diagnostic.reported_width:g}x{diagnostic.reported_height:g}, raster "
            f"{diagnostic.raster_width}x{diagnostic.raster_height}, and annotation "
            f"extent {diagnostic.max_annotation_x:g}x{diagnostic.max_annotation_y:g}"
        )


class OmniDocBenchOracleConversion(BaseModel):
    """One converted document plus the projection proof used to create it."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    document: Document
    diagnostic: OmniDocBenchProjectionDiagnostic


_CATEGORY_TYPES: dict[str, ElementType] = {
    "text_block": ElementType.PARAGRAPH,
    "title": ElementType.TITLE,
    "table": ElementType.TABLE,
    "table_caption": ElementType.CAPTION,
    "table_footnote": ElementType.FOOTNOTE,
    "figure": ElementType.FIGURE,
    "figure_caption": ElementType.CAPTION,
    "figure_footnote": ElementType.FOOTNOTE,
    "equation_isolated": ElementType.FORMULA,
    "equation_caption": ElementType.CAPTION,
    "header": ElementType.HEADER,
    "footer": ElementType.FOOTER,
    "page_footnote": ElementType.FOOTNOTE,
    "page_number": ElementType.PAGE_NUMBER,
    "abandon": ElementType.UNKNOWN,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _positive_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return number


def _polygon(raw: Any, *, label: str) -> tuple[Point, ...]:
    if (
        not isinstance(raw, Sequence)
        or isinstance(raw, (str, bytes, bytearray))
        or len(raw) < 8
        or len(raw) % 2
    ):
        raise ValueError(f"{label}.poly must contain at least four coordinate pairs")
    points: list[Point] = []
    for index in range(0, len(raw), 2):
        x = raw[index]
        y = raw[index + 1]
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, (int, float))
            or not isinstance(y, (int, float))
            or not math.isfinite(float(x))
            or not math.isfinite(float(y))
        ):
            raise ValueError(f"{label}.poly contains a non-finite coordinate")
        points.append(Point(x=float(x), y=float(y)))
    return tuple(points)


def _annotation_geometry(
    record: Mapping[str, Any],
) -> tuple[list[tuple[Mapping[str, Any], tuple[Point, ...]]], int, float, float]:
    raw_detections = record.get("layout_dets")
    if not isinstance(raw_detections, Sequence) or isinstance(
        raw_detections, (str, bytes, bytearray)
    ):
        raise TypeError("layout_dets must be a sequence")
    retained: list[tuple[Mapping[str, Any], tuple[Point, ...]]] = []
    ignored = 0
    maximum_x = 0.0
    maximum_y = 0.0
    for index, raw_detection in enumerate(raw_detections):
        if not isinstance(raw_detection, Mapping):
            raise TypeError(f"layout_dets[{index}] must be an object")
        points = _polygon(raw_detection.get("poly"), label=f"layout_dets[{index}]")
        maximum_x = max(maximum_x, *(point.x for point in points))
        maximum_y = max(maximum_y, *(point.y for point in points))
        if raw_detection.get("ignore") is True:
            ignored += 1
            continue
        retained.append((raw_detection, points))
    return retained, ignored, maximum_x, maximum_y


def validate_omnidocbench_projection(
    record: Mapping[str, Any],
    image_path: str | Path,
) -> OmniDocBenchProjectionDiagnostic:
    """Choose canonical dimensions only when raster/annotation agreement is proven."""

    page_info = record.get("page_info")
    if not isinstance(page_info, Mapping):
        raise TypeError("page_info must be an object")
    reported_width = _positive_number(page_info.get("width"), label="page_info.width")
    reported_height = _positive_number(page_info.get("height"), label="page_info.height")
    path = Path(image_path).expanduser().resolve()
    with Image.open(path) as opened:
        raster_width, raster_height = opened.size
    retained, ignored, maximum_x, maximum_y = _annotation_geometry(record)
    total = len(retained) + ignored
    size_tolerance = 0.5
    matches = (
        abs(reported_width - raster_width) <= size_tolerance
        and abs(reported_height - raster_height) <= size_tolerance
    )
    transposed = (
        abs(reported_width - raster_height) <= size_tolerance
        and abs(reported_height - raster_width) <= size_tolerance
    )
    if matches:
        reason = OmniDocBenchProjectionReason.DIMENSIONS_MATCH
        corrected = False
    elif transposed:
        reason = OmniDocBenchProjectionReason.REPORTED_DIMENSIONS_TRANSPOSED
        corrected = True
    else:
        diagnostic = OmniDocBenchProjectionDiagnostic(
            image_name=path.name,
            reason_code=OmniDocBenchProjectionReason.REPORTED_DIMENSIONS_INCOMPATIBLE,
            reported_width=reported_width,
            reported_height=reported_height,
            raster_width=raster_width,
            raster_height=raster_height,
            canonical_width=raster_width,
            canonical_height=raster_height,
            annotation_count=total,
            retained_annotation_count=len(retained),
            ignored_annotation_count=ignored,
            max_annotation_x=maximum_x,
            max_annotation_y=maximum_y,
        )
        raise OmniDocBenchOracleConversionError(diagnostic)

    coordinate_tolerance = max(1.0, max(raster_width, raster_height) * 0.002)
    if (
        maximum_x > raster_width + coordinate_tolerance
        or maximum_y > raster_height + coordinate_tolerance
    ):
        diagnostic = OmniDocBenchProjectionDiagnostic(
            image_name=path.name,
            reason_code=OmniDocBenchProjectionReason.ANNOTATION_OUT_OF_BOUNDS,
            reported_width=reported_width,
            reported_height=reported_height,
            raster_width=raster_width,
            raster_height=raster_height,
            canonical_width=raster_width,
            canonical_height=raster_height,
            annotation_count=total,
            retained_annotation_count=len(retained),
            ignored_annotation_count=ignored,
            max_annotation_x=maximum_x,
            max_annotation_y=maximum_y,
            corrected=corrected,
        )
        raise OmniDocBenchOracleConversionError(diagnostic)

    return OmniDocBenchProjectionDiagnostic(
        image_name=path.name,
        reason_code=reason,
        reported_width=reported_width,
        reported_height=reported_height,
        raster_width=raster_width,
        raster_height=raster_height,
        canonical_width=raster_width,
        canonical_height=raster_height,
        annotation_count=total,
        retained_annotation_count=len(retained),
        ignored_annotation_count=ignored,
        max_annotation_x=maximum_x,
        max_annotation_y=maximum_y,
        corrected=corrected,
    )


def _element_type(category: str) -> ElementType:
    return _CATEGORY_TYPES.get(category, ElementType.UNKNOWN)


def _element_text(detection: Mapping[str, Any], element_type: ElementType) -> str | None:
    if element_type is ElementType.FORMULA:
        value = detection.get("latex")
    elif element_type in {ElementType.TABLE, ElementType.FIGURE, ElementType.IMAGE}:
        value = None
    else:
        value = detection.get("text")
    return value if isinstance(value, str) else None


def _element_metadata(detection: Mapping[str, Any], category: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "upstream_category": category,
        "oracle_ground_truth": True,
    }
    for name in ("latex", "html", "attribute", "table_edit_status"):
        if name in detection:
            metadata[name] = detection[name]
    return metadata


def convert_omnidocbench_oracle_page(
    record: Mapping[str, Any],
    *,
    record_index: int,
    image_path: str | Path,
    dataset_revision: str,
    markdown_path: str | Path | None = None,
) -> OmniDocBenchOracleConversion:
    """Convert one raw page without changing any GT text, order, or polygon."""

    if record_index < 0:
        raise ValueError("record_index must be non-negative")
    image = Path(image_path).expanduser().resolve()
    diagnostic = validate_omnidocbench_projection(record, image)
    page_info = record.get("page_info")
    assert isinstance(page_info, Mapping)
    image_name = page_info.get("image_path")
    if not isinstance(image_name, str) or Path(image_name).name != image.name:
        raise ValueError("page_info.image_path does not identify image_path")
    retained, _ignored, _maximum_x, _maximum_y = _annotation_geometry(record)
    indexed = list(enumerate(retained))
    indexed.sort(
        key=lambda item: (
            item[1][0].get("order") is None,
            item[1][0].get("order") if item[1][0].get("order") is not None else 0,
            item[0],
        )
    )
    elements: list[Element] = []
    for output_index, (_source_index, (detection, points)) in enumerate(indexed):
        category_value = detection.get("category_type")
        category = category_value if isinstance(category_value, str) else "unknown"
        element_type = _element_type(category)
        text = _element_text(detection, element_type)
        xs = [point.x for point in points]
        ys = [point.y for point in points]
        order_value = detection.get("order")
        reading_order = (
            order_value
            if isinstance(order_value, int) and not isinstance(order_value, bool)
            else None
        )
        annotation_id = detection.get("anno_id")
        annotation_label = str(annotation_id) if annotation_id is not None else str(output_index)
        elements.append(
            Element(
                id=f"odb-{record_index:02d}-{annotation_label}-{output_index}",
                type=element_type,
                bbox=BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys)),
                polygon=list(points),
                text=text,
                reading_order=reading_order,
                confidence=1.0,
                provenance=Provenance(
                    engine="omnidocbench-ground-truth",
                    source_id=f"{image.name}:{annotation_label}",
                    text_confidence=1.0 if text is not None else None,
                    layout_confidence=1.0,
                    metadata={"dataset_revision": dataset_revision},
                ),
                metadata=_element_metadata(detection, category),
            )
        )

    page_attribute = page_info.get("page_attribute")
    page_metadata: dict[str, Any] = {
        "coordinate_system": "pixels_top_left",
        "upstream_page_no": page_info.get("page_no"),
        "page_attribute": page_attribute if isinstance(page_attribute, Mapping) else {},
        "oracle_ground_truth": True,
        "projection_validation": diagnostic.model_dump(mode="json"),
    }
    document_metadata: dict[str, Any] = {
        "dataset": "OmniDocBench_demo",
        "dataset_revision": dataset_revision,
        "evaluation_lane": "oracle_reconstruction",
        "source_image_sha256": _sha256(image),
    }
    if markdown_path is not None:
        markdown = Path(markdown_path).expanduser().resolve()
        document_metadata["reviewed_markdown_sha256"] = _sha256(markdown)
    stem = Path(image.name).stem
    document = Document(
        id=f"omnidocbench-demo-{stem}",
        schema_version=Document.CURRENT_SCHEMA_VERSION,
        pages=[
            Page(
                id="page-1",
                number=1,
                width=float(diagnostic.canonical_width),
                height=float(diagnostic.canonical_height),
                source_type=SourceType.IMAGE,
                elements=elements,
                metadata=page_metadata,
            )
        ],
        metadata=document_metadata,
    )
    return OmniDocBenchOracleConversion(document=document, diagnostic=diagnostic)


def convert_omnidocbench_oracle_dataset(
    annotations_path: str | Path,
    *,
    images_directory: str | Path,
    markdown_directory: str | Path,
    output_directory: str | Path,
    dataset_revision: str,
) -> list[OmniDocBenchProjectionDiagnostic]:
    """Convert every annotation record and atomically write canonical sidecars."""

    annotations = Path(annotations_path).expanduser().resolve()
    payload = json.loads(annotations.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise TypeError("OmniDocBench annotations root must be a list")
    images = Path(images_directory).expanduser().resolve()
    markdown = Path(markdown_directory).expanduser().resolve()
    output = Path(output_directory).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    diagnostics: list[OmniDocBenchProjectionDiagnostic] = []
    for index, record in enumerate(payload):
        if not isinstance(record, Mapping):
            raise TypeError(f"annotation record {index} must be an object")
        page_info = record.get("page_info")
        if not isinstance(page_info, Mapping) or not isinstance(page_info.get("image_path"), str):
            raise ValueError(f"annotation record {index} has no page_info.image_path")
        image_name = Path(page_info["image_path"]).name
        image_path = images / image_name
        markdown_path = markdown / f"{Path(image_name).stem}.md"
        conversion = convert_omnidocbench_oracle_page(
            record,
            record_index=index,
            image_path=image_path,
            dataset_revision=dataset_revision,
            markdown_path=markdown_path,
        )
        destination = output / f"{Path(image_name).stem}.canonical.json"
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_text(
            conversion.document.model_dump_json(indent=2, exclude_unset=True),
            encoding="utf-8",
        )
        os.replace(temporary, destination)
        diagnostics.append(conversion.diagnostic)
    return diagnostics


__all__ = [
    "OmniDocBenchOracleConversion",
    "OmniDocBenchOracleConversionError",
    "OmniDocBenchProjectionDiagnostic",
    "OmniDocBenchProjectionReason",
    "convert_omnidocbench_oracle_dataset",
    "convert_omnidocbench_oracle_page",
    "validate_omnidocbench_projection",
]
