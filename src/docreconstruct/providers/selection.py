"""Deterministic, dependency-free provider capability selection."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import Field

from docreconstruct.ir import CanonicalModel

from .base import (
    ProviderCapabilities,
    ProviderCost,
    ProviderCredentialRequirement,
    ProviderExecutionMode,
    ProviderPrivacy,
)
from .registry import ProviderRegistry, get_registry


class CapabilityRequest(CanonicalModel):
    """Hard requirements used to select compatible registered providers."""

    input_format: str | None = None
    languages: list[str] = Field(default_factory=list)
    text: bool = True
    multilingual: bool = False
    handwriting: bool = False
    formulas: bool = False
    tables: bool = False
    charts: bool = False
    layout: bool = False
    reading_order: bool = False
    styles: bool = False
    images: bool = False
    distorted_photos: bool = False
    dewarping: bool = False
    markdown: bool = False
    bounding_boxes: bool = False
    confidence_scores: bool = False
    execution_modes: list[ProviderExecutionMode] = Field(default_factory=list)
    minimum_privacy: ProviderPrivacy | None = None
    maximum_cost: ProviderCost | None = None
    allow_credentials: bool = True
    commercial_use: bool = False


class ProviderRecommendation(CanonicalModel):
    """Explainable recommendation for one registered provider."""

    provider: str
    compatible: bool
    score: int
    matched: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    capabilities: ProviderCapabilities


_BOOLEAN_FEATURES = (
    "text",
    "multilingual",
    "handwriting",
    "formulas",
    "tables",
    "charts",
    "layout",
    "reading_order",
    "styles",
    "images",
    "distorted_photos",
    "dewarping",
    "markdown",
    "bounding_boxes",
    "confidence_scores",
)
_PRIVACY_RANK = {
    ProviderPrivacy.UNKNOWN: 0,
    ProviderPrivacy.THIRD_PARTY: 1,
    ProviderPrivacy.USER_MANAGED: 2,
    ProviderPrivacy.NO_TRANSFER: 3,
}
_COST_RANK = {
    ProviderCost.FREE: 0,
    ProviderCost.INFRASTRUCTURE: 1,
    ProviderCost.METERED: 2,
    ProviderCost.COMMERCIAL: 3,
    ProviderCost.UNKNOWN: 4,
}


def recommend_providers(
    request: CapabilityRequest,
    *,
    registry: ProviderRegistry | None = None,
    include_incompatible: bool = False,
) -> tuple[ProviderRecommendation, ...]:
    """Rank declared providers without importing or constructing OCR engines."""

    active_registry = registry or get_registry()
    recommendations = [
        _evaluate(name, capabilities, request)
        for name, capabilities in active_registry.capability_items()
    ]
    if not include_incompatible:
        recommendations = [item for item in recommendations if item.compatible]
    recommendations.sort(key=lambda item: (not item.compatible, -item.score, item.provider))
    return tuple(recommendations)


def select_provider(
    request: CapabilityRequest,
    *,
    registry: ProviderRegistry | None = None,
) -> ProviderRecommendation | None:
    """Return the highest-ranked compatible provider declaration, if any."""

    recommendations = recommend_providers(request, registry=registry)
    return recommendations[0] if recommendations else None


def _evaluate(
    name: str,
    capabilities: ProviderCapabilities,
    request: CapabilityRequest,
) -> ProviderRecommendation:
    matched: list[str] = []
    missing: list[str] = []

    if request.input_format:
        requested_input = _normalize_token(request.input_format)
        supported = {_normalize_token(value) for value in capabilities.supported_inputs}
        _record(requested_input in supported, f"input:{requested_input}", matched, missing)

    for feature in _BOOLEAN_FEATURES:
        if getattr(request, feature):
            _record(bool(getattr(capabilities, feature)), feature, matched, missing)

    requested_languages = {_normalize_token(value) for value in request.languages if value.strip()}
    if requested_languages:
        supported_languages = {
            _normalize_token(value) for value in capabilities.languages if value.strip()
        }
        language_match = "*" in supported_languages or requested_languages.issubset(
            supported_languages
        )
        if not supported_languages and capabilities.multilingual:
            language_match = True
        label = "languages:" + ",".join(sorted(requested_languages))
        _record(language_match, label, matched, missing)

    selected_mode_index: int | None = None
    if request.execution_modes:
        available_modes = set(capabilities.execution_modes)
        for index, mode in enumerate(request.execution_modes):
            if mode in available_modes:
                selected_mode_index = index
                break
        _record(
            selected_mode_index is not None,
            "execution:" + "|".join(mode.value for mode in request.execution_modes),
            matched,
            missing,
        )

    if request.minimum_privacy is not None:
        privacy_match = (
            _PRIVACY_RANK[capabilities.privacy] >= _PRIVACY_RANK[request.minimum_privacy]
        )
        _record(
            privacy_match,
            f"privacy>={request.minimum_privacy.value}",
            matched,
            missing,
        )

    if request.maximum_cost is not None:
        cost_match = _COST_RANK[capabilities.cost] <= _COST_RANK[request.maximum_cost]
        _record(
            cost_match,
            f"cost<={request.maximum_cost.value}",
            matched,
            missing,
        )

    if not request.allow_credentials:
        no_credentials = capabilities.credentials is ProviderCredentialRequirement.NONE
        _record(no_credentials, "no_credentials", matched, missing)

    if request.commercial_use:
        _record(
            capabilities.license.commercial_use is True,
            "commercial_use",
            matched,
            missing,
        )

    compatible = not missing
    score = _score(capabilities, matched, selected_mode_index) if compatible else -len(missing)
    return ProviderRecommendation(
        provider=name,
        compatible=compatible,
        score=score,
        matched=matched,
        missing=missing,
        capabilities=capabilities,
    )


def _score(
    capabilities: ProviderCapabilities,
    matched: Iterable[str],
    selected_mode_index: int | None,
) -> int:
    score = 1000 + 20 * sum(1 for _ in matched)
    if selected_mode_index is not None:
        score += max(0, 30 - 5 * selected_mode_index)
    score += 4 * _PRIVACY_RANK[capabilities.privacy]
    score += max(0, 4 - _COST_RANK[capabilities.cost])
    if capabilities.credentials is ProviderCredentialRequirement.NONE:
        score += 2
    return score


def _record(
    condition: bool,
    label: str,
    matched: list[str],
    missing: list[str],
) -> None:
    (matched if condition else missing).append(label)


def _normalize_token(value: str) -> str:
    return value.strip().lower().lstrip(".").replace("-", "_")
