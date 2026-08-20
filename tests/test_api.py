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
    hybrid = client.get("/v1/hybrid/capabilities")

    assert providers.status_code == 200
    assert isinstance(providers.json()["providers"], list)
    assert formats.status_code == 200
    format_rows = formats.json()["formats"]
    assert any(row["name"] == "json" and row["available"] for row in format_rows)
    assert any(row["direction"] == "input" for row in format_rows)
    assert hybrid.status_code == 200
    assert hybrid.json()["evidence_required"] is True
    assert "upload_json" in hybrid.json()["evidence_modes"]
    assert ("hosted_ocr" in hybrid.json()["evidence_modes"]) is hybrid.json()[
        "server_generates_json"
    ]
    assert hybrid.json()["browser_credentials_accepted"] is False
    assert isinstance(hybrid.json()["verified_available"], bool)
    assert isinstance(hybrid.json()["remote_assets_available"], bool)


def test_hybrid_capabilities_expose_allowlist_without_secrets_or_urls(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS",
        "paddleocr_official,mistral_ocr,paddleocr_vl_server,unknown_provider",
    )
    monkeypatch.setenv("PADDLEOCR_ACCESS_TOKEN", "secret-official-paddle-value")
    monkeypatch.setenv("MISTRAL_API_KEY", "secret-mistral-value")
    monkeypatch.setenv("PADDLEOCR_VL_SERVER_URL", "https://private.example.test/ocr")
    monkeypatch.setenv("PADDLEOCR_VL_SERVER_TOKEN", "secret-paddle-value")

    response = client.get("/v1/hybrid/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert [row["name"] for row in payload["hosted_ocr_providers"]] == [
        "paddleocr_official",
        "mistral_ocr",
        "paddleocr_vl_server",
    ]
    assert all(row["available"] for row in payload["hosted_ocr_providers"])
    assert all(
        set(row["supported_inputs"]) <= {"pdf", "png", "jpeg", "tiff"}
        for row in payload["hosted_ocr_providers"]
    )
    assert payload["server_generates_json"] is True
    assert payload["evidence_modes"] == ["upload_json", "hosted_ocr"]
    serialized = response.text
    assert "secret-mistral-value" not in serialized
    assert "secret-paddle-value" not in serialized
    assert "secret-official-paddle-value" not in serialized
    assert "private.example.test" not in serialized
    assert "MISTRAL_API_KEY" not in serialized
    assert "PADDLEOCR_VL_SERVER_URL" not in serialized


def test_hybrid_capabilities_do_not_offer_unconfigured_generation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS", "paddleocr_official")
    monkeypatch.delenv("PADDLEOCR_ACCESS_TOKEN", raising=False)

    response = client.get("/v1/hybrid/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence_modes"] == ["upload_json"]
    assert payload["server_generates_json"] is False
    assert payload["hosted_ocr_providers"] == [
        {
            "name": "paddleocr_official",
            "label": "PaddleOCR official cloud",
            "available": False,
            "cost": "unknown",
            "privacy": "third_party",
            "supported_inputs": ["pdf", "png", "jpeg", "tiff"],
            "capabilities": [
                "geometry",
                "reading_order",
                "tables",
                "images",
                "handwriting",
                "formulas",
                "charts",
                "distorted_photos",
                "dewarping",
            ],
            "reason": "not configured by the server operator",
        }
    ]


def test_hybrid_capabilities_report_operator_features_without_launching_tools(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    renderer = tmp_path / "soffice"
    renderer.write_bytes(b"configured renderer placeholder")
    monkeypatch.setenv("DOCRECONSTRUCT_LIBREOFFICE_PATH", str(renderer))
    monkeypatch.setenv("DOCRECONSTRUCT_ALLOW_REMOTE_ASSETS", "true")

    payload = client.get("/v1/hybrid/capabilities").json()

    assert payload["verified_available"] is True
    assert payload["remote_assets_available"] is True


def test_google_document_ai_requires_location_before_becoming_available(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS", "google_document_ai")
    monkeypatch.setenv("GOOGLE_DOCUMENT_AI_ACCESS_TOKEN", "operator-secret")
    monkeypatch.setenv("GOOGLE_DOCUMENT_AI_PROJECT_ID", "project")
    monkeypatch.setenv("GOOGLE_DOCUMENT_AI_PROCESSOR_ID", "processor")
    monkeypatch.delenv("GOOGLE_DOCUMENT_AI_LOCATION", raising=False)

    unavailable = client.get("/v1/hybrid/capabilities").json()
    assert unavailable["server_generates_json"] is False
    assert unavailable["hosted_ocr_providers"][0]["available"] is False

    monkeypatch.setenv("GOOGLE_DOCUMENT_AI_LOCATION", "us")
    available = client.get("/v1/hybrid/capabilities").json()
    assert available["server_generates_json"] is True
    assert available["hosted_ocr_providers"][0]["available"] is True


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


@pytest.mark.parametrize("endpoint", ["/v1/analyze", "/v1/route", "/v1/reconstruct"])
def test_general_upload_endpoints_block_unpublished_hosted_ocr(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "operator-secret")
    monkeypatch.delenv("DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS", raising=False)

    response = client.post(
        endpoint,
        files={"file": ("scan.png", b"source", "image/png")},
        data={"options": json.dumps({"engines": ["mistral_ocr"]})},
    )

    assert response.status_code == 422
    assert "not offered by this server" in response.text


def test_analyze_uses_allowlisted_hosted_ocr_with_server_managed_consent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import docreconstruct.pipeline as pipeline

    call: dict[str, Any] = {}

    def fake_analyze(source: Path, **kwargs: Any) -> Document:
        call.update(kwargs)
        return _document("hosted-analysis")

    monkeypatch.setenv("DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS", "mistral_ocr")
    monkeypatch.setenv("MISTRAL_API_KEY", "operator-secret")
    monkeypatch.setattr(pipeline, "analyze", fake_analyze)
    response = client.post(
        "/v1/analyze",
        files={"file": ("scan.png", b"source", "image/png")},
        data={
            "options": json.dumps(
                {
                    "engines": ["mistral_ocr"],
                    "provider_options": {"mistral_ocr": {"include_blocks": False}},
                }
            )
        },
    )

    assert response.status_code == 200, response.text
    assert call["provider_options"] == {
        "mistral_ocr": {"include_blocks": False, "allow_remote": True}
    }


def test_analyze_preserves_saved_hosted_provider_json_without_cloud_allowlist(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import docreconstruct.pipeline as pipeline

    call: dict[str, Any] = {}

    def fake_analyze(source: Path, **kwargs: Any) -> Document:
        call.update(kwargs)
        return _document("saved-analysis")

    monkeypatch.delenv("DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS", raising=False)
    monkeypatch.setattr(pipeline, "analyze", fake_analyze)
    response = client.post(
        "/v1/analyze",
        files={"file": ("mistral.json", b'{"pages": []}', "application/json")},
        data={"options": json.dumps({"engines": ["mistral_ocr"]})},
    )

    assert response.status_code == 200, response.text
    assert call["provider_options"] is None


@pytest.mark.parametrize(
    "blocked",
    [
        {"allow_remote": True},
        {"allow_custom_endpoint": True},
        {"api_key": "browser-secret"},
        {"access_token": "browser-secret"},
        {"endpoint": "https://attacker.example.test"},
        {"base_url": "https://attacker.example.test"},
        {"headers": {"Authorization": "Bearer browser-secret"}},
    ],
)
def test_analyze_rejects_client_managed_hosted_credentials_and_endpoints(
    client: TestClient,
    blocked: dict[str, Any],
) -> None:
    response = client.post(
        "/v1/analyze",
        files={"file": ("scan.png", b"source", "image/png")},
        data={
            "options": json.dumps(
                {
                    "engines": ["mistral_ocr"],
                    "provider_options": {"mistral_ocr": blocked},
                }
            )
        },
    )

    assert response.status_code == 422
    assert "managed by the server" in response.text


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


def test_hybrid_upload_runs_project_job_and_returns_docx(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import docreconstruct.reconstruction.hybrid_job as hybrid_job

    call: dict[str, Any] = {}

    def fake_run_hybrid_job(content: Path, layout: Path, **kwargs: Any) -> Any:
        call["content"] = content.read_text(encoding="utf-8")
        call["layout"] = layout.read_bytes()
        evidence = kwargs["evidence"]
        call["evidence"] = evidence.read_bytes()
        call["kwargs"] = kwargs
        Path(kwargs["output"]).write_bytes(b"editable-docx")
        return SimpleNamespace(
            validation=SimpleNamespace(
                passed=True,
                score=0.987654,
                metrics={"rendered_visual": None},
            ),
            phase_seconds={
                "prepare.scan": 0.125,
                "prepare.evidence_match": 0.25,
                "job.total": 0.5,
                "secret.provider": 999,
            },
        )

    monkeypatch.setattr(hybrid_job, "run_hybrid_job", fake_run_hybrid_job)
    response = client.post(
        "/v1/hybrid",
        files={
            "content": ("content.md", b"# Exact wording", "text/markdown"),
            "layout": ("layout.png", b"layout-pixels", "image/png"),
            "evidence": ("paddle.json", b'{"pages": []}', "application/json"),
        },
        data={
            "options": json.dumps(
                {
                    "evidence_provider": "paddleocr",
                    "output_filename": "finished.docx",
                }
            )
        },
    )

    assert response.status_code == 200, response.text
    assert response.content == b"editable-docx"
    assert "finished.docx" in response.headers["content-disposition"]
    assert response.headers["x-docreconstruct-quality"] == "fast"
    assert response.headers["x-docreconstruct-qa-score"] == "0.987654"
    assert "x-docreconstruct-visual-score" not in response.headers
    assert response.headers["x-docreconstruct-duration"] == "0.500000"
    assert response.headers["server-timing"] == (
        "scan;dur=125.000, evidence;dur=250.000, total;dur=500.000"
    )
    assert "secret" not in response.headers["server-timing"]
    assert call["content"] == "# Exact wording"
    assert call["layout"] == b"layout-pixels"
    assert call["evidence"] == b'{"pages": []}'
    assert call["kwargs"]["evidence_provider_hints"] == "paddleocr"
    assert call["kwargs"]["render_backend"] == "native"
    assert call["kwargs"]["online_ocr"] is None


def test_hybrid_requires_uploaded_or_generated_json_evidence(client: TestClient) -> None:
    response = client.post(
        "/v1/hybrid",
        files={
            "content": ("content.md", b"text", "text/markdown"),
            "layout": ("layout.png", b"layout", "image/png"),
        },
    )

    assert response.status_code == 422
    assert "requires OCR JSON evidence" in response.text


def test_hybrid_uses_operator_allowlisted_hosted_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import docreconstruct.reconstruction.hybrid_job as hybrid_job

    call: dict[str, Any] = {}

    def fake_run_hybrid_job(content: Path, layout: Path, **kwargs: Any) -> Any:
        call.update(kwargs)
        Path(kwargs["output"]).write_bytes(b"hosted-docx")
        return SimpleNamespace(validation=SimpleNamespace(passed=True, score=1.0, metrics={}))

    monkeypatch.setenv("DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS", "mistral_ocr")
    monkeypatch.setenv("MISTRAL_API_KEY", "operator-secret")
    monkeypatch.setattr(hybrid_job, "run_hybrid_job", fake_run_hybrid_job)
    response = client.post(
        "/v1/hybrid",
        files={
            "content": ("content.md", b"text", "text/markdown"),
            "layout": ("layout.png", b"layout", "image/png"),
        },
        data={
            "options": json.dumps(
                {
                    "ocr_provider": "mistral_ocr",
                    "ocr_languages": ["vi", "en", "vi"],
                    "ocr_handwriting": True,
                    "ocr_charts": True,
                    "ocr_distorted_photo": True,
                    "ocr_dewarping": True,
                }
            )
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["x-docreconstruct-ocr"] == "mistral_ocr"
    online = call["online_ocr"]
    assert online.providers == ("mistral_ocr",)
    assert online.languages == ("vi", "en")
    assert online.handwriting is True
    assert online.charts is True
    assert online.distorted_photo is True
    assert online.dewarping is True
    assert online.provider_options is None


def test_hybrid_rejects_hosted_provider_not_offered_by_operator(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "operator-secret")
    monkeypatch.delenv("DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS", raising=False)

    response = client.post(
        "/v1/hybrid",
        files={
            "content": ("content.md", b"text", "text/markdown"),
            "layout": ("layout.png", b"layout", "image/png"),
        },
        data={"options": json.dumps({"ocr_provider": "mistral_ocr"})},
    )

    assert response.status_code == 422
    assert "not offered by this server" in response.text


@pytest.mark.parametrize(
    ("filename", "media_type"),
    [
        ("document.pdf", "application/pdf"),
        ("page.png", "image/png"),
        ("photo.jpg", "image/jpeg"),
        ("photo.jpeg", "image/jpeg"),
        ("scan.tif", "image/tiff"),
        ("scan.tiff", "image/tiff"),
    ],
)
def test_hybrid_paddle_official_accepts_supported_layout_formats(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    media_type: str,
) -> None:
    import docreconstruct.reconstruction.hybrid_job as hybrid_job

    call: dict[str, Any] = {}

    def fake_run_hybrid_job(content: Path, layout: Path, **kwargs: Any) -> Any:
        call.update(kwargs)
        Path(kwargs["output"]).write_bytes(b"official-paddle-docx")
        return SimpleNamespace(validation=SimpleNamespace(passed=True, score=1.0, metrics={}))

    monkeypatch.setenv("DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS", "paddleocr_official")
    monkeypatch.setenv("PADDLEOCR_ACCESS_TOKEN", "operator-secret")
    monkeypatch.setattr(hybrid_job, "run_hybrid_job", fake_run_hybrid_job)

    response = client.post(
        "/v1/hybrid",
        files={
            "content": ("content.md", b"text", "text/markdown"),
            "layout": (filename, b"source", media_type),
        },
        data={"options": json.dumps({"ocr_provider": "paddleocr_official"})},
    )

    assert response.status_code == 200, response.text
    assert call["online_ocr"].providers == ("paddleocr_official",)


@pytest.mark.parametrize(
    ("filename", "media_type"),
    [("page.webp", "image/webp"), ("page.bmp", "image/bmp")],
)
def test_hybrid_paddle_official_rejects_unsupported_layout_formats(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    media_type: str,
) -> None:
    monkeypatch.setenv("DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS", "paddleocr_official")
    monkeypatch.setenv("PADDLEOCR_ACCESS_TOKEN", "operator-secret")

    response = client.post(
        "/v1/hybrid",
        files={
            "content": ("content.md", b"text", "text/markdown"),
            "layout": (filename, b"source", media_type),
        },
        data={"options": json.dumps({"ocr_provider": "paddleocr_official"})},
    )

    assert response.status_code == 422
    assert "choose a file ending in" in response.text


def test_hybrid_hosted_ocr_rejects_mime_filename_mismatch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS", "paddleocr_official")
    monkeypatch.setenv("PADDLEOCR_ACCESS_TOKEN", "operator-secret")

    response = client.post(
        "/v1/hybrid",
        files={
            "content": ("content.md", b"text", "text/markdown"),
            "layout": ("page.png", b"source", "image/webp"),
        },
        data={"options": json.dumps({"ocr_provider": "paddleocr_official"})},
    )

    assert response.status_code == 422
    assert "is not supported for hosted OCR" in response.text


def test_hybrid_verified_requires_operator_libreoffice_configuration(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DOCRECONSTRUCT_LIBREOFFICE_PATH", raising=False)
    response = client.post(
        "/v1/hybrid",
        files={
            "content": ("content.md", b"text", "text/markdown"),
            "layout": ("layout.png", b"layout", "image/png"),
            "evidence": ("evidence.json", b'{"pages": []}', "application/json"),
        },
        data={"options": json.dumps({"quality": "verified"})},
    )

    assert response.status_code == 503
    assert "DOCRECONSTRUCT_LIBREOFFICE_PATH" in response.text


def test_hybrid_paddleocr_requires_operator_endpoint(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS", "paddleocr_vl_server")
    monkeypatch.delenv("PADDLEOCR_VL_SERVER_URL", raising=False)
    response = client.post(
        "/v1/hybrid",
        files={
            "content": ("content.md", b"text", "text/markdown"),
            "layout": ("layout.png", b"layout", "image/png"),
        },
        data={"options": json.dumps({"use_paddleocr_vl": True})},
    )

    assert response.status_code == 503
    assert "not configured by the server operator" in response.text


def test_hybrid_paddleocr_uses_operator_managed_server(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import docreconstruct.reconstruction.hybrid_job as hybrid_job

    call: dict[str, Any] = {}

    def fake_run_hybrid_job(content: Path, layout: Path, **kwargs: Any) -> Any:
        call.update(kwargs)
        Path(kwargs["output"]).write_bytes(b"paddle-docx")
        return SimpleNamespace(validation=SimpleNamespace(passed=True, score=1.0, metrics={}))

    monkeypatch.setenv("PADDLEOCR_VL_SERVER_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS", "paddleocr_vl_server")
    monkeypatch.setattr(hybrid_job, "run_hybrid_job", fake_run_hybrid_job)
    response = client.post(
        "/v1/hybrid",
        files={
            "content": ("content.md", b"text", "text/markdown"),
            "layout": ("layout.png", b"layout", "image/png"),
        },
        data={"options": json.dumps({"use_paddleocr_vl": True})},
    )

    assert response.status_code == 200, response.text
    assert response.content == b"paddle-docx"
    assert response.headers["x-docreconstruct-ocr"] == "paddleocr-vl-server"
    online = call["online_ocr"]
    assert online.allow_cloud is True
    assert online.providers == ("paddleocr_vl_server",)
    assert online.maximum_providers == 1
    assert online.provider_options == {
        "paddleocr_vl_server": {
            "allow_custom_endpoint": True,
            "use_doc_unwarping": False,
            "use_chart_recognition": False,
        }
    }


def test_legacy_paddle_switch_cannot_bypass_public_allowlist(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PADDLEOCR_VL_SERVER_URL", "http://127.0.0.1:8080")
    monkeypatch.delenv("DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS", raising=False)

    response = client.post(
        "/v1/hybrid",
        files={
            "content": ("content.md", b"text", "text/markdown"),
            "layout": ("layout.png", b"layout", "image/png"),
        },
        data={"options": json.dumps({"use_paddleocr_vl": True})},
    )

    assert response.status_code == 422
    assert "not offered by this server" in response.text


def test_hybrid_remote_assets_require_operator_opt_in(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOCRECONSTRUCT_ALLOW_REMOTE_ASSETS", raising=False)
    response = client.post(
        "/v1/hybrid",
        files={
            "content": ("content.md", b"![figure](https://example.test/a.png)", "text/markdown"),
            "layout": ("layout.png", b"layout", "image/png"),
            "evidence": ("evidence.json", b'{"pages": []}', "application/json"),
        },
        data={"options": json.dumps({"remote_assets": True})},
    )

    assert response.status_code == 503
    assert "DOCRECONSTRUCT_ALLOW_REMOTE_ASSETS" in response.text


def test_hybrid_operator_can_enable_remote_assets_and_scan_only_provenance(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docreconstruct.reconstruction.hybrid_job as hybrid_job

    call: dict[str, Any] = {}

    def fake_run_hybrid_job(content: Path, layout: Path, **kwargs: Any) -> Any:
        call.update(kwargs)
        Path(kwargs["output"]).write_bytes(b"remote-assets-docx")
        return SimpleNamespace(validation=SimpleNamespace(passed=True, score=1.0, metrics={}))

    monkeypatch.setenv("DOCRECONSTRUCT_ALLOW_REMOTE_ASSETS", "1")
    monkeypatch.setattr(hybrid_job, "run_hybrid_job", fake_run_hybrid_job)
    response = client.post(
        "/v1/hybrid",
        files={
            "content": ("content.md", b"![figure](https://example.test/a.png)", "text/markdown"),
            "layout": ("layout.png", b"layout", "image/png"),
            "evidence": ("evidence.json", b'{"pages": []}', "application/json"),
        },
        data={"options": json.dumps({"remote_assets": True})},
    )

    assert response.status_code == 200, response.text
    assert call["allow_remote_assets"] is True
    assert response.headers["x-docreconstruct-ocr"] == "provided-evidence"


def test_hybrid_verified_exposes_actual_visual_score_only(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docreconstruct.reconstruction.hybrid_job as hybrid_job

    def fake_run_hybrid_job(content: Path, layout: Path, **kwargs: Any) -> Any:
        Path(kwargs["output"]).write_bytes(b"verified-docx")
        return SimpleNamespace(
            validation=SimpleNamespace(
                passed=True,
                score=1.0,
                metrics={"rendered_visual": {"score": 0.731245}},
            )
        )

    monkeypatch.setenv("DOCRECONSTRUCT_LIBREOFFICE_PATH", "operator-libreoffice")
    monkeypatch.setattr(hybrid_job, "run_hybrid_job", fake_run_hybrid_job)
    response = client.post(
        "/v1/hybrid",
        files={
            "content": ("content.md", b"text", "text/markdown"),
            "layout": ("layout.png", b"layout", "image/png"),
            "evidence": ("evidence.json", b'{"pages": []}', "application/json"),
        },
        data={"options": json.dumps({"quality": "verified"})},
    )

    assert response.status_code == 200, response.text
    assert response.headers["x-docreconstruct-qa-score"] == "1.000000"
    assert response.headers["x-docreconstruct-visual-score"] == "0.731245"


@pytest.mark.parametrize(
    "hybrid_options",
    [
        {"minimum_visual_score": 0.8},
        {"evidence_provider": "../../private"},
        {"output_filename": "../escape.docx"},
    ],
)
def test_hybrid_rejects_unsafe_or_incompatible_options(
    client: TestClient, hybrid_options: dict[str, Any]
) -> None:
    response = client.post(
        "/v1/hybrid",
        files={
            "content": ("content.md", b"text", "text/markdown"),
            "layout": ("layout.png", b"layout", "image/png"),
        },
        data={"options": json.dumps(hybrid_options)},
    )

    assert response.status_code == 422


def test_configured_cors_exposes_download_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DOCRECONSTRUCT_CORS_ORIGINS",
        "https://kayurachann.github.io, https://example.test/",
    )
    with TestClient(create_app()) as cors_client:
        response = cors_client.get(
            "/health",
            headers={"Origin": "https://kayurachann.github.io"},
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ("https://kayurachann.github.io")
    exposed = response.headers["access-control-expose-headers"]
    assert "Content-Disposition" in exposed
    assert "X-DocReconstruct-QA-Score" in exposed
    assert "X-DocReconstruct-Visual-Score" in exposed


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
