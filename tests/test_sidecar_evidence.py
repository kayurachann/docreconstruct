from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from docreconstruct.evidence import (
    SidecarEvidenceError,
    detect_sidecar_provider,
    load_sidecar_evidence,
)
from docreconstruct.providers import ProviderContext
from docreconstruct.providers.mistral_ocr import MistralOCRProvider


def _write_json(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _canonical_payload() -> dict[str, Any]:
    return {
        "id": "canonical",
        "pages": [
            {
                "id": "page-1",
                "number": 1,
                "width": 100,
                "height": 200,
                "elements": [],
            }
        ],
        "schema_version": "0.1",
    }


@pytest.mark.parametrize(
    ("expected", "payload"),
    [
        ("json", _canonical_payload()),
        (
            "paddleocr",
            {
                "page_index": 0,
                "width": 100,
                "height": 200,
                "res": {
                    "rec_texts": ["Paddle"],
                    "rec_scores": [0.91],
                    "rec_boxes": [[1, 2, 40, 20]],
                },
            },
        ),
        (
            "mineru",
            {
                "pdf_info": [
                    {
                        "page_idx": 0,
                        "page_size": [100, 200],
                        "para_blocks": [
                            {
                                "type": "text",
                                "bbox": [1, 2, 40, 20],
                                "text": "MinerU",
                                "score": 0.92,
                            }
                        ],
                    }
                ]
            },
        ),
        (
            "olmocr",
            {
                "natural_text": "olmOCR",
                "metadata": {"page_number": 1, "width": 100, "height": 200},
                "confidence": 0.93,
            },
        ),
        (
            "mistral_ocr",
            {
                "model": "mistral-ocr-test",
                "pages": [
                    {
                        "index": 0,
                        "markdown": "Mistral",
                        "dimensions": {"width": 100, "height": 200},
                    }
                ],
            },
        ),
        (
            "azure_document_intelligence",
            {
                "status": "succeeded",
                "analyzeResult": {
                    "apiVersion": "2024-11-30",
                    "modelId": "prebuilt-layout",
                    "content": "Azure",
                    "pages": [{"pageNumber": 1, "width": 100, "height": 200, "unit": "pixel"}],
                    "paragraphs": [
                        {
                            "content": "Azure",
                            "spans": [{"offset": 0, "length": 5}],
                            "boundingRegions": [
                                {"pageNumber": 1, "polygon": [1, 2, 40, 2, 40, 20, 1, 20]}
                            ],
                        }
                    ],
                },
            },
        ),
        (
            "mathpix",
            {
                "mmd": "Mathpix",
                "image_width": 100,
                "image_height": 200,
                "confidence": 0.94,
            },
        ),
        (
            "google_document_ai",
            {
                "document": {
                    "text": "Google",
                    "mimeType": "application/pdf",
                    "pages": [
                        {
                            "pageNumber": 1,
                            "dimension": {"width": 100, "height": 200, "unit": "pixel"},
                            "paragraphs": [
                                {
                                    "layout": {
                                        "textAnchor": {
                                            "textSegments": [{"startIndex": "0", "endIndex": "6"}]
                                        },
                                        "confidence": 0.95,
                                        "boundingPoly": {
                                            "vertices": [
                                                {"x": 1, "y": 2},
                                                {"x": 40, "y": 2},
                                                {"x": 40, "y": 20},
                                                {"x": 1, "y": 20},
                                            ]
                                        },
                                    }
                                }
                            ],
                        }
                    ],
                }
            },
        ),
        (
            "aws_textract",
            {
                "DetectDocumentTextModelVersion": "1.0",
                "DocumentMetadata": {"Pages": 1},
                "Blocks": [
                    {
                        "BlockType": "PAGE",
                        "Id": "page-1",
                        "Page": 1,
                        "Geometry": {"BoundingBox": {"Left": 0, "Top": 0, "Width": 1, "Height": 1}},
                    },
                    {
                        "BlockType": "LINE",
                        "Id": "line-1",
                        "Page": 1,
                        "Text": "AWS",
                        "Confidence": 96,
                        "Geometry": {
                            "BoundingBox": {
                                "Left": 0.1,
                                "Top": 0.1,
                                "Width": 0.4,
                                "Height": 0.1,
                            }
                        },
                    },
                ],
            },
        ),
    ],
)
def test_detects_all_builtin_saved_schemas_and_normalizes_offline(
    tmp_path: Path, expected: str, payload: dict[str, Any]
) -> None:
    detection = detect_sidecar_provider(payload)
    assert detection.provider == expected
    assert detection.confidence >= 0.90
    assert detection.reason

    sidecar = _write_json(tmp_path / f"{expected}.json", payload)
    bundle = load_sidecar_evidence([sidecar])

    assert bundle.errors == ()
    assert len(bundle.documents) == 1
    assert bundle.items[0].provider == expected
    assert bundle.items[0].detection == detection
    assert bundle.items[0].document is bundle.documents[0]


def test_normalized_evidence_retains_confidence_provenance_and_original_context(
    tmp_path: Path,
) -> None:
    sidecar = _write_json(
        tmp_path / "paddle.json",
        {
            "res": {
                "rec_texts": ["evidence"],
                "rec_scores": [0.87],
                "rec_boxes": [[1, 2, 30, 12]],
            }
        },
    )
    bundle = load_sidecar_evidence([sidecar], context=ProviderContext(source="original-scan.pdf"))
    document = bundle.documents[0]
    element = document.pages[0].elements[0]

    assert document.source == "original-scan.pdf"
    assert element.confidence == pytest.approx(0.87)
    assert element.provenance is not None
    assert element.provenance.engine == "paddleocr"
    assert element.provenance.text_confidence == pytest.approx(0.87)
    assert element.text_candidates[0].confidence == pytest.approx(0.87)


def test_path_provider_expressions_and_mapping_override_ambiguous_payloads(
    tmp_path: Path,
) -> None:
    first = _write_json(tmp_path / "first.json", {"text": "linearized one"})
    second = _write_json(tmp_path / "second.json", {"text": "linearized two"})

    assert detect_sidecar_provider({"text": "linearized"}).provider is None
    expressions = load_sidecar_evidence(
        [first, second],
        provider_hints=[f"{first}=olmocr", f"{second}=mathpix"],
    )
    assert [item.provider for item in expressions.items] == ["olmocr", "mathpix"]
    assert all(item.detection.explicit for item in expressions.items)
    assert expressions.errors == ()

    mapped = load_sidecar_evidence(
        [first, second],
        provider_hints={first: "olm_ocr", second.name: "mathpix_ocr"},
    )
    assert [item.provider for item in mapped.items] == ["olmocr", "mathpix"]


def test_non_strict_mode_aggregates_per_file_errors_and_keeps_good_documents(
    tmp_path: Path,
) -> None:
    good = _write_json(tmp_path / "good.json", _canonical_payload())
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid JSON", encoding="utf-8")
    unknown = _write_json(tmp_path / "unknown.json", {"unrelated": True})

    bundle = load_sidecar_evidence([good, bad, unknown], strict=False)

    assert len(bundle.documents) == 1
    assert len(bundle.errors) == 2
    assert str(bad) in bundle.errors[0]
    assert "valid JSON" in bundle.errors[0]
    assert str(unknown) in bundle.errors[1]
    assert "could not identify JSON schema" in bundle.errors[1]
    with pytest.raises(SidecarEvidenceError, match="bad.json"):
        bundle.raise_for_errors()


def test_strict_mode_fails_at_first_sidecar_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    later = _write_json(tmp_path / "later.json", _canonical_payload())

    with pytest.raises(SidecarEvidenceError, match=r"missing\.json"):
        load_sidecar_evidence([missing, later], strict=True)


def test_unknown_explicit_provider_is_a_per_file_error_in_non_strict_mode(
    tmp_path: Path,
) -> None:
    sidecar = _write_json(tmp_path / "result.json", _canonical_payload())

    bundle = load_sidecar_evidence(
        [sidecar], provider_hints={sidecar: "not-a-provider"}, strict=False
    )

    assert bundle.documents == ()
    assert "unknown sidecar provider" in bundle.errors[0]


def test_loader_never_calls_hosted_parse_or_live_inference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sidecar = _write_json(
        tmp_path / "mistral.json",
        {
            "model": "mistral-ocr-test",
            "pages": [
                {
                    "index": 0,
                    "markdown": "saved only",
                    "dimensions": {"width": 100, "height": 200},
                }
            ],
        },
    )

    def forbidden_parse(*args: object, **kwargs: object) -> object:
        raise AssertionError("hosted parse/live inference must not run")

    monkeypatch.setattr(MistralOCRProvider, "parse", forbidden_parse)
    bundle = load_sidecar_evidence([sidecar])

    assert bundle.errors == ()
    assert bundle.items[0].provider == "mistral_ocr"


def test_detects_wrapped_page_records_used_by_saved_adapters() -> None:
    olmocr = {
        "records": [
            {
                "text": "page one",
                "metadata": {"page_number": 1, "width": 100, "height": 200},
            }
        ]
    }
    paddle = {
        "results": [
            {
                "page_index": 0,
                "res": {"rec_texts": ["one"], "rec_boxes": [[1, 2, 3, 4]]},
            }
        ]
    }

    assert detect_sidecar_provider(olmocr).provider == "olmocr"
    assert detect_sidecar_provider(paddle).provider == "paddleocr"
