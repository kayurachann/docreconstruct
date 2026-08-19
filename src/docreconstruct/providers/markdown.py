"""Saved Markdown evidence adapter for OCR websites and local converters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docreconstruct.ir import (
    BBox,
    Document,
    Element,
    ElementType,
    Page,
    Provenance,
    SourceType,
)
from docreconstruct.reconstruction.markdown_content import (
    MarkdownBlock,
    MarkdownBlockKind,
    MarkdownContent,
    parse_markdown_content,
)

from .base import (
    Provider,
    ProviderCapabilities,
    ProviderContext,
    ProviderCost,
    ProviderCredentialRequirement,
    ProviderExecutionMode,
    ProviderInput,
    ProviderInputError,
    ProviderLicense,
    ProviderPrivacy,
    ProviderResult,
)

_TYPE_MAP = {
    MarkdownBlockKind.HEADING: ElementType.HEADING,
    MarkdownBlockKind.PARAGRAPH: ElementType.PARAGRAPH,
    MarkdownBlockKind.OPTION: ElementType.LIST_ITEM,
    MarkdownBlockKind.LIST_ITEM: ElementType.LIST_ITEM,
    MarkdownBlockKind.TABLE: ElementType.TABLE,
    MarkdownBlockKind.IMAGE: ElementType.IMAGE,
    MarkdownBlockKind.CODE: ElementType.TEXT,
    MarkdownBlockKind.EQUATION: ElementType.FORMULA,
    MarkdownBlockKind.RULE: ElementType.UNKNOWN,
}


class MarkdownEvidenceProvider(Provider):
    """Normalize existing provider Markdown without inventing page geometry."""

    name = "markdown"
    _capabilities = ProviderCapabilities(
        provider=name,
        supported_inputs=["md", "markdown"],
        saved_json=False,
        live_inference=False,
        text=True,
        geometry=False,
        reading_order=True,
        tables=True,
        images=True,
        formulas=True,
        layout=True,
        execution_modes=[ProviderExecutionMode.SAVED],
        markdown=True,
        privacy=ProviderPrivacy.NO_TRANSFER,
        license=ProviderLicense(
            name="Apache-2.0 adapter; source Markdown retains its own rights",
            spdx="Apache-2.0",
            open_source=True,
            commercial_use=True,
        ),
        cost=ProviderCost.FREE,
        credentials=ProviderCredentialRequirement.NONE,
        notes=[
            "Imports Markdown exported by OCR websites or local tools.",
            "Markdown has reading order but normally lacks source-page coordinates.",
        ],
    )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def parse(
        self,
        source: ProviderInput,
        *,
        context: ProviderContext | None = None,
    ) -> ProviderResult:
        if not isinstance(source, (str, Path)):
            raise ProviderInputError("Markdown evidence must be supplied as a .md file path")
        path = Path(source).expanduser().resolve()
        content = parse_markdown_content(path)
        document = self.normalize(content, context=context)
        return ProviderResult(
            provider=self.name,
            document=document,
            warnings=[
                "Markdown evidence contains no authoritative source geometry; "
                "combine it with the original PDF/image in hybrid reconstruction."
            ],
        )

    def normalize(
        self,
        payload: Any,
        *,
        context: ProviderContext | None = None,
    ) -> Document:
        if not isinstance(payload, MarkdownContent):
            raise ProviderInputError("MarkdownEvidenceProvider.normalize expects MarkdownContent")
        width = context.page_width if context and context.page_width else 1000.0
        height = context.page_height if context and context.page_height else 1000.0
        band = height / max(len(payload.blocks), 1)
        elements = [
            self._element(block, width=width, height=height, band=band) for block in payload.blocks
        ]
        source = context.source if context and context.source else payload.source
        document_id = context.document_id if context and context.document_id else Path(source).stem
        return Document(
            id=document_id,
            source=source,
            pages=[
                Page(
                    id="page-1",
                    number=1,
                    width=width,
                    height=height,
                    source_type=SourceType.UNKNOWN,
                    elements=elements,
                    metadata={
                        "provider": self.name,
                        "coordinate_system": "synthetic_reading_order_only",
                    },
                )
            ],
            metadata={"provider": self.name, "content_authority": "markdown"},
        )

    def _element(
        self,
        block: MarkdownBlock,
        *,
        width: float,
        height: float,
        band: float,
    ) -> Element:
        y0 = min(height, block.index * band)
        y1 = min(height, max(y0 + 1.0, (block.index + 1) * band))
        metadata: dict[str, Any] = {
            "markdown_kind": block.kind.value,
            "coordinate_system": "synthetic_reading_order_only",
            **block.metadata,
        }
        if block.level is not None:
            metadata["level"] = block.level
        if block.source:
            metadata.update(
                {
                    "src": block.source,
                    "image_ref": block.source,
                    "alt": block.text,
                }
            )
        if block.table_rows:
            metadata["table"] = {"rows": block.table_rows}
        if block.kind is MarkdownBlockKind.EQUATION:
            metadata["latex"] = block.text
        return Element(
            id=block.id,
            type=_TYPE_MAP[block.kind],
            bbox=BBox(x0=0, y0=y0, x1=width, y1=y1),
            text=block.text,
            reading_order=block.index,
            provenance=Provenance(
                engine=self.name,
                source_id=block.id,
                metadata={"asset_source": block.source or "markdown-block"},
            ),
            metadata=metadata,
        )


MarkdownProvider = MarkdownEvidenceProvider

__all__ = ["MarkdownEvidenceProvider", "MarkdownProvider"]
