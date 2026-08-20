"""Strict geometry and identity validation for OmniDocBench pages."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from docreconstruct.ir import BBox, Point

from .models import (
    OmniDocBenchConversionReason,
    OmniDocBenchOracleContractError,
    OmniDocBenchOracleConversionError,
    OmniDocBenchProjectionDiagnostic,
    OmniDocBenchProjectionReason,
)


@dataclass(frozen=True)
class _ValidatedAnnotation:
    source_index: int
    raw: dict[str, Any]
    points: tuple[Point, ...]
    bbox: BBox
    annotation_id: str | int
    order: int | None
    category: str
    ignored: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


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


def _annotation_identity(value: str | int) -> tuple[str, str]:
    return (type(value).__name__, str(value))


def _validated_annotations(record: Mapping[str, Any]) -> list[_ValidatedAnnotation]:
    raw_detections = record.get("layout_dets")
    if not isinstance(raw_detections, Sequence) or isinstance(
        raw_detections, (str, bytes, bytearray)
    ):
        raise TypeError("layout_dets must be a sequence")
    validated: list[_ValidatedAnnotation] = []
    annotation_ids: dict[tuple[str, str], int] = {}
    reading_orders: dict[int, int] = {}
    for index, raw_detection in enumerate(raw_detections):
        if not isinstance(raw_detection, Mapping):
            raise TypeError(f"layout_dets[{index}] must be an object")
        annotation_id = raw_detection.get("anno_id")
        if (
            isinstance(annotation_id, bool)
            or not isinstance(annotation_id, (str, int))
            or (isinstance(annotation_id, str) and not annotation_id.strip())
        ):
            raise OmniDocBenchOracleContractError(
                OmniDocBenchConversionReason.INVALID_ANNOTATION_ID,
                "anno_id must be a non-empty string or integer",
                annotation_index=index,
            )
        identity = _annotation_identity(annotation_id)
        if identity in annotation_ids:
            raise OmniDocBenchOracleContractError(
                OmniDocBenchConversionReason.DUPLICATE_ANNOTATION_ID,
                f"anno_id duplicates layout_dets[{annotation_ids[identity]}]",
                annotation_index=index,
            )
        annotation_ids[identity] = index

        ignore_value = raw_detection.get("ignore")
        if not isinstance(ignore_value, bool):
            raise OmniDocBenchOracleContractError(
                OmniDocBenchConversionReason.INVALID_IGNORE_FLAG,
                "ignore must be explicitly true or false",
                annotation_index=index,
            )
        order_value = raw_detection.get("order")
        if order_value is not None and (
            isinstance(order_value, bool) or not isinstance(order_value, int) or order_value < 0
        ):
            raise OmniDocBenchOracleContractError(
                OmniDocBenchConversionReason.INVALID_READING_ORDER,
                "order must be null or a non-negative integer",
                annotation_index=index,
            )
        if order_value is not None:
            if order_value in reading_orders:
                raise OmniDocBenchOracleContractError(
                    OmniDocBenchConversionReason.DUPLICATE_READING_ORDER,
                    f"order duplicates layout_dets[{reading_orders[order_value]}]",
                    annotation_index=index,
                )
            reading_orders[order_value] = index

        points = _polygon(raw_detection.get("poly"), label=f"layout_dets[{index}]")
        xs = [point.x for point in points]
        ys = [point.y for point in points]
        category_value = raw_detection.get("category_type")
        category = (
            category_value
            if isinstance(category_value, str) and category_value.strip()
            else "unknown"
        )
        validated.append(
            _ValidatedAnnotation(
                source_index=index,
                raw=deepcopy(dict(raw_detection)),
                points=points,
                bbox=BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys)),
                annotation_id=annotation_id,
                order=order_value,
                category=category,
                ignored=ignore_value,
            )
        )
    return validated


def _annotation_geometry(
    record: Mapping[str, Any],
) -> tuple[list[_ValidatedAnnotation], int, float, float, float, float]:
    annotations = _validated_annotations(record)
    retained = [annotation for annotation in annotations if not annotation.ignored]
    ignored = len(annotations) - len(retained)
    if not annotations:
        return retained, ignored, 0.0, 0.0, 0.0, 0.0
    minimum_x = min(point.x for annotation in annotations for point in annotation.points)
    minimum_y = min(point.y for annotation in annotations for point in annotation.points)
    maximum_x = max(point.x for annotation in annotations for point in annotation.points)
    maximum_y = max(point.y for annotation in annotations for point in annotation.points)
    return retained, ignored, minimum_x, minimum_y, maximum_x, maximum_y


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
    retained, ignored, minimum_x, minimum_y, maximum_x, maximum_y = _annotation_geometry(record)
    matches = (
        abs(reported_width - raster_width) <= 0.5 and abs(reported_height - raster_height) <= 0.5
    )
    transposed = (
        abs(reported_width - raster_height) <= 0.5 and abs(reported_height - raster_width) <= 0.5
    )
    if matches:
        reason = OmniDocBenchProjectionReason.DIMENSIONS_MATCH
        corrected = False
    elif transposed:
        reason = OmniDocBenchProjectionReason.REPORTED_DIMENSIONS_TRANSPOSED
        corrected = True
    else:
        diagnostic = _diagnostic(
            path,
            OmniDocBenchProjectionReason.REPORTED_DIMENSIONS_INCOMPATIBLE,
            reported_width,
            reported_height,
            raster_width,
            raster_height,
            retained,
            ignored,
            minimum_x,
            minimum_y,
            maximum_x,
            maximum_y,
        )
        raise OmniDocBenchOracleConversionError(diagnostic)

    tolerance = max(1.0, max(raster_width, raster_height) * 0.002)
    if (
        minimum_x < -tolerance
        or minimum_y < -tolerance
        or maximum_x > raster_width + tolerance
        or maximum_y > raster_height + tolerance
    ):
        diagnostic = _diagnostic(
            path,
            OmniDocBenchProjectionReason.ANNOTATION_OUT_OF_BOUNDS,
            reported_width,
            reported_height,
            raster_width,
            raster_height,
            retained,
            ignored,
            minimum_x,
            minimum_y,
            maximum_x,
            maximum_y,
            corrected=corrected,
        )
        raise OmniDocBenchOracleConversionError(diagnostic)
    return _diagnostic(
        path,
        reason,
        reported_width,
        reported_height,
        raster_width,
        raster_height,
        retained,
        ignored,
        minimum_x,
        minimum_y,
        maximum_x,
        maximum_y,
        corrected=corrected,
    )


def _diagnostic(
    path: Path,
    reason: OmniDocBenchProjectionReason,
    reported_width: float,
    reported_height: float,
    raster_width: int,
    raster_height: int,
    retained: Sequence[_ValidatedAnnotation],
    ignored: int,
    minimum_x: float,
    minimum_y: float,
    maximum_x: float,
    maximum_y: float,
    *,
    corrected: bool = False,
) -> OmniDocBenchProjectionDiagnostic:
    return OmniDocBenchProjectionDiagnostic(
        image_name=path.name,
        reason_code=reason,
        reported_width=reported_width,
        reported_height=reported_height,
        raster_width=raster_width,
        raster_height=raster_height,
        canonical_width=raster_width,
        canonical_height=raster_height,
        annotation_count=len(retained) + ignored,
        retained_annotation_count=len(retained),
        ignored_annotation_count=ignored,
        min_annotation_x=minimum_x,
        min_annotation_y=minimum_y,
        max_annotation_x=maximum_x,
        max_annotation_y=maximum_y,
        corrected=corrected,
    )


__all__ = ["validate_omnidocbench_projection"]
