from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from docreconstruct.evaluation import (
    OCRBenchmarkCase,
    OCRBenchmarkFeatures,
    OCRBenchmarkRunner,
    OCRBenchmarkTags,
    load_ocr_benchmark_manifest,
    run_ocr_benchmark,
)
from docreconstruct.ir import BBox, Document, Element, ElementType, Page
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


class _FixtureOCR(Provider):
    name = "fixture_ocr"
    _capabilities = ProviderCapabilities(
        provider=name,
        supported_inputs=["png"],
        saved_json=False,
        live_inference=True,
        text=True,
        geometry=True,
        reading_order=True,
        tables=True,
        formulas=True,
        handwriting=True,
        layout=True,
        markdown=True,
        execution_modes=[ProviderExecutionMode.API],
        privacy=ProviderPrivacy.THIRD_PARTY,
        license=ProviderLicense(name="fixture", commercial_use=True),
        model_name="fixture-model",
        model_version="1.2.3",
        cost=ProviderCost.FREE,
        credentials=ProviderCredentialRequirement.NONE,
    )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def normalize(self, payload: Any, *, context: ProviderContext | None = None) -> Document:
        del context
        text = str(payload)
        return Document(
            id="fixture-document",
            pages=[
                Page(
                    id="page-1",
                    number=1,
                    width=100,
                    height=200,
                    elements=[
                        Element(
                            id="text-1",
                            type=ElementType.TEXT,
                            bbox=BBox(x0=0, y0=0, x1=100, y1=20),
                            text=text,
                            reading_order=0,
                        )
                    ],
                )
            ],
            metadata={"model": "fixture-model@1.2.3"},
        )

    def parse(
        self,
        source: ProviderInput,
        *,
        context: ProviderContext | None = None,
    ) -> ProviderResult:
        assert context is not None
        assert context.options["allow_remote"] is True
        text = Path(source).read_text(encoding="utf-8")  # type: ignore[arg-type]
        if text == "FAIL":
            raise RuntimeError("intentional fixture failure")
        return ProviderResult(provider=self.name, document=self.normalize(text))


def _registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(_FixtureOCR)
    return registry


def _write_dataset(tmp_path: Path) -> Path:
    (tmp_path / "exact.png").write_text("Xin chào", encoding="utf-8")
    (tmp_path / "exact.md").write_text("Xin chào\n", encoding="utf-8")
    (tmp_path / "imperfect.png").write_text("Revenue 10O", encoding="utf-8")
    (tmp_path / "imperfect.md").write_text("Revenue 100\n", encoding="utf-8")
    (tmp_path / "failed.png").write_text("FAIL", encoding="utf-8")
    (tmp_path / "failed.md").write_text("Never emitted\n", encoding="utf-8")
    manifest = {
        "schema_version": "0.1",
        "profile": "balanced",
        "seed": 17,
        "configuration": {"suite": "offline-fixture"},
        "extraction": {
            "mode": "cloud",
            "providers": ["fixture_ocr"],
            "features": {"formulas": False, "tables": True},
            "provider_options": {"fixture_ocr": {"private_marker": "do-not-report"}},
        },
        "cases": [
            {
                "id": "vi-exact",
                "source": "exact.png",
                "ground_truth": "exact.md",
                "features": {"languages": ["vi"]},
                "tags": {
                    "script": "Latn",
                    "document_type": "invoice",
                    "degradation": "skew",
                    "content_kind": "printed",
                },
                "metadata": {
                    "accessToken": "case-secret",
                    "source_url": "https://example.test/page?sig=signed-secret",
                },
            },
            {
                "id": "en-imperfect",
                "source": "imperfect.png",
                "ground_truth": "imperfect.md",
                "features": {"languages": ["en"]},
                "tags": {
                    "scripts": ["Latn"],
                    "document_types": ["exam"],
                    "degradations": ["perspective"],
                    "content_kinds": ["formula"],
                },
            },
            {
                "id": "failed-provider",
                "source": "failed.png",
                "ground_truth": "failed.md",
                "features": {"languages": ["zh"]},
                "tags": {
                    "script": "Hans",
                    "document_type": "newspaper",
                    "degradation": "blur",
                    "content_kind": "handwriting",
                },
            },
        ],
    }
    path = tmp_path / "ocr-benchmark.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_end_to_end_ocr_benchmark_records_manifests_failures_and_slices(
    tmp_path: Path,
) -> None:
    manifest = _write_dataset(tmp_path)
    output_dir = tmp_path / "outputs"

    first = run_ocr_benchmark(
        manifest,
        output_dir=output_dir,
        allow_cloud=True,
        registry=_registry(),
    )
    second = run_ocr_benchmark(
        tmp_path,
        output_dir=output_dir,
        allow_cloud=True,
        registry=_registry(),
    )

    assert first.to_json() == second.to_json()
    assert len(first.run_fingerprint) == 64
    assert first.successful_cases == 2
    assert first.failed_cases == 1
    assert first.model_versions == {"fixture_ocr": "fixture-model@1.2.3"}
    by_id = {result.case_id: result for result in first.results}
    assert by_id["vi-exact"].score == pytest.approx(1.0)
    assert by_id["vi-exact"].extraction_manifest is not None
    assert by_id["vi-exact"].extraction_manifest["successful_providers"] == ["fixture_ocr"]
    assert by_id["vi-exact"].output_sha256 is not None
    assert by_id["failed-provider"].failure is not None
    assert by_id["failed-provider"].failure.phase == "extraction"
    assert by_id["failed-provider"].failure.error_type == "RuntimeError"

    slices = first.slice_means
    assert slices["language"]["vi"].mean_score == pytest.approx(1.0)
    # ``mean_score`` averages FidelityScore.overall, which spreads the
    # unmeasurable components over the measured ones. Both sides of an OCR
    # case are Markdown, so layout/structure/visual are never measured and the
    # headline cannot fall far however wrong the recognition is. The strict
    # pair says what was actually measured.
    assert first.mean_measurement_coverage is not None
    assert first.mean_measurement_coverage < 1.0
    assert first.mean_overall_strict is not None
    assert first.mean_overall_strict <= first.mean_score
    assert slices["language"]["vi"].mean_overall_strict is not None
    # Editability is a hand-written constant on the Markdown branch, not a
    # measurement, so it must not be published as a component mean.
    assert first.component_means["editability"] is None
    assert slices["script"]["Latn"].total_cases == 2
    assert slices["document_type"]["invoice"].successful_cases == 1
    assert slices["degradation"]["blur"].successful_cases == 0
    assert slices["degradation"]["blur"].mean_score is None
    assert slices["content_kind"]["handwriting"].failed_cases == 1
    assert "do-not-report" not in first.to_json()
    assert "case-secret" not in first.to_json()
    assert "signed-secret" not in first.to_json()


def test_manifest_cannot_authorize_cloud_upload(tmp_path: Path) -> None:
    manifest = _write_dataset(tmp_path)
    cases, _ = load_ocr_benchmark_manifest(manifest)
    report = OCRBenchmarkRunner(
        output_dir=tmp_path / "outputs",
        allow_cloud=False,
        registry=_registry(),
    ).run([cases[0]])

    assert report.failed_cases == 1
    assert report.results[0].failure is not None
    assert report.results[0].failure.error_type == "PermissionError"


def test_direct_cases_validate_markdown_and_duplicate_ids(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    source.write_bytes(b"source")
    wrong_truth = tmp_path / "truth.txt"
    wrong_truth.write_text("truth", encoding="utf-8")

    with pytest.raises(ValueError, match="must be Markdown"):
        OCRBenchmarkCase("wrong", source, wrong_truth)

    truth = tmp_path / "truth.md"
    truth.write_text("truth\n", encoding="utf-8")
    case = OCRBenchmarkCase(
        "same",
        source,
        truth,
        features=OCRBenchmarkFeatures(languages=("vi",)),
        tags=OCRBenchmarkTags(document_types=("form",)),
    )
    runner = OCRBenchmarkRunner(output_dir=tmp_path / "outputs", registry=_registry())
    with pytest.raises(ValueError, match="IDs must be unique"):
        runner.run([case, case])
