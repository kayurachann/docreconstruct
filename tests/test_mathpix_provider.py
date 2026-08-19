from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from docreconstruct.ir import BBox, ElementType
from docreconstruct.providers import (
    HTTPResponse,
    MathpixProvider,
    ProviderAuthenticationError,
    ProviderContext,
    ProviderExecutionMode,
    RemoteInferenceDisabledError,
)


def _image_payload() -> dict[str, Any]:
    return {
        "request_id": "image-request-1",
        "app_id": "must-not-persist",
        "app_key": "must-not-persist-either",
        "text": "# Result\n\n\\(x^2\\)",
        "confidence": 0.94,
        "confidence_rate": 0.97,
        "is_printed": True,
        "is_handwritten": True,
        "image_width": 900,
        "image_height": 1200,
        "version": "SuperNet-test",
        "line_data": [
            {
                "id": "title-1",
                "type": "title",
                "text": "Result",
                "cnt": [[20, 30], [400, 30], [400, 90], [20, 90]],
                "confidence": 0.99,
                "conversion_output": True,
            },
            {
                "id": "math-1",
                "type": "math",
                "text": r"\(x^2\)",
                "cnt": [[20, 120], [300, 120], [300, 200], [20, 200]],
                "confidence": 0.93,
                "data": [{"type": "latex", "value": "x^2"}],
            },
            {
                "id": "table-1",
                "type": "table",
                "text": "A | B",
                "cnt": [[20, 240], [500, 240], [500, 400], [20, 400]],
                "confidence_rate": 0.91,
                "data": [{"type": "tsv", "value": "A\tB\n1\t2"}],
            },
            {
                "id": "figure-1",
                "type": "diagram",
                "text_display": (
                    "![plot](https://cdn.mathpix.com/cropped/plot.png?sig=private-token)"
                ),
                "cnt": [[520, 240], [850, 240], [850, 600], [520, 600]],
            },
        ],
    }


def _pdf_lines_payload() -> dict[str, Any]:
    return {
        "pages": [
            {
                "image-id": "job-1-1",
                "page": 1,
                "page_width": 1000,
                "page_height": 1400,
                "lines": [
                    {
                        "id": "p1-title",
                        "line": 1,
                        "type": "section_header",
                        "text": "Chapter 1",
                        "text_display": "# Chapter 1",
                        "conversion_output": True,
                        "cnt": [[40, 50], [600, 50], [600, 110], [40, 110]],
                        "confidence": 0.98,
                    },
                    {
                        "id": "p1-chart",
                        "line": 2,
                        "type": "chart",
                        "subtype": "line",
                        "text": "",
                        "cnt": [[40, 150], [800, 150], [800, 700], [40, 700]],
                        "confidence_rate": 0.89,
                    },
                ],
            }
        ]
    }


def test_mathpix_saved_image_maps_mmd_geometry_confidence_and_specialists() -> None:
    result = MathpixProvider().parse(_image_payload())
    page = result.document.pages[0]

    assert (page.width, page.height) == (900, 1200)
    assert page.metadata["markdown"].startswith("# Result")
    assert page.metadata["is_handwritten"] is True

    title = next(element for element in page.elements if element.type is ElementType.TITLE)
    assert title.bbox == BBox(x0=20, y0=30, x1=400, y1=90)
    assert title.confidence == pytest.approx(0.99)

    formula = next(element for element in page.elements if element.type is ElementType.FORMULA)
    assert formula.metadata["latex"] == "x^2"
    table = next(element for element in page.elements if element.type is ElementType.TABLE)
    assert table.metadata["rows"] == [["A", "B"], ["1", "2"]]
    figure = next(element for element in page.elements if element.type is ElementType.FIGURE)
    assert figure.metadata["image_ref"] == "https://cdn.mathpix.com/cropped/plot.png"

    serialized = result.model_dump_json()
    assert "must-not-persist" not in serialized
    assert "private-token" not in serialized


def test_mathpix_saved_pdf_lines_preserve_reading_order_and_page_mmd() -> None:
    payload = _pdf_lines_payload()
    payload["mmd"] = "# Chapter 1\n\n![chart](https://cdn.example/chart.png?token=secret)"
    result = MathpixProvider().parse(payload)
    page = result.document.pages[0]

    assert (page.number, page.width, page.height) == (1, 1000, 1400)
    assert [element.reading_order for element in page.elements] == [0, 1]
    assert page.elements[0].type is ElementType.HEADING
    assert page.elements[1].type is ElementType.CHART
    assert result.document.metadata["content_markdown"].startswith("# Chapter 1")
    assert "token=secret" not in result.model_dump_json()


def test_mathpix_capabilities_declare_saved_and_hosted_stem_extraction() -> None:
    capabilities = MathpixProvider().capabilities

    assert capabilities.execution_modes == [
        ProviderExecutionMode.SAVED,
        ProviderExecutionMode.API,
    ]
    assert capabilities.handwriting
    assert capabilities.formulas
    assert capabilities.tables
    assert capabilities.charts
    assert capabilities.markdown
    assert capabilities.bounding_boxes
    assert capabilities.confidence_scores
    assert capabilities.credential_env_vars == ["MATHPIX_APP_ID", "MATHPIX_APP_KEY"]


def test_mathpix_requires_per_call_remote_opt_in_before_http(tmp_path: Path) -> None:
    source = tmp_path / "private.pdf"
    source.write_bytes(b"%PDF-private")
    calls: list[Mapping[str, Any]] = []

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(kwargs)
        raise AssertionError("HTTP must not run without explicit consent")

    with pytest.raises(RemoteInferenceDisabledError, match="allow_remote"):
        MathpixProvider(transport=transport).parse(
            source,
            context=ProviderContext(options={"app_id": "id", "app_key": "key"}),
        )
    assert calls == []


def test_mathpix_missing_credentials_fail_before_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MATHPIX_APP_ID", raising=False)
    monkeypatch.delenv("MATHPIX_APP_KEY", raising=False)
    calls: list[Mapping[str, Any]] = []

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(kwargs)
        raise AssertionError("HTTP must not run without credentials")

    with pytest.raises(ProviderAuthenticationError, match="MATHPIX_APP_ID"):
        MathpixProvider(transport=transport).parse(
            tmp_path / "missing.pdf",
            context=ProviderContext(options={"allow_remote": True}),
        )
    assert calls == []


def test_mathpix_live_image_uses_official_json_contract_and_redacts_secrets() -> None:
    calls: list[dict[str, Any]] = []

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(dict(kwargs))
        return HTTPResponse(
            status=200,
            headers={"x-request-id": "header-request"},
            body=json.dumps(_image_payload()).encode(),
        )

    result = MathpixProvider(transport=transport).parse(
        b"small-png-payload",
        context=ProviderContext(
            source="memory.png",
            options={
                "allow_remote": True,
                "app_id": "private-app-id",
                "app_key": "private-app-key",
                "media_type": "image/png",
            },
        ),
    )

    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.mathpix.com/v3/text"
    assert call["headers"]["app_id"] == "private-app-id"
    assert call["headers"]["app_key"] == "private-app-key"
    request = json.loads(call["body"])
    assert request["src"].startswith("data:image/png;base64,")
    assert request["formats"] == ["text", "data"]
    assert request["include_line_data"] is True
    assert request["enable_document_layout"] is True
    assert request["metadata"]["improve_mathpix"] is False
    assert result.metadata["request_id"] == "header-request"
    assert "private-app" not in result.model_dump_json()


def test_mathpix_signed_image_url_is_sent_but_never_persisted() -> None:
    calls: list[dict[str, Any]] = []

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(dict(kwargs))
        return HTTPResponse(status=200, headers={}, body=json.dumps(_image_payload()).encode())

    secret_url = "https://storage.example/page.png?sig=source-secret#fragment"
    result = MathpixProvider(transport=transport).parse(
        secret_url,
        context=ProviderContext(
            options={
                "allow_remote": True,
                "app_id": "private-app-id",
                "app_key": "private-app-key",
            }
        ),
    )

    assert json.loads(calls[0]["body"])["src"] == secret_url
    assert result.document.source == "https://storage.example/page.png"
    assert "source-secret" not in result.model_dump_json()


def test_mathpix_live_pdf_submits_polls_and_downloads_lines_and_mmd() -> None:
    calls: list[dict[str, Any]] = []
    responses = iter(
        [
            HTTPResponse(status=202, headers={}, body=b'{"pdf_id":"job-1"}'),
            HTTPResponse(status=200, headers={}, body=b'{"status":"split"}'),
            HTTPResponse(
                status=200,
                headers={"x-request-id": "pdf-request"},
                body=(
                    b'{"status":"completed","version":"pdf-v1",'
                    b'"app_id":"private-app-id","app_key":"private-app-key"}'
                ),
            ),
            HTTPResponse(
                status=200,
                headers={},
                body=json.dumps(_pdf_lines_payload()).encode(),
            ),
            HTTPResponse(
                status=200,
                headers={"Content-Type": "text/plain"},
                body=(
                    b"# Chapter 1\n\n![chart](https://cdn.mathpix.com/chart.png?sig=result-secret)"
                ),
            ),
        ]
    )

    def transport(**kwargs: Any) -> HTTPResponse:
        calls.append(dict(kwargs))
        return next(responses)

    slept: list[float] = []
    result = MathpixProvider(transport=transport, sleeper=slept.append).parse(
        b"%PDF-test",
        context=ProviderContext(
            source="memory.pdf",
            options={
                "allow_remote": True,
                "app_id": "private-app-id",
                "app_key": "private-app-key",
                "poll_interval_seconds": 0.25,
            },
        ),
    )

    assert [call["method"] for call in calls] == ["POST", "GET", "GET", "GET", "GET"]
    assert calls[0]["url"] == "https://api.mathpix.com/v3/pdf"
    assert calls[1]["url"].endswith("/v3/pdf/job-1")
    assert calls[3]["url"].endswith("/v3/pdf/job-1.lines.json")
    assert calls[4]["url"].endswith("/v3/pdf/job-1.mmd")
    assert calls[0]["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="options_json"' in calls[0]["body"]
    assert b'"improve_mathpix": false' in calls[0]["body"]
    assert slept == [0.25]
    assert result.metadata["pdf_id"] == "job-1"
    assert result.metadata["request_id"] == "pdf-request"
    serialized = result.model_dump_json()
    assert "private-app" not in serialized
    assert "result-secret" not in serialized
