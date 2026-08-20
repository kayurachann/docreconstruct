"""Immutable measurements and audit reports for bounded correction."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from docreconstruct.evaluation.render_diff import RenderDiffReport
from docreconstruct.reconstruction.constraint_plan import Size
from docreconstruct.reconstruction.constraint_plan.canonical import stable_digest

from .actions import CorrectionAction, ObjectiveComponent

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidateAssessment(_FrozenModel):
    """Renderer-neutral facts measured from one concrete candidate artifact."""

    constraint_plan_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    content_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    layout_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    object_content_sha256: str = Field(pattern=_SHA256_PATTERN)
    object_provenance_sha256: str = Field(pattern=_SHA256_PATTERN)
    object_ids: tuple[str, ...]
    page_sizes: tuple[Size, ...]
    missing_object_ids: tuple[str, ...] = ()
    source_deleted_object_ids: tuple[str, ...] = ()
    native_editable_object_ids: tuple[str, ...] = ()
    raster_substituted_object_ids: tuple[str, ...] = ()
    full_page_raster_pages: tuple[int, ...] = ()
    structure_score: float = Field(ge=0.0, le=1.0)
    layout_score: float = Field(ge=0.0, le=1.0)
    visual_score: float = Field(ge=0.0, le=1.0)
    editability_score: float = Field(ge=0.0, le=1.0)
    render_diff: RenderDiffReport

    @field_validator("object_ids")
    @classmethod
    def object_ids_must_be_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("candidate object IDs must not be blank")
        if len(value) != len(set(value)):
            raise ValueError("candidate object IDs must be unique")
        return value

    @field_validator(
        "missing_object_ids",
        "source_deleted_object_ids",
        "native_editable_object_ids",
        "raster_substituted_object_ids",
    )
    @classmethod
    def diagnostic_object_ids_must_be_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("candidate diagnostic object IDs must not be blank")
        if value != tuple(sorted(set(value), key=str.casefold)):
            raise ValueError("candidate diagnostic object IDs must be unique and ordered")
        return value

    @field_validator("full_page_raster_pages")
    @classmethod
    def raster_pages_must_be_canonical(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(item < 1 for item in value):
            raise ValueError("full-page raster page numbers must be positive")
        if value != tuple(sorted(set(value))):
            raise ValueError("full-page raster page numbers must be unique and ordered")
        return value

    @field_validator("structure_score", "layout_score", "visual_score", "editability_score")
    @classmethod
    def scores_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("candidate scores must be finite")
        return value

    @model_validator(mode="after")
    def render_page_count_must_describe_this_candidate(self) -> Self:
        if self.render_diff.candidate_page_count != len(self.page_sizes):
            raise ValueError("render-diff candidate page count must match measured page sizes")
        return self


class CorrectionObjective(_FrozenModel):
    """Exact nine-part objective; comparison is lexicographic, never a weighted sum."""

    semantic_authority_preserved: bool
    missing_content_count: int = Field(ge=0)
    page_geometry_violation_count: int = Field(ge=0)
    clipping_overflow_count: int = Field(ge=0)
    structure_score: float = Field(ge=0.0, le=1.0)
    layout_score: float = Field(ge=0.0, le=1.0)
    visual_score: float = Field(ge=0.0, le=1.0)
    editability_score: float = Field(ge=0.0, le=1.0)
    correction_count: int = Field(ge=0)

    @field_validator("structure_score", "layout_score", "visual_score", "editability_score")
    @classmethod
    def scores_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("objective scores must be finite")
        return value


class ObjectiveComparison(_FrozenModel):
    """First decisive component and signed improvement of a candidate."""

    component: ObjectiveComponent | None
    delta: float
    improved: bool

    @field_validator("delta")
    @classmethod
    def delta_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("objective delta must be finite")
        return value


class HardInvariantViolation(StrEnum):
    PLAN_FINGERPRINT = "plan_fingerprint"
    AUTHORITY_HASH = "authority_hash"
    OBJECT_CONTENT = "object_content"
    OBJECT_PROVENANCE = "object_provenance"
    OBJECT_IDENTITY = "object_identity"
    MISSING_CONTENT = "missing_content"
    SOURCE_DELETION = "source_deletion"
    PAGE_COUNT = "page_count"
    PAGE_SIZE = "page_size"
    RENDER_REFERENCE = "render_reference"
    FULL_PAGE_RASTER = "full_page_raster"
    REQUIRED_EDITABILITY = "required_editability"
    RASTER_SUBSTITUTION = "raster_substitution"
    EDITABILITY_REGRESSION = "editability_regression"


class IterationDecision(StrEnum):
    ACCEPTED = "accepted"
    HARD_INVARIANT = "hard_invariant"
    NOT_IMPROVED = "not_improved"
    BELOW_MINIMUM_DELTA = "below_minimum_delta"
    REPEATED_STATE = "repeated_state"
    INVALID_ACTION = "invalid_action"
    ACTION_NOT_AUTHORIZED = "action_not_authorized"
    BUILD_FAILED = "build_failed"
    EVALUATION_FAILED = "evaluation_failed"


class TerminalReason(StrEnum):
    PROPOSER_EXHAUSTED = "proposer_exhausted"
    MAX_ITERATIONS = "max_iterations"
    MAX_ACTIONS = "max_actions"
    REPEATED_STATE = "repeated_state"
    INVALID_PROPOSAL = "invalid_proposal"
    INITIAL_INVARIANT_VIOLATION = "initial_invariant_violation"


class CorrectionIterationRecord(_FrozenModel):
    """Stable provenance for one attempted action batch and its rollback decision."""

    iteration: int = Field(ge=1)
    actions: tuple[CorrectionAction, ...]
    before: CorrectionObjective
    after: CorrectionObjective | None = None
    candidate_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    state_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    decision: IterationDecision
    accepted: bool
    rolled_back: bool
    decisive_component: ObjectiveComponent | None = None
    objective_delta: float = 0.0
    invariant_violations: tuple[HardInvariantViolation, ...] = ()

    @field_validator("objective_delta")
    @classmethod
    def objective_delta_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("iteration objective delta must be finite")
        return value

    @field_validator("invariant_violations")
    @classmethod
    def violations_must_be_canonical(
        cls, value: tuple[HardInvariantViolation, ...]
    ) -> tuple[HardInvariantViolation, ...]:
        if value != tuple(sorted(set(value), key=lambda item: item.value)):
            raise ValueError("invariant violations must be unique and ordered")
        return value

    @model_validator(mode="after")
    def decision_fields_must_be_consistent(self) -> Self:
        if self.accepted:
            if self.decision is not IterationDecision.ACCEPTED or self.rolled_back:
                raise ValueError("accepted iterations must use the accepted decision")
            if self.after is None or self.candidate_sha256 is None or self.state_sha256 is None:
                raise ValueError("accepted iterations require a measured candidate")
            if self.invariant_violations:
                raise ValueError("accepted iterations cannot contain hard invariant violations")
        elif not self.rolled_back:
            raise ValueError("every rejected correction attempt must roll back")
        return self


class CorrectionLimits(_FrozenModel):
    """Finite search budget; unbounded autonomous loops cannot be represented."""

    max_iterations: int = Field(default=6, ge=1, le=64)
    max_actions: int = Field(default=12, ge=1, le=256)
    max_actions_per_iteration: int = Field(default=2, ge=1, le=16)
    minimum_objective_delta: float = Field(default=0.001, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def iteration_batch_must_fit_total_budget(self) -> Self:
        if self.max_actions_per_iteration > self.max_actions:
            raise ValueError("per-iteration action limit cannot exceed the total action limit")
        return self


class CorrectionReport(_FrozenModel):
    """Deterministic audit report for a complete bounded search."""

    schema_version: str = "1.0"
    constraint_plan_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    initial_candidate_sha256: str = Field(pattern=_SHA256_PATTERN)
    final_candidate_sha256: str = Field(pattern=_SHA256_PATTERN)
    best_candidate_sha256: str = Field(pattern=_SHA256_PATTERN)
    initial_objective: CorrectionObjective
    best_objective: CorrectionObjective
    initial_invariant_violations: tuple[HardInvariantViolation, ...] = ()
    iterations: tuple[CorrectionIterationRecord, ...]
    best_iteration: int = Field(ge=0)
    total_attempted_actions: int = Field(ge=0)
    total_accepted_actions: int = Field(ge=0)
    visited_state_sha256: tuple[str, ...] = Field(min_length=1)
    authority_hash_unchanged: bool
    terminal_reason: TerminalReason

    @field_validator("visited_state_sha256")
    @classmethod
    def visited_states_must_be_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("visited correction states must be unique")
        return value

    @field_validator("initial_invariant_violations")
    @classmethod
    def initial_violations_must_be_canonical(
        cls, value: tuple[HardInvariantViolation, ...]
    ) -> tuple[HardInvariantViolation, ...]:
        if value != tuple(sorted(set(value), key=lambda item: item.value)):
            raise ValueError("initial invariant violations must be unique and ordered")
        return value

    @model_validator(mode="after")
    def report_accounting_must_be_consistent(self) -> Self:
        expected_iterations = tuple(range(1, len(self.iterations) + 1))
        if tuple(item.iteration for item in self.iterations) != expected_iterations:
            raise ValueError("correction iterations must be consecutive and ordered")
        attempted = sum(len(item.actions) for item in self.iterations)
        accepted = sum(len(item.actions) for item in self.iterations if item.accepted)
        if attempted != self.total_attempted_actions or accepted != self.total_accepted_actions:
            raise ValueError("correction action accounting does not match iteration records")
        if self.final_candidate_sha256 != self.best_candidate_sha256:
            raise ValueError("bounded correction must finish at the best accepted candidate")
        if self.best_iteration > len(self.iterations):
            raise ValueError("best iteration cannot exceed the attempted iteration count")
        return self

    @property
    def fingerprint(self) -> str:
        return stable_digest(self.model_dump(mode="json"))

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = self.model_dump(mode="json")
        payload["fingerprint"] = self.fingerprint
        return payload


__all__ = [
    "CandidateAssessment",
    "CorrectionIterationRecord",
    "CorrectionLimits",
    "CorrectionObjective",
    "CorrectionReport",
    "HardInvariantViolation",
    "IterationDecision",
    "ObjectiveComparison",
    "TerminalReason",
]
