"""Constrained verifier contract for optional VLM/LLM adjudication."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from docreconstruct.ir import Document

VERIFIER_SYSTEM_PROMPT = """You are a document reconstruction verifier.

Resolve structural and formatting ambiguity only. You are not an author.

Non-negotiable rules:
1. Never rewrite, summarize, translate, normalize, or improve source content.
2. Select text only from the supplied OCR candidates and visible evidence.
3. Never invent an illegible character; flag it as uncertain.
4. Preserve source geometry and reading order unless the evidence supports a correction.
5. Every correction must reference an existing object_id and visual evidence.
6. Return only JSON that validates against the supplied response schema.
"""


class VerificationOperation(StrEnum):
    MOVE = "move"
    RESIZE = "resize"
    RETYPE = "retype"
    REORDER = "reorder"
    RESTYLE = "restyle"
    SELECT_TEXT_CANDIDATE = "select-text-candidate"
    FLAG_UNCERTAIN = "flag-uncertain"


class VerificationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str = Field(min_length=1)
    operation: VerificationOperation
    proposed_value: Any = None
    confidence: float = Field(ge=0.0, le=1.0)
    visual_evidence: str = Field(min_length=1)
    source_candidate_id: str | None = None

    @model_validator(mode="after")
    def text_selection_requires_a_candidate(self) -> VerificationProposal:
        if (
            self.operation is VerificationOperation.SELECT_TEXT_CANDIDATE
            and not self.source_candidate_id
        ):
            raise ValueError("text selection requires source_candidate_id")
        return self


class VerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corrections: list[VerificationProposal] = Field(default_factory=list)


def validate_verification_response(
    document: Document,
    response: VerificationResponse | dict[str, Any],
) -> VerificationResponse:
    """Reject invented objects or text candidates before any correction runs."""

    validated = (
        response
        if isinstance(response, VerificationResponse)
        else VerificationResponse.model_validate(response)
    )
    elements = {element.id: element for page in document.pages for element in page.elements}
    for proposal in validated.corrections:
        element = elements.get(proposal.object_id)
        if element is None:
            raise ValueError(f"verification references unknown object {proposal.object_id!r}")
        if proposal.operation is VerificationOperation.SELECT_TEXT_CANDIDATE:
            candidate = next(
                (
                    item
                    for item in element.text_candidates
                    if item.source_element_id == proposal.source_candidate_id
                    or f"{item.engine}:{item.source_element_id or ''}"
                    == proposal.source_candidate_id
                ),
                None,
            )
            if candidate is None:
                raise ValueError(
                    f"unknown text candidate {proposal.source_candidate_id!r} "
                    f"for {proposal.object_id!r}"
                )
            if proposal.proposed_value != candidate.value:
                raise ValueError("proposed text must exactly equal the selected OCR candidate")
    return validated
