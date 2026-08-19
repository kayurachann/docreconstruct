"""Safe prompt contracts for optional ambiguity-resolution plugins."""

from .verifier import (
    VERIFIER_SYSTEM_PROMPT,
    VerificationOperation,
    VerificationProposal,
    VerificationResponse,
    validate_verification_response,
)

__all__ = [
    "VERIFIER_SYSTEM_PROMPT",
    "VerificationOperation",
    "VerificationProposal",
    "VerificationResponse",
    "validate_verification_response",
]
