"""Reading-order recovery that never rewrites recognized text."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _coordinates(element: object) -> tuple[float, float, float, float]:
    bbox = getattr(element, "bbox", None)
    if bbox is None:
        return (0.0, 0.0, 0.0, 0.0)
    if isinstance(bbox, (list, tuple)):
        values = [float(value) for value in bbox[:4]]
        if len(values) != 4:
            return (0.0, 0.0, 0.0, 0.0)
        return (values[0], values[1], values[2], values[3])
    bbox_value: Any = bbox
    return (float(bbox_value.x0), float(bbox_value.y0), float(bbox_value.x1), float(bbox_value.y1))


def _column_groups(
    elements: list[object], page_width: float
) -> tuple[list[object], list[object], list[object]]:
    full: list[object] = []
    left: list[object] = []
    right: list[object] = []
    midpoint = page_width / 2.0
    for element in elements:
        x0, _, x1, _ = _coordinates(element)
        width = max(0.0, x1 - x0)
        if width >= page_width * 0.62 or (x0 < midpoint < x1):
            full.append(element)
        elif (x0 + x1) / 2.0 < midpoint:
            left.append(element)
        else:
            right.append(element)
    return full, left, right


def _ordered(elements: Iterable[object]) -> list[object]:
    return sorted(
        elements, key=lambda element: (_coordinates(element)[1], _coordinates(element)[0])
    )


def infer_reading_order(page: object, *, force: bool = False) -> object:
    """Assign stable reading order, using a conservative two-column heuristic.

    The function updates element ``reading_order`` values in place and returns the
    page for pipeline composition. Existing complete order is preserved unless
    ``force`` is true.
    """

    elements = list(getattr(page, "elements", []) or [])
    if not elements:
        return page
    existing = [getattr(element, "reading_order", None) for element in elements]
    if (
        not force
        and all(value is not None for value in existing)
        and len(set(existing)) == len(existing)
    ):
        return page

    page_width = float(getattr(page, "width", 1.0))
    full, left, right = _column_groups(elements, page_width)
    two_column = len(left) >= 2 and len(right) >= 2
    if not two_column:
        ordered = _ordered(elements)
    else:
        top_boundary = min(
            (
                min(_coordinates(item)[1] for item in left),
                min(_coordinates(item)[1] for item in right),
            )
        )
        top_full = [item for item in full if _coordinates(item)[1] <= top_boundary]
        remaining_full = [item for item in full if item not in top_full]
        ordered = _ordered(top_full) + _ordered(left) + _ordered(right) + _ordered(remaining_full)

    for index, element in enumerate(ordered, start=1):
        mutable_element: Any = element
        try:
            mutable_element.reading_order = index
        except (AttributeError, TypeError):
            object.__setattr__(element, "reading_order", index)
    return page
