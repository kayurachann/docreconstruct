from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from docreconstruct.extraction import ExtractionMode, extract_to_markdown
from docreconstruct.ir import BBox, Document, Element, ElementType, Page, Provenance
from docreconstruct.providers import (
    Provider,
    ProviderCapabilities,
    ProviderContext,
    ProviderCost,
    ProviderCredentialRequirement,
    ProviderExecutionMode,
    ProviderInput,
    ProviderLicense,
    ProviderPrivacy,
    ProviderRegistry,
    ProviderResult,
)


def _document(provider: str, text: str) -> Document:
    return Document(
        id=f"{provider}-document",
        pages=[
            Page(
                id="page-1",
                number=1,
                width=100,
                height=200,
                elements=[
                    Element(
                        id=f"{provider}-text",
                        type=ElementType.TEXT,
                        bbox=BBox(x0=1, y0=1, x1=99, y1=20),
                        text=text,
                        provenance=Provenance(engine=provider),
                    )
                ],
            )
        ],
    )


def _capabilities(
    name: str,
    *,
    geometry: bool = True,
    model_name: str | None = None,
    model_version: str | None = None,
) -> ProviderCapabilities:
    return ProviderCapabilities(
        provider=name,
        supported_inputs=["png"],
        saved_json=False,
        live_inference=True,
        text=True,
        geometry=geometry,
        reading_order=True,
        tables=True,
        formulas=True,
        layout=True,
        execution_modes=[ProviderExecutionMode.API],
        markdown=True,
        privacy=ProviderPrivacy.THIRD_PARTY,
        license=ProviderLicense(name="fixture", commercial_use=True),
        cost=ProviderCost.METERED,
        credentials=ProviderCredentialRequirement.REQUIRED,
        model_name=model_name,
        model_version=model_version,
    )


class _SuccessfulCloudProvider(Provider):
    name = "successful_cloud"
    _capabilities = _capabilities(name)

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def normalize(self, payload: Any, *, context: ProviderContext | None = None) -> Document:
        del payload, context
        return _document(self.name, "Cloud Markdown")

    def parse(
        self,
        source: ProviderInput,
        *,
        context: ProviderContext | None = None,
    ) -> ProviderResult:
        del source
        assert context is not None
        assert context.options["allow_remote"] is True
        return ProviderResult(provider=self.name, document=self.normalize({}))


class _FailingCloudProvider(_SuccessfulCloudProvider):
    name = "failing_cloud"
    _capabilities = _capabilities(name)

    def parse(
        self,
        source: ProviderInput,
        *,
        context: ProviderContext | None = None,
    ) -> ProviderResult:
        del source
        leaked = context.options.get("api_key") if context is not None else None
        raise RuntimeError(f"fixture outage api_key={leaked}")


class _CountingCloudProvider(_SuccessfulCloudProvider):
    name = "counting_cloud"
    _capabilities = _capabilities(
        name,
        model_name="fixture-ocr",
        model_version="2026-08-19",
    )
    parse_calls = 0

    def parse(
        self,
        source: ProviderInput,
        *,
        context: ProviderContext | None = None,
    ) -> ProviderResult:
        del source
        assert context is not None
        type(self).parse_calls += 1
        return ProviderResult(
            provider=self.name,
            document=self.normalize({}),
            warnings=["fixture warning"],
            metadata={
                "model": "fixture-ocr-live",
                "request_id": "request-123",
                "api_key": context.options.get("api_key"),
                "nested": {"access_token": "must-not-survive", "pages": 1},
            },
        )


class _NoGeometryCloudProvider(_SuccessfulCloudProvider):
    name = "no_geometry_cloud"
    _capabilities = _capabilities(name, geometry=False)
    parse_calls = 0

    def parse(
        self,
        source: ProviderInput,
        *,
        context: ProviderContext | None = None,
    ) -> ProviderResult:
        type(self).parse_calls += 1
        return super().parse(source, context=context)


def _registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(_FailingCloudProvider)
    registry.register(_SuccessfulCloudProvider)
    return registry


def _counting_registry(*, include_no_geometry: bool = False) -> ProviderRegistry:
    registry = ProviderRegistry()
    if include_no_geometry:
        registry.register(_NoGeometryCloudProvider)
    registry.register(_CountingCloudProvider)
    return registry


def test_cloud_extraction_requires_explicit_authorization(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    source.write_bytes(b"source")

    with pytest.raises(PermissionError, match="allow_cloud"):
        extract_to_markdown(source, mode=ExtractionMode.CLOUD, registry=_registry())


def test_cloud_extraction_falls_back_and_writes_auditable_markdown(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    output = tmp_path / "content.md"
    source.write_bytes(b"source")

    result = extract_to_markdown(
        source,
        output=output,
        mode="cloud",
        providers=["failing_cloud", "successful_cloud"],
        allow_cloud=True,
        provider_options={
            "failing_cloud": {"api_key": "error-secret"},
            "successful_cloud": {},
        },
        registry=_registry(),
    )

    assert output.read_text(encoding="utf-8") == "Cloud Markdown\n"
    assert result.manifest.successful_providers == ["successful_cloud"]
    assert [attempt.status for attempt in result.manifest.attempts] == ["failed", "succeeded"]
    assert result.manifest.source_sha256
    assert result.manifest.cache_key is None
    assert result.manifest.cache_hit is False
    assert result.manifest.evidence_outputs == []
    assert len(result.documents) == 1
    assert result.evidence_outputs == ()
    assert "error-secret" not in result.manifest.model_dump_json()
    assert "api_key" not in result.manifest.model_dump_json()


def test_auto_cloud_selection_uses_declared_capabilities_without_network(
    tmp_path: Path,
) -> None:
    source = tmp_path / "page.png"
    source.write_bytes(b"source")

    result = extract_to_markdown(
        source,
        mode="cloud",
        allow_cloud=True,
        registry=_registry(),
    )

    assert result.manifest.selected_providers == ["failing_cloud", "successful_cloud"]
    assert result.output.read_text(encoding="utf-8") == "Cloud Markdown\n"


def test_successful_documents_are_persisted_as_hashed_canonical_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "page.png"
    evidence = tmp_path / "evidence"
    source.write_bytes(b"source")
    _CountingCloudProvider.parse_calls = 0

    result = extract_to_markdown(
        source,
        mode="cloud",
        providers=["counting_cloud"],
        allow_cloud=True,
        evidence_directory=evidence,
        provider_options={
            "counting_cloud": {
                "api_key": "top-secret-api-key",
                "endpoint": "https://ocr.example.test/analyze?sig=private",
            }
        },
        registry=_counting_registry(),
    )

    assert _CountingCloudProvider.parse_calls == 1
    assert len(result.documents) == 1
    assert len(result.evidence_outputs) == 1
    evidence_output = result.evidence_outputs[0]
    persisted = Document.model_validate_json(evidence_output.read_bytes())
    assert persisted.id == "counting_cloud-document"
    evidence_hash = hashlib.sha256(evidence_output.read_bytes()).hexdigest()
    assert result.manifest.evidence_sha256[str(evidence_output)] == evidence_hash
    attempt = result.manifest.attempts[0]
    assert attempt.evidence_output == str(evidence_output)
    assert attempt.evidence_sha256 == evidence_hash
    assert attempt.metadata == {
        "model": "fixture-ocr-live",
        "request_id": "request-123",
        "nested": {"pages": 1},
        "model_version": "2026-08-19",
    }
    serialized = result.manifest.model_dump_json()
    assert "top-secret-api-key" not in serialized
    assert "must-not-survive" not in serialized
    assert "api_key" not in serialized
    assert "access_token" not in serialized


def test_ensemble_persists_one_canonical_evidence_file_per_success(
    tmp_path: Path,
) -> None:
    source = tmp_path / "page.png"
    source.write_bytes(b"source")
    registry = ProviderRegistry()
    registry.register(_SuccessfulCloudProvider)
    registry.register(_CountingCloudProvider)
    _CountingCloudProvider.parse_calls = 0

    result = extract_to_markdown(
        source,
        mode="cloud",
        providers=["successful_cloud", "counting_cloud"],
        allow_cloud=True,
        ensemble=True,
        maximum_providers=2,
        evidence_directory=tmp_path / "evidence",
        registry=registry,
    )

    assert result.manifest.ensemble is True
    assert result.manifest.successful_providers == ["successful_cloud", "counting_cloud"]
    assert len(result.documents) == 2
    assert len(result.evidence_outputs) == 2
    assert [
        Document.model_validate_json(path.read_bytes()).id for path in result.evidence_outputs
    ] == [
        "successful_cloud-document",
        "counting_cloud-document",
    ]
    assert all(attempt.evidence_sha256 for attempt in result.manifest.attempts)


def test_canonical_cache_hit_uses_no_provider_and_ignores_secret_rotation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "page.png"
    cache = tmp_path / "cache"
    source.write_bytes(b"source")
    _CountingCloudProvider.parse_calls = 0
    registry = _counting_registry()

    first = extract_to_markdown(
        source,
        mode="cloud",
        providers=["counting_cloud"],
        allow_cloud=True,
        cache_directory=cache,
        provider_options={"counting_cloud": {"api_key": "first-secret", "dpi": 300}},
        registry=registry,
    )
    second = extract_to_markdown(
        source,
        mode="cloud",
        providers=["counting_cloud"],
        allow_cloud=True,
        cache_directory=cache,
        provider_options={"counting_cloud": {"api_key": "rotated-secret", "dpi": 300}},
        registry=registry,
    )
    changed_config = extract_to_markdown(
        source,
        mode="cloud",
        providers=["counting_cloud"],
        allow_cloud=True,
        cache_directory=cache,
        provider_options={"counting_cloud": {"api_key": "third-secret", "dpi": 600}},
        registry=registry,
    )

    assert _CountingCloudProvider.parse_calls == 2
    assert first.manifest.cache_hit is False
    assert second.manifest.cache_hit is True
    assert changed_config.manifest.cache_hit is False
    assert first.manifest.cache_key == second.manifest.cache_key
    assert changed_config.manifest.cache_key != first.manifest.cache_key
    assert second.documents[0].id == "counting_cloud-document"
    cache_manifest = cache / str(first.manifest.cache_key) / "manifest.json"
    cache_text = cache_manifest.read_text(encoding="utf-8")
    assert "first-secret" not in cache_text
    assert "rotated-secret" not in cache_text
    assert "request-123" in cache_text


def test_corrupt_cached_artifact_falls_back_to_provider_and_repairs_cache(
    tmp_path: Path,
) -> None:
    source = tmp_path / "page.png"
    cache = tmp_path / "cache"
    source.write_bytes(b"source")
    _CountingCloudProvider.parse_calls = 0
    registry = _counting_registry()

    first = extract_to_markdown(
        source,
        mode="cloud",
        providers=["counting_cloud"],
        allow_cloud=True,
        cache_directory=cache,
        registry=registry,
    )
    entry = cache / str(first.manifest.cache_key)
    cache_manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
    artifact = entry / cache_manifest["artifacts"][0]["file"]
    artifact.write_bytes(b'{"tampered":true}')

    second = extract_to_markdown(
        source,
        mode="cloud",
        providers=["counting_cloud"],
        allow_cloud=True,
        cache_directory=cache,
        registry=registry,
    )

    assert _CountingCloudProvider.parse_calls == 2
    assert second.manifest.cache_hit is False
    assert any("hash mismatch" in warning for warning in second.manifest.warnings)
    repaired_manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
    repaired_artifact = entry / repaired_manifest["artifacts"][0]["file"]
    assert (
        hashlib.sha256(repaired_artifact.read_bytes()).hexdigest()
        == repaired_manifest["artifacts"][0]["sha256"]
    )


def test_require_geometry_routes_only_to_declared_bounding_box_providers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "page.png"
    source.write_bytes(b"source")
    _CountingCloudProvider.parse_calls = 0
    _NoGeometryCloudProvider.parse_calls = 0

    with pytest.raises(ValueError, match="bounding-box geometry"):
        extract_to_markdown(
            source,
            mode="cloud",
            providers=["no_geometry_cloud"],
            allow_cloud=True,
            require_geometry=True,
            registry=_counting_registry(include_no_geometry=True),
        )
    assert _NoGeometryCloudProvider.parse_calls == 0

    result = extract_to_markdown(
        source,
        mode="cloud",
        allow_cloud=True,
        require_geometry=True,
        registry=_counting_registry(include_no_geometry=True),
    )
    assert result.manifest.selected_providers == ["counting_cloud"]
    assert _CountingCloudProvider.parse_calls == 1
    assert _NoGeometryCloudProvider.parse_calls == 0
