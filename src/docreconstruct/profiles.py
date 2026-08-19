"""Reconstruction profiles and their explicit fidelity trade-offs."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ReconstructionProfile(StrEnum):
    """Named optimization profiles exposed by the SDK, CLI, and API."""

    BALANCED = "balanced"
    PIXEL_PERFECT = "pixel-perfect"
    EDITABLE = "editable"
    DATA = "data"
    ARCHIVAL = "archival"
    PRESENTATION = "presentation"

    @classmethod
    def parse(cls, value: str | ReconstructionProfile) -> ReconstructionProfile:
        if isinstance(value, cls):
            return value
        aliases = {
            "visual": cls.PIXEL_PERFECT,
            "fidelity": cls.PIXEL_PERFECT,
            "replica": cls.PIXEL_PERFECT,
            "semantic": cls.EDITABLE,
            "pixel_perfect": cls.PIXEL_PERFECT,
        }
        normalized = value.strip().lower()
        if normalized in aliases:
            return aliases[normalized]
        return cls(normalized)


class ProfileSettings(BaseModel):
    """Renderer and evaluator priorities for one reconstruction profile."""

    model_config = ConfigDict(frozen=True)

    text_weight: float = Field(ge=0, le=1)
    structure_weight: float = Field(ge=0, le=1)
    spatial_weight: float = Field(ge=0, le=1)
    visual_weight: float = Field(ge=0, le=1)
    editability_weight: float = Field(ge=0, le=1)
    layout_strategy: str

    def normalized_weights(self) -> dict[str, float]:
        raw = {
            "text": self.text_weight,
            "structure": self.structure_weight,
            "spatial": self.spatial_weight,
            "visual": self.visual_weight,
            "editability": self.editability_weight,
        }
        total = sum(raw.values()) or 1.0
        return {name: value / total for name, value in raw.items()}


PROFILE_SETTINGS: dict[ReconstructionProfile, ProfileSettings] = {
    ReconstructionProfile.BALANCED: ProfileSettings(
        text_weight=0.28,
        structure_weight=0.20,
        spatial_weight=0.18,
        visual_weight=0.18,
        editability_weight=0.16,
        layout_strategy="hybrid",
    ),
    ReconstructionProfile.PIXEL_PERFECT: ProfileSettings(
        text_weight=0.20,
        structure_weight=0.10,
        spatial_weight=0.27,
        visual_weight=0.33,
        editability_weight=0.10,
        layout_strategy="fixed",
    ),
    ReconstructionProfile.EDITABLE: ProfileSettings(
        text_weight=0.29,
        structure_weight=0.27,
        spatial_weight=0.10,
        visual_weight=0.08,
        editability_weight=0.26,
        layout_strategy="flow",
    ),
    ReconstructionProfile.DATA: ProfileSettings(
        text_weight=0.38,
        structure_weight=0.30,
        spatial_weight=0.10,
        visual_weight=0.05,
        editability_weight=0.17,
        layout_strategy="data",
    ),
    ReconstructionProfile.ARCHIVAL: ProfileSettings(
        text_weight=0.19,
        structure_weight=0.11,
        spatial_weight=0.27,
        visual_weight=0.34,
        editability_weight=0.09,
        layout_strategy="fixed",
    ),
    ReconstructionProfile.PRESENTATION: ProfileSettings(
        text_weight=0.18,
        structure_weight=0.14,
        spatial_weight=0.29,
        visual_weight=0.25,
        editability_weight=0.14,
        layout_strategy="fixed",
    ),
}


def settings_for(
    profile: str | ReconstructionProfile,
) -> tuple[ReconstructionProfile, ProfileSettings]:
    parsed = ReconstructionProfile.parse(profile)
    return parsed, PROFILE_SETTINGS[parsed]
