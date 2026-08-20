"""Bounded spatial candidate index for page evidence fusion."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence

from docreconstruct.ir import BBox


class SpatialIndexBudgetExceeded(RuntimeError):
    """Raised before a grid index would exceed its explicit entry budget."""

    def __init__(self, consumed_entries: int) -> None:
        super().__init__("spatial index entry budget exceeded")
        self.consumed_entries = consumed_entries


class SpatialQueryBudgetExceeded(RuntimeError):
    """Raised before a spatial query would retain too many candidates."""


class SpatialClusterIndex:
    """Uniform-grid index over cluster envelopes with no overlap false negatives."""

    def __init__(
        self,
        envelopes: Sequence[BBox],
        *,
        page_width: float,
        page_height: float,
        resolution: int = 32,
        x_resolution: int | None = None,
        y_resolution: int | None = None,
        entry_budget: int | None = None,
    ) -> None:
        resolved_x = x_resolution if x_resolution is not None else resolution
        resolved_y = y_resolution if y_resolution is not None else resolution
        if resolved_x <= 0 or resolved_y <= 0:
            raise ValueError("spatial index resolution must be positive")
        self._page_width = page_width
        self._page_height = page_height
        self._x_resolution = resolved_x
        self._y_resolution = resolved_y
        self.entry_count = 0
        self._cells: dict[tuple[int, int], list[int]] = defaultdict(list)
        for index, envelope in enumerate(envelopes):
            x_cells, y_cells = self._box_cell_ranges(envelope)
            cell_count = len(x_cells) * len(y_cells)
            if entry_budget is not None and self.entry_count + cell_count > entry_budget:
                raise SpatialIndexBudgetExceeded(entry_budget)
            self.entry_count += cell_count
            for x_cell in x_cells:
                for y_cell in y_cells:
                    self._cells[(x_cell, y_cell)].append(index)

    def query(self, box: BBox, *, result_budget: int | None = None) -> list[int]:
        """Return canonical indices whose envelopes may overlap ``box``."""

        candidates: set[int] = set()
        x_cells, y_cells = self._box_cell_ranges(box)
        for x_cell in x_cells:
            for y_cell in y_cells:
                for candidate in self._cells.get((x_cell, y_cell), ()):
                    candidates.add(candidate)
                    if result_budget is not None and len(candidates) > result_budget:
                        raise SpatialQueryBudgetExceeded
        return sorted(candidates)

    def _box_cell_ranges(self, box: BBox) -> tuple[range, range]:
        return (
            self._axis_cells(box.x0, box.x1, self._page_width, self._x_resolution),
            self._axis_cells(box.y0, box.y1, self._page_height, self._y_resolution),
        )

    @staticmethod
    def _axis_cells(low: float, high: float, extent: float, resolution: int) -> range:
        start = SpatialClusterIndex._axis_cell(low, extent, resolution)
        end = SpatialClusterIndex._axis_cell(high, extent, resolution)
        return range(min(start, end), max(start, end) + 1)

    @staticmethod
    def _axis_cell(value: float, extent: float, resolution: int) -> int:
        scaled = math.floor(value / extent * resolution)
        return max(0, min(resolution - 1, scaled))


def cluster_envelope(boxes: Sequence[BBox]) -> BBox:
    """Return the union envelope for a non-empty sequence of boxes."""

    if not boxes:
        raise ValueError("at least one box is required for a cluster envelope")
    return BBox(
        x0=min(box.x0 for box in boxes),
        y0=min(box.y0 for box in boxes),
        x1=max(box.x1 for box in boxes),
        y1=max(box.y1 for box in boxes),
    )


__all__ = [
    "SpatialClusterIndex",
    "SpatialIndexBudgetExceeded",
    "SpatialQueryBudgetExceeded",
    "cluster_envelope",
]
