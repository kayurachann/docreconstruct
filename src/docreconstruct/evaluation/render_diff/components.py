"""Foreground component extraction and geometry scoring primitives."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from docreconstruct.evaluation.visual import _foreground_mask, _load_image, _opaque_rgb, _pillow

from .models import RenderNormalizedBox, RenderPixelBox


@dataclass(frozen=True, slots=True)
class ForegroundPage:
    original_width: int
    original_height: int
    width: int
    height: int
    mask: Any


@dataclass(frozen=True, slots=True)
class ForegroundComponent:
    bbox: RenderPixelBox
    foreground_pixels: int
    signature: int

    @property
    def center(self) -> tuple[float, float]:
        return ((self.bbox.x0 + self.bbox.x1) / 2, (self.bbox.y0 + self.bbox.y1) / 2)


def foreground_page(
    source: Any,
    *,
    target_size: tuple[int, int] | None = None,
    foreground_threshold: int = 16,
) -> ForegroundPage:
    """Load one raster and return the visual metric's canonical ink mask."""

    api = _pillow()
    image = _opaque_rgb(_load_image(source, api), api)
    original_width, original_height = image.size
    if target_size is not None and image.size != target_size:
        image = image.resize(target_size, Image.Resampling.LANCZOS)
    mask = _foreground_mask(
        image,
        api,
        threshold=max(1, min(254, int(foreground_threshold))),
        adaptive=True,
    )
    return ForegroundPage(
        original_width=original_width,
        original_height=original_height,
        width=mask.width,
        height=mask.height,
        mask=mask,
    )


def blank_page(size: tuple[int, int]) -> bytes:
    """Create a deterministic blank page accepted by the common image loader."""

    stream = io.BytesIO()
    Image.new("RGB", size, "white").save(stream, format="PNG", optimize=False)
    return stream.getvalue()


def foreground_count(mask: Any) -> int:
    return int(mask.histogram()[255])


def dilate(mask: Any, radius: int) -> Any:
    maximum = max(0, (min(mask.size) - 1) // 2)
    bounded = min(maximum, max(0, int(radius)))
    return mask.copy() if bounded == 0 else mask.filter(ImageFilter.MaxFilter(2 * bounded + 1))


def difference_masks(reference: Any, candidate: Any, *, tolerance: int) -> tuple[Any, Any]:
    """Return unmatched reference ink and unmatched candidate ink masks."""

    ref = np.asarray(reference, dtype=np.uint8) > 0
    cand = np.asarray(candidate, dtype=np.uint8) > 0
    candidate_near = np.asarray(dilate(candidate, tolerance), dtype=np.uint8) > 0
    reference_near = np.asarray(dilate(reference, tolerance), dtype=np.uint8) > 0
    missing = Image.fromarray(np.where(ref & ~candidate_near, 255, 0).astype(np.uint8), mode="L")
    extra = Image.fromarray(np.where(cand & ~reference_near, 255, 0).astype(np.uint8), mode="L")
    return missing, extra


def _find(parent: dict[int, int], label: int) -> int:
    root = label
    while parent[root] != root:
        root = parent[root]
    while parent[label] != label:
        following = parent[label]
        parent[label] = root
        label = following
    return root


def _union(parent: dict[int, int], left: int, right: int) -> int:
    left_root = _find(parent, left)
    right_root = _find(parent, right)
    root = min(left_root, right_root)
    parent[left_root] = root
    parent[right_root] = root
    return root


def _connected_boxes(binary: np.ndarray[Any, Any]) -> list[tuple[int, int, int, int]]:
    """Run-length 8-connected components without an optional SciPy dependency."""

    parent: dict[int, int] = {}
    records: list[tuple[int, int, int, int]] = []
    previous: list[tuple[int, int, int]] = []
    next_label = 0
    for y, row in enumerate(binary):
        padded = np.pad(row.astype(np.int8, copy=False), (1, 1))
        transitions = np.flatnonzero(np.diff(padded))
        current: list[tuple[int, int, int]] = []
        previous_index = 0
        for start, end_exclusive in zip(transitions[::2], transitions[1::2], strict=True):
            end = int(end_exclusive) - 1
            start = int(start)
            while previous_index < len(previous) and previous[previous_index][1] < start - 1:
                previous_index += 1
            overlapping: list[int] = []
            probe = previous_index
            while probe < len(previous) and previous[probe][0] <= end + 1:
                overlapping.append(previous[probe][2])
                probe += 1
            if overlapping:
                label = min(_find(parent, value) for value in overlapping)
                for value in overlapping:
                    label = _union(parent, label, value)
            else:
                label = next_label
                parent[label] = label
                next_label += 1
            current.append((start, end, label))
            records.append((start, end, y, label))
        previous = current

    boxes: dict[int, list[int]] = {}
    for start, end, y, label in records:
        root = _find(parent, label)
        if root not in boxes:
            boxes[root] = [start, y, end + 1, y + 1]
        else:
            box = boxes[root]
            box[0] = min(box[0], start)
            box[1] = min(box[1], y)
            box[2] = max(box[2], end + 1)
            box[3] = max(box[3], y + 1)
    return [(box[0], box[1], box[2], box[3]) for _root, box in sorted(boxes.items())]


def _signature(mask: Any, bbox: RenderPixelBox, *, size: int = 24) -> int:
    crop = mask.crop((bbox.x0, bbox.y0, bbox.x1, bbox.y1))
    resized = crop.resize((size, size), Image.Resampling.NEAREST)
    values = np.asarray(resized, dtype=np.uint8).reshape(-1) > 0
    packed = np.packbits(values, bitorder="big").tobytes()
    return int.from_bytes(packed, byteorder="big", signed=False)


def extract_components(mask: Any) -> tuple[ForegroundComponent, ...]:
    """Extract stable page regions from grouped foreground connectivity."""

    scale = min(1.0, 1024.0 / max(mask.size))
    working = mask
    if scale < 1.0:
        working = mask.resize(
            (max(1, round(mask.width * scale)), max(1, round(mask.height * scale))),
            Image.Resampling.NEAREST,
        )
    grouping_radius = max(1, min(8, round(min(working.size) * 0.003)))
    grouped = np.asarray(dilate(working, grouping_radius), dtype=np.uint8) > 0
    minimum_ink = max(4, mask.width * mask.height // 1_000_000)
    components: list[ForegroundComponent] = []
    for raw_box in _connected_boxes(grouped):
        x0, y0, x1, y1 = raw_box
        if scale < 1.0:
            x0 = max(0, math.floor(x0 / scale))
            y0 = max(0, math.floor(y0 / scale))
            x1 = min(mask.width, math.ceil(x1 / scale))
            y1 = min(mask.height, math.ceil(y1 / scale))
        bbox = RenderPixelBox(x0=x0, y0=y0, x1=x1, y1=y1)
        ink = foreground_count(mask.crop((x0, y0, x1, y1)))
        if ink < minimum_ink:
            continue
        components.append(
            ForegroundComponent(
                bbox=bbox,
                foreground_pixels=ink,
                signature=_signature(mask, bbox),
            )
        )
    return tuple(
        sorted(
            components,
            key=lambda component: (
                component.bbox.y0,
                component.bbox.x0,
                component.bbox.y1,
                component.bbox.x1,
                component.signature,
            ),
        )
    )


def box_union(left: RenderPixelBox, right: RenderPixelBox) -> RenderPixelBox:
    return RenderPixelBox(
        x0=min(left.x0, right.x0),
        y0=min(left.y0, right.y0),
        x1=max(left.x1, right.x1),
        y1=max(left.y1, right.y1),
    )


def clip_box(box: RenderPixelBox, size: tuple[int, int]) -> RenderPixelBox | None:
    width, height = size
    x0 = min(width, max(0, box.x0))
    y0 = min(height, max(0, box.y0))
    x1 = min(width, max(0, box.x1))
    y1 = min(height, max(0, box.y1))
    if x1 <= x0 or y1 <= y0:
        return None
    return RenderPixelBox(x0=x0, y0=y0, x1=x1, y1=y1)


def scale_box(
    box: RenderPixelBox,
    *,
    source_size: tuple[int, int],
    target_size: tuple[int, int],
) -> RenderPixelBox:
    source_width, source_height = source_size
    target_width, target_height = target_size
    return RenderPixelBox(
        x0=round(box.x0 * target_width / source_width),
        y0=round(box.y0 * target_height / source_height),
        x1=round(box.x1 * target_width / source_width),
        y1=round(box.y1 * target_height / source_height),
    )


def normalized_box(box: RenderPixelBox, size: tuple[int, int]) -> RenderNormalizedBox:
    width, height = size
    return RenderNormalizedBox(
        x0=round(max(0.0, min(1.0, box.x0 / width)), 8),
        y0=round(max(0.0, min(1.0, box.y0 / height)), 8),
        x1=round(max(0.0, min(1.0, box.x1 / width)), 8),
        y1=round(max(0.0, min(1.0, box.y1 / height)), 8),
    )


def box_iou(left: RenderPixelBox, right: RenderPixelBox) -> float:
    intersection_width = max(0, min(left.x1, right.x1) - max(left.x0, right.x0))
    intersection_height = max(0, min(left.y1, right.y1) - max(left.y0, right.y0))
    intersection = intersection_width * intersection_height
    union = left.area + right.area - intersection
    return 1.0 if union == 0 else intersection / union


def overlap_fraction(region: RenderPixelBox, target: RenderPixelBox) -> float:
    width = max(0, min(region.x1, target.x1) - max(region.x0, target.x0))
    height = max(0, min(region.y1, target.y1) - max(region.y0, target.y0))
    return width * height / region.area


def component_similarity(
    reference: ForegroundComponent,
    candidate: ForegroundComponent,
    *,
    page_size: tuple[int, int],
) -> tuple[float, float, float, float]:
    """Return shape, ink-area, position, and box-overlap similarities."""

    both = (reference.signature & candidate.signature).bit_count()
    either = (reference.signature | candidate.signature).bit_count()
    shape = 1.0 if either == 0 else both / either
    low, high = sorted((reference.foreground_pixels, candidate.foreground_pixels))
    area = 1.0 if high == 0 else low / high
    ref_x, ref_y = reference.center
    cand_x, cand_y = candidate.center
    width, height = page_size
    distance = math.hypot((ref_x - cand_x) / width, (ref_y - cand_y) / height)
    position = max(0.0, 1.0 - distance / math.sqrt(2.0))
    return shape, area, position, box_iou(reference.bbox, candidate.bbox)


def mask_fraction(mask: Any, box: RenderPixelBox, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return min(1.0, foreground_count(mask.crop((box.x0, box.y0, box.x1, box.y1))) / denominator)


def image_source_size(source: Any) -> tuple[int, int]:
    api = _pillow()
    image = _load_image(source, api)
    return image.size


ImageSource = str | Path | bytes | bytearray | memoryview | Any


__all__ = [
    "ForegroundComponent",
    "ForegroundPage",
    "blank_page",
    "box_iou",
    "box_union",
    "clip_box",
    "component_similarity",
    "difference_masks",
    "dilate",
    "extract_components",
    "foreground_count",
    "foreground_page",
    "image_source_size",
    "mask_fraction",
    "normalized_box",
    "overlap_fraction",
    "scale_box",
]
