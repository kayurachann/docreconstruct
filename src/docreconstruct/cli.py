"""Command-line interface for local, reproducible reconstruction jobs."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, cast

import typer
from rich.console import Console

from docreconstruct import __version__
from docreconstruct.exceptions import DocReconstructError

cli = typer.Typer(
    name="docreconstruct",
    help="Reconstruct editable document structure from PDFs, scans, images, or saved OCR evidence.",
    no_args_is_help=True,
    invoke_without_command=True,
    pretty_exceptions_show_locals=False,
)
console = Console(stderr=True)


def _split_engines(value: str | None) -> list[str] | None:
    if not value:
        return None
    names = [item.strip() for item in value.split(",") if item.strip()]
    return names or None


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    elif is_dataclass(value):
        value = asdict(cast(Any, value))
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _fail(exc: Exception) -> None:
    console.print(f"[red]Error:[/red] {exc}")
    raise typer.Exit(code=2) from exc


@cli.command("reconstruct")
def reconstruct_command(
    source: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False, readable=True),
    output: Path | None = typer.Option(None, "--output", "-o", help="Destination file."),
    output_format: str | None = typer.Option(
        None,
        "--output-format",
        "--to",
        "-f",
        help="Renderer format; inferred from --output, otherwise docx.",
    ),
    engines: str | None = typer.Option(
        None,
        "--engines",
        "--engine",
        "-e",
        help=(
            "Provider or comma-separated providers. Use auto for source-aware defaults; "
            "PaddleOCR/MinerU/olmOCR built-ins consume saved JSON."
        ),
    ),
    fusion: bool = typer.Option(False, "--fusion", help="Fuse evidence from multiple providers."),
    profile: str = typer.Option("balanced", "--profile", help="Fidelity/editability profile."),
    refine: bool = typer.Option(False, "--refine", help="Request visual refinement metadata."),
    maximum_refinement_passes: int = typer.Option(0, "--max-passes", min=0, max=20),
) -> None:
    """Analyze SOURCE and write an editable or inspectable reconstruction."""

    from docreconstruct.pipeline import reconstruct

    selected_format = output_format or (output.suffix.lstrip(".") if output else None) or "docx"
    suffix = "md" if selected_format == "markdown" else selected_format.lstrip(".")
    destination = output or source.with_name(f"{source.stem}.reconstructed.{suffix}")
    try:
        document = reconstruct(
            source,
            output=destination,
            output_format=selected_format,
            engines=_split_engines(engines),
            fusion=fusion,
            profile=profile,
            refine=refine,
            maximum_refinement_passes=maximum_refinement_passes,
        )
    except (DocReconstructError, ImportError, KeyError, RuntimeError, ValueError, OSError) as exc:
        _fail(exc)
        return
    written = document.metadata.get("output", str(destination.resolve()))
    provider_names = document.metadata.get("pipeline", {}).get("providers", [])
    console.print(f"[green]Wrote[/green] {written}")
    element_count = sum(len(page.elements) for page in document.pages)
    console.print(
        f"{len(document.pages)} page(s), {element_count} element(s), "
        f"providers: {', '.join(provider_names) or 'none'}"
    )


@cli.command("extract")
def extract_command(
    source: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="PDF or raster document to extract as Markdown.",
    ),
    output: Path | None = typer.Option(None, "--output", "-o"),
    mode: str = typer.Option("cloud", "--mode", help="cloud, local, or hybrid"),
    providers: str = typer.Option("auto", "--providers", "--provider", "-p"),
    allow_cloud: bool = typer.Option(
        False,
        "--allow-cloud",
        help="Explicitly allow document bytes to be sent to a hosted OCR API.",
    ),
    ensemble: bool = typer.Option(False, "--ensemble"),
    maximum_providers: int = typer.Option(2, "--max-providers", min=1, max=8),
    languages: str | None = typer.Option(None, "--languages"),
    handwriting: bool = typer.Option(False, "--handwriting"),
    formulas: bool = typer.Option(True, "--formulas/--no-formulas"),
    tables: bool = typer.Option(True, "--tables/--no-tables"),
    charts: bool = typer.Option(False, "--charts"),
    distorted_photo: bool = typer.Option(False, "--distorted-photo"),
    dewarping: bool = typer.Option(False, "--dewarping"),
    load_provider_plugins: bool = typer.Option(False, "--load-provider-plugins"),
    report: Path | None = typer.Option(None, "--report"),
) -> None:
    """Run capability-routed OCR and write Markdown plus an auditable run report."""

    try:
        from docreconstruct.extraction import ExtractionMode, extract_to_markdown
        from docreconstruct.providers import registry

        normalized_mode = ExtractionMode(mode.strip().casefold())
        if load_provider_plugins:
            registry.load_entry_points()
        provider_names = [
            item.strip() for item in providers.split(",") if item.strip() and item != "auto"
        ]
        language_names = [item.strip() for item in (languages or "").split(",") if item.strip()]
        result = extract_to_markdown(
            source,
            output=output,
            mode=normalized_mode,
            providers=provider_names or None,
            allow_cloud=allow_cloud,
            ensemble=ensemble,
            maximum_providers=maximum_providers,
            languages=language_names,
            handwriting=handwriting,
            formulas=formulas,
            tables=tables,
            charts=charts,
            distorted_photo=distorted_photo,
            dewarping=dewarping,
            registry=registry,
        )
        console.print(f"[green]Wrote[/green] {result.output}")
        console.print(
            "providers: "
            + ", ".join(result.manifest.successful_providers)
            + f"; mode: {result.manifest.mode.value}"
        )
        if report:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(_json(result.manifest) + "\n", encoding="utf-8")
            console.print(f"[green]Wrote[/green] {report.resolve()}")
    except (DocReconstructError, ImportError, KeyError, RuntimeError, ValueError, OSError) as exc:
        _fail(exc)


@cli.command("hybrid")
def hybrid_command(
    content: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Markdown content authority.",
    ),
    layout: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="PDF or raster-image layout and original-figure authority.",
    ),
    evidence: list[Path] = typer.Option(
        [],
        "--evidence",
        "-E",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help=(
            "Repeatable saved OCR JSON/JSONL sidecar; supplies geometry, style, "
            "confidence, and provenance but never replaces Markdown wording."
        ),
    ),
    evidence_provider: list[str] = typer.Option(
        [],
        "--evidence-provider",
        help=(
            "Optional provider hint, repeated in evidence order or as FILE=PROVIDER; "
            "otherwise the saved JSON schema is detected offline."
        ),
    ),
    strict_evidence: bool = typer.Option(
        True,
        "--strict-evidence/--allow-partial-evidence",
        help="Fail on invalid, ambiguous, or wholly unrelated saved OCR evidence.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Destination; the extension selects the editable renderer.",
    ),
    remote_assets: bool = typer.Option(
        True,
        "--remote-assets/--no-remote-assets",
        help="Reuse image URLs referenced by Markdown, with source-crop fallback.",
    ),
    online_ocr: bool = typer.Option(
        False,
        "--online-ocr",
        help="Run a project-hosted OCR adapter and use its canonical JSON as layout evidence.",
    ),
    allow_cloud: bool = typer.Option(
        False,
        "--allow-cloud",
        help="Explicitly allow the layout source to be uploaded to hosted OCR.",
    ),
    ocr_mode: str = typer.Option("cloud", "--ocr-mode", help="cloud, local, or hybrid"),
    ocr_providers: str = typer.Option(
        "auto",
        "--ocr-providers",
        "--ocr-provider",
        help="Live OCR provider or comma-separated ordered fallback providers.",
    ),
    ocr_ensemble: bool = typer.Option(
        False,
        "--ocr-ensemble",
        help="Upload to multiple selected providers and retain independent JSON evidence.",
    ),
    ocr_maximum_providers: int = typer.Option(
        2,
        "--ocr-max-providers",
        min=1,
        max=8,
    ),
    ocr_languages: str | None = typer.Option(None, "--ocr-languages"),
    ocr_handwriting: bool = typer.Option(False, "--ocr-handwriting"),
    ocr_formulas: bool = typer.Option(True, "--ocr-formulas/--no-ocr-formulas"),
    ocr_tables: bool = typer.Option(True, "--ocr-tables/--no-ocr-tables"),
    ocr_charts: bool = typer.Option(False, "--ocr-charts"),
    ocr_distorted_photo: bool = typer.Option(False, "--ocr-distorted-photo"),
    ocr_dewarping: bool = typer.Option(False, "--ocr-dewarping"),
    ocr_artifacts_dir: Path | None = typer.Option(
        None,
        "--ocr-artifacts-dir",
        help="Directory for audit Markdown, canonical JSON, cache, and extraction manifest.",
    ),
    ocr_cache: bool = typer.Option(
        True,
        "--ocr-cache/--no-ocr-cache",
        help="Reuse SHA-verified canonical OCR results without another upload.",
    ),
    load_ocr_provider_plugins: bool = typer.Option(
        False,
        "--load-ocr-provider-plugins",
        help="Opt in to installed docreconstruct OCR provider entry points.",
    ),
    qa_report: Path | None = typer.Option(
        None,
        "--qa-report",
        help="Optional JSON path for project-native OOXML/layout validation.",
    ),
    qa_backend: str = typer.Option(
        "native",
        "--qa-backend",
        help="Visual QA backend: native (OOXML only), auto, or libreoffice.",
    ),
    qa_renderer_path: Path | None = typer.Option(
        None,
        "--qa-renderer-path",
        help="Optional LibreOffice/soffice executable used by project visual QA.",
    ),
    minimum_visual_score: float | None = typer.Option(
        None,
        "--min-visual-score",
        min=0.0,
        max=1.0,
        help="Fail when the foreground-normalized rendered score is below this value.",
    ),
    qa_render_dir: Path | None = typer.Option(
        None,
        "--qa-render-dir",
        help="Optional directory for project-rendered source/candidate/difference page PNGs.",
    ),
) -> None:
    """Rebuild editable content from Markdown, OCR evidence, and original pixels."""

    from docreconstruct.extraction import ExtractionMode
    from docreconstruct.reconstruction.hybrid_job import (
        OnlineOCRRequest,
        run_hybrid_job,
    )

    normalized_backend = qa_backend.strip().casefold()
    if normalized_backend not in {"native", "auto", "libreoffice"}:
        _fail(ValueError("--qa-backend must be native, auto, or libreoffice"))
        return
    if minimum_visual_score is not None and normalized_backend == "native":
        _fail(ValueError("--min-visual-score requires --qa-backend auto or libreoffice"))
        return
    if qa_renderer_path is not None and normalized_backend == "native":
        _fail(ValueError("--qa-renderer-path requires --qa-backend auto or libreoffice"))
        return
    if qa_renderer_path is not None and not qa_renderer_path.is_file():
        _fail(ValueError(f"--qa-renderer-path is not a file: {qa_renderer_path}"))
        return
    if allow_cloud and not online_ocr:
        _fail(ValueError("--allow-cloud is only meaningful with --online-ocr"))
        return
    try:
        live_request = None
        if online_ocr:
            normalized_mode = ExtractionMode(ocr_mode.strip().casefold())
            if load_ocr_provider_plugins:
                from docreconstruct.providers import registry

                registry.load_entry_points()
            provider_names = tuple(
                item.strip()
                for item in ocr_providers.split(",")
                if item.strip() and item.strip().casefold() != "auto"
            )
            language_names = tuple(
                item.strip() for item in (ocr_languages or "").split(",") if item.strip()
            )
            destination = (
                output.expanduser().resolve()
                if output is not None
                else content.expanduser().resolve().with_name(f"{content.stem}.reconstructed.docx")
            )
            artifacts = (
                ocr_artifacts_dir.expanduser().resolve()
                if ocr_artifacts_dir is not None
                else destination.parent / f"{destination.stem}.ocr"
            )
            live_request = OnlineOCRRequest(
                mode=normalized_mode,
                providers=provider_names,
                allow_cloud=allow_cloud,
                ensemble=ocr_ensemble,
                maximum_providers=ocr_maximum_providers,
                languages=language_names,
                handwriting=ocr_handwriting,
                formulas=ocr_formulas,
                tables=ocr_tables,
                charts=ocr_charts,
                distorted_photo=ocr_distorted_photo,
                dewarping=ocr_dewarping,
                artifacts_directory=artifacts,
                cache=ocr_cache,
            )
        job = run_hybrid_job(
            content,
            layout,
            evidence=evidence,
            evidence_provider_hints=evidence_provider or None,
            strict_evidence=strict_evidence,
            output=output,
            allow_remote_assets=remote_assets,
            online_ocr=live_request,
            render_backend=qa_backend,
            renderer_path=qa_renderer_path,
            minimum_visual_score=minimum_visual_score,
            render_output_dir=qa_render_dir,
            qa_report=qa_report,
        )
        result = job.reconstruction
        validation = job.validation
    except (DocReconstructError, ImportError, RuntimeError, ValueError, OSError) as exc:
        _fail(exc)
        return
    console.print(f"[green]Wrote[/green] {result.output.path}")
    console.print(
        f"content sha256: {result.manifest.content.sha256[:12]}…, "
        f"layout sha256: {result.manifest.layout.sha256[:12]}…"
    )
    if result.manifest.evidence:
        console.print(
            "evidence sha256: "
            + ", ".join(item.sha256[:12] + "…" for item in result.manifest.evidence)
        )
    if result.evidence_summary is not None:
        summary = result.evidence_summary
        console.print(
            "JSON evidence: "
            f"{summary.matched_blocks} matched block(s), "
            f"{summary.geometry_matches} geometry match(es), "
            f"{summary.conflicts} retained conflict(s); providers: "
            + (", ".join(summary.providers) or "none")
        )
    if job.extraction is not None:
        extraction = job.extraction
        console.print(
            "online OCR evidence: "
            + ", ".join(extraction.manifest.successful_providers)
            + ("; cache hit" if extraction.manifest.cache_hit else "; provider run")
        )
        if job.generated_markdown is not None:
            console.print(f"[green]Wrote audit OCR Markdown[/green] {job.generated_markdown}")
        if job.extraction_report is not None:
            console.print(f"[green]Wrote[/green] {job.extraction_report}")
    if job.qa_report is not None:
        console.print(f"[green]Wrote[/green] {job.qa_report}")
    console.print(
        f"QA gates: {validation.score * 100:.2f}% "
        f"({validation.passed_gates}/{validation.measured_gates} measured gates), "
        f"Office Math: {validation.metrics['native_office_math']}, "
        f"display rows: {validation.metrics['native_display_rows']}"
    )
    slot_coverage = validation.metrics.get("source_visual_slot_coverage")
    span_ratio = validation.metrics.get("mapped_vertical_span_ratio")
    geometry_coverage = validation.metrics.get("source_geometry_coverage")
    if (
        isinstance(slot_coverage, (int, float))
        and isinstance(span_ratio, (int, float))
        and isinstance(geometry_coverage, (int, float))
    ):
        console.print(
            "source geometry: "
            f"slots {float(slot_coverage) * 100:.2f}%, "
            f"blocks {float(geometry_coverage) * 100:.2f}%, "
            f"vertical span {float(span_ratio) * 100:.2f}%"
        )
    rendered_visual = validation.metrics.get("rendered_visual")
    render_backend = validation.metrics.get("render_backend")
    if isinstance(render_backend, dict):
        console.print(
            "render QA: "
            f"{render_backend.get('status', 'unknown')}"
            + (
                f", visual {float(rendered_visual['score']) * 100:.2f}%"
                if isinstance(rendered_visual, dict)
                and isinstance(rendered_visual.get("score"), (int, float))
                else ""
            )
        )
    if not validation.passed:
        console.print("[red]Hybrid QA failed; inspect --qa-report for gate details.[/red]")
        raise typer.Exit(code=3)


@cli.command("analyze")
def analyze_command(
    source: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False, readable=True),
    engines: str | None = typer.Option(None, "--engines", "--engine", "-e"),
    fusion: bool = typer.Option(False, "--fusion"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Optional JSON IR path."),
) -> None:
    """Normalize SOURCE into canonical Document IR without Office rendering."""

    from docreconstruct.pipeline import analyze, export

    try:
        document = analyze(source, engines=_split_engines(engines), fusion=fusion)
        if output:
            written = export(document, output, output_format="json")
            console.print(f"[green]Wrote[/green] {written}")
        else:
            typer.echo(document.model_dump_json(indent=2))
    except (DocReconstructError, ImportError, KeyError, RuntimeError, ValueError, OSError) as exc:
        _fail(exc)


@cli.command("providers")
def providers_command() -> None:
    """List installed provider adapters and their declared capabilities."""

    try:
        from docreconstruct.providers import registry

        payload = [
            registry.get(name).capabilities.model_dump(mode="json") for name in registry.names()
        ]
        typer.echo(_json(payload))
    except (ImportError, RuntimeError, ValueError) as exc:
        _fail(exc)


@cli.command("provider-recommend")
def provider_recommend_command(
    source: Path | None = typer.Argument(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Optional source used to infer the input format.",
    ),
    input_format: str | None = typer.Option(None, "--input-format"),
    execution: str = typer.Option(
        "cloud",
        "--execution",
        help="Preferred execution: cloud, local, hybrid, or saved.",
    ),
    languages: str | None = typer.Option(None, "--languages"),
    handwriting: bool = typer.Option(False, "--handwriting"),
    formulas: bool = typer.Option(False, "--formulas"),
    tables: bool = typer.Option(False, "--tables"),
    charts: bool = typer.Option(False, "--charts"),
    distorted_photo: bool = typer.Option(False, "--distorted-photo"),
    dewarping: bool = typer.Option(False, "--dewarping"),
    commercial_use: bool = typer.Option(False, "--commercial-use"),
    include_incompatible: bool = typer.Option(False, "--include-incompatible"),
    output: Path | None = typer.Option(None, "--output", "-o"),
) -> None:
    """Rank installed adapters by declared OCR/Markdown capabilities."""

    try:
        from docreconstruct.providers import (
            CapabilityRequest,
            ProviderExecutionMode,
            recommend_providers,
        )

        execution_modes = {
            "cloud": [ProviderExecutionMode.API],
            "local": [ProviderExecutionMode.LOCAL],
            "hybrid": [ProviderExecutionMode.LOCAL, ProviderExecutionMode.API],
            "saved": [ProviderExecutionMode.SAVED],
        }
        normalized_execution = execution.strip().casefold()
        if normalized_execution not in execution_modes:
            raise ValueError("--execution must be cloud, local, hybrid, or saved")
        inferred_format = input_format or (source.suffix if source else None)
        requested_languages = [
            item.strip() for item in (languages or "").split(",") if item.strip()
        ]
        request = CapabilityRequest(
            input_format=inferred_format,
            languages=requested_languages,
            multilingual=len(requested_languages) > 1,
            handwriting=handwriting,
            formulas=formulas,
            tables=tables,
            charts=charts,
            layout=True,
            reading_order=True,
            distorted_photos=distorted_photo,
            dewarping=dewarping,
            markdown=True,
            execution_modes=execution_modes[normalized_execution],
            commercial_use=commercial_use,
        )
        recommendations = recommend_providers(
            request,
            include_incompatible=include_incompatible,
        )
        payload = _json(
            [recommendation.model_dump(mode="json") for recommendation in recommendations]
        )
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(payload + "\n", encoding="utf-8")
            console.print(f"[green]Wrote[/green] {output.resolve()}")
        else:
            typer.echo(payload)
    except (ImportError, RuntimeError, ValueError, OSError) as exc:
        _fail(exc)


@cli.command("route")
def route_command(
    source: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False, readable=True),
    engines: str | None = typer.Option(None, "--engines", "--engine", "-e"),
    confidence_threshold: float = typer.Option(0.75, "--confidence-threshold", min=0.0, max=1.0),
    force_elements: str | None = typer.Option(
        None, "--force-elements", help="Comma-separated element IDs for selective repair."
    ),
    output: Path | None = typer.Option(None, "--output", "-o"),
) -> None:
    """Create a cost-aware page/region routing plan without running extra engines."""

    from docreconstruct.pipeline import analyze
    from docreconstruct.routing import RoutingPolicy, build_routing_plan

    try:
        document = analyze(source, engines=_split_engines(engines))
        forced = [item.strip() for item in (force_elements or "").split(",") if item.strip()]
        plan = build_routing_plan(
            document,
            policy=RoutingPolicy(confidence_threshold=confidence_threshold),
            force_element_ids=forced,
        )
        payload = plan.model_dump_json(indent=2)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(payload + "\n", encoding="utf-8")
            console.print(f"[green]Wrote[/green] {output.resolve()}")
        else:
            typer.echo(payload)
    except (DocReconstructError, ImportError, KeyError, RuntimeError, ValueError, OSError) as exc:
        _fail(exc)


@cli.command("formats")
def formats_command() -> None:
    """List registered renderers and whether optional dependencies are present."""

    from docreconstruct.renderers import registry

    payload = []
    for name in registry.formats():
        renderer = registry.get(name)
        payload.append(
            {
                "name": name,
                "extension": renderer.extension,
                "media_type": renderer.media_type,
                "available": renderer.is_available(),
            }
        )
    typer.echo(_json(payload))


@cli.command("schema")
def schema_command(
    kind: str = typer.Option(
        "document",
        "--kind",
        help="Schema kind: document, provider-capabilities, extraction-run, or training-dataset.",
    ),
    output: Path | None = typer.Option(None, "--output", "-o"),
) -> None:
    """Print or write a public JSON Schema."""

    from pydantic import BaseModel

    from docreconstruct.extraction import ExtractionRunManifest
    from docreconstruct.ir import Document
    from docreconstruct.providers import ProviderCapabilities
    from docreconstruct.training import DatasetManifest

    models: dict[str, type[BaseModel]] = {
        "document": Document,
        "provider-capabilities": ProviderCapabilities,
        "extraction-run": ExtractionRunManifest,
        "training-dataset": DatasetManifest,
    }
    normalized_kind = kind.strip().casefold()
    if normalized_kind not in models:
        _fail(ValueError(f"--kind must be one of: {', '.join(models)}"))
        return
    payload = json.dumps(
        models[normalized_kind].model_json_schema(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
        console.print(f"[green]Wrote[/green] {output.resolve()}")
    else:
        typer.echo(payload)


@cli.command("compare")
def compare_command(
    reference: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False),
    candidate: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False),
    profile: str = typer.Option("balanced", "--profile"),
    render_backend: str = typer.Option(
        "native",
        "--render-backend",
        help=("DOCX visual-render backend: native (no external process), auto, or libreoffice."),
    ),
    renderer_path: Path | None = typer.Option(
        None,
        "--renderer-path",
        help="Explicit LibreOffice/soffice executable; requires an external render backend.",
    ),
    output: Path | None = typer.Option(None, "--output", "-o"),
) -> None:
    """Compare supported artifacts using the metrics available for each pair."""

    try:
        from docreconstruct import evaluation

        normalized_backend = render_backend.strip().casefold()
        if normalized_backend not in {"native", "auto", "libreoffice"}:
            raise ValueError("--render-backend must be native, auto, or libreoffice")
        if renderer_path is not None:
            if normalized_backend == "native":
                raise ValueError("--renderer-path requires --render-backend auto or libreoffice")
            if not renderer_path.is_file():
                raise ValueError(f"--renderer-path is not a file: {renderer_path}")
        result = evaluation.evaluate(
            reference,
            candidate,
            profile=profile,
            render_backend=normalized_backend,
            renderer_path=renderer_path,
        )
        payload = _json(result)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(payload + "\n", encoding="utf-8")
            console.print(f"[green]Wrote[/green] {output.resolve()}")
        else:
            typer.echo(payload)
    except (DocReconstructError, ImportError, RuntimeError, ValueError, OSError) as exc:
        _fail(exc)


@cli.command("benchmark")
def benchmark_command(
    dataset: Path = typer.Argument(..., exists=True, readable=True),
    output: Path = typer.Option(Path("benchmark-report.json"), "--output", "-o"),
    profile: str = typer.Option("balanced", "--profile"),
) -> None:
    """Run a manifest-backed reproducible fidelity benchmark."""

    try:
        from docreconstruct import evaluation

        result = evaluation.run_benchmark(dataset, profile=profile)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(_json(result) + "\n", encoding="utf-8")
        console.print(f"[green]Wrote[/green] {output.resolve()}")
    except (DocReconstructError, ImportError, RuntimeError, ValueError, OSError) as exc:
        _fail(exc)


@cli.command("benchmark-ocr")
def benchmark_ocr_command(
    dataset: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="OCR benchmark manifest or directory containing ocr-benchmark.json.",
    ),
    output: Path = typer.Option(Path("ocr-benchmark-report.json"), "--output", "-o"),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        help="Directory for generated Markdown candidates.",
    ),
    profile: str | None = typer.Option(None, "--profile"),
    seed: int | None = typer.Option(None, "--seed"),
    allow_cloud: bool = typer.Option(
        False,
        "--allow-cloud",
        help="Explicitly allow cloud/hybrid benchmark cases to upload documents.",
    ),
    record_timings: bool = typer.Option(False, "--record-timings"),
    fail_fast: bool = typer.Option(False, "--fail-fast"),
    load_provider_plugins: bool = typer.Option(False, "--load-provider-plugins"),
) -> None:
    """Run OCR providers end to end and report scores by document/degradation slice."""

    try:
        from docreconstruct import evaluation
        from docreconstruct.providers import registry

        if load_provider_plugins:
            registry.load_entry_points()
        report = evaluation.run_ocr_benchmark(
            dataset,
            output_dir=output_dir,
            output_path=output,
            profile=profile,
            seed=seed,
            allow_cloud=allow_cloud,
            registry=registry,
            record_timings=record_timings,
            fail_fast=fail_fast,
        )
        console.print(f"[green]Wrote[/green] {output.resolve()}")
        mean = "unavailable" if report.mean_score is None else f"{report.mean_score:.6f}"
        console.print(
            f"cases: {report.successful_cases} succeeded, {report.failed_cases} failed; "
            f"mean score: {mean}"
        )
        if report.failed_cases:
            raise typer.Exit(code=3)
    except typer.Exit:
        raise
    except (DocReconstructError, ImportError, RuntimeError, ValueError, OSError) as exc:
        _fail(exc)


@cli.command("dataset-validate")
def dataset_validate_command(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="License-aware OCR training dataset manifest (JSON).",
    ),
    lane: str = typer.Option(
        "research-only",
        "--lane",
        help="Allowed use lane: commercial-permissive, research-only, or private-opt-in.",
    ),
    output: Path | None = typer.Option(None, "--output", "-o"),
    verify_hashes: bool = typer.Option(True, "--verify-hashes/--no-verify-hashes"),
) -> None:
    """Check rights, files, hashes, duplicates, and train/test leakage."""

    try:
        from docreconstruct.training import (
            DataUsageLane,
            load_dataset_manifest,
            validate_dataset,
        )

        selected_lane = DataUsageLane(lane.strip().casefold())
        result = validate_dataset(
            load_dataset_manifest(manifest),
            lane=selected_lane,
            verify_hashes=verify_hashes,
        )
        payload = _json(result)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(payload + "\n", encoding="utf-8")
            console.print(f"[green]Wrote[/green] {output.resolve()}")
        else:
            typer.echo(payload)
        if not result.valid:
            raise typer.Exit(code=3)
    except typer.Exit:
        raise
    except (ImportError, RuntimeError, ValueError, OSError) as exc:
        _fail(exc)


@cli.command("train-plan")
def train_plan_command(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    backend: str = typer.Option("olmocr", "--backend", "-b"),
    lane: str = typer.Option("research-only", "--lane"),
    output: Path = typer.Option(Path("training-plan.json"), "--output", "-o"),
) -> None:
    """Create a reproducible fine-tuning plan without downloading or training a model."""

    try:
        from docreconstruct.training import (
            DataUsageLane,
            build_training_plan,
            load_dataset_manifest,
        )

        plan = build_training_plan(
            load_dataset_manifest(manifest),
            backend=backend,
            lane=DataUsageLane(lane.strip().casefold()),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(_json(plan) + "\n", encoding="utf-8")
        console.print(f"[green]Wrote[/green] {output.resolve()}")
        if not plan.executable:
            console.print(
                "[yellow]Dry run:[/yellow] install an opt-in docreconstruct.trainers "
                "backend before executing fine-tuning."
            )
    except (ImportError, RuntimeError, ValueError, OSError) as exc:
        _fail(exc)


@cli.callback()
def _callback(
    version: bool = typer.Option(False, "--version", help="Show the package version and exit."),
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()


_COMMANDS = {
    "reconstruct",
    "extract",
    "hybrid",
    "analyze",
    "route",
    "providers",
    "provider-recommend",
    "formats",
    "schema",
    "compare",
    "benchmark",
    "benchmark-ocr",
    "dataset-validate",
    "train-plan",
}


def app() -> None:
    """Console-script wrapper supporting both bare and explicit reconstruction syntax."""

    arguments = sys.argv[1:]
    if arguments and arguments[0] not in _COMMANDS and not arguments[0].startswith("-"):
        sys.argv.insert(1, "reconstruct")
    cli()


if __name__ == "__main__":  # pragma: no cover
    app()
