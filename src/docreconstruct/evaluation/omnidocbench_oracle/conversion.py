"""Value-preserving conversion of validated OmniDocBench pages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from docreconstruct.ir import (
    Document,
    Element,
    ElementType,
    Page,
    Provenance,
    SourceType,
)

from .models import (
    OmniDocBenchConversionReason,
    OmniDocBenchConversionWarning,
    OmniDocBenchOracleContractError,
    OmniDocBenchOracleConversion,
    OmniDocBenchPageConversionReport,
    OmniDocBenchProjectionDiagnostic,
)
from .projection import (
    _annotation_identity,
    _canonical_json_bytes,
    _pretty_json_bytes,
    _sha256,
    _sha256_bytes,
    _validated_annotations,
    _ValidatedAnnotation,
    validate_omnidocbench_projection,
)

_CATEGORY_TYPES: dict[str, ElementType] = {
    "text_block": ElementType.PARAGRAPH,
    "title": ElementType.TITLE,
    "table": ElementType.TABLE,
    "table_caption": ElementType.CAPTION,
    "table_footnote": ElementType.FOOTNOTE,
    "figure": ElementType.FIGURE,
    "figure_caption": ElementType.CAPTION,
    "figure_footnote": ElementType.FOOTNOTE,
    "equation_isolated": ElementType.FORMULA,
    "equation_caption": ElementType.CAPTION,
    "header": ElementType.HEADER,
    "footer": ElementType.FOOTER,
    "page_footnote": ElementType.FOOTNOTE,
    "page_number": ElementType.PAGE_NUMBER,
    "abandon": ElementType.UNKNOWN,
}
_TEXT_AUTHORITY_FIELDS = ("text", "latex", "html", "html_2", "html_3")


def _extra_relations(record: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    extra_value = record.get("extra", {})
    if not isinstance(extra_value, Mapping):
        raise OmniDocBenchOracleContractError(
            OmniDocBenchConversionReason.INVALID_EXTRA_RELATION,
            "extra must be an object",
        )
    extra = deepcopy(dict(extra_value))
    relation_value = extra.get("relation", [])
    if not isinstance(relation_value, Sequence) or isinstance(
        relation_value, (str, bytes, bytearray)
    ):
        raise OmniDocBenchOracleContractError(
            OmniDocBenchConversionReason.INVALID_EXTRA_RELATION,
            "extra.relation must be a sequence",
        )
    relations: list[dict[str, Any]] = []
    for index, relation in enumerate(relation_value):
        if not isinstance(relation, Mapping):
            raise OmniDocBenchOracleContractError(
                OmniDocBenchConversionReason.INVALID_EXTRA_RELATION,
                f"extra.relation[{index}] must be an object",
            )
        relations.append(deepcopy(dict(relation)))
    return extra, relations


def _element_type(category: str) -> ElementType:
    return _CATEGORY_TYPES.get(category, ElementType.UNKNOWN)


def _element_text(detection: Mapping[str, Any], element_type: ElementType) -> str | None:
    if element_type is ElementType.FORMULA:
        value = detection.get("latex")
    elif element_type in {ElementType.TABLE, ElementType.FIGURE, ElementType.IMAGE}:
        value = None
    else:
        value = detection.get("text")
    return value if isinstance(value, str) else None


def _element_metadata(annotation: _ValidatedAnnotation) -> dict[str, Any]:
    detection = annotation.raw
    metadata: dict[str, Any] = {
        "upstream_category": annotation.category,
        "source_annotation_id": annotation.annotation_id,
        "source_annotation_index": annotation.source_index,
        "source_polygon": list(detection["poly"]),
        "source_bbox": annotation.bbox.model_dump(mode="json"),
        "source_reading_order": annotation.order,
        "source_ignore": annotation.ignored,
        "source_annotation": deepcopy(detection),
        "oracle_ground_truth": True,
    }
    for name in (
        "text",
        "latex",
        "html",
        "html_2",
        "html_3",
        "attribute",
        "table_edit_status",
        "merge_list",
    ):
        if name in detection:
            metadata[name] = detection[name]
    return metadata


def _element_id(record_index: int, annotation: _ValidatedAnnotation) -> str:
    identity = _canonical_json_bytes(
        {"source_index": annotation.source_index, "annotation_id": annotation.annotation_id}
    )
    return f"odb-r{record_index:06d}-a{annotation.source_index:05d}-{_sha256_bytes(identity)[:16]}"


def _annotation_audit(
    annotation: _ValidatedAnnotation,
    projected_element_id: str | None,
) -> dict[str, Any]:
    return {
        "source_index": annotation.source_index,
        "source_annotation_id": annotation.annotation_id,
        "source_polygon": list(annotation.raw["poly"]),
        "source_bbox": annotation.bbox.model_dump(mode="json"),
        "source_reading_order": annotation.order,
        "source_category_type": annotation.raw.get("category_type"),
        "canonical_element_type": _element_type(annotation.category).value,
        "ignored": annotation.ignored,
        "projection_status": "ignored_audit_only" if annotation.ignored else "projected",
        "projected_element_id": projected_element_id,
        "raw_annotation": deepcopy(annotation.raw),
    }


def _text_authority_payload(audits: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for audit in audits:
        raw_value = audit.get("raw_annotation")
        if not isinstance(raw_value, Mapping):
            raise OmniDocBenchOracleContractError(
                OmniDocBenchConversionReason.TEXT_HASH_MISMATCH,
                "canonical audit record has no raw_annotation object",
            )
        payload.append(
            {
                "source_index": audit.get("source_index"),
                "source_annotation_id": audit.get("source_annotation_id"),
                "ignored": audit.get("ignored"),
                "fields": {
                    name: raw_value[name] for name in _TEXT_AUTHORITY_FIELDS if name in raw_value
                },
            }
        )
    return payload


def _text_authority_hash(audits: Sequence[Mapping[str, Any]]) -> str:
    return _sha256_bytes(_canonical_json_bytes(_text_authority_payload(audits)))


def _relation_hash(extra: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical_json_bytes(extra.get("relation", [])))


def _conversion_warnings(
    annotations: Sequence[_ValidatedAnnotation],
    relations: Sequence[Mapping[str, Any]],
    diagnostic: OmniDocBenchProjectionDiagnostic,
) -> list[OmniDocBenchConversionWarning]:
    warnings: list[OmniDocBenchConversionWarning] = []
    if diagnostic.corrected:
        warnings.append(
            OmniDocBenchConversionWarning(
                reason_code=OmniDocBenchConversionReason.REPORTED_DIMENSIONS_TRANSPOSED,
                message=(
                    "Reported page dimensions were proven transposed by the decoded raster; "
                    "polygons were preserved without rotation."
                ),
            )
        )
    ignored = [item for item in annotations if item.ignored]
    if ignored:
        warnings.append(
            OmniDocBenchConversionWarning(
                reason_code=OmniDocBenchConversionReason.IGNORED_ANNOTATION_AUDIT_ONLY,
                message=(
                    "Ignored annotations were retained verbatim in the page audit metadata "
                    "and intentionally not projected as renderable elements."
                ),
                annotation_indices=[item.source_index for item in ignored],
                annotation_ids=[item.annotation_id for item in ignored],
                details={"count": len(ignored)},
            )
        )
    missing = [item for item in annotations if not item.ignored and item.order is None]
    if missing:
        warnings.append(
            OmniDocBenchConversionWarning(
                reason_code=OmniDocBenchConversionReason.MISSING_READING_ORDER,
                message="One or more projected annotations have no reading-order value.",
                annotation_indices=[item.source_index for item in missing],
                annotation_ids=[item.annotation_id for item in missing],
                details={"count": len(missing)},
            )
        )
    unsupported = [
        item for item in annotations if not item.ignored and item.category not in _CATEGORY_TYPES
    ]
    if unsupported:
        categories: dict[str, int] = {}
        for item in unsupported:
            categories[item.category] = categories.get(item.category, 0) + 1
        warnings.append(
            OmniDocBenchConversionWarning(
                reason_code=OmniDocBenchConversionReason.UNSUPPORTED_CATEGORY_PROJECTED_AS_UNKNOWN,
                message=(
                    "Unsupported categories were projected as canonical unknown elements; "
                    "their raw type and payload remain in metadata."
                ),
                annotation_indices=[item.source_index for item in unsupported],
                annotation_ids=[item.annotation_id for item in unsupported],
                details={"categories": dict(sorted(categories.items()))},
            )
        )
    identities = {_annotation_identity(item.annotation_id) for item in annotations}
    indices = {item.source_index for item in annotations}
    unknown: list[int] = []
    for index, relation in enumerate(relations):
        for key in ("source_anno_id", "target_anno_id"):
            value = relation.get(key)
            valid_id = (
                not isinstance(value, bool)
                and isinstance(value, (str, int))
                and _annotation_identity(value) in identities
            )
            valid_index = (
                isinstance(value, int) and not isinstance(value, bool) and value in indices
            )
            if not valid_id and not valid_index:
                unknown.append(index)
                break
    if unknown:
        warnings.append(
            OmniDocBenchConversionWarning(
                reason_code=OmniDocBenchConversionReason.RELATION_REFERENCES_UNKNOWN_ANNOTATION,
                message=(
                    "One or more relations reference annotation IDs absent from this page; "
                    "the relations were still retained verbatim for audit."
                ),
                details={"relation_indices": unknown},
            )
        )
    return warnings


def convert_omnidocbench_oracle_page(
    record: Mapping[str, Any],
    *,
    record_index: int,
    image_path: str | Path,
    dataset_revision: str,
    markdown_path: str | Path | None = None,
) -> OmniDocBenchOracleConversion:
    """Convert one raw page without changing any GT text, order, or polygon."""

    if record_index < 0:
        raise ValueError("record_index must be non-negative")
    image = Path(image_path).expanduser().resolve()
    diagnostic = validate_omnidocbench_projection(record, image)
    page_info = record.get("page_info")
    assert isinstance(page_info, Mapping)
    image_name = page_info.get("image_path")
    if not isinstance(image_name, str) or Path(image_name).name != image.name:
        raise ValueError("page_info.image_path does not identify image_path")
    annotations = _validated_annotations(record)
    extra, relations = _extra_relations(record)
    retained = sorted(
        (item for item in annotations if not item.ignored),
        key=lambda item: (
            item.order is None,
            item.order if item.order is not None else 0,
            item.source_index,
        ),
    )
    elements, projected_ids = _project_elements(retained, record_index, image, dataset_revision)
    audits = [_annotation_audit(item, projected_ids.get(item.source_index)) for item in annotations]
    page_attribute = page_info.get("page_attribute")
    page_metadata: dict[str, Any] = {
        "coordinate_system": "pixels_top_left",
        "upstream_page_no": page_info.get("page_no"),
        "page_attribute": deepcopy(dict(page_attribute))
        if isinstance(page_attribute, Mapping)
        else {},
        "omnidocbench_page_info": deepcopy(dict(page_info)),
        "omnidocbench_annotations": audits,
        "omnidocbench_extra": extra,
        "oracle_ground_truth": True,
        "projection_validation": diagnostic.model_dump(mode="json"),
    }
    raster_sha = _sha256(image)
    metadata: dict[str, Any] = {
        "dataset": "OmniDocBench",
        "dataset_revision": dataset_revision,
        "converter_contract": "omnidocbench-to-canonical-ir/0.2",
        "evaluation_lane": "oracle_reconstruction",
        "source_image_sha256": raster_sha,
        "source_annotation_record_sha256": _sha256_bytes(_canonical_json_bytes(record)),
    }
    if markdown_path is not None:
        metadata["reviewed_markdown_sha256"] = _sha256(Path(markdown_path).expanduser().resolve())
    document = Document(
        id=f"omnidocbench-demo-{Path(image.name).stem}",
        schema_version=Document.CURRENT_SCHEMA_VERSION,
        pages=[
            Page(
                id="page-1",
                number=1,
                width=float(diagnostic.canonical_width),
                height=float(diagnostic.canonical_height),
                source_type=SourceType.IMAGE,
                elements=elements,
                metadata=page_metadata,
            )
        ],
        metadata=metadata,
    )
    return _validated_conversion(
        document, record, record_index, image, diagnostic, annotations, retained, relations, audits
    )


def _project_elements(
    annotations: Sequence[_ValidatedAnnotation],
    record_index: int,
    image: Path,
    dataset_revision: str,
) -> tuple[list[Element], dict[int, str]]:
    elements: list[Element] = []
    ids: dict[int, str] = {}
    for item in annotations:
        element_type = _element_type(item.category)
        text = _element_text(item.raw, element_type)
        element_id = _element_id(record_index, item)
        ids[item.source_index] = element_id
        elements.append(
            Element(
                id=element_id,
                type=element_type,
                bbox=item.bbox,
                polygon=list(item.points),
                text=text,
                reading_order=item.order,
                confidence=1.0,
                provenance=Provenance(
                    engine="omnidocbench-ground-truth",
                    source_id=f"{image.name}:{item.annotation_id}",
                    text_confidence=1.0 if text is not None else None,
                    layout_confidence=1.0,
                    metadata={
                        "dataset_revision": dataset_revision,
                        "source_annotation_id": item.annotation_id,
                        "source_annotation_index": item.source_index,
                    },
                ),
                metadata=_element_metadata(item),
            )
        )
    return elements, ids


def _validated_conversion(
    document: Document,
    record: Mapping[str, Any],
    record_index: int,
    image: Path,
    diagnostic: OmniDocBenchProjectionDiagnostic,
    annotations: Sequence[_ValidatedAnnotation],
    retained: Sequence[_ValidatedAnnotation],
    relations: Sequence[Mapping[str, Any]],
    audits: Sequence[Mapping[str, Any]],
) -> OmniDocBenchOracleConversion:
    payload = _pretty_json_bytes(document.model_dump(mode="json", exclude_unset=True))
    round_trip = Document.model_validate_json(payload)
    output_audits = round_trip.pages[0].metadata.get("omnidocbench_annotations")
    if not isinstance(output_audits, list) or not all(
        isinstance(item, Mapping) for item in output_audits
    ):
        raise OmniDocBenchOracleContractError(
            OmniDocBenchConversionReason.TEXT_HASH_MISMATCH, "output omitted annotation audits"
        )
    source_text_hash = _text_authority_hash(audits)
    output_text_hash = _text_authority_hash(output_audits)
    direct_match = all(
        element.text == _element_text(item.raw, _element_type(item.category))
        for element, item in zip(document.pages[0].elements, retained, strict=True)
    )
    if (
        len(output_audits) != len(audits)
        or source_text_hash != output_text_hash
        or not direct_match
    ):
        raise OmniDocBenchOracleContractError(
            OmniDocBenchConversionReason.TEXT_HASH_MISMATCH, "output changed authoritative text"
        )
    extra = document.pages[0].metadata["omnidocbench_extra"]
    output_extra = round_trip.pages[0].metadata.get("omnidocbench_extra")
    if not isinstance(output_extra, Mapping) or _relation_hash(extra) != _relation_hash(
        output_extra
    ):
        raise OmniDocBenchOracleContractError(
            OmniDocBenchConversionReason.RELATION_HASH_MISMATCH, "output changed extra.relation"
        )
    warnings = _conversion_warnings(annotations, relations, diagnostic)
    reasons = list(
        dict.fromkeys(
            [diagnostic.reason_code.value, *(warning.reason_code.value for warning in warnings)]
        )
    )
    report = OmniDocBenchPageConversionReport(
        record_index=record_index,
        image_name=image.name,
        output_name=f"{image.stem}.canonical.json",
        annotation_count=len(annotations),
        projected_element_count=len(retained),
        ignored_count=len(annotations) - len(retained),
        audited_annotation_count=len(audits),
        relation_count=len(relations),
        text_hash_match=True,
        relation_hash_match=True,
        page_geometry_valid=True,
        annotation_ids_unique=True,
        reading_orders_unique=True,
        reading_order_complete=all(item.order is not None for item in retained),
        source_text_sha256=source_text_hash,
        output_text_sha256=output_text_hash,
        annotation_input_sha256=_sha256_bytes(_canonical_json_bytes(record)),
        raster_input_sha256=document.metadata["source_image_sha256"],
        canonical_output_sha256=_sha256_bytes(payload),
        reason_codes=reasons,
        warnings=warnings,
        projection=diagnostic,
    )
    return OmniDocBenchOracleConversion(document=document, diagnostic=diagnostic, report=report)


__all__ = ["convert_omnidocbench_oracle_page"]
