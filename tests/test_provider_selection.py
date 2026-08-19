from __future__ import annotations

from typing import Any

from docreconstruct.ir import Document
from docreconstruct.providers import (
    CapabilityRequest,
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
    recommend_providers,
    registry,
    select_provider,
)


def test_legacy_capability_fields_derive_unambiguous_new_fields() -> None:
    capabilities = ProviderCapabilities(
        provider="legacy",
        saved_json=True,
        live_inference=True,
        geometry=True,
        reading_order=True,
    )

    assert capabilities.execution_modes == [
        ProviderExecutionMode.SAVED,
        ProviderExecutionMode.LOCAL,
    ]
    assert capabilities.bounding_boxes is True
    assert capabilities.layout is True


def test_builtin_capability_recommendation_for_distorted_handwriting() -> None:
    request = CapabilityRequest(
        input_format="json",
        multilingual=True,
        handwriting=True,
        formulas=True,
        tables=True,
        layout=True,
        distorted_photos=True,
        dewarping=True,
        markdown=True,
        bounding_boxes=True,
        confidence_scores=True,
        execution_modes=[ProviderExecutionMode.SAVED],
        minimum_privacy=ProviderPrivacy.NO_TRANSFER,
        maximum_cost=ProviderCost.FREE,
        allow_credentials=False,
        commercial_use=True,
    )

    recommendation = select_provider(request)

    assert recommendation is not None
    assert recommendation.provider == "paddleocr"
    assert recommendation.compatible is True
    assert recommendation.missing == []
    assert registry.get_capabilities("paddleocr") is recommendation.capabilities


def test_recommendation_is_deterministic_and_explains_incompatibility() -> None:
    request = CapabilityRequest(
        input_format="pdf",
        markdown=True,
        execution_modes=[ProviderExecutionMode.LOCAL],
    )

    first = recommend_providers(request, include_incompatible=True)
    second = recommend_providers(request, include_incompatible=True)

    assert first == second
    assert all(item.compatible is False for item in first)
    native = next(item for item in first if item.provider == "native_pdf")
    assert native.missing == ["markdown"]


class _NeverConstructedProvider(Provider):
    name = "never_constructed"
    _capabilities = ProviderCapabilities(
        provider=name,
        supported_inputs=["png"],
        saved_json=False,
        execution_modes=[ProviderExecutionMode.API],
        markdown=True,
        privacy=ProviderPrivacy.THIRD_PARTY,
        license=ProviderLicense(
            name="test",
            commercial_use=True,
        ),
        cost=ProviderCost.METERED,
        credentials=ProviderCredentialRequirement.REQUIRED,
        credential_env_vars=["TEST_API_KEY"],
    )

    def __init__(self) -> None:
        raise AssertionError("selection must not construct providers")

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def normalize(
        self,
        payload: Any,
        *,
        context: ProviderContext | None = None,
    ) -> Document:
        raise NotImplementedError

    def parse(
        self,
        source: ProviderInput,
        *,
        context: ProviderContext | None = None,
    ) -> Any:
        raise NotImplementedError


def test_selection_reads_class_declaration_without_constructing_provider() -> None:
    custom = ProviderRegistry()
    custom.register(_NeverConstructedProvider)

    allowed = recommend_providers(
        CapabilityRequest(
            input_format="png",
            markdown=True,
            execution_modes=[ProviderExecutionMode.API],
            maximum_cost=ProviderCost.METERED,
            commercial_use=True,
        ),
        registry=custom,
    )
    denied = recommend_providers(
        CapabilityRequest(
            input_format="png",
            markdown=True,
            execution_modes=[ProviderExecutionMode.API],
            allow_credentials=False,
        ),
        registry=custom,
        include_incompatible=True,
    )

    assert [item.provider for item in allowed] == ["never_constructed"]
    assert denied[0].missing == ["no_credentials"]
