"""Observation-only diagnostics for the current deterministic evidence matcher."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

import docreconstruct.reconstruction.evidence_matching as matching
from docreconstruct.reconstruction.markdown_content import (
    MarkdownBlock,
    MarkdownBlockKind,
    MarkdownContent,
)
from docreconstruct.reconstruction.scan_layout import ScanDocumentLayout

from .candidates import (
    ObservedCandidate,
    accepted_trace,
    candidate_element_ids,
    source_id,
    text_inventory,
    top_traces,
    unsafe_geometry_traces,
)
from .models import (
    AlignmentCandidateTrace,
    AlignmentDecisionStatus,
    AlignmentDecisionTrace,
    AlignmentReason,
    AlignmentReasonCount,
    AlignmentReport,
    AlignmentSummary,
    canonical_reasons,
)
from .privacy import canonical_sha256, opaque_block_id, opaque_candidate_id

_AMBIGUITY_DELTA = 0.01


def _source_observations(
    source: Any,
    blocks: Sequence[MarkdownBlock],
    scan_pages: Mapping[int, Any],
    workspace: Any,
    top_n: int,
) -> tuple[
    dict[str, list[ObservedCandidate]],
    dict[str, list[ObservedCandidate]],
    dict[str, list[AlignmentCandidateTrace]],
    Counter[str],
    Counter[str],
    set[str],
]:
    source_identifier = source_id(source.document)
    units = matching._evidence_units(source, scan_pages, workspace)
    accepted: dict[str, list[ObservedCandidate]] = defaultdict(list)
    selected: dict[str, list[ObservedCandidate]] = defaultdict(list)
    rejected: dict[str, list[AlignmentCandidateTrace]] = defaultdict(list)
    considered: Counter[str] = Counter()
    retained: Counter[str] = Counter()
    budget_blocks: set[str] = set()
    unsafe, unsafe_considered = unsafe_geometry_traces(
        source,
        blocks,
        scan_pages,
        workspace,
        top_n,
    )
    for block_id, traces in unsafe.items():
        rejected[block_id].extend(traces)
    considered.update(unsafe_considered)
    if not units:
        return accepted, selected, rejected, considered, retained, budget_blocks

    candidate_index = matching._TextCandidateIndex(blocks, units, workspace)
    aligned = matching._align_source(blocks, units, candidate_index, workspace)
    exhaustive = not candidate_index.alignment_respects_anchors(aligned)
    if exhaustive:
        aligned = matching._align_source(
            blocks,
            units,
            candidate_index,
            workspace,
            exhaustive=True,
        )
    contributions = [
        *aligned,
        *matching._shared_consecutive_text_candidates(blocks, units, aligned, workspace),
        *matching._unreliable_exact_visual_candidates(blocks, units, aligned),
    ]
    for candidate in contributions:
        item = ObservedCandidate(
            source_identifier,
            candidate,
            candidate_element_ids(source_identifier, candidate, units),
        )
        selected[candidate.block.id].append(item)

    cache: dict[int, list[Any]] = {}
    for block in blocks:
        candidates = matching._block_candidates(
            block,
            blocks,
            units,
            cache,
            candidate_index,
            workspace,
            exhaustive=exhaustive,
        )
        retained[block.id] += len(candidates)
        for candidate in candidates:
            accepted[block.id].append(
                ObservedCandidate(
                    source_identifier,
                    candidate,
                    candidate_element_ids(source_identifier, candidate, units),
                )
            )
        for item in selected.get(block.id, ()):
            if item.identity not in {candidate.identity for candidate in accepted[block.id]}:
                accepted[block.id].append(item)
                retained[block.id] += 1
        if block.kind is MarkdownBlockKind.IMAGE:
            considered[block.id] += sum(
                unit.element_type in matching._VISUAL_TYPES for unit in units
            )
            continue
        span_count, below, span_limited, budget_reached = text_inventory(
            block,
            source_identifier,
            candidate_index,
            units,
            workspace,
            exhaustive=exhaustive,
            retained_count=len(candidates),
            top_n=top_n,
        )
        considered[block.id] += span_count
        rejected[block.id].extend(below)
        rejected[block.id].extend(span_limited)
        if budget_reached:
            budget_blocks.add(block.id)
    return accepted, selected, rejected, considered, retained, budget_blocks


def _path_reasons(
    block: MarkdownBlock,
    item: ObservedCandidate,
    selected: Mapping[str, Sequence[ObservedCandidate]],
) -> tuple[AlignmentReason, ...]:
    peers = [
        candidate
        for block_id, candidates in selected.items()
        if block_id != block.id
        for candidate in candidates
        if candidate.source_id == item.source_id
    ]
    previous = [candidate for candidate in peers if candidate.candidate.block.index < block.index]
    following = [candidate for candidate in peers if candidate.candidate.block.index > block.index]
    reasons: list[AlignmentReason] = []
    if previous:
        neighbor = max(previous, key=lambda candidate: candidate.candidate.block.index)
        if item.candidate.page_number < neighbor.candidate.page_number:
            reasons.append(AlignmentReason.PAGE_CONFLICT)
        if item.candidate.start < neighbor.candidate.end:
            reasons.append(AlignmentReason.ORDER_CONFLICT)
    if following:
        neighbor = min(following, key=lambda candidate: candidate.candidate.block.index)
        if item.candidate.page_number > neighbor.candidate.page_number:
            reasons.append(AlignmentReason.PAGE_CONFLICT)
        if item.candidate.end > neighbor.candidate.start:
            reasons.append(AlignmentReason.ORDER_CONFLICT)
    return canonical_reasons(reasons)


def _selected_trace(
    block: MarkdownBlock,
    match: Any,
    contributions: Sequence[ObservedCandidate],
) -> AlignmentCandidateTrace:
    ordered = sorted(contributions, key=lambda item: item.identity)
    element_ids = tuple(sorted({value for item in ordered for value in item.element_ids}))
    text_score = math.fsum(item.candidate.text_score for item in ordered) / max(1, len(ordered))
    type_score = math.fsum(item.candidate.type_score for item in ordered) / max(1, len(ordered))
    geometry_score = math.fsum(
        matching._box_agreement(item.candidate.bbox, match.source_bbox) for item in ordered
    ) / max(1, len(ordered))
    source_id = canonical_sha256(sorted({item.source_id for item in ordered}))
    return AlignmentCandidateTrace(
        candidate_id=opaque_candidate_id(
            block_id=opaque_block_id(block.index),
            element_ids=element_ids,
            page_number=match.page_number,
            source_id=source_id,
            start=min((item.candidate.start for item in ordered), default=0),
            end=max((item.candidate.end for item in ordered), default=0),
            origin="selected_fused",
        ),
        block_id=opaque_block_id(block.index),
        element_ids=element_ids,
        page_number=match.page_number,
        text_score=text_score,
        geometry_score=geometry_score,
        type_score=type_score,
        order_score=1.0,
        total_score=match.match_score,
    )


def build_alignment_report(
    content: MarkdownContent,
    layout: ScanDocumentLayout,
    evidence: matching.EvidenceDocuments,
    *,
    matches: Sequence[matching.EvidenceMatch] | None = None,
    top_n: int = 5,
) -> AlignmentReport:
    """Explain every block while leaving candidate generation and acceptance unchanged."""

    if not 1 <= top_n <= 20:
        raise ValueError("alignment report top_n must be between 1 and 20")
    sources = matching._document_sources(evidence)
    scan_pages = {page.number: page for page in layout.pages}
    workspace = matching._MatchWorkspace.create()
    accepted: dict[str, list[ObservedCandidate]] = defaultdict(list)
    selected: dict[str, list[ObservedCandidate]] = defaultdict(list)
    rejected: dict[str, list[AlignmentCandidateTrace]] = defaultdict(list)
    considered: Counter[str] = Counter()
    retained: Counter[str] = Counter()
    budget_blocks: set[str] = set()
    for source in sources:
        (
            source_accepted,
            source_selected,
            source_rejected,
            source_considered,
            source_retained,
            source_budget_blocks,
        ) = _source_observations(source, content.blocks, scan_pages, workspace, top_n)
        for block_id, accepted_candidates in source_accepted.items():
            accepted[block_id].extend(accepted_candidates)
        for block_id, selected_candidates in source_selected.items():
            selected[block_id].extend(selected_candidates)
        for block_id, rejected_candidates in source_rejected.items():
            rejected[block_id].extend(rejected_candidates)
        considered.update(source_considered)
        retained.update(source_retained)
        budget_blocks.update(source_budget_blocks)

    resolved_matches = tuple(
        matches
        if matches is not None
        else matching.match_sidecar_evidence(content, layout, evidence)
    )
    by_block = {match.block_id: match for match in resolved_matches}
    decisions: list[AlignmentDecisionTrace] = []
    for block in content.blocks:
        final = by_block.get(block.id)
        selected_items = selected.get(block.id, [])
        selected_identities = {item.identity for item in selected_items}
        accepted_items = sorted(accepted.get(block.id, []), key=lambda item: item.identity)
        decision_reasons: list[AlignmentReason] = []
        if block.id in budget_blocks:
            decision_reasons.append(AlignmentReason.CANDIDATE_BUDGET_REACHED)
        rejected_traces = list(rejected.get(block.id, []))
        if any(
            AlignmentReason.SPAN_LIMIT_REACHED in trace.rejection_reasons
            for trace in rejected_traces
        ):
            decision_reasons.append(AlignmentReason.SPAN_LIMIT_REACHED)

        alternatives: list[AlignmentCandidateTrace] = list(rejected_traces)
        scores = sorted((item.candidate.match_score for item in accepted_items), reverse=True)
        ambiguous = len(scores) > 1 and scores[0] - scores[1] <= _AMBIGUITY_DELTA
        for item in accepted_items:
            if item.identity in selected_identities:
                continue
            reasons = list(_path_reasons(block, item, selected))
            if item.candidate.type_score < 0.62:
                reasons.append(AlignmentReason.TYPE_CONFLICT)
            if not reasons:
                reasons.append(
                    AlignmentReason.AMBIGUOUS_CANDIDATES
                    if ambiguous
                    else AlignmentReason.ORDER_CONFLICT
                )
            if final is not None and (
                item.candidate.page_number != final.page_number
                or matching._box_agreement(item.candidate.bbox, final.source_bbox)
                < matching._GEOMETRY_AGREEMENT
            ):
                reasons.append(AlignmentReason.REGION_CONFLICT)
            alternatives.append(
                accepted_trace(
                    block,
                    item,
                    reasons=canonical_reasons(reasons),
                    order_score=(0.0 if AlignmentReason.ORDER_CONFLICT in reasons else 1.0),
                )
            )

        if final is not None:
            status = AlignmentDecisionStatus.MATCHED
            selected_trace = _selected_trace(block, final, selected_items)
        elif accepted_items and ambiguous:
            status = AlignmentDecisionStatus.AMBIGUOUS
            selected_trace = None
            decision_reasons.append(AlignmentReason.AMBIGUOUS_CANDIDATES)
            locations = {
                (item.candidate.page_number, item.candidate.bbox.model_dump_json())
                for item in accepted_items
                if item.candidate.match_score >= scores[0] - _AMBIGUITY_DELTA
            }
            if len(locations) > 1:
                decision_reasons.append(AlignmentReason.REGION_CONFLICT)
        elif accepted_items or rejected_traces:
            status = AlignmentDecisionStatus.REJECTED
            selected_trace = None
            decision_reasons.extend(
                reason for trace in alternatives for reason in trace.rejection_reasons
            )
            if not decision_reasons:
                decision_reasons.append(AlignmentReason.ORDER_CONFLICT)
        else:
            status = AlignmentDecisionStatus.UNMATCHED
            selected_trace = None
            decision_reasons.append(AlignmentReason.NO_TEXT_CANDIDATE)

        decisions.append(
            AlignmentDecisionTrace(
                block_id=opaque_block_id(block.index),
                block_index=block.index,
                block_kind=block.kind.value,
                status=status,
                selected_candidate=selected_trace,
                alternatives=tuple(top_traces(alternatives, top_n)),
                reason_codes=canonical_reasons(decision_reasons),
                candidates_considered=considered[block.id],
                candidates_retained=retained[block.id],
            )
        )

    statuses = Counter(decision.status for decision in decisions)
    reason_counts = Counter(reason for decision in decisions for reason in decision.reason_codes)
    summary = AlignmentSummary(
        total_blocks=len(decisions),
        matched=statuses[AlignmentDecisionStatus.MATCHED],
        ambiguous=statuses[AlignmentDecisionStatus.AMBIGUOUS],
        unmatched=statuses[AlignmentDecisionStatus.UNMATCHED],
        rejected=statuses[AlignmentDecisionStatus.REJECTED],
        reason_counts=tuple(
            AlignmentReasonCount(reason=reason, count=reason_counts[reason])
            for reason in AlignmentReason
            if reason_counts[reason]
        ),
    )
    return AlignmentReport(
        top_n=top_n,
        maximum_span=matching._MAX_SPAN,
        candidate_budget=matching._MAX_CANDIDATES_PER_BLOCK,
        decisions=tuple(decisions),
        summary=summary,
    )


__all__ = ["build_alignment_report"]
