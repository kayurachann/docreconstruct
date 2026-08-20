"""Versioned, content-safe models for evidence-alignment decision traces."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AlignmentReason(StrEnum):
    """Stable machine-readable explanations; values are part of the report contract."""

    NO_TEXT_CANDIDATE = "no_text_candidate"
    TEXT_BELOW_THRESHOLD = "text_below_threshold"
    UNSAFE_GEOMETRY = "unsafe_geometry"
    PAGE_CONFLICT = "page_conflict"
    ORDER_CONFLICT = "order_conflict"
    TYPE_CONFLICT = "type_conflict"
    AMBIGUOUS_CANDIDATES = "ambiguous_candidates"
    SPAN_LIMIT_REACHED = "span_limit_reached"
    CANDIDATE_BUDGET_REACHED = "candidate_budget_reached"
    PROJECTION_INVALID = "projection_invalid"
    REGION_CONFLICT = "region_conflict"


class AlignmentDecisionStatus(StrEnum):
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"
    REJECTED = "rejected"


_REASON_ORDER = {reason: index for index, reason in enumerate(AlignmentReason)}


def canonical_reasons(
    values: list[AlignmentReason] | tuple[AlignmentReason, ...],
) -> tuple[AlignmentReason, ...]:
    """Deduplicate reason codes in their versioned declaration order."""

    return tuple(sorted(set(values), key=_REASON_ORDER.__getitem__))


class AlignmentCandidateTrace(_FrozenModel):
    """One accepted or rejected candidate, without source text or raw identifiers."""

    candidate_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    block_id: str = Field(pattern=r"^block-[0-9]{6}$")
    element_ids: tuple[str, ...] = ()
    page_number: int = Field(ge=1)
    text_score: float = Field(ge=0.0, le=1.0)
    geometry_score: float = Field(ge=0.0, le=1.0)
    type_score: float = Field(ge=0.0, le=1.0)
    order_score: float = Field(ge=0.0, le=1.0)
    total_score: float = Field(ge=0.0, le=1.0)
    rejection_reasons: tuple[AlignmentReason, ...] = ()

    @field_validator(
        "text_score",
        "geometry_score",
        "type_score",
        "order_score",
        "total_score",
    )
    @classmethod
    def scores_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("alignment scores must be finite")
        return value

    @field_validator("element_ids")
    @classmethod
    def element_ids_must_be_opaque_and_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.startswith("element-") for item in value):
            raise ValueError("alignment reports may contain only opaque element identifiers")
        if value != tuple(sorted(set(value))):
            raise ValueError("opaque element identifiers must be unique and ordered")
        return value

    @field_validator("rejection_reasons")
    @classmethod
    def rejection_reasons_must_be_canonical(
        cls, value: tuple[AlignmentReason, ...]
    ) -> tuple[AlignmentReason, ...]:
        if value != canonical_reasons(value):
            raise ValueError("candidate rejection reasons must use canonical order")
        return value


class AlignmentDecisionTrace(_FrozenModel):
    """The exact diagnostic disposition of one Markdown-authority block."""

    block_id: str = Field(pattern=r"^block-[0-9]{6}$")
    block_index: int = Field(ge=0)
    block_kind: str = Field(min_length=1)
    status: AlignmentDecisionStatus
    selected_candidate: AlignmentCandidateTrace | None = None
    alternatives: tuple[AlignmentCandidateTrace, ...] = ()
    reason_codes: tuple[AlignmentReason, ...] = ()
    candidates_considered: int = Field(ge=0)
    candidates_retained: int = Field(ge=0)

    @field_validator("reason_codes")
    @classmethod
    def reason_codes_must_be_canonical(
        cls, value: tuple[AlignmentReason, ...]
    ) -> tuple[AlignmentReason, ...]:
        if value != canonical_reasons(value):
            raise ValueError("decision reasons must use canonical order")
        return value

    @model_validator(mode="after")
    def decision_shape_must_match_status(self) -> Self:
        if self.status is AlignmentDecisionStatus.MATCHED:
            if self.selected_candidate is None:
                raise ValueError("matched alignment decisions require a selected candidate")
            if self.selected_candidate.rejection_reasons:
                raise ValueError("selected alignment candidates cannot have rejection reasons")
        elif self.selected_candidate is not None:
            raise ValueError("only matched decisions may expose a selected candidate")
        if self.status is not AlignmentDecisionStatus.MATCHED and not self.reason_codes:
            raise ValueError("non-matched alignment decisions require a reason code")
        candidate_ids = [candidate.candidate_id for candidate in self.alternatives]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("alignment alternatives must be unique")
        if (
            self.selected_candidate is not None
            and self.selected_candidate.candidate_id in candidate_ids
        ):
            raise ValueError("the selected alignment candidate cannot also be an alternative")
        return self


class AlignmentReasonCount(_FrozenModel):
    reason: AlignmentReason
    count: int = Field(ge=1)


class AlignmentSummary(_FrozenModel):
    """Failure-inclusive counts for all Markdown blocks in the report."""

    total_blocks: int = Field(ge=0)
    matched: int = Field(ge=0)
    ambiguous: int = Field(ge=0)
    unmatched: int = Field(ge=0)
    rejected: int = Field(ge=0)
    reason_counts: tuple[AlignmentReasonCount, ...] = ()

    @model_validator(mode="after")
    def statuses_must_cover_every_block(self) -> Self:
        if self.matched + self.ambiguous + self.unmatched + self.rejected != self.total_blocks:
            raise ValueError("alignment status counts must cover every Markdown block")
        reasons = tuple(item.reason for item in self.reason_counts)
        if reasons != canonical_reasons(reasons):
            raise ValueError("alignment reason counts must use canonical order")
        return self


class AlignmentPrivacyPolicy(_FrozenModel):
    """Machine-readable guarantee that the portable report excludes authority content."""

    identifier_policy: Literal["opaque_positional_v1"] = "opaque_positional_v1"
    content_included: Literal[False] = False
    source_paths_included: Literal[False] = False
    raw_provider_ids_included: Literal[False] = False
    excluded_fields: tuple[str, ...] = (
        "markdown_text",
        "ocr_text",
        "content_derived_identifiers",
        "source_paths",
        "page_pixels",
        "raw_block_ids",
        "raw_element_ids",
    )


class AlignmentDebugPolicy(_FrozenModel):
    """Structured limitation for visual debug artifacts deferred by the privacy contract."""

    status: Literal["disabled_for_privacy"] = "disabled_for_privacy"
    reason: Literal["page_pixels_and_authority_text_require_explicit_redaction_contract"] = (
        "page_pixels_and_authority_text_require_explicit_redaction_contract"
    )


class AlignmentReport(_FrozenModel):
    """Versioned, deterministic report produced without affecting matcher acceptance."""

    schema_version: Literal["1.0"] = "1.0"
    matcher_contract: Literal["evidence_matching_v1_observation_only"] = (
        "evidence_matching_v1_observation_only"
    )
    top_n: int = Field(ge=1, le=20)
    maximum_span: int = Field(ge=1)
    candidate_budget: int = Field(ge=1)
    decisions: tuple[AlignmentDecisionTrace, ...]
    summary: AlignmentSummary
    privacy: AlignmentPrivacyPolicy = AlignmentPrivacyPolicy()
    debug_artifacts: AlignmentDebugPolicy = AlignmentDebugPolicy()

    @model_validator(mode="after")
    def report_must_have_one_ordered_decision_per_block(self) -> Self:
        if len(self.decisions) != self.summary.total_blocks:
            raise ValueError("alignment decisions must match the summary block count")
        indices = tuple(decision.block_index for decision in self.decisions)
        if indices != tuple(range(len(indices))):
            raise ValueError("alignment decisions must be in contiguous Markdown order")
        if any(len(decision.alternatives) > self.top_n for decision in self.decisions):
            raise ValueError("alignment alternatives exceed the configured top-N limit")
        return self


__all__ = [
    "AlignmentCandidateTrace",
    "AlignmentDebugPolicy",
    "AlignmentDecisionStatus",
    "AlignmentDecisionTrace",
    "AlignmentPrivacyPolicy",
    "AlignmentReason",
    "AlignmentReasonCount",
    "AlignmentReport",
    "AlignmentSummary",
    "canonical_reasons",
]
