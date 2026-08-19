from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from docreconstruct.providers import (
    HTTPResponse,
    PaddleOCRVLServerProvider,
    ProviderContext,
    ProviderInputError,
    RemoteInferenceDisabledError,
    registry,
)


def _server_payload() -> dict[str, Any]:
    return {
        "logId": "request-123",
        "errorCode": 0,
        "errorMsg": "Success",
        "result": {
            "layoutParsingResults": [
                {
                    "prunedResult": {
                        "width": 1000,
                        "height": 1400,
                        "parsing_res_list": [
                            {
                                "block_id": 7,
                                "block_order": 0,
                                "block_label": "title",
                                "block_bbox": [100, 80, 900, 160],
                                "block_content": "Fast editable documents",
                                "score": 0.98,
                            }
                        ],
                    },
                    "markdown": {"text": "# Fast editable documents", "images": None},
                    "outputImages": {},
                    "inputImage": None,
                }
            ]
        },
    }


def test_server_provider_is_registered_with_live_geometry_capabilities() -> None:
    provider = registry.get("paddleocr-vl-server")
    assert isinstance(provider, PaddleOCRVLServerProvider)
    assert provider.capabilities.live_inference is True
    assert provider.capabilities.bounding_boxes is True
    assert provider.capabilities.formulas is True


def test_saved_official_server_response_normalizes_without_http() -> None:
    provider = PaddleOCRVLServerProvider(transport=lambda **_: pytest.fail("no HTTP"))
    result = provider.parse(_server_payload())

    assert result.document.pages[0].width == 1000
    assert result.document.pages[0].height == 1400
    element = result.document.pages[0].elements[0]
    assert element.text == "Fast editable documents"
    assert element.reading_order == 0
    assert element.provenance is not None
    assert element.provenance.engine == "paddleocr_vl_server"


def test_live_server_requires_explicit_remote_consent() -> None:
    with pytest.raises(RemoteInferenceDisabledError):
        PaddleOCRVLServerProvider().parse(b"image", context=ProviderContext())


def test_live_loopback_server_uses_full_pipeline_api_and_fast_payload() -> None:
    captured: dict[str, Any] = {}

    def transport(**kwargs: Any) -> HTTPResponse:
        captured.update(kwargs)
        return HTTPResponse(
            status=200,
            headers={"x-request-id": "header-request"},
            body=json.dumps(_server_payload()).encode(),
        )

    context = ProviderContext(
        source="page.png",
        options={
            "allow_remote": True,
            "endpoint": "http://127.0.0.1:8080",
            "media_type": "image/png",
            "server_token": "top-secret",
            "timeout_seconds": 7.5,
            "use_doc_unwarping": True,
        },
    )
    result = PaddleOCRVLServerProvider(transport=transport).parse(
        b"fake-png",
        context=context,
    )

    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:8080/layout-parsing"
    assert captured["timeout"] == 7.5
    assert captured["headers"]["Authorization"] == "Bearer top-secret"
    request = json.loads(captured["body"])
    assert base64.b64decode(request["file"]) == b"fake-png"
    assert request["fileType"] == 1
    assert request["visualize"] is False
    assert request["returnMarkdownImages"] is False
    assert request["useDocUnwarping"] is True
    assert result.metadata["request_id"] == "header-request"
    serialized = result.model_dump_json()
    assert "top-secret" not in serialized
    assert result.document.pages[0].elements[0].text == "Fast editable documents"


def test_markdown_images_are_returned_only_after_explicit_opt_in() -> None:
    captured: dict[str, Any] = {}

    def transport(**kwargs: Any) -> HTTPResponse:
        captured.update(kwargs)
        return HTTPResponse(status=200, headers={}, body=json.dumps(_server_payload()).encode())

    PaddleOCRVLServerProvider(transport=transport).parse(
        b"fake-png",
        context=ProviderContext(
            options={
                "allow_remote": True,
                "endpoint": "http://localhost:8080",
                "media_type": "image/png",
                "return_markdown_images": True,
            }
        ),
    )

    assert json.loads(captured["body"])["returnMarkdownImages"] is True


def test_pdf_request_sets_official_file_type(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-fast")
    captured: dict[str, Any] = {}

    def transport(**kwargs: Any) -> HTTPResponse:
        captured.update(kwargs)
        return HTTPResponse(status=200, headers={}, body=json.dumps(_server_payload()).encode())

    PaddleOCRVLServerProvider(transport=transport).parse(
        source,
        context=ProviderContext(
            options={"allow_remote": True, "endpoint": "http://localhost:8080"}
        ),
    )
    assert json.loads(captured["body"])["fileType"] == 0


def test_remote_endpoint_requires_https_and_explicit_operator_trust() -> None:
    provider = PaddleOCRVLServerProvider(transport=lambda **_: pytest.fail("no HTTP"))
    with pytest.raises(ProviderInputError, match="HTTPS"):
        provider.parse(
            b"image",
            context=ProviderContext(
                options={"allow_remote": True, "endpoint": "http://gpu.example.com:8080"}
            ),
        )
    with pytest.raises(ProviderInputError, match="allow_custom_endpoint"):
        provider.parse(
            b"image",
            context=ProviderContext(
                options={"allow_remote": True, "endpoint": "https://gpu.example.com"}
            ),
        )
