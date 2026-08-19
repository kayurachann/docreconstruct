from __future__ import annotations

import json
import sys
import types
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

from docreconstruct.api.app import create_app
from docreconstruct.ir import Document, Page, SourceType


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app()) as test_client:
        yield test_client


def _document(document_id: str = "api-result") -> Document:
    return Document(
        id=document_id,
        pages=[
            Page(
                id="page-1",
                number=1,
                width=100,
                height=200,
                source_type=SourceType.IMAGE,
            )
        ],
    )


def test_health_and_openapi_are_available(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["api_version"] == "v1"
    assert client.get("/openapi.json").status_code == 200


def test_discovery_endpoints_are_truthful_shapes(client: TestClient) -> None:
    providers = client.get("/v1/providers")
    formats = client.get("/v1/formats")

    assert providers.status_code == 200
    assert isinstance(providers.json()["providers"], list)
    assert formats.status_code == 200
    format_rows = formats.json()["formats"]
    assert any(row["name"] == "json" and row["available"] for row in format_rows)
    assert any(row["direction"] == "input" for row in format_rows)


def test_analyze_stages_upload_and_delegates_to_pipeline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import docreconstruct.pipeline as pipeline

    call: dict[str, Any] = {}

    def fake_analyze(source: Path, **kwargs: Any) -> Document:
        call["source"] = source
        call["content"] = source.read_bytes()
        call["kwargs"] = kwargs
        return _document("analyzed")

    monkeypatch.setattr(pipeline, "analyze", fake_analyze)
    response = client.post(
        "/v1/analyze",
        files={"file": ("scan.png", b"synthetic image", "image/png")},
        data={"options": json.dumps({"engines": ["fake"], "fusion": True})},
    )

    assert response.status_code == 200, response.text
    assert response.json()["document"]["id"] == "analyzed"
    assert response.json()["engines"] == ["fake"]
    assert call["content"] == b"synthetic image"
    assert call["kwargs"] == {
        "engines": ["fake"],
        "fusion": True,
        "provider_options": None,
    }
    assert not call["source"].exists()


def test_reconstruct_returns_rendered_download(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import docreconstruct.pipeline as pipeline

    call: dict[str, Any] = {}

    def fake_reconstruct(source: Path, **kwargs: Any) -> Document:
        call["source_name"] = source.name
        call["kwargs"] = kwargs
        Path(kwargs["output"]).write_text("<!doctype html><p>reconstructed</p>", encoding="utf-8")
        return _document("reconstructed")

    monkeypatch.setattr(pipeline, "reconstruct", fake_reconstruct)
    response = client.post(
        "/v1/reconstruct",
        files={"file": ("source.png", b"synthetic image", "image/png")},
        data={
            "options": json.dumps(
                {
                    "output_format": "html",
                    "profile": "editable",
                    "output_filename": "result.html",
                }
            )
        },
    )

    assert response.status_code == 200, response.text
    assert response.text == "<!doctype html><p>reconstructed</p>"
    assert response.headers["content-type"].startswith("text/html")
    assert "result.html" in response.headers["content-disposition"]
    assert call["source_name"] == "source.png"
    assert call["kwargs"]["output_format"] == "html"
    assert call["kwargs"]["profile"] == "editable"


def test_route_returns_cost_aware_plan(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import docreconstruct.pipeline as pipeline

    monkeypatch.setattr(pipeline, "analyze", lambda source, **kwargs: _document("routed"))
    response = client.post(
        "/v1/route",
        files={"file": ("scan.png", b"synthetic image", "image/png")},
        data={"options": json.dumps({"confidence_threshold": 0.8})},
    )

    assert response.status_code == 200, response.text
    plan = response.json()["plan"]
    assert plan["document_id"] == "routed"
    assert plan["tasks"][0]["primary_provider"] == "paddleocr"
    assert plan["tasks"][0]["require_consensus"] is False


def test_compare_passes_artifacts_to_evaluation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_evaluate(reference: Path, candidate: Path, **kwargs: Any) -> Any:
        assert reference.name == "reference.png"
        assert candidate.name == "candidate.png"
        assert reference.read_bytes() == b"reference"
        assert candidate.read_bytes() == b"candidate"
        assert kwargs == {"profile": "balanced", "output_format": None}
        return SimpleNamespace(
            fidelity=SimpleNamespace(overall=0.75),
            to_dict=lambda: {"fidelity": {"overall": 0.75}, "components": {}},
        )

    fake_evaluation = types.ModuleType("docreconstruct.evaluation")
    fake_evaluation.evaluate = fake_evaluate  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "docreconstruct.evaluation", fake_evaluation)

    response = client.post(
        "/v1/compare",
        files={
            "reference": ("reference.png", b"reference", "image/png"),
            "candidate": ("candidate.png", b"candidate", "image/png"),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["overall_score"] == pytest.approx(0.75)


def test_compare_scores_real_raster_pair(client: TestClient) -> None:
    from PIL import Image

    payload = BytesIO()
    Image.new("RGB", (8, 8), "white").save(payload, format="PNG")
    image_bytes = payload.getvalue()

    response = client.post(
        "/v1/compare",
        files={
            "reference": ("reference.png", image_bytes, "image/png"),
            "candidate": ("candidate.png", image_bytes, "image/png"),
        },
    )

    assert response.status_code == 200, response.text
    report = response.json()["report"]
    assert report["visual"]["score"] == pytest.approx(1.0)
    assert report["visual"]["pages_compared"] == 1
    assert "visual" in report["metadata"]["measured_components"]


def test_real_raster_reconstruction_returns_self_contained_html(client: TestClient) -> None:
    from PIL import Image

    payload = BytesIO()
    Image.new("RGB", (16, 12), "white").save(payload, format="PNG")
    response = client.post(
        "/v1/reconstruct",
        files={"file": ("page.png", payload.getvalue(), "image/png")},
        data={"options": json.dumps({"output_format": "html"})},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert "data:image/png;base64," in response.text
    assert "dr-page" in response.text


@pytest.mark.parametrize(
    ("options", "expected_detail"),
    [
        ("not-json", "options must be valid JSON"),
        (json.dumps({"unexpected": True}), "Extra inputs are not permitted"),
        (json.dumps({"output_filename": "../escape.html"}), "plain filename"),
    ],
)
def test_reconstruct_rejects_invalid_options(
    client: TestClient, options: str, expected_detail: str
) -> None:
    response = client.post(
        "/v1/reconstruct",
        files={"file": ("source.png", b"source", "image/png")},
        data={"options": options},
    )

    assert response.status_code == 422
    assert expected_detail in response.text


def test_empty_upload_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/v1/analyze",
        files={"file": ("empty.png", b"", "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "uploaded file is empty"


@pytest.mark.parametrize(
    "blocked_options",
    [
        {
            "provider_options": {
                "provider_sources": {"paddleocr": "C:/private/provider-output.json"}
            }
        },
        {"provider_options": {"custom": {"template_path": "/etc/private-template.docx"}}},
        {"renderer_options": {"template_path": "/etc/private-template.docx"}},
    ],
)
def test_upload_api_rejects_server_file_options(
    client: TestClient, blocked_options: dict[str, Any]
) -> None:
    response = client.post(
        "/v1/reconstruct",
        files={"file": ("source.png", b"source", "image/png")},
        data={"options": json.dumps(blocked_options)},
    )

    assert response.status_code == 422
    assert "not allowed by the upload API" in response.text or "Extra inputs" in response.text
