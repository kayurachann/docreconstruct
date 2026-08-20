"""Content-safe decision traces for evidence alignment."""

from .diagnostics import build_alignment_report
from .models import (
    AlignmentCandidateTrace,
    AlignmentDebugPolicy,
    AlignmentDecisionStatus,
    AlignmentDecisionTrace,
    AlignmentPrivacyPolicy,
    AlignmentReason,
    AlignmentReasonCount,
    AlignmentReport,
    AlignmentSummary,
)
from .reporting import write_alignment_report
from .topology_builder import (
    build_document_reading_order_graphs,
    build_page_reading_order_graph,
)
from .topology_models import (
    PageRegion,
    PageRegionKind,
    ReadingOrderEdge,
    ReadingOrderGraph,
    ReadingOrderRelation,
)

__all__ = [
    "AlignmentCandidateTrace",
    "AlignmentDebugPolicy",
    "AlignmentDecisionStatus",
    "AlignmentDecisionTrace",
    "AlignmentPrivacyPolicy",
    "AlignmentReason",
    "AlignmentReasonCount",
    "AlignmentReport",
    "AlignmentSummary",
    "PageRegion",
    "PageRegionKind",
    "ReadingOrderEdge",
    "ReadingOrderGraph",
    "ReadingOrderRelation",
    "build_alignment_report",
    "build_document_reading_order_graphs",
    "build_page_reading_order_graph",
    "write_alignment_report",
]
