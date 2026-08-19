"""High-level, model-agnostic reconstruction pipeline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from docreconstruct.analysis import infer_reading_order
from docreconstruct.exceptions import ReconstructionError, UnsupportedInputError
from docreconstruct.ir import Document
from docreconstruct.preprocessing import analyze_source, image_to_document
from docreconstruct.profiles import ReconstructionProfile
from docreconstruct.reconstruction import TargetFormat, build_plan

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}


def _engine_names(engines: Sequence[str] | str | None, suffix: str) -> list[str]:
    if isinstance(engines, str):
        engines = engines.split(",")
    names = [name.strip().lower().replace("-", "_") for name in engines or [] if name.strip()]
    if names == ["auto"]:
        names = []
    if names:
        return names
    if suffix == ".pdf":
        return ["native_pdf"]
    if suffix == ".json":
        return ["json"]
    if suffix in {".md", ".markdown"}:
        return ["markdown"]
    return []


def _options_for(name: str, options: Mapping[str, Any] | None) -> dict[str, Any]:
    if not options:
        return {}
    nested = options.get(name)
    if isinstance(nested, Mapping):
        return dict(nested)
    return {
        key: value
        for key, value in options.items()
        if key not in {"provider_sources", "provider_weights"}
    }


def _provider_source(name: str, source: Path, options: Mapping[str, Any] | None) -> Path:
    """Resolve an explicit or conventional saved-output sidecar."""

    provider_sources = options.get("provider_sources") if options else None
    if isinstance(provider_sources, Mapping):
        configured = provider_sources.get(name)
        if configured:
            return Path(str(configured)).expanduser().resolve()
    for candidate in (
        source.with_name(f"{source.name}.{name}.json"),
        source.with_name(f"{source.stem}.{name}.json"),
        source.with_name(f"{source.stem}.{name}.jsonl"),
    ):
        if candidate.is_file():
            return candidate
    return source


def _run_provider(
    name: str,
    source: Path,
    *,
    original_source: Path | None = None,
    page_width: float | None = None,
    page_height: float | None = None,
    options: Mapping[str, Any] | None,
) -> tuple[Document, list[str]]:
    from docreconstruct.providers import ProviderContext, get_provider
    from docreconstruct.providers.base import ProviderInferenceUnsupportedError

    provider = get_provider(name)
    capabilities = provider.capabilities
    context_source = original_source or source
    context = ProviderContext(
        source=str(context_source),
        page_width=page_width,
        page_height=page_height,
        options=_options_for(name, options),
    )
    if (
        source.suffix.lower() != ".json"
        and not capabilities.live_inference
        and name
        not in {
            "native_pdf",
            "json",
            "markdown",
        }
    ):
        try:
            provider.infer(  # type: ignore[attr-defined]
                source,
                context=context,
            )
        except AttributeError as exc:
            raise ProviderInferenceUnsupportedError(
                f"Provider {name!r} cannot perform live inference. "
                "Pass its saved JSON result instead."
            ) from exc
    result = provider.parse(
        source,
        context=context,
    )
    return result.document, list(result.warnings)


def _fuse(documents: list[Document]) -> Document:
    if len(documents) == 1:
        return documents[0]
    from docreconstruct.normalization import fuse_documents

    return fuse_documents(documents)


def analyze(
    source: str | Path,
    *,
    engines: Sequence[str] | str | None = None,
    fusion: bool = False,
    provider_options: Mapping[str, Any] | None = None,
) -> Document:
    """Extract a source into canonical IR without rendering it.

    Heavy OCR engines are deliberately not vendored. Built-in PaddleOCR,
    MinerU, and olmOCR adapters consume saved JSON; live provider plugins can
    declare ``live_inference=True`` and receive the original source here.
    """

    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    names = _engine_names(engines, suffix)
    warnings: list[str] = []
    try:
        source_inspection = analyze_source(path)
        inspection = source_inspection.model_dump(mode="json")
    except Exception as exc:  # inspection must not discard otherwise valid provider evidence
        source_inspection = None
        inspection = {"warning": f"Source inspection unavailable: {exc}"}
    context_width = context_height = None
    if source_inspection is not None and len(source_inspection.pages) == 1:
        context_width = source_inspection.pages[0].width
        context_height = source_inspection.pages[0].height
    if not names and suffix in _IMAGE_SUFFIXES:
        document = image_to_document(path)
        names = ["source_image"]
    elif not names:
        raise UnsupportedInputError(
            f"No provider selected for {suffix or '<no extension>'}; "
            "pass --engines or a supported input."
        )
    else:
        results: list[Document] = []
        for name in names:
            provider_source = _provider_source(name, path, provider_options)
            if provider_source != path and not provider_source.is_file():
                raise FileNotFoundError(provider_source)
            document, provider_warnings = _run_provider(
                name,
                provider_source,
                original_source=path,
                page_width=context_width,
                page_height=context_height,
                options=provider_options,
            )
            results.append(document)
            warnings.extend(provider_warnings)
        if len(results) > 1 and not fusion:
            warnings.append(
                "Multiple providers imply evidence fusion; fusion was enabled automatically."
            )
        document = _fuse(results)
        if provider_options and provider_options.get("provider_weights"):
            warnings.append(
                "provider_weights were recorded but the built-in v0.1 resolver uses "
                "confidence, consensus, and spatial overlap only."
            )

    for page in document.pages:
        infer_reading_order(page)
    from docreconstruct.routing import build_routing_plan

    routing_plan = build_routing_plan(document)
    warnings.extend(routing_plan.warnings)
    metadata = dict(document.metadata)
    metadata["routing_plan"] = routing_plan.model_dump(mode="json")
    metadata["pipeline"] = {
        "providers": names,
        "fusion": len(names) > 1,
        "warnings": warnings,
        "source_analysis": inspection,
    }
    document.metadata = metadata
    return document


def export(
    document: Document,
    destination: str | Path,
    *,
    output_format: str | TargetFormat | None = None,
    renderer_options: Mapping[str, Any] | None = None,
) -> Path:
    """Render canonical IR through a registered deterministic renderer."""

    from docreconstruct.renderers import registry, render

    path = Path(destination).expanduser()
    raw_format = getattr(output_format, "value", output_format)
    automatic = raw_format is None or str(raw_format).lower() == "auto"
    requested = path.suffix.lstrip(".") if automatic and path.suffix else raw_format or "auto"
    selected = build_plan(document, target=requested).target.value
    renderer_available = selected in registry and registry.get(selected).is_available()
    if not renderer_available:
        available = set(registry.formats(available_only=True))
        if automatic and not path.suffix:
            selected = "docx" if "docx" in available else "html"
            metadata = dict(document.metadata)
            warnings = list(metadata.get("warnings", []))
            warnings.append(
                f"Automatic target format has no installed renderer; used {selected!r} instead."
            )
            metadata["warnings"] = warnings
            document.metadata = metadata
        else:
            formats = ", ".join(sorted(available)) or "none"
            raise ReconstructionError(
                f"No {selected!r} renderer is registered. Available formats: {formats}."
            )
    if not path.suffix:
        path = path.with_suffix(f".{selected}")
    return render(document, path, format=selected, **dict(renderer_options or {}))


def reconstruct(
    source: str | Path,
    *,
    output: str | Path | None = None,
    output_format: str | None = None,
    engines: Sequence[str] | str | None = None,
    fusion: bool = False,
    profile: str | ReconstructionProfile = ReconstructionProfile.BALANCED,
    refine: bool = False,
    maximum_refinement_passes: int = 0,
    provider_options: Mapping[str, Any] | None = None,
    renderer_options: Mapping[str, Any] | None = None,
) -> Document:
    """Analyze, plan, and optionally export a high-fidelity editable document."""

    if maximum_refinement_passes < 0:
        raise ValueError("maximum_refinement_passes cannot be negative")
    document = analyze(
        source,
        engines=engines,
        fusion=fusion,
        provider_options=provider_options,
    )
    requested_format = output_format
    if requested_format is None or requested_format.lower() == "auto":
        requested_format = (
            Path(output).suffix.lstrip(".") if output and Path(output).suffix else "auto"
        )
    target = requested_format
    plan = build_plan(document, target=target, profile=profile)
    metadata = dict(document.metadata)
    metadata["reconstruction_plan"] = plan.model_dump(mode="json")
    if refine:
        warnings = list(metadata.get("warnings", []))
        warnings.append(
            "Visual refinement requires a visual critic; use reconstruction.refine_document "
            "with a render-and-score callback. No unaudited correction was applied."
        )
        metadata["warnings"] = warnings
        metadata["requested_refinement_passes"] = maximum_refinement_passes
    document.metadata = metadata
    if output is not None:
        written = export(
            document,
            output,
            output_format=target,
            renderer_options=renderer_options,
        )
        metadata = dict(document.metadata)
        metadata["output"] = str(written)
        document.metadata = metadata
    return document
