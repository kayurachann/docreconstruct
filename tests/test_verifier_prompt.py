from __future__ import annotations

import pytest

from docreconstruct import BBox, Document, Element, Page, TextCandidate
from docreconstruct.prompts import (
    VERIFIER_SYSTEM_PROMPT,
    VerificationOperation,
    validate_verification_response,
)


def _document() -> Document:
    return Document(
        id="doc",
        pages=[
            Page(
                id="p1",
                number=1,
                width=100,
                height=100,
                elements=[
                    Element(
                        id="amount",
                        bbox=BBox(x0=0, y0=0, x1=50, y1=10),
                        text="$12,804,921",
                        text_candidates=[
                            TextCandidate(
                                engine="paddleocr",
                                value="$12,804,921",
                                source_element_id="paddle-1",
                            )
                        ],
                    )
                ],
            )
        ],
    )


def test_prompt_explicitly_forbids_authorship() -> None:
    assert "Never rewrite" in VERIFIER_SYSTEM_PROMPT
    assert "Never invent" in VERIFIER_SYSTEM_PROMPT


def test_verifier_accepts_only_an_existing_exact_candidate() -> None:
    response = validate_verification_response(
        _document(),
        {
            "corrections": [
                {
                    "object_id": "amount",
                    "operation": VerificationOperation.SELECT_TEXT_CANDIDATE,
                    "proposed_value": "$12,804,921",
                    "confidence": 0.99,
                    "visual_evidence": "All final digits are visible in the crop.",
                    "source_candidate_id": "paddle-1",
                }
            ]
        },
    )
    assert response.corrections[0].proposed_value == "$12,804,921"


def test_verifier_rejects_invented_text() -> None:
    with pytest.raises(ValueError, match="exactly equal"):
        validate_verification_response(
            _document(),
            {
                "corrections": [
                    {
                        "object_id": "amount",
                        "operation": "select-text-candidate",
                        "proposed_value": "$99,999,999",
                        "confidence": 0.99,
                        "visual_evidence": "guessed",
                        "source_candidate_id": "paddle-1",
                    }
                ]
            },
        )
