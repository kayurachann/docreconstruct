from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from docreconstruct.ir import BBox, ElementType
from docreconstruct.providers import (
    AzureDocumentIntelligenceProvider,
    HTTPResponse,
    MistralOCRProvider,
    ProviderAuthenticationError,
    ProviderContext,
    ProviderInputError,
    RemoteInferenceDisabledError,
)
from docreconstruct.providers._hosted import load_hosted_source, safe_raw
from docreconstruct.reconstruction.evidence_matching import _orthogonal_page_box


def _mistral_payload() -> dict[str, Any]:
    return {
        "model": "mistral-ocr-test",
        "usage_info": {"pages_processed": 1},
        "pages": [
            {
                "index": 0,
                "markdown": "# Sample title\n\nBody text",
                "dimensions": {"width": 1000, "height": 1400},
                "blocks": [
                    {
                        "id": "block-title",
                        "type": "title",
                        "markdown": "# Sample title",
                        "bbox": [10, 20, 400, 80],
                        "confidence": 0.96,
                    },
                    {
                        "id": "block-table",
                        "type": "table",
                        "html": "<table><tr><th>A</th><th>B</th></tr></table>",
                        "bbox": [10, 100, 600, 300],
                        "confidence": 0.92,
                    },
                ],
                "images": [
                    {
                        "id": "figure-1.png",
                        "top_left_x": 100,
                        "top_left_y": 200,
                        "bottom_right_x": 500,
                        "bottom_right_y": 600,
                        "image_base64": "data:image/png;base64,AAAA",
                    }
                ],
            }
        ],
    }


def _azure_payload() -> dict[str, Any]:
    content = "# Sample title\n\nBody text"
    return {
        "status": "succeeded",
        "analyzeResult": {
            "apiVersion": "2024-11-30",
            "modelId": "prebuilt-layout",
            "contentFormat": "markdown",
            "content": content,
            "pages": [
                {
                    "pageNumber": 1,
                    "width": 8.5,
                    "height": 11,
                    "unit": "inch",
                    "spans": [{"offset": 0, "length": len(content)}],
                    "words": [
                        {
                            "content": "Sample",
                            "confidence": 0.94,
                            "span": {"offset": 2, "length": 6},
                        }
                    ],
                    "formulas": [
                        {
                            "kind": "display",
                            "value": r"\frac{1}{2}",
                            "confidence": 0.91,
                            "polygon": [1, 3, 3, 3, 3, 3.5, 1, 3.5],
                            "span": {"offset": 16, "length": 1},
                        }
                    ],
                    "selectionMarks": [
                        {
                            "state": "selected",
                            "confidence": 0.99,
                            "polygon": [4, 3, 4.2, 3, 4.2, 3.2, 4, 3.2],
                            "span": {"offset": 17, "length": 1},
                        }
                    ],
                }
            ],
            "paragraphs": [
                {
                    "role": "title",
                    "content": "Sample title",
                    "spans": [{"offset": 2, "length": 12}],
                    "boundingRegions": [
                        {
                            "pageNumber": 1,
                            "polygon": [1, 1, 3, 1, 3, 1.5, 1, 1.5],
                        }
                    ],
                }
            ],
            "tables": [
                {
                    "rowCount": 1,
                    "columnCount": 2,
                    "spans": [{"offset": 16, "length": 4}],
                    "boundingRegions": [
                        {
                            "pageNumber": 1,
                            "polygon": [1, 4, 5, 4, 5, 5, 1, 5],
                        }
                    ],
                    "cells": [
                        {"rowIndex": 0, "columnIndex": 0, "content": "A"},
                        {"rowIndex": 0, "columnIndex": 1, "content": "B"},
                    ],
                }
            ],
            "styles": [
                {
                    "isHandwritten": True,
                    "similarFontFamily": "Arial",
                    "fontWeight": "bold",
                    "spans": [{"offset": 2, "length": 12}],
                }
            ],
            "languages": [{"locale": "en", "confidence": 0.98}],
        },
    }


def test_mistral_saved_response_preserves_markdown_geometry_and_raw_provenance() -> None:
    result = MistralOCRProvider().parse(_mistral_payload())
    page = result.document.pages[0]

    assert (page.number, page.width, page.height) == (1, 1000, 1400)
    assert page.metadata["markdown"] == "# Sample title\n\nBody text"
    title = next(element for element in page.elements if element.type is ElementType.TITLE)
    assert title.bbox == BBox(x0=10, y0=20, x1=400, y1=80)
    assert title.confidence == pytest.approx(0.96)
    assert title.provenance is not None
    assert title.provenance.source_id == "block-title"
    assert title.text_candidates[0].value == "# Sample title"
    table = next(element for element in page.elements if element.type is ElementType.TABLE)
    assert table.metadata["html"].startswith("<table>")
    assert table.metadata["content_format"] == "html"

    image = next(element for element in page.elements if element.type is ElementType.IMAGE)
    assert image.metadata["image_ref"] == "figure-1.png"
    assert image.metadata["image"]["data"] == "AAAA"
    assert image.metadata["image"]["mime_type"] == "image/png"
    omitted = image.metadata["raw"]["image_base64"]
    assert omitted["omitted"] is True
    assert omitted["characters"] > 0
    assert "sha256" in omitted


def test_azure_singular_span_records_keep_their_reading_position() -> None:
    """Formulas and selection marks carry `span`, not `spans`.

    Reading only the plural key left them with no content offset, so the
    `default=10**12` sentinel sorted every one of them behind all the prose on
    the page regardless of where they actually appear.
    """

    page = AzureDocumentIntelligenceProvider().parse(_azure_payload()).document.pages[0]

    # Title is at offset 2, formula and table at 16, selection mark at 17.
    assert [element.type for element in page.elements] == [
        ElementType.TITLE,
        ElementType.FORMULA,
        ElementType.TABLE,
        ElementType.CHECKBOX,
    ]
    assert [element.reading_order for element in page.elements] == [0, 1, 2, 3]


@pytest.mark.parametrize(
    ("angle", "expected"),
    [
        (None, 0.0),
        (0.0, 0.0),
        (0.4231, 0.0),
        (-0.7, 0.0),
        (89.6, 90.0),
        (180.0, 180.0),
        (-90.0, 270.0),
    ],
)
def test_azure_content_skew_is_not_treated_as_a_page_quadrant(
    angle: float | None,
    expected: float,
) -> None:
    """`pages[].angle` is measured content skew, not a page rotation.

    `Page.rotation` means an orthogonal quarter turn everywhere else, and
    `_orthogonal_page_box` returns `None` for anything else — so a scan tilted
    by a fraction of a degree projected no boxes at all and the page
    contributed zero evidence.
    """

    payload = _azure_payload()
    if angle is None:
        payload["analyzeResult"]["pages"][0].pop("angle", None)
    else:
        payload["analyzeResult"]["pages"][0]["angle"] = angle

    page = AzureDocumentIntelligenceProvider().parse(payload).document.pages[0]

    assert page.rotation == expected
    assert page.metadata["content_skew_degrees"] == angle
    # Whatever the skew, the page must stay projectable.
    assert _orthogonal_page_box(page, BBox(x0=1, y0=1, x1=2, y1=2)) is not None


def test_azure_saved_response_maps_markdown_blocks_styles_and_inch_geometry() -> None:
    result = AzureDocumentIntelligenceProvider().parse(_azure_payload())
    page = result.document.pages[0]

    assert page.width == pytest.approx(612)
    assert page.height == pytest.approx(792)
    assert page.metadata["markdown"] == "# Sample title\n\nBody text"
    title = next(element for element in page.elements if element.type is ElementType.TITLE)
    assert title.bbox == BBox(x0=72, y0=72, x1=216, y1=108)
    assert title.style.font_family == "Arial"
    assert title.style.font_weight == 700
    assert title.metadata["handwriting"] is True
    assert title.confidence == pytest.approx(0.94)

    table = next(element for element in page.elements if element.type is ElementType.TABLE)
    assert table.metadata["rows"] == [["A", "B"]]
    formula = next(element for element in page.elements if element.type is ElementType.FORMULA)
    assert formula.metadata["latex"] == r"\frac{1}{2}"
    checkbox = next(element for element in page.elements if element.type is ElementType.CHECKBOX)
    assert checkbox.text == "☒"
    assert result.document.metadata["content_markdown"].startswith("# Sample")


@pytest.mark.parametrize(
    ("provider_type", "options"),
    [
        (MistralOCRProvider, {"api_key": "not-used"}),
        (
            AzureDocumentIntelligenceProvider,
            {"endpoint": "https://example.cognitiveservices.azure.com", "api_key": "not-used"},
        ),
    ],
)
def test_hosted_inference_requires_explicit_per_call_privacy_opt_in(
    provider_type: type[MistralOCRProvider] | type[AzureDocumentIntelligenceProvider],
    options: dict[str, Any],
    tmp_path: Path,
) -> None:
    source = tmp_path / "private.pdf"
    source.write_bytes(b"private document")
    calls: list[dict[str, Any]] = []

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(dict(kwargs))
        raise AssertionError("HTTP must not run without explicit consent")

    provider = provider_type(transport=transport)

    with pytest.raises(RemoteInferenceDisabledError, match="allow_remote"):
        provider.parse(source, context=ProviderContext(options=options))
    assert calls == []


def test_missing_credentials_fail_before_any_mistral_http_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    calls: list[Mapping[str, Any]] = []

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(kwargs)
        raise AssertionError("HTTP must not run without credentials")

    with pytest.raises(ProviderAuthenticationError, match="MISTRAL_API_KEY"):
        MistralOCRProvider(transport=transport).parse(
            tmp_path / "missing.pdf",
            context=ProviderContext(options={"allow_remote": True}),
        )
    assert calls == []


def test_missing_credentials_fail_before_any_azure_http_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", raising=False)
    monkeypatch.delenv("DOCUMENTINTELLIGENCE_API_KEY", raising=False)
    calls: list[Mapping[str, Any]] = []

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(kwargs)
        raise AssertionError("HTTP must not run without credentials")

    with pytest.raises(ProviderAuthenticationError, match="AZURE_DOCUMENT_INTELLIGENCE_KEY"):
        AzureDocumentIntelligenceProvider(transport=transport).parse(
            tmp_path / "missing.pdf",
            context=ProviderContext(
                options={
                    "allow_remote": True,
                    "endpoint": "https://example.cognitiveservices.azure.com",
                }
            ),
        )
    assert calls == []


def test_mistral_live_call_uses_mocked_official_http_boundary_without_persisting_secret() -> None:
    calls: list[dict[str, Any]] = []

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(dict(kwargs))
        return HTTPResponse(
            status=200,
            headers={"x-request-id": "mistral-request"},
            body=json.dumps(_mistral_payload()).encode(),
        )

    result = MistralOCRProvider(transport=transport).parse(
        b"%PDF-test",
        context=ProviderContext(
            source="memory.pdf",
            options={"allow_remote": True, "api_key": "mistral-secret"},
        ),
    )

    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.mistral.ai/v1/ocr"
    assert call["headers"]["Authorization"] == "Bearer mistral-secret"
    request = json.loads(call["body"])
    assert request["document"]["type"] == "document_url"
    assert request["document"]["document_url"].startswith("data:application/pdf;base64,")
    assert result.metadata["request_id"] == "mistral-request"
    assert "mistral-secret" not in result.model_dump_json()


def test_signed_source_url_is_sent_but_not_persisted_in_mistral_provenance() -> None:
    calls: list[dict[str, Any]] = []

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(dict(kwargs))
        return HTTPResponse(
            status=200,
            headers={},
            body=json.dumps(_mistral_payload()).encode(),
        )

    secret_url = "https://storage.example/input.pdf?sig=signed-secret#fragment"
    result = MistralOCRProvider(transport=transport).parse(
        secret_url,
        context=ProviderContext(
            options={"allow_remote": True, "api_key": "mistral-secret"},
        ),
    )

    request = json.loads(calls[0]["body"])
    assert request["document"]["document_url"] == secret_url
    assert result.document.source == "https://storage.example/input.pdf"
    serialized = result.model_dump_json()
    assert "signed-secret" not in serialized
    assert "mistral-secret" not in serialized


def test_hosted_helpers_reject_untrusted_endpoints_and_redact_nested_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "page.pdf"
    source.write_bytes(b"x" * 1024)
    with pytest.raises(ProviderInputError, match="official provider endpoint"):
        MistralOCRProvider(transport=lambda **_: pytest.fail("must not call HTTP")).parse(
            source,
            context=ProviderContext(
                options={
                    "allow_remote": True,
                    "api_key": "secret",
                    "endpoint": "https://attacker.example/ocr",
                }
            ),
        )

    raw = {
        "accessToken": "secret-token",
        "message": "fetch https://cdn.example/a.png?sig=secret#fragment now",
    }
    serialized = json.dumps(safe_raw(raw))
    assert "secret-token" not in serialized
    assert "sig=secret" not in serialized
    assert "https://cdn.example/a.png" in serialized

    monkeypatch.setattr(Path, "read_bytes", lambda _: pytest.fail("must reject before read"))
    with pytest.raises(ProviderInputError, match="configured limit"):
        load_hosted_source(source, context=None, maximum_megabytes=0.0001)


def test_azure_live_call_posts_then_polls_through_mocked_http_without_persisting_secret() -> None:
    calls: list[dict[str, Any]] = []
    responses = iter(
        [
            HTTPResponse(
                status=202,
                headers={
                    "Operation-Location": (
                        "https://example.cognitiveservices.azure.com/"
                        "documentintelligence/documentModels/prebuilt-layout/"
                        "analyzeResults/job-1?api-version=2024-11-30"
                    )
                },
            ),
            HTTPResponse(status=200, headers={}, body=b'{"status":"running"}'),
            HTTPResponse(
                status=200,
                headers={"apim-request-id": "azure-request"},
                body=json.dumps(_azure_payload()).encode(),
            ),
        ]
    )

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(dict(kwargs))
        return next(responses)

    slept: list[float] = []
    result = AzureDocumentIntelligenceProvider(
        transport=transport,
        sleeper=slept.append,
    ).parse(
        b"%PDF-test",
        context=ProviderContext(
            source="memory.pdf",
            options={
                "allow_remote": True,
                "api_key": "azure-secret",
                "endpoint": "https://example.cognitiveservices.azure.com",
                "poll_interval_seconds": 0,
            },
        ),
    )

    assert [call["method"] for call in calls] == ["POST", "GET", "GET"]
    assert "outputContentFormat=markdown" in calls[0]["url"]
    assert calls[0]["headers"]["Ocp-Apim-Subscription-Key"] == "azure-secret"
    assert "base64Source" in json.loads(calls[0]["body"])
    assert result.metadata["request_id"] == "azure-request"
    assert slept == []
    assert "azure-secret" not in result.model_dump_json()


def test_mistral_page_range_response_keeps_its_absolute_page_numbers() -> None:
    """Mistral's ``index`` is always zero-based.

    The provider inferred that only when the response happened to contain
    index 0, so every page-range request came back shifted: pages 2-4 of a
    document were labelled 1-3 and silently overwrote the wrong originals.
    """

    payload = _mistral_payload()
    first = payload["pages"][0]
    payload["pages"] = [
        {**first, "index": index, "blocks": first["blocks"], "images": []} for index in (1, 2, 3)
    ]

    result = MistralOCRProvider().parse(payload)

    assert [page.number for page in result.document.pages] == [2, 3, 4]


def test_mistral_full_document_numbering_is_unchanged() -> None:
    payload = _mistral_payload()
    first = payload["pages"][0]
    payload["pages"] = [{**first, "index": index, "images": []} for index in (0, 1, 2)]

    result = MistralOCRProvider().parse(payload)

    assert [page.number for page in result.document.pages] == [1, 2, 3]


def test_mistral_blocks_without_geometry_are_kept_against_the_page_box() -> None:
    """A bbox-less block was dropped entirely.

    Losing it also suppressed the markdown fallback, so a response whose blocks
    all lacked geometry produced a page with no elements at all rather than the
    recognized text. Sibling providers keep the content and label the
    coordinate system instead.
    """

    payload = _mistral_payload()
    page_payload = payload["pages"][0]
    for block in page_payload["blocks"]:
        block.pop("bbox")
    page_payload["images"][0] = {
        "id": "figure-1.png",
        "image_base64": "data:image/png;base64,AAAA",
    }

    page = MistralOCRProvider().parse(payload).document.pages[0]

    title = next(element for element in page.elements if element.type is ElementType.TITLE)
    assert title.text_candidates[0].value == "# Sample title"
    assert title.bbox == BBox(x0=0, y0=0, x1=1000, y1=1400)
    assert title.metadata["coordinate_system"] == "full_page_fallback"

    image = next(element for element in page.elements if element.type is ElementType.IMAGE)
    assert image.metadata["coordinate_system"] == "full_page_fallback"


def test_mistral_blocks_with_geometry_are_labelled_as_source() -> None:
    page = MistralOCRProvider().parse(_mistral_payload()).document.pages[0]
    title = next(element for element in page.elements if element.type is ElementType.TITLE)

    assert title.metadata["coordinate_system"] == "source"
