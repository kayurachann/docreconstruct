"""Built-in document renderers and the public rendering façade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import (
    BaseRenderer,
    OptionalDependencyError,
    Renderer,
    RendererError,
    RendererRegistry,
    get_renderer,
    registry,
    render_document,
)
from .docx import DOCXRenderer, DocxRenderer
from .html import HTMLRenderer, HtmlRenderer
from .json import JSONRenderer, JsonRenderer, to_jsonable
from .markdown import MarkdownRenderer, MDRenderer


def _register_builtins() -> None:
    for name, renderer in (
        ("json", JSONRenderer),
        ("html", HTMLRenderer),
        ("htm", HTMLRenderer),
        ("docx", DOCXRenderer),
        ("markdown", MarkdownRenderer),
        ("md", MarkdownRenderer),
    ):
        if name not in registry:
            registry.register(name, renderer)


_register_builtins()


def get_registry() -> RendererRegistry:
    return registry


def render(
    document: Any,
    output_path: str | Path,
    format: str | None = None,
    **options: Any,
) -> Path:
    """Render *document* to *output_path* and return its resolved path.

    The format defaults to the output suffix (``.md`` maps to ``markdown``).
    """

    destination = Path(output_path)
    selected = format or destination.suffix.lstrip(".")
    if not selected:
        raise ValueError("format is required when output_path has no extension")
    return get_renderer(selected, **options).write(document, destination)


__all__ = [
    "BaseRenderer",
    "DOCXRenderer",
    "DocxRenderer",
    "HTMLRenderer",
    "HtmlRenderer",
    "JSONRenderer",
    "JsonRenderer",
    "MDRenderer",
    "MarkdownRenderer",
    "OptionalDependencyError",
    "Renderer",
    "RendererError",
    "RendererRegistry",
    "get_renderer",
    "get_registry",
    "registry",
    "render",
    "render_document",
    "to_jsonable",
]
