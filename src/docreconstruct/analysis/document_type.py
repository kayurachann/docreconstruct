"""Small, explainable document-archetype classifier."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path


class DocumentArchetype(StrEnum):
    REPORT = "report"
    INVOICE = "invoice"
    SPREADSHEET = "spreadsheet"
    PRESENTATION = "presentation"
    ACADEMIC_PAPER = "academic-paper"
    FORM = "form"
    BOOK = "book"
    BROCHURE = "brochure"
    FINANCIAL_STATEMENT = "financial-statement"
    MIXED = "mixed"


def _kind(element: object) -> str:
    value = getattr(element, "type", "")
    return str(getattr(value, "value", value)).lower()


def classify_document(document: object) -> DocumentArchetype:
    """Classify a Document IR using auditable structural and lexical signals."""

    pages = list(getattr(document, "pages", []) or [])
    elements = [element for page in pages for element in getattr(page, "elements", [])]
    kinds = [_kind(element) for element in elements]
    text = "\n".join(str(getattr(element, "text", "") or "") for element in elements)
    lowered = text.lower()
    table_ratio = kinds.count("table") / max(len(elements), 1)
    image_ratio = sum(kind in {"image", "figure", "chart"} for kind in kinds) / max(
        len(elements), 1
    )

    if re.search(r"\b(invoice|invoice number|amount due|bill to)\b", lowered):
        return DocumentArchetype.INVOICE
    if re.search(r"\b(balance sheet|cash flows?|income statement|total assets)\b", lowered):
        return DocumentArchetype.FINANCIAL_STATEMENT
    if table_ratio >= 0.35:
        return DocumentArchetype.SPREADSHEET
    if re.search(r"\b(abstract|methodology|references|doi:)\b", lowered):
        return DocumentArchetype.ACADEMIC_PAPER
    if sum(token in text for token in ("☐", "☑", "____")) >= 2:
        return DocumentArchetype.FORM
    if image_ratio >= 0.35 and len(pages) <= 4:
        return DocumentArchetype.BROCHURE
    if len(pages) <= 3 and image_ratio >= 0.18:
        return DocumentArchetype.PRESENTATION
    if len(pages) >= 20:
        return DocumentArchetype.BOOK
    if elements:
        return DocumentArchetype.REPORT
    source = getattr(document, "source", None)
    if source and Path(str(source)).suffix.lower() in {".ppt", ".pptx"}:
        return DocumentArchetype.PRESENTATION
    return DocumentArchetype.MIXED
