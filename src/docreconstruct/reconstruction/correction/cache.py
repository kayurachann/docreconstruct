"""SHA-256 keyed render/evaluation cache for concrete candidate artifacts."""

from __future__ import annotations

import hashlib
from threading import RLock

from docreconstruct.reconstruction.constraint_plan import ConstraintPlan
from docreconstruct.reconstruction.constraint_plan.canonical import stable_digest

from .models import CandidateAssessment
from .protocols import CandidateEvaluator


def candidate_sha256(artifact: bytes) -> str:
    """Hash exact DOCX/candidate bytes; paths and action labels never enter the key."""

    return hashlib.sha256(artifact).hexdigest()


class CandidateRenderCache:
    """Cache one plan-scoped assessment for each exact candidate content hash.

    Rendering is identified by the candidate digest, but an assessment also
    contains plan-relative object IDs, authority hashes, and reference-page
    diagnostics.  Including the constraint-plan fingerprint in the internal
    key prevents a cache shared by two jobs from reusing those measurements
    across incompatible authority contracts.
    """

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], CandidateAssessment] = {}
        self._hits = 0
        self._misses = 0
        self._lock = RLock()

    @property
    def hits(self) -> int:
        with self._lock:
            return self._hits

    @property
    def misses(self) -> int:
        with self._lock:
            return self._misses

    @property
    def keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted({digest for digest, _plan in self._items}))

    def evaluate(
        self,
        artifact: bytes,
        evaluator: CandidateEvaluator,
        constraint_plan: ConstraintPlan,
    ) -> tuple[str, CandidateAssessment]:
        """Return the one assessment associated with these exact artifact bytes."""

        digest = candidate_sha256(artifact)
        cache_key = (digest, constraint_plan.fingerprint)
        with self._lock:
            cached = self._items.get(cache_key)
            if cached is not None:
                self._hits += 1
                return digest, cached
            self._misses += 1
        measured = CandidateAssessment.model_validate(evaluator(artifact, digest, constraint_plan))
        with self._lock:
            raced = self._items.get(cache_key)
            if raced is not None:
                if stable_digest(raced.model_dump(mode="json")) != stable_digest(
                    measured.model_dump(mode="json")
                ):
                    raise ValueError("one candidate hash produced inconsistent assessments")
                self._hits += 1
                return digest, raced
            self._items[cache_key] = measured
        return digest, measured


__all__ = ["CandidateRenderCache", "candidate_sha256"]
