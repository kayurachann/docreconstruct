"""Deterministic reconstruction planning."""

from .evidence_matching import EvidenceDocuments, EvidenceMatch, match_sidecar_evidence
from .hybrid import (
    HybridEvidenceSummary,
    HybridPreparedRenderPlan,
    HybridPreparedSources,
    HybridReconstructionResult,
    HybridSourceManifest,
    SourceFingerprint,
    finalize_hybrid_reconstruction,
    prepare_hybrid_render_plan,
    prepare_hybrid_sources,
    prepare_markdown_layout_sources,
    prepare_markdown_pdf_sources,
    reconstruct_hybrid,
)
from .planner import ReconstructionPlan, TargetFormat, build_plan, choose_output_format
from .refinement import (
    CriticResult,
    LayoutCorrection,
    RefinementResult,
    apply_layout_corrections,
    refine_document,
)

__all__ = [
    "CriticResult",
    "EvidenceDocuments",
    "EvidenceMatch",
    "HybridEvidenceSummary",
    "HybridPreparedRenderPlan",
    "HybridPreparedSources",
    "HybridReconstructionResult",
    "HybridSourceManifest",
    "LayoutCorrection",
    "ReconstructionPlan",
    "RefinementResult",
    "SourceFingerprint",
    "TargetFormat",
    "apply_layout_corrections",
    "build_plan",
    "choose_output_format",
    "finalize_hybrid_reconstruction",
    "match_sidecar_evidence",
    "prepare_hybrid_render_plan",
    "prepare_hybrid_sources",
    "prepare_markdown_layout_sources",
    "prepare_markdown_pdf_sources",
    "reconstruct_hybrid",
    "refine_document",
]
