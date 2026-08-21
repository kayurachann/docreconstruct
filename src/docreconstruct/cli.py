"""Command-line interface for local, reproducible reconstruction jobs."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
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


# On-device engines `convert` may select without any consent flag, in
# preference order. Membership still requires the executable to be present.
_CONVERT_LOCAL_ENGINES: tuple[str, ...] = ("tesseract_local",)


def _installed_local_engines() -> list[str]:
    """Return bundled on-device OCR engines whose executables are present."""

    available: list[str] = []
    for name in _CONVERT_LOCAL_ENGINES:
        if name == "tesseract_local":
            try:
                from docreconstruct.providers.tesseract_local import _find_tesseract

                _find_tesseract(None)
            except Exception:  # noqa: BLE001 - absence, not failure
                continue
            available.append(name)
    return available


def _convert_engine_plan(source: Path) -> tuple[list[str], str | None]:
    """Choose default engines, preferring a loss-free native PDF text layer.

    A born-digital PDF carries exact wording and geometry; OCR-ing its raster
    would discard both. Image-heavy PDFs that still carry a text layer keep
    the OCR default (their thousands of embedded images make the native route
    much slower) but return a note so the user can opt in. Classification is
    best-effort — any analysis failure simply falls back to installed OCR.
    """

    engines: list[str] = []
    note: str | None = None
    if source.suffix.casefold() == ".pdf":
        try:
            from docreconstruct.preprocessing import SourceKind, analyze_source

            analysis = analyze_source(source)
        except Exception:  # noqa: BLE001 - classification is best-effort
            analysis = None
        if analysis is not None and analysis.pages:
            if analysis.kind is SourceKind.NATIVE:
                engines.append("native_pdf")
            elif any(page.native_characters >= 20 for page in analysis.pages):
                note = (
                    "This PDF carries an embedded text layer alongside heavy image "
                    "content. OCR is the default here; --ocr-provider native_pdf "
                    "uses the exact embedded wording instead (slower on "
                    "formula-heavy files)."
                )
    engines.extend(_installed_local_engines())
    return engines, note


def _convert_extraction_mode(provider_names: list[str], registry: Any) -> Any:
    """Pick the narrowest extraction mode the named providers can satisfy."""

    from docreconstruct.extraction import ExtractionMode
    from docreconstruct.providers import ProviderExecutionMode

    local = hosted = False
    for name in provider_names:
        capabilities = registry.get_capabilities(name)
        if capabilities is None:
            raise KeyError(f"unknown provider {name!r}")
        if ProviderExecutionMode.LOCAL in capabilities.execution_modes:
            local = True
        else:
            hosted = True
    if hosted:
        return ExtractionMode.HYBRID if local else ExtractionMode.CLOUD
    return ExtractionMode.LOCAL


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
    from docreconstruct.reconstruction import TargetFormat

    def _known_format(value: str) -> TargetFormat | None:
        try:
            return TargetFormat.parse(value)
        except ValueError:
            return None

    # An explicit --output-format wins over the filename, which silently wrote
    # HTML into a file called report.docx. Only complain when both are named
    # and genuinely disagree; an unrecognized suffix stays the caller's business.
    if output_format and output is not None and output.suffix:
        chosen = _known_format(output_format)
        implied = _known_format(output.suffix)
        if (
            chosen is not None
            and implied is not None
            and TargetFormat.AUTO not in {chosen, implied}
            and chosen is not implied
        ):
            _fail(
                ValueError(
                    f"--output-format {output_format!r} conflicts with the {output.suffix!r} "
                    f"extension of {output.name!r}; drop one or make them agree"
                )
            )

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


@cli.command("convert")
def convert_command(
    source: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="PDF or raster scan to convert into an editable document.",
    ),
    output: Path | None = typer.Argument(
        None,
        help="Destination file; defaults to SOURCE with a .docx extension.",
    ),
    ocr_provider: str | None = typer.Option(
        None,
        "--ocr-provider",
        "--ocr-providers",
        help=(
            "Override the auto-detected local OCR engine with a provider or "
            "comma-separated ordered fallback providers."
        ),
    ),
    allow_cloud: bool = typer.Option(
        False,
        "--allow-cloud",
        help="Explicitly allow an overriding hosted provider to receive document bytes.",
    ),
    languages: str | None = typer.Option(
        None,
        "--languages",
        help="Comma-separated OCR language hints, e.g. vie,eng.",
    ),
    keep_intermediates: bool = typer.Option(
        False,
        "--keep-intermediates",
        help=(
            "Keep the generated Markdown and OCR JSON beside OUTPUT so the "
            "Markdown can be reviewed, corrected, and re-run with `hybrid`."
        ),
    ),
    strict_qa: bool = typer.Option(
        False,
        "--strict-qa",
        help="Exit with code 3 when any hybrid QA gate fails instead of warning.",
    ),
    load_provider_plugins: bool = typer.Option(
        False,
        "--load-provider-plugins",
        help="Opt in to installed docreconstruct OCR provider entry points.",
    ),
) -> None:
    """Convert a scan or PDF to an editable document with one command.

    Born-digital PDFs use their exact embedded text layer directly; scans run
    through an installed local OCR engine (Tesseract is detected
    automatically). Either way the Markdown content authority and JSON
    geometry evidence are generated for you, then the document is rebuilt
    through the same three-authority pipeline as `hybrid` and its QA score is
    printed.
    """

    import tempfile
    from contextlib import nullcontext

    from docreconstruct.extraction import ExtractionMode, extract_to_markdown
    from docreconstruct.providers import registry
    from docreconstruct.reconstruction.hybrid_job import run_hybrid_job

    try:
        source = source.expanduser().resolve()
        destination = (output or source.with_suffix(".docx")).expanduser().resolve()
        if destination == source:
            raise ValueError("OUTPUT would overwrite SOURCE; pass a different destination")
        if load_provider_plugins:
            registry.load_entry_points()
        provider_names = [
            item.strip()
            for item in (ocr_provider or "").split(",")
            if item.strip() and item.strip().casefold() != "auto"
        ]
        if provider_names:
            mode = _convert_extraction_mode(provider_names, registry)
        else:
            provider_names, engine_note = _convert_engine_plan(source)
            if not provider_names:
                raise RuntimeError(
                    "no local OCR engine was found; install Tesseract "
                    "(https://tesseract-ocr.github.io/tessdoc/Installation.html) "
                    "or name an engine explicitly with --ocr-provider"
                )
            if engine_note:
                console.print(engine_note)
            mode = ExtractionMode.LOCAL
        if mode is not ExtractionMode.LOCAL and not allow_cloud:
            raise ValueError(
                "--ocr-provider selected a hosted service that would receive the "
                "document; pass --allow-cloud to permit that, or choose a local engine"
            )
        language_names = [item.strip() for item in (languages or "").split(",") if item.strip()]
        # Hybrid reconstruction crops figures from the source pixels, so raw
        # image bytes in the native evidence would be pure dead weight.
        extraction_provider_options: dict[str, dict[str, Any]] | None = None
        if "native_pdf" in provider_names:
            extraction_provider_options = {name: {} for name in provider_names}
            extraction_provider_options["native_pdf"] = {"include_image_bytes": False}
        workspace = (
            nullcontext(str(destination.parent / f"{destination.stem}.convert"))
            if keep_intermediates
            else tempfile.TemporaryDirectory(prefix="docreconstruct-convert-")
        )
        with workspace as raw_workdir:
            intermediates = Path(raw_workdir)
            intermediates.mkdir(parents=True, exist_ok=True)
            markdown_path = intermediates / f"{source.stem}.ocr.md"
            extraction = extract_to_markdown(
                source,
                output=markdown_path,
                mode=mode,
                providers=provider_names,
                allow_cloud=allow_cloud,
                languages=language_names,
                require_geometry=True,
                provider_options=extraction_provider_options,
                evidence_directory=intermediates / "evidence",
                registry=registry,
            )
            if not extraction.evidence_outputs:
                raise RuntimeError("OCR produced no canonical geometry evidence")
            (intermediates / "extraction.run.json").write_text(
                extraction.manifest.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
            evidence_paths = tuple(path.resolve() for path in extraction.evidence_outputs)
            job = run_hybrid_job(
                markdown_path,
                source,
                evidence=evidence_paths,
                evidence_provider_hints={str(path): "json" for path in evidence_paths},
                output=destination,
            )
    except (DocReconstructError, ImportError, KeyError, RuntimeError, ValueError, OSError) as exc:
        _fail(exc)
        return
    validation = job.validation
    console.print(f"[green]Wrote[/green] {job.reconstruction.output.path}")
    console.print(
        "OCR: "
        + ", ".join(extraction.manifest.successful_providers)
        + f"; mode: {extraction.manifest.mode.value}"
    )
    console.print(
        f"QA gates: {validation.score * 100:.2f}% "
        f"({validation.passed_gates}/{validation.measured_gates} measured gates)"
    )
    if keep_intermediates:
        console.print(f"[green]Kept intermediates[/green] {intermediates}")
        console.print(f"  Markdown content authority: {markdown_path}")
        for path in evidence_paths:
            console.print(f"  OCR evidence: {path}")
        console.print(
            "Fix the Markdown by hand, then rebuild with: docreconstruct hybrid "
            f'"{markdown_path}" "{source}" '
            + " ".join(f'-E "{path}"' for path in evidence_paths)
            + f' -o "{destination}"'
        )
    else:
        console.print(
            "Pass --keep-intermediates to keep the generated Markdown and OCR JSON "
            "for review and correction."
        )
    if not validation.passed:
        message = (
            f"{validation.measured_gates - validation.passed_gates} QA gate(s) failed; "
            "automatic quality is bounded by the OCR engine. Use --keep-intermediates "
            "to review and correct the generated Markdown."
        )
        if strict_qa:
            console.print(f"[red]{message}[/red]")
            raise typer.Exit(code=3)
        console.print(f"[yellow]{message}[/yellow]")


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
    alignment_report: Path | None = typer.Option(
        None,
        "--alignment-report",
        help=(
            "Optional content-safe JSON trace for every evidence-alignment decision; "
            "raw text, source paths, page pixels, and provider element IDs are excluded."
        ),
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
            alignment_report=alignment_report,
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
    if job.alignment_report is not None:
        console.print(f"[green]Wrote alignment trace[/green] {job.alignment_report}")
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
    timings = job.phase_seconds
    if timings:
        evidence_seconds = float(timings.get("prepare.evidence_load", 0.0)) + float(
            timings.get("prepare.evidence_match", 0.0)
        )
        console.print(
            "pipeline timing: "
            f"total {float(timings.get('job.total', 0.0)):.3f}s; "
            f"scan {float(timings.get('prepare.scan', 0.0)):.3f}s; "
            f"evidence {evidence_seconds:.3f}s; "
            f"DOCX {float(timings.get('reconstruct.docx_render', 0.0)):.3f}s; "
            f"native QA {float(timings.get('qa.native', 0.0)):.3f}s; "
            f"render QA {float(timings.get('qa.render', 0.0)):.3f}s; "
            f"visual QA {float(timings.get('qa.visual', 0.0)):.3f}s"
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

        report = evaluation.run_benchmark(dataset, profile=profile)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(_json(report) + "\n", encoding="utf-8")
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


@cli.command("benchmark-source")
def benchmark_source_command(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help=("Source-only benchmark manifest or directory containing source-benchmark.json."),
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Override the manifest output directory.",
    ),
    subset: str | None = typer.Option(
        None,
        "--subset",
        help="Dataset slice: all, hard, or an exact page_attribute.subset value.",
    ),
    shard_index: int = typer.Option(0, "--shard-index", min=0),
    shard_count: int = typer.Option(1, "--shard-count", min=1),
    system: list[str] = typer.Option(
        [],
        "--system",
        help="Run only this configured system; repeat for more than one.",
    ),
    resume: bool = typer.Option(
        True,
        "--resume/--no-resume",
        help="Reuse only checkpoints whose command and exact input fingerprint still match.",
    ),
    official_evaluator: bool = typer.Option(
        True,
        "--official-evaluator/--no-official-evaluator",
        help="Run the separately pinned official evaluator after all predictions exist.",
    ),
) -> None:
    """Run isolated parsers on identical source pages and retain every failure."""

    try:
        from docreconstruct import evaluation

        report = evaluation.run_source_benchmark(
            manifest,
            output_dir=output_dir,
            subset=subset,
            shard_index=shard_index,
            shard_count=shard_count,
            resume=resume,
            run_official_evaluator=official_evaluator,
            systems=system or None,
        )
        destination = (
            output_dir.resolve()
            if output_dir is not None
            else evaluation.load_source_benchmark_manifest(manifest).output_dir
        )
        if shard_count > 1:
            destination = destination / "shards" / f"{shard_index:05d}-of-{shard_count:05d}"
        console.print(
            f"[green]Wrote[/green] {(destination / 'source-benchmark-report.json').resolve()}"
        )
        for summary in report.summaries:
            console.print(
                f"{summary.system}: {summary.successful_cases}/{summary.total_cases} succeeded; "
                f"{summary.failed_cases} failures retained in the evaluator denominator"
            )
        evaluator_failed = any(
            result.status is not evaluation.SourceRunStatus.SUCCESS
            for result in report.evaluator_results
        )
        if report.failed_cases or evaluator_failed:
            raise typer.Exit(code=3)
    except typer.Exit:
        raise
    except (DocReconstructError, ImportError, RuntimeError, ValueError, OSError) as exc:
        _fail(exc)


@cli.command("convert-omnidocbench")
def convert_omnidocbench_command(
    dataset_root: Path = typer.Option(
        ...,
        "--dataset-root",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Private OmniDocBench checkout or downloaded dataset root.",
    ),
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        file_okay=False,
        help="Directory for canonical JSON sidecars and the conversion report.",
    ),
    manifest: Path | None = typer.Option(
        None,
        "--manifest",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Optional corpus lock containing the pinned revision and input SHA-256.",
    ),
    annotations: Path | None = typer.Option(
        None,
        "--annotations",
        help="Annotation JSON path, absolute or relative to --dataset-root.",
    ),
    images_directory: Path | None = typer.Option(
        None,
        "--images",
        "--images-directory",
        help="Raster directory, absolute or relative to --dataset-root.",
    ),
    dataset_revision: str | None = typer.Option(
        None,
        "--dataset-revision",
        help="Immutable dataset revision; may instead come from --manifest.",
    ),
    report: Path | None = typer.Option(
        None,
        "--report",
        help="Report path; defaults to OUTPUT/conversion-report.json.",
    ),
) -> None:
    """Project private OmniDocBench annotations into audited canonical JSON."""

    try:
        from docreconstruct import evaluation

        root = dataset_root.expanduser().resolve()
        manifest_payload: Mapping[str, Any] = {}
        upstream: Mapping[str, Any] = {}
        expected_annotations_sha256: str | None = None
        expected_image_sha256: dict[str, str] = {}
        manifest_annotation_path: str | None = None
        manifest_revision: str | None = None
        if manifest is not None:
            loaded_manifest = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(loaded_manifest, dict):
                raise ValueError("--manifest root must be a JSON object")
            manifest_payload = loaded_manifest
            upstream_value = manifest_payload.get("upstream", {})
            if not isinstance(upstream_value, dict):
                raise ValueError("--manifest upstream must be a JSON object")
            upstream = upstream_value
            revision_value = upstream.get("revision")
            if isinstance(revision_value, str) and revision_value.strip():
                manifest_revision = revision_value.strip()
            annotation_value = upstream.get("annotation_file", {})
            if isinstance(annotation_value, dict):
                path_value = annotation_value.get("path")
                sha_value = annotation_value.get("sha256")
                if isinstance(path_value, str) and path_value.strip():
                    manifest_annotation_path = path_value
                if isinstance(sha_value, str) and sha_value.strip():
                    expected_annotations_sha256 = sha_value.strip().casefold()
            cases_value = manifest_payload.get("cases", [])
            if isinstance(cases_value, list):
                for case in cases_value:
                    if not isinstance(case, dict):
                        continue
                    image_name = case.get("upstream_image")
                    image_value = case.get("image")
                    image_sha = image_value.get("sha256") if isinstance(image_value, dict) else None
                    if isinstance(image_name, str) and isinstance(image_sha, str):
                        expected_image_sha256[Path(image_name).name] = image_sha.casefold()

        selected_revision = (dataset_revision or manifest_revision or "").strip()
        if not selected_revision:
            raise ValueError("provide --dataset-revision or a manifest with upstream.revision")
        if (
            dataset_revision is not None
            and manifest_revision is not None
            and dataset_revision.strip() != manifest_revision
        ):
            raise ValueError("--dataset-revision does not match --manifest upstream.revision")

        annotation_value = annotations or (
            Path(manifest_annotation_path) if manifest_annotation_path else None
        )
        if annotation_value is not None:
            annotation_path = (
                annotation_value if annotation_value.is_absolute() else root / annotation_value
            ).resolve()
        else:
            candidates = [
                root / "OmniDocBench.json",
                root / "OmniDocBench_demo.json",
                root / "demo_data" / "omnidocbench_demo" / "OmniDocBench_demo.json",
            ]
            existing = [candidate.resolve() for candidate in candidates if candidate.is_file()]
            if len(existing) != 1:
                raise ValueError("could not select exactly one annotation JSON; pass --annotations")
            annotation_path = existing[0]
        if not annotation_path.is_file():
            raise ValueError(f"annotation JSON does not exist: {annotation_path}")

        if images_directory is not None:
            images_path = (
                images_directory if images_directory.is_absolute() else root / images_directory
            ).resolve()
        else:
            image_candidates = [annotation_path.parent / "images", root / "images"]
            existing_image_directories = list(
                dict.fromkeys(
                    candidate.resolve() for candidate in image_candidates if candidate.is_dir()
                )
            )
            if len(existing_image_directories) != 1:
                raise ValueError("could not select exactly one raster directory; pass --images")
            images_path = existing_image_directories[0]
        if not images_path.is_dir():
            raise ValueError(f"raster directory does not exist: {images_path}")

        destination = output.expanduser().resolve()
        destination_report = (
            report.expanduser().resolve()
            if report is not None
            else destination / "conversion-report.json"
        )
        conversion_report = evaluation.convert_omnidocbench_oracle_dataset(
            annotation_path,
            images_directory=images_path,
            output_directory=destination,
            dataset_revision=selected_revision,
            report_path=destination_report,
            expected_annotations_sha256=expected_annotations_sha256,
            expected_image_sha256=expected_image_sha256,
        )
        console.print(f"[green]Wrote[/green] {destination_report}")
        console.print(
            f"pages: {conversion_report.page_count}; annotations: "
            f"{conversion_report.annotation_count}; projected: "
            f"{conversion_report.projected_element_count}; ignored but audited: "
            f"{conversion_report.ignored_count}; warnings: "
            f"{conversion_report.warning_count}"
        )
    except (ImportError, RuntimeError, TypeError, ValueError, OSError) as exc:
        _fail(exc)


@cli.command("benchmark-reconstruction")
def benchmark_reconstruction_command(
    dataset: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help=(
            "Three-source benchmark manifest or directory containing reconstruction-benchmark.json."
        ),
    ),
    output: Path = typer.Option(
        Path("reconstruction-benchmark-report.json"),
        "--output",
        "-o",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        help="Directory for fresh per-run DOCX, QA, and optional render artifacts.",
    ),
    seed: int | None = typer.Option(None, "--seed"),
    qa_backend: str = typer.Option(
        "native",
        "--qa-backend",
        "--render-backend",
        help="QA backend: native (no external process), auto, or libreoffice.",
    ),
    qa_renderer_path: Path | None = typer.Option(
        None,
        "--qa-renderer-path",
        "--renderer-path",
        help="Explicit LibreOffice/soffice executable for rendered benchmark mode.",
    ),
    minimum_visual_score: float | None = typer.Option(
        None,
        "--min-visual-score",
        min=0.0,
        max=1.0,
    ),
    save_render_artifacts: bool = typer.Option(
        False,
        "--save-render-artifacts",
        help="Retain source, candidate, and difference PNGs for rendered cases.",
    ),
    allow_remote_assets: bool = typer.Option(
        False,
        "--allow-remote-assets",
        help=(
            "Runtime opt-in for remote Markdown assets. A manifest request is also "
            "required; manifests cannot enable network access by themselves."
        ),
    ),
    fail_fast: bool = typer.Option(False, "--fail-fast"),
) -> None:
    """Generate DOCX candidates from all three authorities and score the full job."""

    normalized_backend = qa_backend.strip().casefold()
    if normalized_backend not in {"native", "auto", "libreoffice"}:
        _fail(ValueError("--qa-backend must be native, auto, or libreoffice"))
        return
    if qa_renderer_path is not None and normalized_backend == "native":
        _fail(ValueError("--qa-renderer-path requires --qa-backend auto or libreoffice"))
        return
    if qa_renderer_path is not None and not qa_renderer_path.is_file():
        _fail(ValueError(f"--qa-renderer-path is not a file: {qa_renderer_path}"))
        return
    if minimum_visual_score is not None and normalized_backend == "native":
        _fail(ValueError("--min-visual-score requires --qa-backend auto or libreoffice"))
        return
    try:
        from docreconstruct import evaluation

        report = evaluation.run_reconstruction_benchmark(
            dataset,
            output_dir=output_dir,
            output_path=output,
            seed=seed,
            render_backend=normalized_backend,
            renderer_path=qa_renderer_path,
            minimum_visual_score=minimum_visual_score,
            save_render_artifacts=save_render_artifacts,
            allow_remote_assets=allow_remote_assets,
            fail_fast=fail_fast,
        )
        console.print(f"[green]Wrote[/green] {output.resolve()}")
        quality = (
            f"incomplete ({report.quality_complete_cases}/{len(report.results)} cases; "
            "rendered fidelity required)"
            if report.mean_quality_score is None
            else f"{report.mean_quality_score:.6f}"
        )
        operational = (
            "unavailable"
            if report.operational_success_rate is None
            else f"{report.operational_success_rate:.6f}"
        )
        validation_gates = (
            "unavailable"
            if report.mean_validation_gate_score is None
            else f"{report.mean_validation_gate_score:.6f}"
        )
        console.print(
            f"cases: {report.successful_cases} succeeded, {report.failed_cases} failed, "
            f"{report.accepted_cases} accepted; quality: {quality}; "
            f"validation gates: {validation_gates}; "
            f"operational: {operational}"
        )
        if report.failed_cases or report.accepted_cases != len(report.results):
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
    "convert",
    "extract",
    "hybrid",
    "analyze",
    "route",
    "providers",
    "provider-recommend",
    "formats",
    "schema",
    "compare",
    "convert-omnidocbench",
    "benchmark",
    "benchmark-ocr",
    "benchmark-source",
    "benchmark-reconstruction",
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
