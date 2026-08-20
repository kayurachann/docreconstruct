"""Local Tesseract OCR adapter with line geometry and bounded subprocesses."""

from __future__ import annotations

import csv
import io
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageSequence

from docreconstruct.ir import (
    BBox,
    Document,
    Element,
    ElementType,
    Page,
    Provenance,
    SourceType,
    TextCandidate,
)

from ._utils import document_id
from .base import (
    Provider,
    ProviderCapabilities,
    ProviderContext,
    ProviderCost,
    ProviderCredentialRequirement,
    ProviderDependencyError,
    ProviderExecutionMode,
    ProviderInput,
    ProviderInputError,
    ProviderLicense,
    ProviderPrivacy,
    ProviderResult,
)

_LANGUAGE_TOKEN = re.compile(r"^[A-Za-z0-9_+-]+$")
_MAX_TSV_BYTES = 64 * 1024 * 1024
_WINDOWS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_LANGUAGE_ALIASES = {
    "en": "eng",
    "eng": "eng",
    "ru": "rus",
    "rus": "rus",
    "vi": "vie",
    "vie": "vie",
    "zh": "chi_sim",
    "zh_cn": "chi_sim",
    "zh-cn": "chi_sim",
    "chi_sim": "chi_sim",
    "zh_tw": "chi_tra",
    "zh-tw": "chi_tra",
    "chi_tra": "chi_tra",
}


@dataclass(frozen=True, slots=True)
class _RasterPage:
    path: Path
    width: int
    height: int


@dataclass(slots=True)
class _LineAccumulator:
    words: list[str]
    left: int
    top: int
    right: int
    bottom: int
    weighted_confidence: float = 0.0
    confidence_weight: int = 0

    def add(
        self,
        text: str,
        *,
        left: int,
        top: int,
        width: int,
        height: int,
        confidence: float,
    ) -> None:
        self.words.append(text)
        self.left = min(self.left, left)
        self.top = min(self.top, top)
        self.right = max(self.right, left + width)
        self.bottom = max(self.bottom, top + height)
        weight = max(1, len(text))
        if confidence >= 0:
            self.weighted_confidence += confidence * weight
            self.confidence_weight += weight

    @property
    def confidence(self) -> float | None:
        if not self.confidence_weight:
            return None
        return max(0.0, min(1.0, self.weighted_confidence / self.confidence_weight / 100.0))


class TesseractLocalProvider(Provider):
    """Run an installed Tesseract binary without sending source bytes anywhere."""

    name = "tesseract_local"
    _capabilities = ProviderCapabilities(
        provider=name,
        supported_inputs=["pdf", "png", "jpeg", "tiff", "bmp", "webp"],
        saved_json=False,
        live_inference=True,
        text=True,
        geometry=True,
        reading_order=True,
        multilingual=True,
        layout=True,
        execution_modes=[ProviderExecutionMode.LOCAL],
        markdown=True,
        bounding_boxes=True,
        confidence_scores=True,
        privacy=ProviderPrivacy.NO_TRANSFER,
        license=ProviderLicense(
            name="Apache License 2.0",
            spdx="Apache-2.0",
            open_source=True,
            commercial_use=True,
            restrictions=[
                "Tesseract language data and optional PDF raster dependencies have their own terms."
            ],
        ),
        model_name="Tesseract OCR",
        cost=ProviderCost.FREE,
        credentials=ProviderCredentialRequirement.NONE,
        notes=[
            "Requires an installed Tesseract executable and the requested traineddata files.",
            "Plain OCR lines do not preserve tables or mathematical structure.",
        ],
    )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def normalize(
        self,
        payload: Any,
        *,
        context: ProviderContext | None = None,
    ) -> Document:
        del context
        if isinstance(payload, Document):
            return payload
        try:
            return Document.model_validate(payload)
        except Exception as exc:
            raise ProviderInputError(
                "TesseractLocalProvider.normalize() accepts canonical Document data; "
                "use parse() for live OCR"
            ) from exc

    def parse(
        self,
        source: ProviderInput,
        *,
        context: ProviderContext | None = None,
    ) -> ProviderResult:
        if not isinstance(source, (str, Path)):
            raise ProviderInputError("TesseractLocalProvider expects a local file path")
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise ProviderInputError(f"OCR source does not exist: {path}")

        effective_context = context or ProviderContext(source=str(path))
        options = effective_context.options
        executable = _find_tesseract(options.get("executable"))
        timeout = _finite_option(options, "timeout_seconds", 120.0, minimum=1.0, maximum=600.0)
        pdf_dpi = int(_finite_option(options, "pdf_dpi", 144.0, minimum=72.0, maximum=600.0))
        page_segmentation = int(
            _finite_option(options, "page_segmentation", 3.0, minimum=0.0, maximum=13.0)
        )
        tessdata_dir = _optional_directory(options.get("tessdata_dir"), label="tessdata_dir")
        languages = _language_argument(effective_context, tessdata_dir=tessdata_dir)

        pages: list[Page] = []
        warnings: list[str] = []
        with _raster_pages(path, pdf_dpi=pdf_dpi) as raster_pages:
            for page_number, raster in enumerate(raster_pages, start=1):
                tsv, stderr = _run_tesseract(
                    executable,
                    raster.path,
                    languages=languages,
                    page_segmentation=page_segmentation,
                    tessdata_dir=tessdata_dir,
                    timeout=timeout,
                )
                if stderr.strip():
                    warnings.extend(
                        f"page {page_number}: {line.strip()}"
                        for line in stderr.splitlines()
                        if line.strip()
                    )
                pages.append(
                    _page_from_tsv(
                        tsv,
                        page_number=page_number,
                        width=raster.width,
                        height=raster.height,
                        languages=languages,
                    )
                )

        source_label = effective_context.source or str(path)
        document = Document(
            id=document_id(self.name, effective_context),
            source=source_label,
            pages=pages,
            metadata={
                "provider": self.name,
                "languages": languages,
                "page_segmentation": page_segmentation,
                "pdf_dpi": pdf_dpi,
            },
        )
        return ProviderResult(
            provider=self.name,
            document=document,
            warnings=warnings,
            metadata={"languages": languages},
        )


def _finite_option(
    options: Mapping[str, Any],
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    value = options.get(key, default)
    if isinstance(value, bool):
        raise ProviderInputError(f"{key} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderInputError(f"{key} must be numeric") from exc
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ProviderInputError(f"{key} must be between {minimum:g} and {maximum:g}")
    return result


def _optional_directory(value: Any, *, label: str) -> Path | None:
    if value is None or value == "":
        return None
    directory = Path(str(value)).expanduser().resolve()
    if not directory.is_dir():
        raise ProviderInputError(f"{label} is not a directory: {directory}")
    return directory


def _find_tesseract(configured: Any) -> Path:
    candidates: list[Path] = []
    if configured not in (None, ""):
        candidates.append(Path(str(configured)).expanduser())
    env_value = os.environ.get("TESSERACT_CMD")
    if env_value:
        candidates.append(Path(env_value).expanduser())
    discovered = shutil.which("tesseract")
    if discovered:
        candidates.append(Path(discovered))
    if os.name == "nt":
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        candidates.append(Path(program_files) / "Tesseract-OCR" / "tesseract.exe")
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    raise ProviderDependencyError(
        "Tesseract executable was not found. Install Tesseract, put it on PATH, "
        "or set TESSERACT_CMD."
    )


def _language_argument(context: ProviderContext, *, tessdata_dir: Path | None) -> str:
    configured = context.options.get("language")
    if configured is not None:
        requested = [part.strip() for part in str(configured).split("+") if part.strip()]
    else:
        raw = context.metadata.get("languages", [])
        requested = [str(item).strip() for item in raw] if isinstance(raw, list) else []
    normalized: list[str] = []
    for item in requested or ["eng"]:
        token = _LANGUAGE_ALIASES.get(item.casefold(), item)
        if not _LANGUAGE_TOKEN.fullmatch(token):
            raise ProviderInputError(f"invalid Tesseract language token: {item!r}")
        if token not in normalized:
            normalized.append(token)

    if tessdata_dir is not None:
        available = {path.stem for path in tessdata_dir.glob("*.traineddata")}
        missing = [token for token in normalized if token not in available]
        if missing:
            raise ProviderDependencyError(
                "missing Tesseract traineddata: " + ", ".join(sorted(missing))
            )
    return "+".join(normalized)


@contextmanager
def _raster_pages(path: Path, *, pdf_dpi: int) -> Iterator[list[_RasterPage]]:
    with tempfile.TemporaryDirectory(prefix="docreconstruct-tesseract-") as temporary:
        root = Path(temporary)
        suffix = path.suffix.casefold()
        pages: list[_RasterPage] = []
        if suffix == ".pdf":
            try:
                import pymupdf as fitz
            except ImportError as exc:
                raise ProviderDependencyError(
                    "Tesseract PDF input requires optional dependency PyMuPDF"
                ) from exc
            try:
                pdf = fitz.open(str(path))
            except Exception as exc:
                raise ProviderInputError(f"could not open PDF for OCR: {exc}") from exc
            try:
                for index in range(pdf.page_count):
                    pixmap = pdf.load_page(index).get_pixmap(dpi=pdf_dpi, alpha=False)
                    image_path = root / f"page-{index + 1}.png"
                    pixmap.save(str(image_path))
                    pages.append(_RasterPage(image_path, int(pixmap.width), int(pixmap.height)))
            finally:
                pdf.close()
        else:
            try:
                with Image.open(path) as image:
                    for index, frame in enumerate(ImageSequence.Iterator(image), start=1):
                        converted = frame.convert("RGB")
                        image_path = root / f"page-{index}.png"
                        converted.save(image_path, format="PNG")
                        pages.append(_RasterPage(image_path, converted.width, converted.height))
            except Exception as exc:
                raise ProviderInputError(f"could not open raster image for OCR: {exc}") from exc
        if not pages:
            raise ProviderInputError("OCR source contains no pages")
        yield pages


def _run_tesseract(
    executable: Path,
    image_path: Path,
    *,
    languages: str,
    page_segmentation: int,
    tessdata_dir: Path | None,
    timeout: float,
) -> tuple[str, str]:
    with tempfile.TemporaryDirectory(prefix="docreconstruct-tesseract-output-") as temporary:
        output_base = Path(temporary) / "result"
        command = [
            str(executable),
            str(image_path),
            str(output_base),
            "-l",
            languages,
            "--psm",
            str(page_segmentation),
        ]
        if tessdata_dir is not None:
            command.extend(["--tessdata-dir", str(tessdata_dir)])
        command.append("tsv")
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
                shell=False,
                creationflags=_WINDOWS_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Tesseract exceeded the {timeout:g} second limit") from exc
        except OSError as exc:
            raise ProviderDependencyError(f"could not start Tesseract: {exc}") from exc
        stderr = completed.stderr.decode("utf-8", errors="replace")
        if completed.returncode != 0:
            diagnostic = stderr.strip().splitlines()[-1] if stderr.strip() else "no diagnostic"
            raise ProviderInputError(
                f"Tesseract failed with exit code {completed.returncode}: {diagnostic}"
            )
        tsv_path = output_base.with_suffix(".tsv")
        try:
            size = tsv_path.stat().st_size
        except OSError as exc:
            raise ProviderInputError("Tesseract did not produce TSV output") from exc
        if size > _MAX_TSV_BYTES:
            raise ProviderInputError("Tesseract TSV output exceeds the 64 MiB safety limit")
        try:
            text = tsv_path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise ProviderInputError(f"could not read Tesseract TSV output: {exc}") from exc
        return text, stderr


def _page_from_tsv(
    payload: str,
    *,
    page_number: int,
    width: int,
    height: int,
    languages: str,
) -> Page:
    lines: OrderedDict[tuple[int, int, int, int], _LineAccumulator] = OrderedDict()
    try:
        # Tesseract's TSV writer neither quotes nor escapes the recognized
        # text, and `text` is the last column.  With the csv module's default
        # quoting, a word that merely begins with `"` — an opening double quote
        # in ordinary prose — puts the reader into a quoted field, where it
        # swallows every following tab and newline to the end of the file.  The
        # surviving row still parses, so nothing raises and the rest of the page
        # is silently gone.
        rows = csv.DictReader(io.StringIO(payload), delimiter="\t", quoting=csv.QUOTE_NONE)
        for row in rows:
            if row.get("level") != "5":
                continue
            text = (row.get("text") or "").strip()
            if not text:
                continue
            left = int(row["left"])
            top = int(row["top"])
            word_width = int(row["width"])
            word_height = int(row["height"])
            if word_width <= 0 or word_height <= 0:
                continue
            key = (
                int(row["page_num"]),
                int(row["block_num"]),
                int(row["par_num"]),
                int(row["line_num"]),
            )
            word_confidence = float(row.get("conf") or -1)
            accumulator = lines.get(key)
            if accumulator is None:
                accumulator = _LineAccumulator(
                    words=[],
                    left=left,
                    top=top,
                    right=left + word_width,
                    bottom=top + word_height,
                )
                lines[key] = accumulator
            accumulator.add(
                text,
                left=left,
                top=top,
                width=word_width,
                height=word_height,
                confidence=word_confidence,
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderInputError(f"invalid Tesseract TSV output: {exc}") from exc

    elements: list[Element] = []
    for reading_order, (key, line) in enumerate(lines.items()):
        text = " ".join(line.words)
        line_confidence = line.confidence
        element_id = f"page-{page_number}-line-{reading_order + 1}"
        bbox = BBox(
            x0=max(0, line.left),
            y0=max(0, line.top),
            x1=min(width, line.right),
            y1=min(height, line.bottom),
        )
        elements.append(
            Element(
                id=element_id,
                type=ElementType.TEXT,
                bbox=bbox,
                text=text,
                reading_order=reading_order,
                confidence=line_confidence,
                provenance=Provenance(
                    engine=TesseractLocalProvider.name,
                    source_id="/".join(str(part) for part in key),
                    text_confidence=line_confidence,
                    layout_confidence=line_confidence,
                ),
                text_candidates=[
                    TextCandidate(
                        engine=TesseractLocalProvider.name,
                        value=text,
                        confidence=line_confidence,
                        source_element_id=element_id,
                    )
                ],
            )
        )
    return Page(
        id=f"page-{page_number}",
        number=page_number,
        width=float(width),
        height=float(height),
        elements=elements,
        source_type=SourceType.SCANNED,
        metadata={"provider": TesseractLocalProvider.name, "languages": languages},
    )


TesseractProvider = TesseractLocalProvider

__all__ = ["TesseractLocalProvider", "TesseractProvider"]
