"""Small, dependency-free helpers shared by the renderers.

The renderer layer deliberately accepts the public IR by protocol instead of
depending on a concrete validation library.  This keeps it usable with the
Pydantic IR models as well as provider-owned model subclasses.
"""

from __future__ import annotations

import dataclasses
import enum
import html.parser
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast


class _TableHTMLParser(html.parser.HTMLParser):
    """Extract the first table's text cells without fetching any resources."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.table_depth = 0
        self.in_row = False
        self.in_cell = False
        self.suppressed = 0
        self.cell_parts: list[str] = []
        self.cell_span = (1, 1)
        self.current_row: list[tuple[str, int, int]] = []
        self.rows: list[list[tuple[str, int, int]]] = []

    @staticmethod
    def _span(attrs: list[tuple[str, str | None]], name: str) -> int:
        for key, raw in attrs:
            if key.lower() == name and raw:
                try:
                    return max(1, min(100, int(raw)))
                except ValueError:
                    return 1
        return 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self.table_depth += 1
        elif tag in {"script", "style", "template"}:
            self.suppressed += 1
        elif self.table_depth == 1 and tag == "tr":
            self.in_row = True
            self.current_row = []
        elif self.table_depth == 1 and self.in_row and tag in {"td", "th"}:
            self.in_cell = True
            self.cell_parts = []
            self.cell_span = (self._span(attrs, "colspan"), self._span(attrs, "rowspan"))
        elif self.in_cell and tag == "br":
            self.cell_parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.table_depth == 1 and tag in {"td", "th"} and self.in_cell:
            colspan, rowspan = self.cell_span
            self.current_row.append(("".join(self.cell_parts).strip(), colspan, rowspan))
            self.in_cell = False
            self.cell_parts = []
        elif self.table_depth == 1 and tag == "tr" and self.in_row:
            if self.current_row:
                self.rows.append(self.current_row)
            self.in_row = False
            self.current_row = []
        elif tag == "table" and self.table_depth:
            self.table_depth -= 1
        elif tag in {"script", "style", "template"} and self.suppressed:
            self.suppressed -= 1

    def handle_data(self, data: str) -> None:
        if self.in_cell and not self.suppressed:
            self.cell_parts.append(data)

    def _layout(self) -> tuple[list[list[str]], list[list[tuple[int, int]]]]:
        """Resolve the rows into a rectangular grid plus each slot's span.

        A slot is either an anchor carrying its own ``(colspan, rowspan)`` — an
        ordinary cell is ``(1, 1)`` — or a slot covered by an anchor above or to
        its left, marked ``(0, 0)``. The text grid alone cannot express that:
        a covered slot and a genuinely empty cell are both ``""``.
        """

        grid: list[list[str]] = []
        span_grid: list[list[tuple[int, int]]] = []
        active_rowspans: dict[int, int] = {}
        for source_row in self.rows:
            occupied = set(active_rowspans)
            covered = set(occupied)
            anchors: dict[int, tuple[int, int]] = {}
            row: list[str] = []
            if occupied:
                row.extend("" for _ in range(max(occupied) + 1))
            column = 0
            new_spans: dict[int, int] = {}
            for text, colspan, rowspan in source_row:
                while column in occupied:
                    column += 1
                required = column + colspan
                if len(row) < required:
                    row.extend("" for _ in range(required - len(row)))
                row[column] = text
                anchors[column] = (colspan, rowspan)
                for offset in range(column, column + colspan):
                    occupied.add(offset)
                    if offset != column:
                        covered.add(offset)
                    if rowspan > 1:
                        new_spans[offset] = max(new_spans.get(offset, 0), rowspan - 1)
                column += colspan
            grid.append(row)
            span_grid.append(
                [
                    (0, 0) if index in covered else anchors.get(index, (1, 1))
                    for index in range(len(row))
                ]
            )
            active_rowspans = {
                column: remaining - 1
                for column, remaining in active_rowspans.items()
                if remaining > 1
            }
            for column, remaining in new_spans.items():
                active_rowspans[column] = max(active_rowspans.get(column, 0), remaining)
        width = max((len(row) for row in grid), default=0)
        return (
            [row + [""] * (width - len(row)) for row in grid],
            [spans + [(1, 1)] * (width - len(spans)) for spans in span_grid],
        )

    def grid(self) -> list[list[str]]:
        return self._layout()[0]

    def span_grid(self) -> list[list[tuple[int, int]]]:
        return self._layout()[1]


def _rows_from_html(source: str) -> list[list[str]]:
    parser = _TableHTMLParser()
    parser.feed(source)
    parser.close()
    return parser.grid()


def rows_and_spans_from_html(source: str) -> tuple[list[list[str]], list[list[tuple[int, int]]]]:
    """Return the flattened cell text and, alongside it, each slot's span."""

    parser = _TableHTMLParser()
    parser.feed(source)
    parser.close()
    return parser._layout()


def table_spans(element: Any) -> list[list[tuple[int, int]]]:
    """Return each table slot's ``(colspan, rowspan)``, or ``[]`` when unknown.

    Only the HTML source carries spans; a row/cell list has already been
    flattened by whoever produced it. An empty result means "render every slot
    as an ordinary cell", which is what the flattened grid already implies.
    """

    metadata = element_metadata(element)
    html_source = metadata.get("table_html", metadata.get("html"))
    if html_source is None and isinstance(metadata.get("table"), Mapping):
        html_source = metadata["table"].get("html", metadata["table"].get("table_html"))
    if isinstance(html_source, str) and html_source.strip():
        rows, spans = rows_and_spans_from_html(html_source)
        if rows and any(span != (1, 1) for row in spans for span in row):
            return spans
    return []


def value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def mapping(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, Mapping):
        return dict(obj)
    if hasattr(obj, "model_dump"):
        dumped = obj.model_dump(exclude_none=True)
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    if dataclasses.is_dataclass(obj):
        dumped = dataclasses.asdict(cast(Any, obj))
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    try:
        return {key: item for key, item in vars(obj).items() if not key.startswith("_")}
    except TypeError:
        return {}


def sequence(obj: Any, name: str) -> list[Any]:
    found = value(obj, name, ())
    if found is None or isinstance(found, (str, bytes, bytearray, Mapping)):
        return []
    try:
        return list(found)
    except TypeError:
        return []


def pages(document: Any) -> list[Any]:
    return sequence(document, "pages")


def elements(page: Any) -> list[Any]:
    return sequence(page, "elements")


def enum_text(obj: Any, default: str = "") -> str:
    if obj is None:
        return default
    if isinstance(obj, enum.Enum):
        return str(obj.value)
    return str(obj)


def element_type(element: Any) -> str:
    return enum_text(value(element, "type", "text"), "text").strip().lower()


def element_text(element: Any) -> str:
    found = value(element, "text", "")
    return "" if found is None else str(found)


def element_style(element: Any) -> dict[str, Any]:
    return mapping(value(element, "style", None))


def element_metadata(element: Any) -> dict[str, Any]:
    data = mapping(value(element, "metadata", None))
    # Some provider adapters call this field ``data`` or ``attributes``.
    if not data:
        data = mapping(value(element, "data", None))
    if not data:
        data = mapping(value(element, "attributes", None))
    return data


def finite_number(number: Any, default: float = 0.0) -> float:
    try:
        result = float(number)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def bbox_tuple(bbox_or_element: Any) -> tuple[float, float, float, float]:
    """Return ``(left, top, right, bottom)`` for common bbox shapes."""

    raw = value(bbox_or_element, "bbox", bbox_or_element)
    if raw is None:
        return (0.0, 0.0, 0.0, 0.0)
    if isinstance(raw, Mapping) or hasattr(raw, "__dict__"):
        left = value(raw, "x0", value(raw, "left", value(raw, "x", 0.0)))
        top = value(raw, "y0", value(raw, "top", value(raw, "y", 0.0)))
        right = value(raw, "x1", value(raw, "right", None))
        bottom = value(raw, "y1", value(raw, "bottom", None))
        if right is None:
            right = finite_number(left) + finite_number(value(raw, "width", 0.0))
        if bottom is None:
            bottom = finite_number(top) + finite_number(value(raw, "height", 0.0))
        result = (
            finite_number(left),
            finite_number(top),
            finite_number(right),
            finite_number(bottom),
        )
    else:
        try:
            coords = list(raw)
        except TypeError:
            coords = []
        result = tuple(finite_number(item) for item in coords[:4])  # type: ignore[assignment]
        if len(result) != 4:
            return (0.0, 0.0, 0.0, 0.0)
    left, top, right, bottom = result
    return (min(left, right), min(top, bottom), max(left, right), max(top, bottom))


def ordered_elements(page: Any) -> list[Any]:
    """Use reading order when present, retaining provider order as a tie break."""

    indexed = list(enumerate(elements(page)))

    def key(pair: tuple[int, Any]) -> tuple[int, float, int]:
        index, element = pair
        order = value(element, "reading_order", None)
        if order is None:
            return (1, float(index), index)
        return (0, finite_number(order, float(index)), index)

    return [element for _, element in sorted(indexed, key=key)]


def table_rows(element: Any) -> list[list[str]]:
    """Normalize common table metadata encodings into a rectangular grid."""

    metadata = element_metadata(element)
    raw_rows = metadata.get("rows")
    if raw_rows is None and isinstance(metadata.get("table"), Mapping):
        raw_rows = metadata["table"].get("rows")
    if raw_rows is None:
        raw_rows = value(element, "rows", None)
    if isinstance(raw_rows, Iterable) and not isinstance(
        raw_rows, (str, bytes, bytearray, Mapping)
    ):
        rows: list[list[str]] = []
        for raw_row in raw_rows:
            if isinstance(raw_row, Mapping):
                raw_row = raw_row.get("cells", raw_row.get("values", ()))
            if isinstance(raw_row, Iterable) and not isinstance(
                raw_row, (str, bytes, bytearray, Mapping)
            ):
                row: list[str] = []
                for cell in raw_row:
                    if isinstance(cell, Mapping) or hasattr(cell, "text"):
                        cell = value(cell, "text", value(cell, "value", ""))
                    row.append("" if cell is None else str(cell))
                rows.append(row)
        if rows:
            return rows

    html_source = metadata.get("table_html", metadata.get("html"))
    if html_source is None and isinstance(metadata.get("table"), Mapping):
        html_source = metadata["table"].get("html", metadata["table"].get("table_html"))
    if isinstance(html_source, str) and html_source.strip():
        parsed_rows = _rows_from_html(html_source)
        if parsed_rows:
            return parsed_rows

    cells = metadata.get("cells", value(element, "cells", None))
    if not isinstance(cells, Iterable) or isinstance(cells, (str, bytes, bytearray, Mapping)):
        return []
    positioned: list[tuple[int, int, str]] = []
    for cell in cells:
        row_index = int(finite_number(value(cell, "row", value(cell, "row_index", 0))))
        column_index = int(
            finite_number(value(cell, "column", value(cell, "col", value(cell, "column_index", 0))))
        )
        text = value(cell, "text", value(cell, "value", ""))
        positioned.append(
            (max(0, row_index), max(0, column_index), "" if text is None else str(text))
        )
    if not positioned:
        return []
    row_count = max(row for row, _, _ in positioned) + 1
    column_count = max(column for _, column, _ in positioned) + 1
    result = [["" for _ in range(column_count)] for _ in range(row_count)]
    for row_index, column_index, text in positioned:
        result[row_index][column_index] = text
    return result


def path_or_none(candidate: Any) -> Path | None:
    if isinstance(candidate, Path):
        return candidate
    if (
        isinstance(candidate, str)
        and candidate
        and not candidate.startswith(("data:", "http:", "https:"))
    ):
        return Path(candidate)
    return None


def allowed_local_path(
    candidate: Any,
    *,
    allow_local_files: bool = False,
    local_file_root: str | Path | None = None,
) -> Path | None:
    """Resolve a local asset only after an explicit, optionally rooted opt-in."""

    if not allow_local_files:
        return None
    path = path_or_none(candidate)
    if path is None:
        return None
    root = Path(local_file_root).expanduser().resolve() if local_file_root is not None else None
    if root is not None and not path.is_absolute():
        path = root / path
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError:
        return None
    if root is not None and not resolved.is_relative_to(root):
        return None
    return resolved if resolved.is_file() else None
