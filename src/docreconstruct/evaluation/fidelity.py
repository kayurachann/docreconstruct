"""Weighted aggregate document fidelity profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from docreconstruct.profiles import PROFILE_SETTINGS

COMPONENTS = ("text", "layout", "structure", "editability", "visual")

FIDELITY_PROFILES: dict[str, dict[str, float]] = {
    profile.value: {
        "text": settings.text_weight,
        "layout": settings.spatial_weight,
        "structure": settings.structure_weight,
        "editability": settings.editability_weight,
        "visual": settings.visual_weight,
    }
    for profile, settings in PROFILE_SETTINGS.items()
}

_PROFILE_ALIASES = {
    "visual": "pixel-perfect",
    "fidelity": "pixel-perfect",
    "replica": "pixel-perfect",
    "pixel_perfect": "pixel-perfect",
    "semantic": "editable",
}


def _score(value: Any) -> float | None:
    if value is None:
        return None
    value = getattr(value, "score", value)
    numeric = float(value)
    if not 0.0 <= numeric <= 1.0:
        raise ValueError(f"fidelity component must be between 0 and 1, got {numeric}")
    return numeric


@dataclass(frozen=True)
class FidelityScore:
    text: float | None = None
    layout: float | None = None
    structure: float | None = None
    editability: float | None = None
    visual: float | None = None
    profile: str = "balanced"
    custom_weights: dict[str, float] | None = field(default=None, repr=False, compare=False)
    weights: dict[str, float] = field(init=False)
    overall: float = field(init=False)

    def __post_init__(self) -> None:
        canonical_profile = _PROFILE_ALIASES.get(self.profile.lower(), self.profile.lower())
        if self.custom_weights is None:
            try:
                configured = dict(FIDELITY_PROFILES[canonical_profile])
            except KeyError as exc:
                choices = ", ".join(sorted(FIDELITY_PROFILES))
                raise ValueError(
                    f"unknown fidelity profile {self.profile!r}; choose {choices}"
                ) from exc
        else:
            unknown = set(self.custom_weights) - set(COMPONENTS)
            if unknown:
                raise ValueError(f"unknown fidelity weight(s): {', '.join(sorted(unknown))}")
            configured = {name: float(self.custom_weights.get(name, 0.0)) for name in COMPONENTS}
            if any(weight < 0 for weight in configured.values()) or not any(configured.values()):
                raise ValueError("custom fidelity weights must be non-negative and not all zero")

        components = {name: _score(getattr(self, name)) for name in COMPONENTS}
        available_weight = sum(
            configured[name] for name, score in components.items() if score is not None
        )
        if available_weight == 0:
            normalized = {name: 0.0 for name in COMPONENTS}
            overall = 0.0
        else:
            normalized = {
                name: (configured[name] / available_weight if components[name] is not None else 0.0)
                for name in COMPONENTS
            }
            overall = sum((components[name] or 0.0) * normalized[name] for name in COMPONENTS)

        for name, score in components.items():
            object.__setattr__(self, name, score)
        object.__setattr__(self, "profile", canonical_profile)
        object.__setattr__(self, "weights", normalized)
        object.__setattr__(self, "overall", max(0.0, min(1.0, overall)))

    @classmethod
    def from_metrics(
        cls,
        *,
        text: Any = None,
        layout: Any = None,
        structure: Any = None,
        editability: Any = None,
        visual: Any = None,
        profile: str = "balanced",
        weights: dict[str, float] | None = None,
    ) -> FidelityScore:
        return cls(
            text=_score(text),
            layout=_score(layout),
            structure=_score(structure),
            editability=_score(editability),
            visual=_score(visual),
            profile=profile,
            custom_weights=weights,
        )

    @property
    def percentage(self) -> float:
        return self.overall * 100.0

    @property
    def components(self) -> dict[str, float | None]:
        return {name: getattr(self, name) for name in COMPONENTS}

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "overall": self.overall,
            "percentage": self.percentage,
            "components": self.components,
            "weights": dict(self.weights),
        }


def calculate_fidelity(
    *,
    text: Any = None,
    layout: Any = None,
    structure: Any = None,
    editability: Any = None,
    visual: Any = None,
    profile: str = "balanced",
    weights: dict[str, float] | None = None,
) -> FidelityScore:
    return FidelityScore.from_metrics(
        text=text,
        layout=layout,
        structure=structure,
        editability=editability,
        visual=visual,
        profile=profile,
        weights=weights,
    )
