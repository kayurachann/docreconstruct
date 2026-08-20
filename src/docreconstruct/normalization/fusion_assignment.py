"""Deterministic cardinality-first bipartite assignment for evidence fusion."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence


class AssignmentBudgetExceeded(RuntimeError):
    """Raised before assignment component matrices exceed their work budget."""


def maximum_cardinality_score_sparse_assignment(
    row_count: int,
    scores: Mapping[tuple[int, int], float],
    *,
    cell_budget: int,
) -> tuple[list[tuple[int, int]], int]:
    """Solve independent sparse components and report allocated dense cells."""

    if row_count < 0:
        raise ValueError("row_count must be non-negative")
    if cell_budget <= 0:
        raise ValueError("cell_budget must be positive")
    row_columns: dict[int, set[int]] = defaultdict(set)
    column_rows: dict[int, set[int]] = defaultdict(set)
    for (row, column), score in scores.items():
        if not 0 <= row < row_count or column < 0:
            raise ValueError("sparse assignment edge index is out of range")
        if not math.isfinite(score):
            raise ValueError("assignment scores must be finite")
        row_columns[row].add(column)
        column_rows[column].add(row)

    allocated_cells = 0
    matches: list[tuple[int, int]] = []
    for rows, columns in _bipartite_components(row_columns, column_rows):
        component_cells = len(rows) * len(columns)
        if allocated_cells + component_cells > cell_budget:
            raise AssignmentBudgetExceeded
        allocated_cells += component_cells
        matrix = [[scores.get((row, column)) for column in columns] for row in rows]
        matches.extend(
            (rows[local_row], columns[local_column])
            for local_row, local_column in maximum_cardinality_score_assignment(matrix)
        )
    return sorted(matches), allocated_cells


def _bipartite_components(
    row_columns: Mapping[int, set[int]],
    column_rows: Mapping[int, set[int]],
) -> list[tuple[list[int], list[int]]]:
    components: list[tuple[list[int], list[int]]] = []
    unseen_rows = set(row_columns)
    while unseen_rows:
        pending_rows = [min(unseen_rows)]
        rows: set[int] = set()
        columns: set[int] = set()
        while pending_rows:
            row = pending_rows.pop()
            if row in rows:
                continue
            rows.add(row)
            unseen_rows.discard(row)
            new_columns = row_columns.get(row, set()) - columns
            columns.update(new_columns)
            for column in sorted(new_columns, reverse=True):
                pending_rows.extend(sorted(column_rows[column] - rows, reverse=True))
        components.append((sorted(rows), sorted(columns)))
    return components


def maximum_cardinality_score_assignment(
    scores: Sequence[Sequence[float | None]],
) -> list[tuple[int, int]]:
    """Match rows to real columns, maximizing cardinality before total score.

    ``None`` denotes a forbidden edge. Dummy columns let every row remain
    unmatched. A cardinality bonus larger than every possible aggregate score
    makes the dense Hungarian solve lexicographic: first maximum cardinality,
    then maximum score. Canonical row/column order provides deterministic ties.
    """

    row_count = len(scores)
    if row_count == 0:
        return []
    real_column_count = len(scores[0])
    if any(len(row) != real_column_count for row in scores):
        raise ValueError("assignment score rows must have equal length")
    if real_column_count == 0:
        return []

    valid_scores = [score for row in scores for score in row if score is not None]
    if any(not math.isfinite(score) for score in valid_scores):
        raise ValueError("assignment scores must be finite")
    maximum_magnitude = max((abs(score) for score in valid_scores), default=1.0)
    cardinality_bonus = (2.0 * row_count + 1.0) * max(1.0, maximum_magnitude) + 1.0
    forbidden_weight = -cardinality_bonus * float(row_count + 1)
    weights: list[list[float]] = []
    for row in scores:
        real_weights = [
            forbidden_weight if score is None else cardinality_bonus + score for score in row
        ]
        weights.append(real_weights + [0.0] * row_count)

    maximum_weight = cardinality_bonus + max(valid_scores, default=0.0)
    costs = [[maximum_weight - weight for weight in row] for row in weights]
    assigned_columns = _hungarian_minimize(costs)
    return [
        (row_index, column_index)
        for row_index, column_index in enumerate(assigned_columns)
        if column_index < real_column_count and scores[row_index][column_index] is not None
    ]


def _hungarian_minimize(costs: Sequence[Sequence[float]]) -> list[int]:
    """Return one minimum-cost column per row for a rows <= columns matrix."""

    row_count = len(costs)
    column_count = len(costs[0]) if costs else 0
    if row_count > column_count:
        raise ValueError("Hungarian assignment requires at least as many columns as rows")

    row_potential = [0.0] * (row_count + 1)
    column_potential = [0.0] * (column_count + 1)
    column_row = [0] * (column_count + 1)
    predecessor = [0] * (column_count + 1)

    for row in range(1, row_count + 1):
        column_row[0] = row
        minimum = [float("inf")] * (column_count + 1)
        used = [False] * (column_count + 1)
        current_column = 0
        while True:
            used[current_column] = True
            current_row = column_row[current_column]
            delta = float("inf")
            next_column = 0
            for column in range(1, column_count + 1):
                if used[column]:
                    continue
                reduced = (
                    costs[current_row - 1][column - 1]
                    - row_potential[current_row]
                    - column_potential[column]
                )
                if reduced < minimum[column]:
                    minimum[column] = reduced
                    predecessor[column] = current_column
                if minimum[column] < delta:
                    delta = minimum[column]
                    next_column = column
            for column in range(column_count + 1):
                if used[column]:
                    row_potential[column_row[column]] += delta
                    column_potential[column] -= delta
                else:
                    minimum[column] -= delta
            current_column = next_column
            if column_row[current_column] == 0:
                break
        while True:
            previous_column = predecessor[current_column]
            column_row[current_column] = column_row[previous_column]
            current_column = previous_column
            if current_column == 0:
                break

    assigned = [-1] * row_count
    for column in range(1, column_count + 1):
        if column_row[column] != 0:
            assigned[column_row[column] - 1] = column - 1
    return assigned


__all__ = [
    "AssignmentBudgetExceeded",
    "maximum_cardinality_score_assignment",
    "maximum_cardinality_score_sparse_assignment",
]
