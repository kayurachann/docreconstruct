"""Portable models for licensed OCR/document-model training datasets."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DataUsageLane(StrEnum):
    """Whether a sample may contribute to redistributable/commercial weights."""

    COMMERCIAL_PERMISSIVE = "commercial-permissive"
    RESEARCH_ONLY = "research-only"
    PRIVATE_OPT_IN = "private-opt-in"
    UNKNOWN = "unknown"


class SplitName(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


class DatasetSample(BaseModel):
    """One source/ground-truth pair with leakage and rights metadata."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    source: Path
    ground_truth: Path
    split: SplitName | None = None
    usage_lane: DataUsageLane = DataUsageLane.UNKNOWN
    license_id: str | None = None
    license_url: str | None = None
    attribution: str | None = None
    commercial_use: bool | None = None
    redistribution: bool | None = None
    consent_scope: str | None = None
    pii_status: str = "unknown"
    languages: list[str] = Field(default_factory=list)
    scripts: list[str] = Field(default_factory=list)
    document_types: list[str] = Field(default_factory=list)
    content_kinds: list[str] = Field(default_factory=list)
    degradations: list[str] = Field(default_factory=list)
    group_ids: dict[str, str] = Field(default_factory=dict)
    source_sha256: str | None = None
    ground_truth_sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _rights_are_explicit_for_permissive_lane(self) -> DatasetSample:
        if self.usage_lane is DataUsageLane.COMMERCIAL_PERMISSIVE:
            if self.commercial_use is not True:
                raise ValueError(
                    "commercial-permissive samples must explicitly set commercial_use=true"
                )
            if not self.license_id:
                raise ValueError("commercial-permissive samples require license_id")
        if self.usage_lane is DataUsageLane.PRIVATE_OPT_IN and not self.consent_scope:
            raise ValueError("private-opt-in samples require consent_scope")
        return self


class DatasetManifest(BaseModel):
    """Versioned collection used by validation, benchmarking, or fine-tuning."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    name: str = Field(min_length=1)
    version: str = "unversioned"
    seed: int = 0
    description: str | None = None
    samples: list[DatasetSample] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_ids(self) -> DatasetManifest:
        ids = [sample.id for sample in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError("dataset sample IDs must be unique")
        return self


class DatasetValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    dataset_fingerprint: str
    sample_count: int
    split_counts: dict[str, int]
    lane_counts: dict[str, int]
    missing_files: list[str] = Field(default_factory=list)
    hash_mismatches: list[str] = Field(default_factory=list)
    leakage_groups: list[str] = Field(default_factory=list)
    duplicate_sources: list[str] = Field(default_factory=list)
    rights_errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TrainerDescriptor(BaseModel):
    """Research-backed trainer that can be supplied by an optional plugin."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str
    project_url: str
    tasks: list[str]
    execution_modes: list[str]
    entry_point: str
    license_notes: str


class TrainingPlan(BaseModel):
    """Auditable dry-run plan; it is not a claim that training has executed."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    backend: TrainerDescriptor
    dataset_name: str
    dataset_version: str
    dataset_fingerprint: str
    seed: int
    usage_lane: DataUsageLane
    split_counts: dict[str, int]
    sample_ids: dict[str, list[str]]
    evaluation_slices: list[str]
    required_metrics: list[str]
    source_tree_fingerprint: str
    executable: bool = False
    plugin_group: str = "docreconstruct.trainers"
    warnings: list[str] = Field(default_factory=list)
