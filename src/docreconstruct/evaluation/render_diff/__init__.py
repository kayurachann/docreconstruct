"""Project-owned render-difference localization diagnostics."""

from .localizer import localize_page_render_diff, localize_render_differences
from .models import (
    RENDER_DIFF_METRIC_VERSION,
    RENDER_DIFF_REPORT_SCHEMA_VERSION,
    RenderDiffComponentScores,
    RenderDiffDiagnostic,
    RenderDiffKind,
    RenderDiffPageSummary,
    RenderDiffReport,
    RenderedObjectRegion,
    RenderNormalizedBox,
    RenderPixelBox,
)

__all__ = [
    "RENDER_DIFF_METRIC_VERSION",
    "RENDER_DIFF_REPORT_SCHEMA_VERSION",
    "RenderDiffComponentScores",
    "RenderDiffDiagnostic",
    "RenderDiffKind",
    "RenderDiffPageSummary",
    "RenderDiffReport",
    "RenderNormalizedBox",
    "RenderPixelBox",
    "RenderedObjectRegion",
    "localize_page_render_diff",
    "localize_render_differences",
]
