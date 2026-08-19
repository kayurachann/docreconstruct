"""One auditable Markdown + layout + optional OCR reconstruction job."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from docreconstruct.evaluation.hybrid_validation import (
    HybridValidationReport,
    validate_hybrid,
)
from docreconstruct.evidence import ProviderHints
from docreconstruct.extraction import ExtractionMode, ExtractionResult, extract_to_markdown
from docreconstruct.reconstruction.hybrid import (
    HybridReconstructionResult,
    prepare_hybrid_sources,
    reconstruct_hybrid,
)


class OnlineOCRRequest(BaseModel):
    """Explicit, credential-free policy for optional live OCR evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    mode: ExtractionMode = ExtractionMode.CLOUD
    providers: tuple[str, ...] = ()
    allow_cloud: bool = False
    ensemble: bool = False
    maximum_providers: int = Field(default=2, ge=1, le=8)
    languages: tuple[str, ...] = ()
    handwriting: bool = False
    formulas: bool = True
    tables: bool = True
    charts: bool = False
    distorted_photo: bool = False
    dewarping: bool = False
    artifacts_directory: Path
    cache: bool = True
    provider_options: Mapping[str, Any] | None = Field(default=None, exclude=True, repr=False)

    def model_post_init(self, __context: Any) -> None:
        """Reject any policy that could upload without explicit consent."""

        if self.mode in {ExtractionMode.CLOUD, ExtractionMode.HYBRID} and not self.allow_cloud:
            raise ValueError(
                f"{self.mode.value} OCR can upload document bytes; allow_cloud must be true"
            )
        normalized = tuple(dict.fromkeys(name.strip() for name in self.providers if name.strip()))
        object.__setattr__(self, "providers", normalized)
        object.__setattr__(
            self,
            "languages",
            tuple(dict.fromkeys(name.strip() for name in self.languages if name.strip())),
        )


@dataclass(frozen=True, slots=True)
class HybridJobResult:
    """Complete result of reconstruction, validation, and optional OCR evidence."""

    reconstruction: HybridReconstructionResult
    validation: HybridValidationReport
    evidence: tuple[Path, ...]
    extraction: ExtractionResult | None = None
    generated_markdown: Path | None = None
    extraction_report: Path | None = None
    qa_report: Path | None = None
    phase_seconds: Mapping[str, float] = field(default_factory=dict)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, value: str) -> Path:
    """Write a small job report atomically without retaining a partial file."""

    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(value.rstrip("\n") + "\n")
            temporary = Path(stream.name)
        os.replace(temporary, path)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return path


def _paths(value: str | Path | Sequence[str | Path] | None) -> tuple[Path, ...]:
    sources = (value,) if isinstance(value, (str, Path)) else tuple(value or ())
    resolved: list[Path] = []
    seen: set[Path] = set()
    for item in sources:
        path = Path(item).expanduser().resolve()
        if path not in seen:
            resolved.append(path)
            seen.add(path)
    return tuple(resolved)


def _hint_mapping(
    evidence: Sequence[Path],
    hints: ProviderHints | None,
) -> dict[str | Path, str]:
    """Convert user hints to a mapping so generated JSON can be appended safely."""

    if hints is None:
        return {}
    if isinstance(hints, Mapping):
        return {str(key): value for key, value in hints.items()}
    if isinstance(hints, str):
        values: tuple[str | None, ...] = (hints,)
    else:
        values = tuple(hints)
    expressions = [value for value in values if isinstance(value, str) and "=" in value]
    if expressions:
        if len(expressions) != len(values):
            raise ValueError(
                "provider hints cannot mix positional names with path=provider expressions"
            )
        mapping: dict[str | Path, str] = {}
        for expression in expressions:
            raw_path, separator, raw_provider = expression.rpartition("=")
            if not separator or not raw_path.strip() or not raw_provider.strip():
                raise ValueError(f"invalid provider hint {expression!r}; expected path=provider")
            if raw_path.strip() in mapping:
                raise ValueError(f"duplicate provider hint for {raw_path.strip()!r}")
            mapping[raw_path.strip()] = raw_provider.strip()
        return mapping
    if len(values) != len(evidence):
        raise ValueError(
            f"expected {len(evidence)} positional provider hint(s), received {len(values)}"
        )
    return {
        str(path): provider
        for path, provider in zip(evidence, values, strict=True)
        if provider is not None
    }


def run_hybrid_job(
    content: str | Path,
    layout: str | Path,
    *,
    evidence: str | Path | Sequence[str | Path] | None = None,
    evidence_provider_hints: ProviderHints | None = None,
    strict_evidence: bool = True,
    output: str | Path | None = None,
    allow_remote_assets: bool = True,
    online_ocr: OnlineOCRRequest | None = None,
    render_backend: str = "native",
    renderer_path: str | Path | None = None,
    minimum_visual_score: float | None = None,
    render_output_dir: str | Path | None = None,
    qa_report: str | Path | None = None,
) -> HybridJobResult:
    """Run the project pipeline once with explicit authorities and side effects.

    Markdown is always the exact content authority. Live OCR output is saved as
    canonical JSON and contributes only geometry, style, confidence, and
    provenance. LibreOffice is reached only through ``render_backend``.
    """

    job_started = perf_counter()
    phase_seconds: dict[str, float] = {}
    content_path = Path(content).expanduser().resolve()
    layout_path = Path(layout).expanduser().resolve()
    phase_started = perf_counter()
    content_before = _sha256(content_path)
    layout_before = _sha256(layout_path)
    phase_seconds["authority.hash"] = perf_counter() - phase_started
    user_evidence = _paths(evidence)
    combined_evidence = list(user_evidence)
    hints = _hint_mapping(user_evidence, evidence_provider_hints)
    extraction: ExtractionResult | None = None
    generated_markdown: Path | None = None
    extraction_report: Path | None = None

    if online_ocr is not None:
        phase_started = perf_counter()
        artifacts = online_ocr.artifacts_directory.expanduser().resolve()
        evidence_directory = artifacts / "evidence"
        cache_directory = artifacts / "cache" if online_ocr.cache else None
        generated_markdown = artifacts / f"{layout_path.stem}.online-ocr.md"
        extraction = extract_to_markdown(
            layout_path,
            output=generated_markdown,
            mode=online_ocr.mode,
            providers=online_ocr.providers or None,
            allow_cloud=online_ocr.allow_cloud,
            ensemble=online_ocr.ensemble,
            maximum_providers=online_ocr.maximum_providers,
            languages=online_ocr.languages,
            handwriting=online_ocr.handwriting,
            formulas=online_ocr.formulas,
            tables=online_ocr.tables,
            charts=online_ocr.charts,
            distorted_photo=online_ocr.distorted_photo,
            dewarping=online_ocr.dewarping,
            require_geometry=True,
            provider_options=online_ocr.provider_options,
            evidence_directory=evidence_directory,
            cache_directory=cache_directory,
        )
        if not extraction.evidence_outputs:
            raise RuntimeError("online OCR produced no canonical geometry evidence")
        for path in extraction.evidence_outputs:
            resolved = path.resolve()
            if resolved not in combined_evidence:
                combined_evidence.append(resolved)
            hints[str(resolved)] = "json"
        extraction_report = _atomic_text(
            artifacts / "extraction.run.json",
            extraction.manifest.model_dump_json(indent=2),
        )
        if extraction.manifest.source_sha256 != layout_before:
            raise RuntimeError("online OCR source hash does not match the layout authority")
        if _sha256(content_path) != content_before:
            raise RuntimeError("Markdown content authority changed during online OCR")
        if _sha256(layout_path) != layout_before:
            raise RuntimeError("layout authority changed during online OCR")
        phase_seconds["ocr.online"] = perf_counter() - phase_started
    else:
        phase_seconds["ocr.online"] = 0.0

    hint_argument: Mapping[str | Path, str] | None = hints or None
    prepared = prepare_hybrid_sources(
        content_path,
        layout_path,
        evidence=tuple(combined_evidence),
        evidence_provider_hints=hint_argument,
        strict_evidence=strict_evidence,
        _phase_seconds=phase_seconds,
    )
    reconstruction = reconstruct_hybrid(
        content_path,
        layout_path,
        evidence=tuple(combined_evidence),
        evidence_provider_hints=hint_argument,
        strict_evidence=strict_evidence,
        output=output,
        allow_remote_assets=allow_remote_assets,
        _prepared_sources=prepared,
        _phase_seconds=phase_seconds,
    )
    if reconstruction.manifest.content.sha256 != content_before:
        raise RuntimeError("Markdown content authority changed during reconstruction")
    if reconstruction.manifest.layout.sha256 != layout_before:
        raise RuntimeError("layout authority changed during reconstruction")
    validation = validate_hybrid(
        content_path,
        layout_path,
        reconstruction.output.path,
        evidence=tuple(combined_evidence),
        evidence_provider_hints=hint_argument,
        strict_evidence=strict_evidence,
        render_backend=render_backend,
        renderer_path=renderer_path,
        minimum_visual_score=minimum_visual_score,
        render_output_dir=render_output_dir,
        _prepared_sources=prepared,
        _phase_seconds=phase_seconds,
    )
    qa_path = None
    if qa_report is not None:
        qa_path = _atomic_text(
            Path(qa_report).expanduser().resolve(),
            validation.model_dump_json(indent=2),
        )
    phase_seconds["job.total"] = perf_counter() - job_started
    return HybridJobResult(
        reconstruction=reconstruction,
        validation=validation,
        evidence=tuple(combined_evidence),
        extraction=extraction,
        generated_markdown=generated_markdown,
        extraction_report=extraction_report,
        qa_report=qa_path,
        phase_seconds=dict(phase_seconds),
    )


__all__ = ["HybridJobResult", "OnlineOCRRequest", "run_hybrid_job"]
