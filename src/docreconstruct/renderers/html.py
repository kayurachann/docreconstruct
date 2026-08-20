"""Self-contained fixed-page HTML renderer."""

from __future__ import annotations

import base64
import binascii
import html
import mimetypes
import re
from pathlib import Path
from typing import Any

from ._utils import (
    allowed_local_path,
    bbox_tuple,
    element_metadata,
    element_style,
    element_text,
    element_type,
    enum_text,
    finite_number,
    mapping,
    ordered_elements,
    pages,
    table_rows,
    table_spans,
    value,
)
from .base import Renderer

_SAFE_CSS_TOKEN = re.compile(r"^[^;{}<>]*$")


def _css_text(candidate: Any, fallback: str | None = None) -> str | None:
    if candidate is None:
        return fallback
    candidate = str(candidate).strip()
    return candidate if candidate and _SAFE_CSS_TOKEN.fullmatch(candidate) else fallback


def _css_length(candidate: Any, unit: str = "px") -> str | None:
    if candidate is None:
        return None
    return f"{finite_number(candidate):g}{unit}"


def _style_declarations(element: Any) -> str:
    left, top, right, bottom = bbox_tuple(element)
    style = element_style(element)
    declarations = [
        f"left:{left:g}px",
        f"top:{top:g}px",
        f"width:{max(0.0, right - left):g}px",
        f"height:{max(0.0, bottom - top):g}px",
    ]
    font_family = _css_text(style.get("font_family"))
    if font_family:
        escaped_family = font_family.replace('"', '\\"')
        declarations.append(f'font-family:"{escaped_family}"')
    if (font_size := _css_length(style.get("font_size"))) is not None:
        declarations.append(f"font-size:{font_size}")
    weight = style.get("font_weight")
    if weight is not None:
        safe_weight = _css_text(weight)
        if safe_weight:
            declarations.append(f"font-weight:{safe_weight}")
    if style.get("italic"):
        declarations.append("font-style:italic")
    if style.get("underline"):
        declarations.append("text-decoration:underline")
    alignment = enum_text(style.get("alignment", "")).lower()
    if alignment in {"left", "right", "center", "justify", "start", "end"}:
        declarations.append(f"text-align:{alignment}")
    if (line_height := _css_length(style.get("line_height"))) is not None:
        declarations.append(f"line-height:{line_height}")
    for source, target in (("color", "color"), ("background_color", "background-color")):
        if (color := _css_text(style.get(source))) is not None:
            declarations.append(f"{target}:{color}")
    rotation = finite_number(style.get("rotation", 0.0))
    if rotation:
        declarations.extend((f"transform:rotate({rotation:g}deg)", "transform-origin:top left"))
    if style.get("opacity") is not None:
        opacity = max(0.0, min(1.0, finite_number(style["opacity"], 1.0)))
        declarations.append(f"opacity:{opacity:g}")
    z_index = value(element, "z_index", element_metadata(element).get("z_index"))
    if z_index is not None:
        declarations.append(f"z-index:{int(finite_number(z_index))}")
    return ";".join(declarations)


def _data_uri(
    element: Any,
    *,
    allow_local_files: bool = False,
    local_file_root: str | Path | None = None,
) -> str | None:
    metadata = element_metadata(element)
    nested = metadata.get("image")
    image = dict(nested) if isinstance(nested, dict) else {}

    for key in ("data_uri", "src", "image_ref"):
        candidate = image.get(key, metadata.get(key))
        if isinstance(candidate, str) and candidate.startswith("data:image/"):
            return candidate

    raw = image.get(
        "bytes",
        image.get("data", metadata.get("image_bytes", metadata.get("image_data"))),
    )
    mime = str(image.get("mime_type", metadata.get("mime_type", "image/png")))
    if isinstance(raw, str):
        try:
            raw = base64.b64decode(raw, validate=True)
        except (ValueError, binascii.Error):
            raw = None
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return f"data:{mime};base64,{base64.b64encode(bytes(raw)).decode('ascii')}"

    for key in ("path", "src", "image_ref"):
        candidate = image.get(key, metadata.get(key))
        path = allowed_local_path(
            candidate,
            allow_local_files=allow_local_files,
            local_file_root=local_file_root,
        )
        if path is not None:
            guessed_mime = mimetypes.guess_type(path.name)[0] or mime
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{guessed_mime};base64,{encoded}"
    return None


def _render_table(element: Any) -> str:
    rows = table_rows(element)
    if not rows:
        return f'<div class="dr-text">{html.escape(element_text(element))}</div>'
    metadata = element_metadata(element)
    header_rows = max(0, int(finite_number(metadata.get("header_rows", 1), 1.0)))
    # The flattened grid renders a merged cell as its anchor plus empty boxes
    # where the source had none.  When the source spans are known, re-emit them
    # and drop the covered slots so the table keeps its real shape.
    spans = table_spans(element)
    rendered_rows: list[str] = []
    for row_index, row in enumerate(rows):
        tag = "th" if row_index < header_rows else "td"
        row_spans = spans[row_index] if row_index < len(spans) else []
        rendered_cells: list[str] = []
        for column_index, cell in enumerate(row):
            colspan, rowspan = row_spans[column_index] if column_index < len(row_spans) else (1, 1)
            if (colspan, rowspan) == (0, 0):
                continue
            attributes = ""
            if colspan > 1:
                attributes += f' colspan="{colspan}"'
            if rowspan > 1:
                attributes += f' rowspan="{rowspan}"'
            rendered_cells.append(f"<{tag}{attributes}>{html.escape(cell)}</{tag}>")
        rendered_rows.append(f"<tr>{''.join(rendered_cells)}</tr>")
    return '<table class="dr-table">' + "".join(rendered_rows) + "</table>"


def _render_element(
    element: Any,
    *,
    allow_local_files: bool = False,
    local_file_root: str | Path | None = None,
) -> str:
    kind = element_type(element)
    identifier = html.escape(str(value(element, "id", "")), quote=True)
    style = html.escape(_style_declarations(element), quote=True)
    common = (
        f'class="dr-element dr-{html.escape(kind, quote=True)}" '
        f'data-element-id="{identifier}" style="{style}"'
    )
    if kind == "table":
        content = _render_table(element)
        tag = "div"
    elif kind in {"image", "figure", "chart", "signature", "stamp"}:
        source = _data_uri(
            element,
            allow_local_files=allow_local_files,
            local_file_root=local_file_root,
        )
        alt = element_metadata(element).get("alt", element_text(element))
        if source:
            content = (
                f'<img src="{html.escape(source, quote=True)}" '
                f'alt="{html.escape(str(alt), quote=True)}">'
            )
        else:
            label = str(alt) if alt else kind
            content = f'<span class="dr-image-placeholder">{html.escape(label)}</span>'
        tag = "figure"
    else:
        content = html.escape(element_text(element))
        if kind == "title":
            tag = "h1"
        elif kind == "heading":
            level = int(
                max(
                    1,
                    min(
                        6,
                        finite_number(
                            element_metadata(element).get(
                                "level", element_style(element).get("heading_level", 2)
                            ),
                            2.0,
                        ),
                    ),
                )
            )
            tag = f"h{level}"
        elif kind in {"paragraph", "caption", "footnote", "header", "footer"}:
            tag = "p"
        else:
            tag = "div"
    return f"<{tag} {common}>{content}</{tag}>"


class HTMLRenderer(Renderer[str]):
    """Render each source page as a fixed-size, absolutely positioned canvas."""

    format = "html"
    extension = ".html"
    media_type = "text/html"

    def __init__(
        self,
        *,
        title: str | None = None,
        page_gap: int = 24,
        allow_local_files: bool = False,
        local_file_root: str | Path | None = None,
    ) -> None:
        self.title = title
        self.page_gap = max(0, int(page_gap))
        self.allow_local_files = bool(allow_local_files)
        self.local_file_root = Path(local_file_root) if local_file_root is not None else None

    def render(self, document: Any) -> str:
        metadata = mapping(value(document, "metadata", None))
        title = (
            self.title or metadata.get("title") or value(document, "id", "Reconstructed document")
        )
        page_html: list[str] = []
        for index, page in enumerate(pages(document), start=1):
            width = max(1.0, finite_number(value(page, "width", 1.0), 1.0))
            height = max(1.0, finite_number(value(page, "height", 1.0), 1.0))
            page_id = html.escape(str(value(page, "id", index)), quote=True)
            number = html.escape(str(value(page, "number", index)), quote=True)
            contents = "".join(
                _render_element(
                    element,
                    allow_local_files=self.allow_local_files,
                    local_file_root=self.local_file_root,
                )
                for element in ordered_elements(page)
            )
            page_html.append(
                f'<section class="dr-page" data-page-id="{page_id}" data-page-number="{number}" '
                f'style="width:{width:g}px;height:{height:g}px">{contents}</section>'
            )
        css = f"""
:root {{ color-scheme: light; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: #e5e7eb; }}
body {{ display: flex; flex-direction: column; align-items: center; gap: {self.page_gap}px;
        padding: {self.page_gap}px; font-family: Arial, sans-serif; }}
.dr-page {{ position: relative; flex: 0 0 auto; overflow: hidden; background: white;
            box-shadow: 0 1px 5px rgba(0,0,0,.2); }}
.dr-element {{ position: absolute; margin: 0; padding: 0; overflow: hidden;
               white-space: pre-wrap; overflow-wrap: break-word; }}
.dr-element > img {{ display: block; width: 100%; height: 100%; object-fit: contain; }}
.dr-element.dr-image, .dr-element.dr-figure, .dr-element.dr-chart,
.dr-element.dr-signature, .dr-element.dr-stamp {{ display: block; }}
.dr-image-placeholder {{ display: flex; width: 100%; height: 100%; align-items: center;
                         justify-content: center; border: 1px dashed #9ca3af; color: #6b7280; }}
.dr-table {{ width: 100%; height: 100%; table-layout: fixed; border-collapse: collapse; }}
.dr-table th, .dr-table td {{ border: 1px solid currentColor; padding: 2px 4px;
                              overflow: hidden; white-space: pre-wrap; text-align: inherit; }}
@media print {{
  html, body {{ display: block; padding: 0; background: white; }}
  .dr-page {{ margin: 0; box-shadow: none; break-after: page; }}
}}
""".strip()
        return (
            '<!doctype html>\n<html lang="en">\n<head>\n'
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{html.escape(str(title))}</title>\n<style>{css}</style>\n"
            "</head>\n<body>\n" + "\n".join(page_html) + "\n</body>\n</html>\n"
        )


HtmlRenderer = HTMLRenderer
