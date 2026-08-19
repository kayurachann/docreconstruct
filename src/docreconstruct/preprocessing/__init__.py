"""Input inspection and lossless source preparation."""

from .image import image_to_document
from .source import InputAnalysis, PageAnalysis, SourceKind, analyze_source

__all__ = [
    "InputAnalysis",
    "PageAnalysis",
    "SourceKind",
    "analyze_source",
    "image_to_document",
]
