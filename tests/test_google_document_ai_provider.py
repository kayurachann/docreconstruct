from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from docreconstruct.ir import BBox, ElementType
from docreconstruct.providers import (
    GoogleDocumentAIProvider,
    HTTPResponse,
    ProviderAuthenticationError,
    ProviderContext,
    ProviderExecutionMode,
    ProviderInputError,
    RemoteInferenceDisabledError,
)


def _layout(
    start: int,
    end: int,
    vertices: list[dict[str, float | int]],
    *,
    confidence: float,
    normalized: bool = False,
) -> dict[str, Any]:
    key = "normalizedVertices" if normalized else "vertices"
    return {
        "textAnchor": {"textSegments": [{"startIndex": str(start), "endIndex": str(end)}]},
        "confidence": confidence,
        "boundingPoly": {key: vertices},
    }


def _payload() -> dict[str, Any]:
    text = "Invoice\nTotal\n$42\nAlpha Beta\n"
    return {
        "humanReviewStatus": {"state": "SKIPPED", "humanReviewOperation": ""},
        "document": {
            "docid": "google-result-1",
            "mimeType": "application/pdf",
            "text": text,
            "pages": [
                {
                    "pageNumber": 1,
                    "image": {
                        "width": 1000,
                        "height": 2000,
                        "mimeType": "image/png",
                        "content": "image-bytes-must-not-persist",
                    },
                    "dimension": {"width": 8.5, "height": 11, "unit": "in"},
                    "debugAccessToken": "saved-response-secret",
                    "sourceUri": (
                        "https://storage.example/result.json?signature=saved-url-secret#fragment"
                    ),
                    "layout": {
                        **_layout(
                            0,
                            len(text),
                            [{"x": 0, "y": 0}, {"x": 1000, "y": 2000}],
                            confidence=0.99,
                        ),
                        "orientation": "PAGE_RIGHT",
                    },
                    "detectedLanguages": [{"languageCode": "en", "confidence": 0.98}],
                    "paragraphs": [
                        {
                            "layout": _layout(
                                0,
                                7,
                                [
                                    {"x": 0.1, "y": 0.1},
                                    {"x": 0.5, "y": 0.1},
                                    {"x": 0.5, "y": 0.2},
                                    {"x": 0.1, "y": 0.2},
                                ],
                                confidence=0.97,
                                normalized=True,
                            ),
                            "detectedLanguages": [{"languageCode": "en-US", "confidence": 0.96}],
                        },
                        {
                            "layout": _layout(
                                18,
                                29,
                                [
                                    {"x": 100, "y": 900},
                                    {"x": 600, "y": 900},
                                    {"x": 600, "y": 980},
                                    {"x": 100, "y": 980},
                                ],
                                confidence=0.9,
                            )
                        },
                    ],
                    "blocks": [
                        {
                            "layout": _layout(
                                0,
                                29,
                                [
                                    {"x": 50, "y": 100},
                                    {"x": 700, "y": 100},
                                    {"x": 700, "y": 1000},
                                    {"x": 50, "y": 1000},
                                ],
                                confidence=0.88,
                            )
                        }
                    ],
                    "tokens": [
                        {
                            "layout": _layout(
                                0,
                                7,
                                [
                                    {"x": 100, "y": 200},
                                    {"x": 500, "y": 200},
                                    {"x": 500, "y": 400},
                                    {"x": 100, "y": 400},
                                ],
                                confidence=0.95,
                            ),
                            "detectedBreak": {"type": "SPACE"},
                            "detectedLanguages": [{"languageCode": "en", "confidence": 0.94}],
                            "styleInfo": {
                                "fontSize": 12,
                                "fontType": "Roboto",
                                "fontWeight": 700,
                                "bold": True,
                                "italic": True,
                                "underlined": False,
                                "handwritten": True,
                                "textColor": {"red": 1, "green": 0.5, "blue": 0},
                            },
                        },
                        {
                            "layout": _layout(
                                18,
                                23,
                                [
                                    {"x": 100, "y": 900},
                                    {"x": 300, "y": 900},
                                    {"x": 300, "y": 980},
                                    {"x": 100, "y": 980},
                                ],
                                confidence=0.89,
                            ),
                            "styleInfo": {"handwritten": False},
                        },
                    ],
                    "tables": [
                        {
                            "layout": _layout(
                                8,
                                17,
                                [
                                    {"x": 100, "y": 500},
                                    {"x": 700, "y": 500},
                                    {"x": 700, "y": 800},
                                    {"x": 100, "y": 800},
                                ],
                                confidence=0.93,
                            ),
                            "headerRows": [
                                {
                                    "cells": [
                                        {
                                            "layout": _layout(
                                                8,
                                                13,
                                                [{"x": 100, "y": 500}, {"x": 400, "y": 600}],
                                                confidence=0.92,
                                            )
                                        }
                                    ]
                                }
                            ],
                            "bodyRows": [
                                {
                                    "cells": [
                                        {
                                            "layout": _layout(
                                                14,
                                                17,
                                                [{"x": 100, "y": 600}, {"x": 400, "y": 700}],
                                                confidence=0.91,
                                            )
                                        }
                                    ]
                                }
                            ],
                        }
                    ],
                    "formFields": [
                        {
                            "fieldName": _layout(
                                8,
                                13,
                                [{"x": 100, "y": 1100}, {"x": 300, "y": 1200}],
                                confidence=0.94,
                            ),
                            "fieldValue": _layout(
                                14,
                                17,
                                [{"x": 350, "y": 1100}, {"x": 500, "y": 1200}],
                                confidence=0.9,
                            ),
                            "correctedKeyText": "Amount",
                            "correctedValueText": "42 USD",
                            "nameDetectedLanguages": [{"languageCode": "en", "confidence": 0.99}],
                            "valueDetectedLanguages": [{"languageCode": "en", "confidence": 0.97}],
                        }
                    ],
                    "imageQualityScores": {"qualityScore": 0.87},
                }
            ],
        },
    }


def test_saved_process_response_normalizes_all_requested_document_ai_evidence() -> None:
    result = GoogleDocumentAIProvider().parse(_payload())
    page = result.document.pages[0]

    assert (page.number, page.width, page.height, page.rotation) == (1, 1000, 2000, 90)
    assert page.metadata["detected_languages"] == [
        {"language_code": "en", "confidence": pytest.approx(0.98)}
    ]
    assert page.metadata["handwriting"] is True

    paragraph = next(element for element in page.elements if element.id == "page-1-paragraph-1")
    assert paragraph.type is ElementType.PARAGRAPH
    assert paragraph.text == "Invoice"
    assert paragraph.bbox == BBox(x0=100, y0=200, x1=500, y1=400)
    assert paragraph.confidence == pytest.approx(0.97)
    assert paragraph.metadata["coordinate_system"] == "normalized_scaled"

    block = next(element for element in page.elements if element.id == "page-1-block-1")
    assert block.text == "Invoice\nTotal\n$42\nAlpha Beta\n"
    token = next(element for element in page.elements if element.id == "page-1-token-1")
    assert token.metadata["handwriting"] is True
    assert token.metadata["detected_break"] == {"type": "SPACE"}
    assert token.style.font_family == "Roboto"
    assert token.style.font_size == 12
    assert token.style.font_weight == 700
    assert token.style.italic is True
    assert token.style.color == "#FF8000"

    table = next(element for element in page.elements if element.type is ElementType.TABLE)
    assert table.metadata["rows"] == [["Total"], ["$42"]]
    assert table.metadata["markdown"] == "| Total |\n| --- |\n| $42 |"
    field = next(
        element for element in page.elements if element.metadata["record_kind"] == "form_field"
    )
    assert field.text == "**Amount:** 42 USD"
    assert field.metadata["field_name"] == "Amount"
    assert field.metadata["field_value"] == "42 USD"
    assert field.confidence == pytest.approx(0.9)

    assert [element.reading_order for element in page.elements] == list(range(len(page.elements)))
    assert "Invoice" in page.metadata["markdown"]
    assert "| Total |" in page.metadata["markdown"]
    assert result.document.metadata["content_markdown"] == page.metadata["markdown"]
    assert result.document.metadata["process_response"]["human_review_status"]["state"] == (
        "SKIPPED"
    )
    serialized = result.model_dump_json()
    assert "image-bytes-must-not-persist" not in serialized
    assert "saved-response-secret" not in serialized
    assert "saved-url-secret" not in serialized
    assert page.metadata["raw"]["image"]["content"]["omitted"] is True
    assert page.metadata["raw"]["sourceUri"] == "https://storage.example/result.json"


def test_saved_document_directly_maps_checkbox_form_field() -> None:
    payload = _payload()["document"]
    assert isinstance(payload, dict)
    page = payload["pages"][0]
    page["formFields"] = [
        {
            "fieldName": {
                "textAnchor": {"content": "Approved"},
                "confidence": 0.97,
                "boundingPoly": {"vertices": [{"x": 10, "y": 10}, {"x": 50, "y": 30}]},
            },
            "fieldValue": {
                "textAnchor": {"content": ""},
                "confidence": 0.96,
                "boundingPoly": {"vertices": [{"x": 60, "y": 10}, {"x": 80, "y": 30}]},
            },
            "valueType": "filled_checkbox",
        }
    ]

    result = GoogleDocumentAIProvider().parse(payload)
    checkbox = next(
        element
        for element in result.document.pages[0].elements
        if element.type is ElementType.CHECKBOX
    )

    assert checkbox.text == "**Approved:** ☒"
    assert checkbox.metadata["selected"] is True
    assert checkbox.metadata["field_value"] == "☒"


def test_capabilities_declare_saved_and_explicit_hosted_document_ai() -> None:
    capabilities = GoogleDocumentAIProvider().capabilities

    assert capabilities.execution_modes == [
        ProviderExecutionMode.SAVED,
        ProviderExecutionMode.API,
    ]
    assert capabilities.tables
    assert capabilities.styles
    assert capabilities.handwriting
    assert capabilities.markdown
    assert capabilities.bounding_boxes
    assert capabilities.confidence_scores
    assert capabilities.credential_env_vars == ["GOOGLE_DOCUMENT_AI_ACCESS_TOKEN"]


def test_live_processing_requires_explicit_remote_opt_in_before_http(tmp_path: Path) -> None:
    source = tmp_path / "private.pdf"
    source.write_bytes(b"private document")
    calls: list[Mapping[str, Any]] = []

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(kwargs)
        raise AssertionError("HTTP must not run")

    with pytest.raises(RemoteInferenceDisabledError, match="allow_remote"):
        GoogleDocumentAIProvider(transport=transport).parse(
            source,
            context=ProviderContext(
                options={
                    "access_token": "secret",
                    "project_id": "sample-project",
                    "location": "us",
                    "processor_id": "processor-1",
                }
            ),
        )
    assert calls == []


def test_missing_access_token_fails_before_source_or_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("GOOGLE_DOCUMENT_AI_ACCESS_TOKEN", raising=False)
    calls: list[Mapping[str, Any]] = []

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(kwargs)
        raise AssertionError("HTTP must not run")

    with pytest.raises(ProviderAuthenticationError, match="GOOGLE_DOCUMENT_AI_ACCESS_TOKEN"):
        GoogleDocumentAIProvider(transport=transport).parse(
            tmp_path / "does-not-exist.pdf",
            context=ProviderContext(options={"allow_remote": True}),
        )
    assert calls == []


def test_mocked_live_processor_version_request_is_offline_and_never_persists_token() -> None:
    calls: list[dict[str, Any]] = []

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(dict(kwargs))
        return HTTPResponse(
            status=200,
            headers={"x-goog-request-id": "google-request-1"},
            body=json.dumps(_payload()).encode(),
        )

    signed_label = "https://storage.example/input.pdf?signature=private#fragment"
    result = GoogleDocumentAIProvider(transport=transport).parse(
        b"%PDF-test",
        context=ProviderContext(
            source=signed_label,
            options={
                "allow_remote": True,
                "access_token": "google-secret-token",
                "project_id": "sample-project",
                "location": "us",
                "processor_id": "processor-1",
                "processor_version": "stable",
                "process_options": {"ocrConfig": {"premiumFeatures": {"computeStyleInfo": True}}},
                "field_mask": "text,pages.pageNumber,pages.paragraphs,pages.tokens",
                "imageless_mode": True,
            },
        ),
    )

    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == (
        "https://us-documentai.googleapis.com/v1/projects/sample-project/locations/us/"
        "processors/processor-1/processorVersions/stable:process"
    )
    assert call["headers"]["Authorization"] == "Bearer google-secret-token"
    body = json.loads(call["body"])
    assert body["rawDocument"]["mimeType"] == "application/pdf"
    assert body["rawDocument"]["content"] == "JVBERi10ZXN0"
    assert body["processOptions"]["ocrConfig"]["premiumFeatures"] == {"computeStyleInfo": True}
    assert body["imagelessMode"] is True
    assert result.metadata["request_id"] == "google-request-1"
    assert result.document.source == "https://storage.example/input.pdf"
    serialized = result.model_dump_json()
    assert "google-secret-token" not in serialized
    assert "private" not in serialized
    assert "fragment" not in serialized


def test_mocked_gcs_request_uses_official_gcs_document_shape_and_global_endpoint() -> None:
    calls: list[dict[str, Any]] = []

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(dict(kwargs))
        return HTTPResponse(status=200, headers={}, body=json.dumps(_payload()).encode())

    GoogleDocumentAIProvider(transport=transport).parse(
        "gs://sample-bucket/input.pdf",
        context=ProviderContext(
            options={
                "allow_remote": True,
                "access_token": "secret",
                "project_id": "sample-project",
                "location": "global",
                "processor_id": "processor-1",
                "media_type": "application/pdf",
            }
        ),
    )

    assert calls[0]["url"].startswith("https://documentai.googleapis.com/v1/")
    request = json.loads(calls[0]["body"])
    assert request == {
        "gcsDocument": {
            "gcsUri": "gs://sample-bucket/input.pdf",
            "mimeType": "application/pdf",
        }
    }


def test_https_signed_source_is_rejected_without_echoing_or_sending_it() -> None:
    calls: list[Mapping[str, Any]] = []

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(kwargs)
        raise AssertionError("HTTP must not run")

    secret_url = "https://storage.example/input.pdf?signature=top-secret#fragment"
    with pytest.raises(ProviderInputError) as captured:
        GoogleDocumentAIProvider(transport=transport).parse(
            secret_url,
            context=ProviderContext(
                options={
                    "allow_remote": True,
                    "access_token": "secret",
                    "project_id": "sample-project",
                    "location": "us",
                    "processor_id": "processor-1",
                }
            ),
        )

    assert "top-secret" not in str(captured.value)
    assert "fragment" not in str(captured.value)
    assert calls == []
