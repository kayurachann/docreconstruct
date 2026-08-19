"""Readable semantic Markdown fallback renderer."""

from __future__ import annotations

from typing import Any

from ._utils import (
    element_metadata,
    element_style,
    element_text,
    element_type,
    finite_number,
    ordered_elements,
    pages,
    table_rows,
)
from .base import Renderer


def _escape_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _table_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    columns = max((len(row) for row in rows), default=0)
    if columns == 0:
        return ""
    normalized = [row + [""] * (columns - len(row)) for row in rows]
    lines = ["| " + " | ".join(_escape_cell(cell) for cell in normalized[0]) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(columns)) + " |")
    lines.extend(
        "| " + " | ".join(_escape_cell(cell) for cell in row) + " |" for row in normalized[1:]
    )
    return "\n".join(lines)


class MarkdownRenderer(Renderer[str]):
    format = "markdown"
    extension = ".md"
    media_type = "text/markdown"

    def __init__(self, *, page_markers: bool = False) -> None:
        self.page_markers = page_markers

    def render(self, document: Any) -> str:
        blocks: list[str] = []
        for page_index, page in enumerate(pages(document), start=1):
            if self.page_markers:
                blocks.append(f"<!-- page: {page_index} -->")
            for element in ordered_elements(page):
                kind = element_type(element)
                text = element_text(element)
                if kind == "title":
                    blocks.append(f"# {text}")
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
                    blocks.append(f"{'#' * level} {text}")
                elif kind == "list_item":
                    marker = "1." if element_metadata(element).get("ordered") else "-"
                    blocks.append(f"{marker} {text}")
                elif kind == "table":
                    blocks.append(_table_markdown(table_rows(element)) or text)
                elif kind in {"image", "figure", "chart"}:
                    metadata = element_metadata(element)
                    alt = str(metadata.get("alt", text or kind))
                    reference = metadata.get("image_ref", metadata.get("src"))
                    if (
                        isinstance(reference, str)
                        and reference
                        and not reference.startswith("data:")
                    ):
                        blocks.append(f"![{alt}]({reference})")
                    else:
                        blocks.append(f"[{kind}: {alt}]")
                elif text:
                    blocks.append(text)
            if page_index < len(pages(document)):
                blocks.append("---")
        return "\n\n".join(block for block in blocks if block != "") + ("\n" if blocks else "")


MDRenderer = MarkdownRenderer
