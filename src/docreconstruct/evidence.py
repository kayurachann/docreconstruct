"""Offline loading and schema detection for OCR JSON sidecar evidence.

The loader deliberately calls provider ``normalize`` methods rather than
``parse``/``infer``.  Schema detection is structural and dependency-free, so
loading a saved sidecar can never upload the source document or trigger live
OCR inference.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from docreconstruct.ir import Document
from docreconstruct.providers._utils import load_json_source
from docreconstruct.providers.aws_textract import AWSTextractProvider
from docreconstruct.providers.azure_document_intelligence import (
    AzureDocumentIntelligenceProvider,
)
from docreconstruct.providers.base import Provider, ProviderContext
from docreconstruct.providers.google_document_ai import GoogleDocumentAIProvider
from docreconstruct.providers.json_provider import JSONProvider
from docreconstruct.providers.mathpix import MathpixProvider
from docreconstruct.providers.mineru import MinerUProvider
from docreconstruct.providers.mistral_ocr import MistralOCRProvider
from docreconstruct.providers.olmocr import OlmOCRProvider
from docreconstruct.providers.paddleocr import PaddleOCRProvider, is_paddle_vl_page_wrapper

SidecarPath: TypeAlias = str | Path
ProviderHintKey: TypeAlias = str | Path
ProviderHints: TypeAlias = str | Sequence[str | None] | Mapping[ProviderHintKey, str]

_SEQUENCE_EXCLUSIONS = (str, bytes, bytearray)
_DETECTION_THRESHOLD = 0.60
_AMBIGUITY_MARGIN = 0.05


class SidecarEvidenceError(ValueError):
    """Raised when strict sidecar loading cannot produce canonical evidence."""


@dataclass(frozen=True, slots=True)
class DetectionCandidate:
    """One deterministic provider-schema match."""

    provider: str
    confidence: float
    reason: str


@dataclass(frozen=True, slots=True)
class SidecarDetection:
    """Structural schema-detection result for one decoded sidecar payload."""

    provider: str | None
    confidence: float
    reason: str
    candidates: tuple[DetectionCandidate, ...] = ()
    explicit: bool = False

    @property
    def ambiguous(self) -> bool:
        """Whether another candidate is close enough to merit a warning."""

        return (
            not self.explicit
            and len(self.candidates) > 1
            and self.candidates[0].confidence - self.candidates[1].confidence <= _AMBIGUITY_MARGIN
        )


@dataclass(frozen=True, slots=True)
class SidecarEvidence:
    """One sidecar's canonical evidence or its isolated load failure."""

    path: Path
    provider: str | None
    detection: SidecarDetection
    document: Document | None = None
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.document is not None and self.error is None


@dataclass(frozen=True, slots=True)
class SidecarEvidenceBundle:
    """Aggregate result that preserves per-file diagnostics and ordering."""

    items: tuple[SidecarEvidence, ...]

    @property
    def documents(self) -> tuple[Document, ...]:
        return tuple(item.document for item in self.items if item.document is not None)

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(f"{item.path}: {warning}" for item in self.items for warning in item.warnings)

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(f"{item.path}: {item.error}" for item in self.items if item.error is not None)

    @property
    def succeeded(self) -> bool:
        return bool(self.items) and not self.errors

    def raise_for_errors(self) -> None:
        """Raise one readable exception containing all aggregated failures."""

        if self.errors:
            raise SidecarEvidenceError("; ".join(self.errors))


_PROVIDER_FACTORIES: dict[str, type[Provider]] = {
    "json": JSONProvider,
    "paddleocr": PaddleOCRProvider,
    "mineru": MinerUProvider,
    "olmocr": OlmOCRProvider,
    "mistral_ocr": MistralOCRProvider,
    "azure_document_intelligence": AzureDocumentIntelligenceProvider,
    "mathpix": MathpixProvider,
    "google_document_ai": GoogleDocumentAIProvider,
    "aws_textract": AWSTextractProvider,
}

_PROVIDER_ALIASES = {
    "canonical": "json",
    "canonical_ir": "json",
    "document_graph": "json",
    "docir": "json",
    "paddle": "paddleocr",
    "paddle_ocr": "paddleocr",
    "miner_u": "mineru",
    "olm_ocr": "olmocr",
    "mistral": "mistral_ocr",
    "azure": "azure_document_intelligence",
    "azure_document_ai": "azure_document_intelligence",
    "azure_layout": "azure_document_intelligence",
    "mathpix_ocr": "mathpix",
    "google": "google_document_ai",
    "google_docai": "google_document_ai",
    "google_documentai": "google_document_ai",
    "aws": "aws_textract",
    "amazon_textract": "aws_textract",
    "textract": "aws_textract",
}


def detect_sidecar_provider(payload: Any) -> SidecarDetection:
    """Identify a built-in saved-result schema without instantiating providers."""

    candidates = tuple(
        sorted(
            (
                candidate
                for provider, scorer in _SCORERS
                if (candidate := scorer(payload, provider)) is not None
            ),
            key=lambda item: (-item.confidence, item.provider),
        )
    )
    if not candidates or candidates[0].confidence < _DETECTION_THRESHOLD:
        return SidecarDetection(
            provider=None,
            confidence=candidates[0].confidence if candidates else 0.0,
            reason=(
                candidates[0].reason
                if candidates
                else "no known canonical or OCR provider schema markers were found"
            ),
            candidates=candidates,
        )
    winner = candidates[0]
    return SidecarDetection(
        provider=winner.provider,
        confidence=winner.confidence,
        reason=winner.reason,
        candidates=candidates,
    )


def load_sidecar_evidence(
    paths: Sequence[SidecarPath],
    *,
    provider_hints: ProviderHints | None = None,
    context: ProviderContext | None = None,
    strict: bool = False,
) -> SidecarEvidenceBundle:
    """Load repeatable JSON/JSONL paths as canonical provider documents.

    ``provider_hints`` accepts a positional sequence, a mapping keyed by path,
    or repeatable ``"path=provider"`` expressions.  A bare provider string is
    accepted for a single path.  ``strict=False`` isolates per-file failures;
    ``strict=True`` raises :class:`SidecarEvidenceError` at the first failure.
    """

    sidecar_paths = tuple(Path(path) for path in paths)
    hints = _resolve_provider_hints(sidecar_paths, provider_hints)
    items: list[SidecarEvidence] = []
    for index, path in enumerate(sidecar_paths):
        hint = hints[index]
        try:
            payload, _source_label = load_json_source(path)
            detection = (
                _explicit_detection(hint) if hint is not None else detect_sidecar_provider(payload)
            )
            if detection.provider is None:
                raise SidecarEvidenceError(
                    "could not identify JSON schema; pass an explicit provider hint "
                    f"(best structural match confidence={detection.confidence:.2f})"
                )
            provider = _PROVIDER_FACTORIES[detection.provider]()
            effective_context = _sidecar_context(path, context)
            document = provider.normalize(payload, context=effective_context)
            warnings = _evidence_warnings(detection, document)
            items.append(
                SidecarEvidence(
                    path=path,
                    provider=detection.provider,
                    detection=detection,
                    document=document,
                    warnings=warnings,
                )
            )
        except Exception as exc:  # noqa: BLE001 - aggregation is this API's purpose
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            error = _error_message(exc)
            if strict:
                raise SidecarEvidenceError(f"{path}: {error}") from exc
            failed_detection = (
                _explicit_detection(hint)
                if hint is not None and _normalize_provider_name(hint, raise_unknown=False)
                else SidecarDetection(provider=None, confidence=0.0, reason="load failed")
            )
            items.append(
                SidecarEvidence(
                    path=path,
                    provider=failed_detection.provider,
                    detection=failed_detection,
                    error=error,
                )
            )
    return SidecarEvidenceBundle(items=tuple(items))


def _explicit_detection(provider: str) -> SidecarDetection:
    normalized = _normalize_provider_name(provider)
    candidate = DetectionCandidate(
        provider=normalized,
        confidence=1.0,
        reason="explicit provider hint",
    )
    return SidecarDetection(
        provider=normalized,
        confidence=1.0,
        reason=candidate.reason,
        candidates=(candidate,),
        explicit=True,
    )


def _normalize_provider_name(provider: str, *, raise_unknown: bool = True) -> str:
    normalized = provider.strip().casefold().replace("-", "_").replace(" ", "_")
    normalized = _PROVIDER_ALIASES.get(normalized, normalized)
    if normalized not in _PROVIDER_FACTORIES:
        if not raise_unknown:
            return ""
        available = ", ".join(sorted(_PROVIDER_FACTORIES))
        raise SidecarEvidenceError(
            f"unknown sidecar provider {provider!r}; supported providers: {available}"
        )
    return normalized


def _resolve_provider_hints(
    paths: Sequence[Path], provider_hints: ProviderHints | None
) -> tuple[str | None, ...]:
    if provider_hints is None:
        return (None,) * len(paths)
    if isinstance(provider_hints, Mapping):
        return tuple(_mapping_hint(path, provider_hints) for path in paths)
    if isinstance(provider_hints, str):
        if "=" in provider_hints:
            mapping = _hint_expression_mapping((provider_hints,))
            return tuple(_mapping_hint(path, mapping) for path in paths)
        if len(paths) != 1:
            raise SidecarEvidenceError(
                "a bare provider hint is only valid for one sidecar; use path=provider"
            )
        return (provider_hints,)

    values = tuple(provider_hints)
    expressions = [value for value in values if isinstance(value, str) and "=" in value]
    if expressions:
        if len(expressions) != len(values):
            raise SidecarEvidenceError(
                "provider hints cannot mix positional names with path=provider expressions"
            )
        mapping = _hint_expression_mapping(tuple(expressions))
        return tuple(_mapping_hint(path, mapping) for path in paths)
    if len(values) != len(paths):
        raise SidecarEvidenceError(
            f"expected {len(paths)} positional provider hint(s), received {len(values)}"
        )
    return values


def _hint_expression_mapping(expressions: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for expression in expressions:
        raw_path, separator, raw_provider = expression.rpartition("=")
        if not separator or not raw_path.strip() or not raw_provider.strip():
            raise SidecarEvidenceError(
                f"invalid provider hint {expression!r}; expected path=provider"
            )
        key = raw_path.strip()
        if key in result:
            raise SidecarEvidenceError(f"duplicate provider hint for {key!r}")
        result[key] = raw_provider.strip()
    return result


def _mapping_hint(path: Path, hints: Mapping[Any, str]) -> str | None:
    string_hints = {str(key): value for key, value in hints.items()}
    lookup_keys = (str(path), str(path.absolute()), path.name)
    for key in lookup_keys:
        if key in string_hints:
            return string_hints[key]
    return None


def _sidecar_context(path: Path, context: ProviderContext | None) -> ProviderContext:
    if context is None:
        return ProviderContext(source=str(path))
    if context.source is not None:
        return context
    return context.model_copy(update={"source": str(path)})


def _evidence_warnings(detection: SidecarDetection, document: Document) -> tuple[str, ...]:
    warnings: list[str] = []
    if not detection.explicit and detection.confidence < 0.80:
        warnings.append(
            f"low-confidence schema detection ({detection.confidence:.2f}); "
            "use an explicit provider hint if the schema is known"
        )
    if detection.ambiguous:
        runner_up = detection.candidates[1]
        warnings.append(
            "ambiguous schema detection: selected "
            f"{detection.provider} ({detection.confidence:.2f}) over "
            f"{runner_up.provider} ({runner_up.confidence:.2f}); "
            "use an explicit provider hint to override"
        )
    if not document.pages:
        warnings.append("normalized document contains no pages")
    elif not any(page.elements for page in document.pages):
        warnings.append("normalized document contains pages but no positioned elements")
    return tuple(warnings)


def _error_message(exc: BaseException) -> str:
    message = str(exc).strip() or type(exc).__name__
    if isinstance(exc, SidecarEvidenceError):
        return message
    return f"{type(exc).__name__}: {message}"


def _candidate(provider: str, confidence: float, reason: str) -> DetectionCandidate:
    return DetectionCandidate(provider=provider, confidence=confidence, reason=reason)


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _records(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, _SEQUENCE_EXCLUSIONS):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _nested_with_pages(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("response", "result", "data"):
        nested = _mapping(payload.get(key))
        if nested is not None and isinstance(nested.get("pages"), Sequence):
            return nested
    return payload


def _score_canonical(payload: Any, provider: str) -> DetectionCandidate | None:
    if isinstance(payload, Sequence) and not isinstance(payload, _SEQUENCE_EXCLUSIONS):
        values = list(payload)
        if len(values) == 1:
            payload = values[0]
    root = _mapping(payload)
    if root is None:
        return None
    wrapped = False
    for key in ("document", "document_graph", "docir"):
        nested = _mapping(root.get(key))
        if nested is not None and "id" in nested and "pages" in nested:
            root = nested
            wrapped = True
            break
    if not isinstance(root.get("id"), str) or not isinstance(root.get("pages"), Sequence):
        return None
    pages = _records(root.get("pages"))
    canonical_pages = bool(pages) and all(
        {"id", "number", "width", "height", "elements"}.issubset(page) for page in pages
    )
    if root.get("schema_version") == Document.CURRENT_SCHEMA_VERSION:
        return _candidate(provider, 1.0, "canonical schema_version and document fields")
    if canonical_pages or (not pages and root.get("pages") == []):
        confidence = 0.99 if wrapped else 0.98
        return _candidate(provider, confidence, "canonical document/page field set")
    return None


def _score_aws(payload: Any, provider: str) -> DetectionCandidate | None:
    root = _mapping(payload)
    if root is None or not isinstance(root.get("Blocks"), Sequence):
        return None
    blocks = _records(root.get("Blocks"))
    if not blocks and root.get("Blocks") != []:
        return None
    markers = sum("BlockType" in block for block in blocks[:10])
    if markers or any(
        key in root for key in ("AnalyzeDocumentModelVersion", "DetectDocumentTextModelVersion")
    ):
        return _candidate(provider, 0.99, "Amazon Textract Blocks/BlockType fields")
    return _candidate(provider, 0.86, "Amazon Textract Blocks array")


def _score_azure(payload: Any, provider: str) -> DetectionCandidate | None:
    root = _mapping(payload)
    if root is None:
        return None
    result = _mapping(root.get("analyzeResult"))
    if result is not None:
        return _candidate(provider, 0.99, "Azure analyzeResult envelope")
    for key in ("result", "data", "response"):
        nested = _mapping(root.get(key))
        if nested is not None and isinstance(nested.get("analyzeResult"), Mapping):
            return _candidate(provider, 0.98, f"Azure {key}.analyzeResult envelope")
    candidate_root = _nested_with_pages(root)
    pages = _records(candidate_root.get("pages"))
    azure_markers = {"apiVersion", "modelId", "contentFormat", "paragraphs", "styles"}
    page_markers = bool(pages) and any(
        "pageNumber" in page and any(key in page for key in ("words", "formulas", "selectionMarks"))
        for page in pages
    )
    if isinstance(candidate_root.get("pages"), Sequence) and (
        azure_markers.intersection(candidate_root) or page_markers
    ):
        return _candidate(provider, 0.91, "Azure layout result fields and page records")
    return None


def _score_google(payload: Any, provider: str) -> DetectionCandidate | None:
    root = _mapping(payload)
    if root is None:
        return None
    document = _mapping(root.get("document"))
    if document is not None and _google_document_shape(document):
        return _candidate(provider, 0.99, "Google Document AI ProcessResponse.document")
    for key in ("result", "response", "data"):
        nested = _mapping(root.get(key))
        document = _mapping(nested.get("document")) if nested is not None else None
        if document is not None and _google_document_shape(document):
            return _candidate(provider, 0.98, f"Google Document AI {key}.document")
    if _google_document_shape(root):
        return _candidate(provider, 0.93, "Google Document AI document page/layout fields")
    return None


def _google_document_shape(root: Mapping[str, Any]) -> bool:
    pages = _records(root.get("pages"))
    if not isinstance(root.get("pages"), Sequence):
        return False
    document_marker = any(key in root for key in ("text", "mimeType", "docid", "entities"))
    page_marker = any(
        any(key in page for key in ("image", "dimension", "layout", "formFields"))
        or any(
            isinstance(record.get("layout"), Mapping)
            for collection in ("blocks", "paragraphs", "tokens", "tables")
            for record in _records(page.get(collection))
        )
        for page in pages
    )
    return document_marker and (page_marker or root.get("pages") == [])


def _score_mistral(payload: Any, provider: str) -> DetectionCandidate | None:
    root = _mapping(payload)
    if root is None:
        return None
    root = _nested_with_pages(root)
    pages = _records(root.get("pages"))
    if not pages:
        return None
    model = str(root.get("model") or "").casefold()
    page_markers = sum(
        any(key in page for key in ("markdown", "dimensions", "images", "bbox_annotations"))
        for page in pages
    )
    index_markers = sum("index" in page for page in pages)
    if "mistral" in model:
        return _candidate(provider, 0.98, "Mistral model identifier and pages")
    if page_markers and index_markers:
        return _candidate(provider, 0.95, "Mistral page index/markdown/dimensions fields")
    return None


def _score_mathpix(payload: Any, provider: str) -> DetectionCandidate | None:
    root = _mapping(payload)
    if root is None:
        return None
    for key in ("response", "result"):
        nested = _mapping(root.get(key))
        if nested is not None and any(
            marker in nested for marker in ("line_data", "mmd", "latex_styled", "pdf_id")
        ):
            root = nested
            break
    strong = {
        "line_data",
        "mmd",
        "latex_styled",
        "pdf_id",
        "confidence_rate",
        "is_printed",
        "is_handwritten",
    }.intersection(root)
    if strong:
        return _candidate(provider, 0.98, f"Mathpix field(s): {', '.join(sorted(strong))}")
    pages = _records(root.get("pages"))
    if any(
        _records(page.get("lines"))
        and any(
            any(key in line for key in ("cnt", "text_display", "conversion_output"))
            for line in _records(page.get("lines"))
        )
        for page in pages
    ):
        return _candidate(provider, 0.96, "Mathpix page lines with contour/display fields")
    return None


def _score_mineru(payload: Any, provider: str) -> DetectionCandidate | None:
    root = _mapping(payload)
    if root is not None:
        strong = {"pdf_info", "content_list", "page_info", "page_infos"}.intersection(root)
        if strong:
            return _candidate(provider, 0.97, f"MinerU field(s): {', '.join(sorted(strong))}")
        pages = _records(root.get("pages"))
        if any(any(key in page for key in ("para_blocks", "content_list")) for page in pages):
            return _candidate(provider, 0.92, "MinerU page block/content-list fields")
        for key in ("data", "results"):
            nested_records = _records(root.get(key))
            if (
                nested_records
                and all("page_idx" in record or "page_index" in record for record in nested_records)
                and any("bbox" in record and "type" in record for record in nested_records)
            ):
                return _candidate(provider, 0.90, f"MinerU {key} content-list records")
    records = _records(payload)
    if (
        records
        and all("page_idx" in record or "page_index" in record for record in records)
        and any("bbox" in record and "type" in record for record in records)
    ):
        return _candidate(provider, 0.88, "MinerU flat content-list page/bbox/type records")
    if _mineru_content_list_v2(payload):
        return _candidate(provider, 0.93, "MinerU page-grouped content-list-v2 records")
    return None


def _mineru_content_list_v2(payload: Any) -> bool:
    if not isinstance(payload, Sequence) or isinstance(payload, _SEQUENCE_EXCLUSIONS):
        return False
    pages = list(payload)
    if not pages or not all(
        isinstance(page, Sequence) and not isinstance(page, _SEQUENCE_EXCLUSIONS) for page in pages
    ):
        return False
    blocks = [block for page in pages for block in page]
    return bool(blocks) and all(
        isinstance(block, Mapping)
        and isinstance(block.get("type"), str)
        and isinstance(block.get("content"), Mapping)
        for block in blocks
    )


def _score_paddle(payload: Any, provider: str) -> DetectionCandidate | None:
    root = _mapping(payload)
    if root is not None:
        if is_paddle_vl_page_wrapper(root):
            return _candidate(provider, 0.99, "PaddleOCR-VL per-page result envelope")
        data = _mapping(root.get("res")) or root
        if isinstance(data.get("rec_texts"), Sequence) and any(
            isinstance(data.get(key), Sequence)
            for key in ("rec_boxes", "dt_polys", "rec_polys", "boxes")
        ):
            return _candidate(provider, 0.99, "PaddleOCR rec_texts and detection boxes")
        if isinstance(root.get("ocr_results"), Sequence):
            return _candidate(provider, 0.94, "PaddleOCR ocr_results array")
        pages = _records(root.get("pages"))
        if any(
            isinstance((_mapping(page.get("res")) or page).get("rec_texts"), Sequence)
            for page in pages
        ):
            return _candidate(provider, 0.96, "PaddleOCR page recognition arrays")
        results = _records(root.get("results"))
        if any(
            isinstance((_mapping(record.get("res")) or record).get("rec_texts"), Sequence)
            for record in results
        ):
            return _candidate(provider, 0.95, "PaddleOCR results recognition arrays")
    if isinstance(payload, Sequence) and not isinstance(payload, _SEQUENCE_EXCLUSIONS):
        values = list(payload)
        if values and all(is_paddle_vl_page_wrapper(value) for value in values):
            return _candidate(provider, 0.99, "PaddleOCR-VL ordered page-result envelopes")
        if values and _paddle_legacy_entry(values[0]):
            return _candidate(provider, 0.99, "PaddleOCR legacy polygon/text-score tuples")
        mapped = _records(values)
        if mapped and any(
            "res" in record and any(key in record for key in ("bbox", "box", "type"))
            for record in mapped
        ):
            return _candidate(provider, 0.84, "PaddleOCR/PP-Structure region records")
    return None


def _paddle_legacy_entry(value: Any) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, _SEQUENCE_EXCLUSIONS):
        return False
    values = list(value)
    if len(values) < 2:
        return False
    text_score = values[1]
    return (
        isinstance(text_score, Sequence)
        and not isinstance(text_score, _SEQUENCE_EXCLUSIONS)
        and bool(text_score)
        and isinstance(list(text_score)[0], str)
    )


def _score_olmocr(payload: Any, provider: str) -> DetectionCandidate | None:
    root = _mapping(payload)
    if root is not None:
        if "natural_text" in root:
            return _candidate(provider, 0.97, "olmOCR natural_text field")
        metadata = _mapping(root.get("metadata"))
        if (
            isinstance(root.get("text"), str)
            and metadata is not None
            and any(
                key in metadata
                for key in ("Source-File", "source_file", "page_number", "page_num", "model")
            )
        ):
            return _candidate(provider, 0.92, "olmOCR text and page/source metadata")
        if isinstance(root.get("completion"), str) and metadata is not None:
            return _candidate(provider, 0.90, "olmOCR completion and metadata")
        messages = _records(root.get("messages"))
        if messages and any(
            message.get("role") == "assistant" and isinstance(message.get("content"), str)
            for message in messages
        ):
            return _candidate(provider, 0.82, "olmOCR assistant-message linearized output")
        for key in ("pages", "results", "records", "outputs"):
            nested_records = _records(root.get(key))
            if nested_records and all(
                "natural_text" in record
                or (
                    isinstance(record.get("text"), str)
                    and isinstance(record.get("metadata"), Mapping)
                )
                for record in nested_records
            ):
                return _candidate(provider, 0.94, f"olmOCR {key} page records")
    records = _records(payload)
    if records and all(
        "natural_text" in record
        or (isinstance(record.get("text"), str) and isinstance(record.get("metadata"), Mapping))
        for record in records
    ):
        return _candidate(provider, 0.95, "olmOCR JSONL page records")
    return None


_SCORERS = (
    ("json", _score_canonical),
    ("aws_textract", _score_aws),
    ("azure_document_intelligence", _score_azure),
    ("google_document_ai", _score_google),
    ("mistral_ocr", _score_mistral),
    ("mathpix", _score_mathpix),
    ("mineru", _score_mineru),
    ("paddleocr", _score_paddle),
    ("olmocr", _score_olmocr),
)


__all__ = [
    "DetectionCandidate",
    "ProviderHints",
    "SidecarDetection",
    "SidecarEvidence",
    "SidecarEvidenceBundle",
    "SidecarEvidenceError",
    "SidecarPath",
    "detect_sidecar_provider",
    "load_sidecar_evidence",
]
