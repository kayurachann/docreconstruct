"""Born-digital PDF extraction through optional PyMuPDF."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docreconstruct.ir import (
    BBox,
    Document,
    Element,
    ElementStyle,
    ElementType,
    Page,
    Provenance,
    SourceType,
    TextCandidate,
)

from ._utils import document_id, slug
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


def _pdf_color(value: Any) -> str | None:
    if not isinstance(value, int):
        return None
    return f"#{value & 0xFFFFFF:06X}"


class NativePDFProvider(Provider):
    """Extract native PDF spans without rasterizing or OCRing them."""

    name = "native_pdf"
    _capabilities = ProviderCapabilities(
        provider=name,
        supported_inputs=["pdf"],
        saved_json=False,
        live_inference=True,
        text=True,
        geometry=True,
        reading_order=True,
        styles=True,
        tables=False,
        images=True,
        multilingual=True,
        layout=True,
        execution_modes=[ProviderExecutionMode.LOCAL],
        bounding_boxes=True,
        privacy=ProviderPrivacy.NO_TRANSFER,
        license=ProviderLicense(
            name="Apache License 2.0",
            spdx="Apache-2.0",
            open_source=True,
            commercial_use=True,
            restrictions=["Optional PyMuPDF dependency has its own license terms."],
        ),
        model_name="PyMuPDF native text extraction",
        cost=ProviderCost.INFRASTRUCTURE,
        credentials=ProviderCredentialRequirement.NONE,
        notes=["Requires the optional PyMuPDF package."],
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
        """Validate pre-normalized data; native raw extraction happens in parse."""

        del context
        if isinstance(payload, Document):
            return payload
        try:
            return Document.model_validate(payload)
        except Exception as exc:
            raise ProviderInputError(
                "NativePDFProvider.normalize() accepts canonical Document data; "
                "use parse() to extract a PDF"
            ) from exc

    def parse(
        self,
        source: ProviderInput,
        *,
        context: ProviderContext | None = None,
    ) -> ProviderResult:
        try:
            import pymupdf as fitz
        except ImportError as exc:
            raise ProviderDependencyError(
                "NativePDFProvider requires optional dependency PyMuPDF. "
                "Install it with `pip install PyMuPDF`."
            ) from exc

        source_label: str | None = context.source if context else None
        try:
            if isinstance(source, Path):
                source_label = source_label or str(source)
                pdf = fitz.open(str(source))
            elif isinstance(source, str):
                source_label = source_label or source
                pdf = fitz.open(source)
            elif isinstance(source, (bytes, bytearray)):
                pdf = fitz.open(stream=bytes(source), filetype="pdf")
            else:
                raise ProviderInputError("NativePDFProvider expects a PDF path or PDF bytes")
        except ProviderInputError:
            raise
        except Exception as exc:
            raise ProviderInputError(f"could not open PDF: {exc}") from exc

        effective_context = context or ProviderContext()
        if source_label and effective_context.source is None:
            effective_context = effective_context.model_copy(update={"source": source_label})
        pages: list[Page] = []
        warnings: list[str] = []
        try:
            for page_index in range(pdf.page_count):
                pdf_page = pdf.load_page(page_index)
                pages.append(self._extract_page(pdf_page, page_index, warnings))
        finally:
            pdf.close()

        document = Document(
            id=document_id(self.name, effective_context),
            pages=pages,
            source=source_label,
            metadata={"provider": self.name},
        )
        return ProviderResult(provider=self.name, document=document, warnings=warnings)

    def _extract_page(self, pdf_page: Any, page_index: int, warnings: list[str]) -> Page:
        page_number = page_index + 1
        elements: list[Element] = []
        try:
            page_dict = pdf_page.get_text("dict", sort=True)
        except TypeError:
            page_dict = pdf_page.get_text("dict")

        reading_order = 0
        image_count = 0
        image_area = 0.0
        for block_index, block in enumerate(page_dict.get("blocks", [])):
            block_bbox = block.get("bbox")
            if block.get("type") == 1:
                try:
                    bbox = BBox.from_sequence(list(block_bbox))
                except (TypeError, ValueError):
                    continue
                image_count += 1
                image_area += bbox.area
                extension = block.get("ext")
                mime_type = f"image/{extension}" if extension else "application/octet-stream"
                image_payload = {
                    "bytes": block.get("image"),
                    "mime_type": mime_type,
                    "extension": extension,
                    "width": block.get("width"),
                    "height": block.get("height"),
                }
                elements.append(
                    Element(
                        id=f"page-{page_number}-image-{image_count}",
                        type=ElementType.IMAGE,
                        bbox=bbox,
                        reading_order=reading_order,
                        confidence=1.0,
                        provenance=Provenance(
                            engine=self.name,
                            source_id=f"block-{block_index}",
                            layout_confidence=1.0,
                        ),
                        metadata={
                            "image": {
                                key: value
                                for key, value in image_payload.items()
                                if value is not None
                            },
                            **{key: block[key] for key in ("colorspace", "xref") if key in block},
                        },
                    )
                )
                reading_order += 1
                continue

            for line_index, line in enumerate(block.get("lines", [])):
                direction = line.get("dir")
                rotation = None
                if isinstance(direction, (list, tuple)) and len(direction) == 2:
                    import math

                    rotation = math.degrees(math.atan2(direction[1], direction[0]))
                for span_index, span in enumerate(line.get("spans", [])):
                    text = span.get("text")
                    if not isinstance(text, str) or not text:
                        continue
                    try:
                        bbox = BBox.from_sequence(list(span["bbox"]))
                    except (KeyError, TypeError, ValueError):
                        warnings.append(f"page {page_number}: skipped span without a valid bbox")
                        continue
                    flags = int(span.get("flags") or 0)
                    confidence = 1.0
                    element_id = (
                        f"page-{page_number}-text-{block_index + 1}-"
                        f"{line_index + 1}-{span_index + 1}"
                    )
                    elements.append(
                        Element(
                            id=element_id,
                            type=ElementType.TEXT,
                            bbox=bbox,
                            text=text,
                            reading_order=reading_order,
                            confidence=confidence,
                            style=ElementStyle(
                                font_family=span.get("font") or None,
                                font_size=span.get("size") or None,
                                font_weight=700 if flags & 16 else 400,
                                italic=bool(flags & 2),
                                color=_pdf_color(span.get("color")),
                                rotation=rotation,
                            ),
                            provenance=Provenance(
                                engine=self.name,
                                source_id=element_id,
                                text_confidence=1.0,
                                layout_confidence=1.0,
                                metadata={"flags": flags},
                            ),
                            text_candidates=[
                                TextCandidate(
                                    engine=self.name,
                                    value=text,
                                    confidence=1.0,
                                    source_element_id=element_id,
                                )
                            ],
                        )
                    )
                    reading_order += 1

        rect = pdf_page.rect
        has_text = any(element.text for element in elements)
        raster_coverage = (
            image_area / float(rect.width * rect.height) if rect.width and rect.height else 0.0
        )
        if has_text and raster_coverage >= 0.25:
            source_type = SourceType.HYBRID
        elif has_text:
            source_type = SourceType.NATIVE
        else:
            source_type = SourceType.SCANNED
        return Page(
            id=f"page-{page_number}",
            number=page_number,
            width=float(rect.width),
            height=float(rect.height),
            rotation=float(pdf_page.rotation or 0),
            elements=elements,
            source_type=source_type,
            metadata={
                "provider": self.name,
                "native_text": has_text,
                "embedded_image_blocks": image_count,
                "raster_coverage": raster_coverage,
                "source_page_id": slug(str(getattr(pdf_page, "number", page_index))),
            },
        )


# Alternate casing commonly used in prose and configuration.
NativePdfProvider = NativePDFProvider
