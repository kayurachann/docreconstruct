"""Deterministic minimum-cost bipartite assignment for evaluator elements.

The evaluator intentionally owns this small Hungarian implementation instead
of making SciPy a core dependency.  Callers provide a page-local cost matrix;
``None`` marks a forbidden semantic pair.  Explicit dummy rows and columns let
the optimizer leave unsafe pairs unmatched instead of forcing a misleading
comparison.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class AssignmentPair:
    """One globally selected reference/candidate pair."""

    reference_index: int
    candidate_index: int
    cost: float


@dataclass(frozen=True)
class AssignmentResult:
    """Selected pairs plus the indices deliberately left unmatched."""

    pairs: tuple[AssignmentPair, ...]
    unmatched_reference: tuple[int, ...]
    unmatched_candidate: tuple[int, ...]


def _hungarian(costs: Sequence[Sequence[float]]) -> list[int]:
    """Return the minimum-cost column for every row of a square matrix."""

    size = len(costs)
    if size == 0:
        return []
    if any(len(row) != size for row in costs):
        raise ValueError("Hungarian cost matrix must be square")
    if any(not math.isfinite(value) for row in costs for value in row):
        raise ValueError("Hungarian costs must be finite")

    # Potentials-based O(n^3) Hungarian algorithm.  Strict comparisons and
    # ascending column scans make ties deterministic.
    u = [0.0] * (size + 1)
    v = [0.0] * (size + 1)
    matching = [0] * (size + 1)
    predecessor = [0] * (size + 1)
    for row_index in range(1, size + 1):
        matching[0] = row_index
        current_column = 0
        minimum = [math.inf] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[current_column] = True
            current_row = matching[current_column]
            delta = math.inf
            next_column = 0
            for column_index in range(1, size + 1):
                if used[column_index]:
                    continue
                reduced = (
                    costs[current_row - 1][column_index - 1] - u[current_row] - v[column_index]
                )
                if reduced < minimum[column_index]:
                    minimum[column_index] = reduced
                    predecessor[column_index] = current_column
                if minimum[column_index] < delta:
                    delta = minimum[column_index]
                    next_column = column_index
            for column_index in range(size + 1):
                if used[column_index]:
                    u[matching[column_index]] += delta
                    v[column_index] -= delta
                else:
                    minimum[column_index] -= delta
            current_column = next_column
            if matching[current_column] == 0:
                break
        while True:
            previous_column = predecessor[current_column]
            matching[current_column] = matching[previous_column]
            current_column = previous_column
            if current_column == 0:
                break

    columns = [-1] * size
    for column_index in range(1, size + 1):
        row_index = matching[column_index]
        if row_index:
            columns[row_index - 1] = column_index - 1
    return columns


def _auction_assignment(
    pair_costs: Sequence[Sequence[float | None]],
    *,
    candidate_count: int,
    unmatched_cost: float,
) -> list[int]:
    """Sparse deterministic auction for large evaluator pages.

    Matching a real pair saves ``2 * unmatched_cost - pair_cost`` versus
    leaving both endpoints unmatched. Costs are quantized to six decimals;
    epsilon below one divided by the bidder count gives an optimal assignment
    on the provided integer graph. Each bidder also owns a private zero-value
    dummy item, so unsafe pairs are never forced.
    """

    reference_count = len(pair_costs)
    scale = 1_000_000
    options: list[list[tuple[int, int]]] = []
    for ref_index, row in enumerate(pair_costs):
        real_options = [
            (candidate_index, round((2.0 * unmatched_cost - cost) * scale))
            for candidate_index, cost in enumerate(row)
            if cost is not None and cost < 2.0 * unmatched_cost
        ]
        real_options.sort(key=lambda item: (-item[1], item[0]))
        real_options.append((candidate_count + ref_index, 0))
        options.append(real_options)

    item_count = candidate_count + reference_count
    prices = [0.0] * item_count
    owners = [-1] * item_count
    assignments = [-1] * reference_count
    pending = deque(range(reference_count))
    epsilon = 1.0 / (reference_count + 1)
    while pending:
        ref_index = pending.popleft()
        ranked = sorted(
            (
                (benefit - prices[item_index], item_index)
                for item_index, benefit in options[ref_index]
            ),
            key=lambda item: (-item[0], item[1]),
        )
        best_value, best_item = ranked[0]
        second_value = ranked[1][0] if len(ranked) > 1 else best_value - 1.0
        if best_item >= candidate_count:
            assignments[ref_index] = best_item
            owners[best_item] = ref_index
            continue
        prices[best_item] += best_value - second_value + epsilon
        previous_owner = owners[best_item]
        owners[best_item] = ref_index
        assignments[ref_index] = best_item
        if previous_owner >= 0:
            assignments[previous_owner] = -1
            pending.append(previous_owner)
    return assignments


def minimum_cost_assignment(
    pair_costs: Sequence[Sequence[float | None]],
    *,
    candidate_count: int | None = None,
    unmatched_cost: float = 0.4,
) -> AssignmentResult:
    """Globally assign a rectangular page-local cost matrix.

    A real pair competes with two unmatched operations, so a pair whose cost
    is at least ``2 * unmatched_cost`` is deliberately left unmatched.
    """

    reference_count = len(pair_costs)
    if candidate_count is None:
        candidate_count = len(pair_costs[0]) if pair_costs else 0
    if candidate_count < 0:
        raise ValueError("candidate_count must be non-negative")
    if any(len(row) != candidate_count for row in pair_costs):
        raise ValueError("all assignment cost rows must have candidate_count entries")
    if not math.isfinite(unmatched_cost) or unmatched_cost <= 0:
        raise ValueError("unmatched_cost must be a positive finite number")
    for row in pair_costs:
        for value in row:
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError("pair costs must be non-negative finite numbers or None")

    if reference_count == 0 or candidate_count == 0:
        return AssignmentResult(
            pairs=(),
            unmatched_reference=tuple(range(reference_count)),
            unmatched_candidate=tuple(range(candidate_count)),
        )

    size = reference_count + candidate_count
    if size <= 192:
        forbidden = max(1_000_000.0, unmatched_cost * (size + 1) * 100.0)
        matrix = [[forbidden for _ in range(size)] for _ in range(size)]
        for ref_index, row in enumerate(pair_costs):
            for cand_index, cost in enumerate(row):
                if cost is not None:
                    matrix[ref_index][cand_index] = float(cost)
            for dummy_column in range(candidate_count, size):
                matrix[ref_index][dummy_column] = unmatched_cost
        for dummy_row in range(reference_count, size):
            for cand_index in range(candidate_count):
                matrix[dummy_row][cand_index] = unmatched_cost
            for dummy_column in range(candidate_count, size):
                matrix[dummy_row][dummy_column] = 0.0
        columns = _hungarian(matrix)[:reference_count]
    else:
        columns = _auction_assignment(
            pair_costs,
            candidate_count=candidate_count,
            unmatched_cost=unmatched_cost,
        )
    selected: list[AssignmentPair] = []
    for ref_index, cand_index in enumerate(columns):
        if not 0 <= cand_index < candidate_count:
            continue
        pair_cost = pair_costs[ref_index][cand_index]
        if pair_cost is not None and pair_cost < 2.0 * unmatched_cost:
            selected.append(AssignmentPair(ref_index, cand_index, pair_cost))
    pairs = tuple(selected)
    matched_refs = {pair.reference_index for pair in pairs}
    matched_cands = {pair.candidate_index for pair in pairs}
    return AssignmentResult(
        pairs=pairs,
        unmatched_reference=tuple(
            index for index in range(reference_count) if index not in matched_refs
        ),
        unmatched_candidate=tuple(
            index for index in range(candidate_count) if index not in matched_cands
        ),
    )


__all__ = ["AssignmentPair", "AssignmentResult", "minimum_cost_assignment"]
