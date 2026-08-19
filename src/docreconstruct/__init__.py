"""Turn document pixels into editable document structure."""

from docreconstruct.evidence import (
    DetectionCandidate,
    SidecarDetection,
    SidecarEvidence,
    SidecarEvidenceBundle,
    SidecarEvidenceError,
    detect_sidecar_provider,
    load_sidecar_evidence,
)
from docreconstruct.ir import (
    BBox,
    Document,
    Element,
    ElementStyle,
    ElementType,
    Page,
    Point,
    Provenance,
    Relationship,
    SourceType,
    TextCandidate,
)
from docreconstruct.pipeline import analyze, export, reconstruct
from docreconstruct.profiles import ReconstructionProfile
from docreconstruct.reconstruction import (
    EvidenceDocuments,
    EvidenceMatch,
    HybridEvidenceSummary,
    HybridReconstructionResult,
    HybridSourceManifest,
    SourceFingerprint,
    finalize_hybrid_reconstruction,
    match_sidecar_evidence,
    prepare_markdown_layout_sources,
    prepare_markdown_pdf_sources,
    reconstruct_hybrid,
)
from docreconstruct.reconstruction.hybrid_job import (
    HybridJobResult,
    OnlineOCRRequest,
    run_hybrid_job,
)
from docreconstruct.routing import DocumentRouter, RoutingPlan, RoutingPolicy, build_routing_plan

__version__ = "0.1.0"

__all__ = [
    "BBox",
    "DetectionCandidate",
    "Document",
    "DocumentRouter",
    "Element",
    "ElementStyle",
    "ElementType",
    "EvidenceDocuments",
    "EvidenceMatch",
    "HybridReconstructionResult",
    "HybridEvidenceSummary",
    "HybridJobResult",
    "HybridSourceManifest",
    "Page",
    "OnlineOCRRequest",
    "Point",
    "Provenance",
    "ReconstructionProfile",
    "Relationship",
    "RoutingPlan",
    "RoutingPolicy",
    "SourceFingerprint",
    "SourceType",
    "SidecarDetection",
    "SidecarEvidence",
    "SidecarEvidenceBundle",
    "SidecarEvidenceError",
    "TextCandidate",
    "analyze",
    "build_routing_plan",
    "detect_sidecar_provider",
    "export",
    "finalize_hybrid_reconstruction",
    "load_sidecar_evidence",
    "match_sidecar_evidence",
    "prepare_markdown_layout_sources",
    "prepare_markdown_pdf_sources",
    "reconstruct_hybrid",
    "reconstruct",
    "run_hybrid_job",
]
