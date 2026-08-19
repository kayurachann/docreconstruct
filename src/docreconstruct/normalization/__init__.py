"""Normalization and cross-provider evidence fusion."""

from .fusion import (
    EvidenceFusion,
    fuse_documents,
    fuse_element_evidence,
    fuse_elements,
    fuse_pages,
)

__all__ = [
    "EvidenceFusion",
    "fuse_documents",
    "fuse_element_evidence",
    "fuse_elements",
    "fuse_pages",
]
