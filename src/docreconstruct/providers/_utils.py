"""Internal helpers for tolerant saved-result normalization."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from docreconstruct.ir import BBox, Element, ElementType, Point

from .base import ProviderInput, ProviderInputError

_JSON_EXTENSIONS = {".json", ".jsonl", ".ndjson"}


def looks_like_inline_json(value: str) -> bool:
    stripped = value.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def looks_like_non_json_file(value: str | Path) -> bool:
    if isinstance(value, str) and looks_like_inline_json(value):
        return False
    try:
        suffix = Path(value).suffix.lower()
    except (OSError, ValueError):
        return False
    return bool(suffix and suffix not in _JSON_EXTENSIONS)


def _decode_json_or_jsonl(text: str, *, label: str) -> Any:
    text = text.lstrip("\ufeff").strip()
    if not text:
        raise ProviderInputError(f"{label} is empty")
    try:
        return json.loads(text)
    except json.JSONDecodeError as json_error:
        records: list[Any] = []
        try:
            for _line_number, line in enumerate(text.splitlines(), start=1):
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        except json.JSONDecodeError as line_error:
            raise ProviderInputError(
                f"{label} is neither valid JSON nor JSONL (line {_line_number}: {line_error.msg})"
            ) from json_error
        if not records:
            raise ProviderInputError(f"{label} contains no JSON records") from json_error
        return records


def load_json_source(source: ProviderInput) -> tuple[Any, str | None]:
    """Load a mapping/sequence, JSON string, JSONL, or JSON file."""

    if isinstance(source, Mapping):
        return source, None
    if isinstance(source, Sequence) and not isinstance(source, (str, bytes, bytearray, Path)):
        return source, None
    if isinstance(source, (bytes, bytearray)):
        try:
            text = bytes(source).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProviderInputError("saved provider output must be UTF-8 JSON") from exc
        return _decode_json_or_jsonl(text, label="input bytes"), None

    if isinstance(source, Path):
        path = source
    elif isinstance(source, str) and not looks_like_inline_json(source):
        try:
            path = Path(source)
        except (OSError, ValueError):
            path = None
    else:
        path = None

    if path is not None:
        try:
            exists = path.is_file()
        except OSError:
            exists = False
        if exists:
            try:
                text = path.read_text(encoding="utf-8-sig")
            except OSError as exc:
                raise ProviderInputError(f"could not read {path}: {exc}") from exc
            return _decode_json_or_jsonl(text, label=str(path)), str(path)
        if isinstance(source, Path) or path.suffix.lower() in _JSON_EXTENSIONS:
            raise ProviderInputError(f"saved provider result does not exist: {path}")

    if isinstance(source, str):
        return _decode_json_or_jsonl(source, label="input string"), None
    raise ProviderInputError(f"unsupported provider input type: {type(source).__name__}")


def document_id(provider: str, context: Any = None) -> str:
    if context is not None and context.document_id:
        return context.document_id
    if context is not None and context.source:
        try:
            stem = Path(context.source).stem
        except (OSError, ValueError):
            stem = ""
        if stem:
            return slug(stem)
    return f"{slug(provider)}-document"


def slug(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-.")
    return result or "item"


def as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def confidence(value: Any) -> float | None:
    result = as_float(value)
    if result is None:
        return None
    if 1 < result <= 100:
        result /= 100.0
    return max(0.0, min(1.0, result))


def coerce_bbox(value: Any) -> BBox | None:
    """Accept four-coordinate boxes, polygons, and common bbox mappings."""

    if value is None:
        return None
    if isinstance(value, BBox):
        return value
    if isinstance(value, Mapping):
        direct_sets = (
            ("x0", "y0", "x1", "y1"),
            ("left", "top", "right", "bottom"),
            ("xmin", "ymin", "xmax", "ymax"),
        )
        for keys in direct_sets:
            if all(key in value for key in keys):
                maybe_coords = [as_float(value[key]) for key in keys]
                direct_coords = [coord for coord in maybe_coords if coord is not None]
                if len(direct_coords) == 4:
                    return _ordered_bbox(*direct_coords)
        if all(key in value for key in ("x", "y", "width", "height")):
            x = as_float(value["x"])
            y = as_float(value["y"])
            width = as_float(value["width"])
            height = as_float(value["height"])
            if x is not None and y is not None and width is not None and height is not None:
                return _ordered_bbox(x, y, x + width, y + height)
        for key in (
            "bbox",
            "box",
            "rect",
            "region",
            "coordinate",
            "coordinates",
            "polygon",
            "poly",
            "dt_poly",
        ):
            if key in value:
                box = coerce_bbox(value[key])
                if box is not None:
                    return box
        return None

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    if len(value) == 4 and all(as_float(item) is not None for item in value):
        sequence_coords = [float(item) for item in value]
        return _ordered_bbox(*sequence_coords)

    points: list[tuple[float, float]] = []
    if len(value) >= 3 and all(
        isinstance(item, Sequence)
        and not isinstance(item, (str, bytes, bytearray))
        and len(item) >= 2
        for item in value
    ):
        for item in value:
            x, y = as_float(item[0]), as_float(item[1])
            if x is not None and y is not None:
                points.append((x, y))
    elif len(value) >= 6 and len(value) % 2 == 0:
        maybe_flat = [as_float(item) for item in value]
        flat = [item for item in maybe_flat if item is not None]
        if len(flat) == len(maybe_flat):
            points = [(flat[index], flat[index + 1]) for index in range(0, len(flat), 2)]
    if not points:
        return None
    return BBox(
        x0=min(point[0] for point in points),
        y0=min(point[1] for point in points),
        x1=max(point[0] for point in points),
        y1=max(point[1] for point in points),
    )


def coerce_polygon(value: Any) -> list[Point]:
    """Preserve a provider polygon when it contains explicit point evidence."""

    if isinstance(value, Mapping):
        for key in ("polygon", "poly", "dt_poly", "points", "coordinates"):
            if key in value:
                polygon = coerce_polygon(value[key])
                if polygon:
                    return polygon
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    pairs: list[tuple[Any, Any]] = []
    if len(value) >= 3 and all(
        isinstance(item, Sequence)
        and not isinstance(item, (str, bytes, bytearray))
        and len(item) >= 2
        for item in value
    ):
        pairs = [(item[0], item[1]) for item in value]
    elif len(value) >= 6 and len(value) % 2 == 0:
        pairs = [(value[index], value[index + 1]) for index in range(0, len(value), 2)]
    points: list[Point] = []
    for raw_x, raw_y in pairs:
        x, y = as_float(raw_x), as_float(raw_y)
        if x is None or y is None:
            return []
        points.append(Point(x=x, y=y))
    return points


def _ordered_bbox(x0: float, y0: float, x1: float, y1: float) -> BBox:
    return BBox(x0=min(x0, x1), y0=min(y0, y1), x1=max(x0, x1), y1=max(y0, y1))


_TYPE_MAP: tuple[tuple[str, ElementType], ...] = (
    ("page_number", ElementType.PAGE_NUMBER),
    ("list_item", ElementType.LIST_ITEM),
    ("footnote", ElementType.FOOTNOTE),
    ("formula", ElementType.FORMULA),
    ("equation", ElementType.FORMULA),
    ("caption", ElementType.CAPTION),
    ("header", ElementType.HEADER),
    ("footer", ElementType.FOOTER),
    ("title", ElementType.TITLE),
    ("heading", ElementType.HEADING),
    ("section", ElementType.HEADING),
    ("paragraph", ElementType.PARAGRAPH),
    ("table", ElementType.TABLE),
    ("chart", ElementType.CHART),
    ("figure", ElementType.FIGURE),
    ("image", ElementType.IMAGE),
    ("picture", ElementType.IMAGE),
    ("stamp", ElementType.STAMP),
    ("signature", ElementType.SIGNATURE),
    ("checkbox", ElementType.CHECKBOX),
    ("list", ElementType.LIST_ITEM),
    ("text", ElementType.TEXT),
)


def element_type(value: Any, *, default: ElementType = ElementType.TEXT) -> ElementType:
    if isinstance(value, ElementType):
        return value
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    for token, result in _TYPE_MAP:
        if token in normalized:
            return result
    return default


# How each container joins its children.  `spans` are the fragments *within*
# one visual line — a styled run, an inline equation, a CJK segment — so joining
# them with a newline split a single line into several and injected whitespace
# that is not in the document.  The structured MinerU path already joins inline
# spans with "".  Adjacent-duplicate removal is also wrong at that level: three
# spans reading "1", "0", "0" are the number 100, not a repeat.
_CONTAINER_SEPARATORS: tuple[tuple[str, str], ...] = (
    ("lines", "\n"),
    ("spans", ""),
    ("children", "\n"),
    ("blocks", "\n"),
    ("content", "\n"),
    ("res", "\n"),
)


def text_from(value: Any, *, separator: str = "\n") -> str | None:
    """Extract readable text from common block/span response shapes."""

    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("natural_text", "text", "content", "markdown", "value", "rec_text"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
        pieces: list[str] = []
        for key, key_separator in _CONTAINER_SEPARATORS:
            if key in value:
                candidate = text_from(value[key], separator=key_separator)
                if candidate:
                    pieces.append(candidate)
        return _join_pieces(pieces, separator) or None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        pieces = [piece for item in value if (piece := text_from(item, separator=separator))]
        return _join_pieces(pieces, separator) or None
    return None


def _join_pieces(pieces: list[str], separator: str) -> str:
    if separator:
        pieces = _dedupe_adjacent(pieces)
    return separator.join(pieces)


def _dedupe_adjacent(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if not result or result[-1] != value:
            result.append(value)
    return result


def page_dimensions(
    payload: Any,
    elements: Sequence[Element],
    *,
    context: Any = None,
) -> tuple[float, float]:
    """Read explicit dimensions or conservatively derive them from boxes."""

    width = height = None
    if isinstance(payload, Mapping):
        for key in ("width", "page_width", "image_width", "w"):
            if (width := as_float(payload.get(key))) is not None and width > 0:
                break
        for key in ("height", "page_height", "image_height", "h"):
            if (height := as_float(payload.get(key))) is not None and height > 0:
                break
        for key in ("page_size", "size", "dimensions", "img_shape", "image_size"):
            size = payload.get(key)
            if isinstance(size, Mapping):
                width = width or as_float(size.get("width") or size.get("w"))
                height = height or as_float(size.get("height") or size.get("h"))
            elif isinstance(size, Sequence) and not isinstance(size, (str, bytes, bytearray)):
                if len(size) >= 2:
                    # Image shapes conventionally use [height, width, channels].
                    if key == "img_shape":
                        height = height or as_float(size[0])
                        width = width or as_float(size[1])
                    else:
                        width = width or as_float(size[0])
                        height = height or as_float(size[1])
    if context is not None:
        width = width or context.page_width
        height = height or context.page_height
    if elements:
        width = width or max(element.bbox.x1 for element in elements)
        height = height or max(element.bbox.y1 for element in elements)
    return (float(width or 1.0), float(height or 1.0))


def page_number(payload: Any, fallback_index: int) -> int:
    if isinstance(payload, Mapping):
        for key in ("page_number", "page_num", "page_no", "number"):
            value = as_float(payload.get(key))
            if value is not None:
                return max(1, int(value))
        for key in ("page_index", "page_idx", "index"):
            value = as_float(payload.get(key))
            if value is not None:
                return max(1, int(value) + 1)
    return fallback_index + 1


def unique_elements(elements: Sequence[Element]) -> list[Element]:
    """Remove exact adapter duplicates while preserving reading order."""

    seen: set[tuple[str | None, float, float, float, float, ElementType]] = set()
    result: list[Element] = []
    for element in elements:
        key = (
            element.text,
            round(element.bbox.x0, 3),
            round(element.bbox.y0, 3),
            round(element.bbox.x1, 3),
            round(element.bbox.y1, 3),
            element.type,
        )
        if key not in seen:
            seen.add(key)
            result.append(element)
    return [
        element.model_copy(
            update={"reading_order": index, "id": element.id or f"element-{index + 1}"}
        )
        for index, element in enumerate(result)
    ]
