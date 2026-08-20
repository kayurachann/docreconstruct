from __future__ import annotations

import json
from typing import Any

import pytest

from docreconstruct.providers import (
    HTTPResponse,
    PaddleOCROfficialProvider,
    ProviderContext,
    ProviderHTTPError,
    ProviderInputError,
    RemoteInferenceDisabledError,
)


def _page() -> dict[str, Any]:
    return {
        "prunedResult": {
            "parsing_res_list": [
                {
                    "block_bbox": [10, 20, 210, 55],
                    "block_content": "Xin chào",
                    "block_label": "text",
                    "block_order": 1,
                }
            ]
        },
        "markdown": {"text": "Xin chào", "images": {}},
        "inputImage": "https://storage.example.test/input.png",
    }


def _json_response(payload: Any, status: int = 200) -> HTTPResponse:
    return HTTPResponse(
        status=status,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode("utf-8"),
    )


def test_official_provider_declares_distinct_hosted_capabilities() -> None:
    provider = PaddleOCROfficialProvider()

    assert provider.name == "paddleocr_official"
    assert provider.capabilities.live_inference is True
    assert provider.capabilities.saved_json is True
    assert provider.capabilities.bounding_boxes is True
    assert provider.capabilities.credential_env_vars == ["PADDLEOCR_ACCESS_TOKEN"]


def test_official_provider_normalizes_saved_jsonl_shape() -> None:
    provider = PaddleOCROfficialProvider()
    result = provider.parse([{"result": {"layoutParsingResults": [_page()]}}])

    assert result.provider == "paddleocr_official"
    assert len(result.document.pages) == 1
    assert result.document.pages[0].elements[0].text == "Xin chào"
    assert result.document.pages[0].elements[0].provenance.engine == "paddleocr_official"


def test_official_provider_calls_submit_poll_and_jsonl_without_replaying_token() -> None:
    calls: list[dict[str, Any]] = []
    responses = iter(
        [
            _json_response({"code": 0, "data": {"jobId": "job-123"}}, status=202),
            _json_response(
                {
                    "code": 0,
                    "data": {
                        "state": "done",
                        "resultUrl": {"jsonUrl": "https://storage.example.test/result.jsonl"},
                    },
                }
            ),
            HTTPResponse(
                status=200,
                headers={"content-type": "application/x-ndjson"},
                body=(
                    json.dumps({"result": {"layoutParsingResults": [_page()]}}).encode("utf-8")
                    + b"\n"
                ),
            ),
        ]
    )

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(kwargs)
        return next(responses)

    provider = PaddleOCROfficialProvider(
        transport=transport,
        resolver=lambda _hostname, _port: ("93.184.216.34",),
    )
    result = provider.parse(
        b"synthetic-image",
        context=ProviderContext(
            source="scan.png",
            options={
                "allow_remote": True,
                "access_token": "operator-only-token",
                "media_type": "image/png",
                "use_doc_unwarping": True,
                "use_chart_recognition": True,
            },
        ),
    )

    assert result.document.pages[0].elements[0].text == "Xin chào"
    assert result.metadata["endpoint"] == "https://paddleocr.aistudio-app.com"
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
    assert calls[0]["headers"]["Authorization"] == "Bearer operator-only-token"
    assert calls[0]["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b"PaddleOCR-VL-1.6" in calls[0]["body"]
    assert b'"useDocUnwarping":true' in calls[0]["body"]
    assert calls[1]["url"].endswith("/api/v2/ocr/jobs/job-123")
    assert calls[2]["url"] == "https://storage.example.test/result.jsonl"
    assert "Authorization" not in calls[2]["headers"]
    assert "operator-only-token" not in json.dumps(result.metadata)


def test_official_provider_requires_remote_consent() -> None:
    provider = PaddleOCROfficialProvider(transport=lambda **_: pytest.fail("no HTTP"))

    with pytest.raises(RemoteInferenceDisabledError):
        provider.parse(
            b"image",
            context=ProviderContext(options={"access_token": "secret"}),
        )


def test_official_provider_rejects_untrusted_base_url() -> None:
    provider = PaddleOCROfficialProvider(transport=lambda **_: pytest.fail("no HTTP"))

    with pytest.raises(ProviderInputError, match="official provider endpoint"):
        provider.parse(
            b"image",
            context=ProviderContext(
                options={
                    "allow_remote": True,
                    "access_token": "secret",
                    "base_url": "https://attacker.example.test",
                }
            ),
        )


def test_official_provider_rejects_non_https_result_url() -> None:
    responses = iter(
        [
            _json_response({"code": 0, "data": {"jobId": "job-123"}}, status=202),
            _json_response(
                {
                    "code": 0,
                    "data": {
                        "state": "done",
                        "resultUrl": {"jsonUrl": "http://storage.example.test/result.jsonl"},
                    },
                }
            ),
        ]
    )
    provider = PaddleOCROfficialProvider(transport=lambda **_: next(responses))

    with pytest.raises(ProviderInputError, match="absolute HTTPS URL"):
        provider.parse(
            b"image",
            context=ProviderContext(options={"allow_remote": True, "access_token": "secret"}),
        )


@pytest.mark.parametrize(
    ("json_url", "addresses"),
    [
        ("https://127.0.0.1/result.jsonl", ("127.0.0.1",)),
        ("https://storage.example.test/result.jsonl", ("10.0.0.9",)),
    ],
)
def test_official_provider_rejects_private_result_targets(
    json_url: str, addresses: tuple[str, ...]
) -> None:
    responses = iter(
        [
            _json_response({"code": 0, "data": {"jobId": "job-123"}}, status=202),
            _json_response(
                {
                    "code": 0,
                    "data": {"state": "done", "resultUrl": {"jsonUrl": json_url}},
                }
            ),
        ]
    )
    provider = PaddleOCROfficialProvider(
        transport=lambda **_: next(responses),
        resolver=lambda _hostname, _port: addresses,
    )

    with pytest.raises(ProviderInputError, match="public host"):
        provider.parse(
            b"image",
            context=ProviderContext(options={"allow_remote": True, "access_token": "secret"}),
        )


def test_official_provider_polling_timeout_is_bounded() -> None:
    calls = 0
    now = [0.0]

    def transport(**_kwargs: Any) -> HTTPResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _json_response({"code": 0, "data": {"jobId": "job-123"}}, status=202)
        return _json_response({"code": 0, "data": {"state": "pending"}})

    def sleeper(seconds: float) -> None:
        now[0] += seconds

    provider = PaddleOCROfficialProvider(
        transport=transport,
        sleeper=sleeper,
        clock=lambda: now[0],
    )

    with pytest.raises(ProviderHTTPError, match="polling timeout"):
        provider.parse(
            b"image",
            context=ProviderContext(
                options={
                    "allow_remote": True,
                    "access_token": "secret",
                    "timeout_seconds": 1,
                    "poll_timeout_seconds": 1,
                }
            ),
        )
    assert calls == 3
