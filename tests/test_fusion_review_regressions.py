"""Regression tests for independently reviewed fusion safety blockers."""

from __future__ import annotations

import itertools

import pytest

from docreconstruct.ir import (
    BBox,
    Document,
    Element,
    ElementStyle,
    ElementType,
    Page,
    Point,
    Provenance,
    Relationship,
)
from docreconstruct.normalization import fuse_element_evidence, fuse_pages
from docreconstruct.normalization.fusion_assignment import (
    maximum_cardinality_score_assignment,
    maximum_cardinality_score_sparse_assignment,
)
from docreconstruct.normalization.fusion_clustering import (
    _pair_match_score,
    logical_provider_set,
)
from docreconstruct.normalization.fusion_sources import document_source_identity


def _observation(
    engine: str | None,
    element_id: str,
    text: str | None,
    box: tuple[float, float, float, float],
    *,
    kind: ElementType = ElementType.TEXT,
    relationships: Relationship | None = None,
    provenance: Provenance | None = None,
) -> Element:
    resolved_provenance = provenance
    if engine is not None and provenance is None:
        resolved_provenance = Provenance(
            engine=engine,
            source_id=f"{engine}:{element_id}",
            text_confidence=0.9,
            layout_confidence=0.9,
        )
    return Element(
        id=element_id,
        type=kind,
        bbox=BBox.from_sequence(box),
        text=text,
        confidence=0.9,
        relationships=relationships or Relationship(),
        provenance=resolved_provenance,
    )


def _page(engine: str, elements: list[Element]) -> Page:
    return Page(
        id=f"{engine}-page",
        number=1,
        width=1000,
        height=1000,
        elements=elements,
    )


def test_tied_sort_keys_use_complete_content_and_preserve_all_fields() -> None:
    common = {
        "id": "duplicate-id",
        "type": ElementType.TEXT,
        "bbox": BBox(x0=0, y0=0, x1=100, y1=20),
        "text": "same",
        "confidence": 0.8,
        "provenance": Provenance(engine="same", source_id="same-source"),
    }
    left = Element(
        **common,
        polygon=[Point(x=1, y=1), Point(x=2, y=2)],
        z_index=8,
        source_crop=BBox(x0=1, y0=1, x1=11, y1=11),
        style=ElementStyle(font_family="Zed"),
        relationships=Relationship(parent="left-parent", references=["left-ref"]),
        metadata={"tie": "z", "left": True},
    )
    right = Element(
        **common,
        polygon=[Point(x=3, y=3), Point(x=4, y=4)],
        z_index=2,
        source_crop=BBox(x0=2, y0=2, x1=12, y1=12),
        style=ElementStyle(font_family="Alpha"),
        relationships=Relationship(parent="right-parent", references=["right-ref"]),
        metadata={"tie": "a", "right": True},
    )

    forward = fuse_element_evidence([left, right], element_id="stable")
    reverse = fuse_element_evidence([right, left], element_id="stable")

    assert forward.model_dump_json() == reverse.model_dump_json()
    assert forward.polygon
    assert forward.source_crop is not None
    assert forward.z_index == 2
    assert forward.style.font_family in {"Alpha", "Zed"}
    assert set(forward.relationships.references) == {"left-ref", "right-ref"}
    assert forward.metadata["left"] is True
    assert forward.metadata["right"] is True


def _nested_ensemble() -> Provenance:
    leaf_a = Provenance(engine="provider-a", source_id="a")
    inner = Provenance(
        engine="ensemble",
        source_id="inner",
        metadata={"inner": "retained"},
        contributors=[leaf_a],
    )
    return Provenance(
        engine="ensemble",
        source_id="top",
        metadata={"top": "retained"},
        contributors=[inner, Provenance(engine="provider-b", source_id="b")],
    )


def test_nested_ensemble_provider_sets_flatten_and_overlap_is_forbidden() -> None:
    nested = _nested_ensemble()
    assert logical_provider_set(nested, source_identity="source") == {
        "provider-a",
        "provider-b",
    }
    ensemble_page = _page(
        "ensemble",
        [_observation(None, "ensemble", "same", (0, 0, 100, 20), provenance=nested)],
    )
    raw_a_page = _page(
        "raw-a",
        [_observation("provider-a", "raw-a", "same", (0, 0, 100, 20))],
    )

    assert len(fuse_pages([ensemble_page, raw_a_page]).elements) == 2


def test_same_logical_provider_from_distinct_sources_cannot_self_correlate() -> None:
    first = _page(
        "first",
        [_observation("same-provider", "first", "same", (0, 0, 100, 20))],
    )
    second = _page(
        "second",
        [_observation("same-provider", "second", "same", (0, 0, 100, 20))],
    )

    assert len(fuse_pages([first, second]).elements) == 2


def test_top_level_ensemble_provenance_metadata_survives_valid_fusion() -> None:
    nested = _nested_ensemble()
    ensemble_page = _page(
        "ensemble",
        [_observation(None, "ensemble", "same", (0, 0, 100, 20), provenance=nested)],
    )
    raw_c_page = _page(
        "raw-c",
        [_observation("provider-c", "raw-c", "same", (0, 0, 100, 20))],
    )

    fused = fuse_pages([raw_c_page, ensemble_page]).elements[0]

    top = next(
        contributor
        for contributor in fused.provenance.contributors
        if contributor.source_id == "top"
    )
    assert top.metadata == {"top": "retained"}
    assert top.contributors[0].metadata == {"inner": "retained"}


def test_relationship_remap_is_namespaced_by_source_document() -> None:
    page_a = _page(
        "a",
        [
            _observation("a", "target", "target a", (0, 0, 100, 20)),
            _observation(
                "a",
                "child",
                "shared child",
                (0, 100, 100, 120),
                relationships=Relationship(references=["target"]),
            ),
        ],
    )
    page_b = _page(
        "b",
        [
            _observation("b", "target", "target b", (0, 50, 100, 70)),
            _observation(
                "b",
                "child",
                "shared child",
                (0, 100, 100, 120),
                relationships=Relationship(references=["target"]),
            ),
        ],
    )

    fused = fuse_pages([page_b, page_a])
    child = next(element for element in fused.elements if element.text == "shared child")
    target_ids = {element.id for element in fused.elements if element is not child}

    assert "target" not in child.relationships.references
    assert set(child.relationships.references) == target_ids


def test_unknown_missing_text_predicate_is_symmetric_against_text() -> None:
    unknown = _observation(
        "unknown-engine",
        "unknown",
        None,
        (0, 0, 100, 20),
        kind=ElementType.UNKNOWN,
    )
    text = _observation("text-engine", "text", "visible", (0, 0, 100, 20))

    assert (
        _pair_match_score(
            unknown,
            text,
            iou_threshold=0.5,
            text_similarity_threshold=0.75,
        )
        is None
    )
    assert (
        _pair_match_score(
            text,
            unknown,
            iou_threshold=0.5,
            text_similarity_threshold=0.75,
        )
        is None
    )


def test_sequence_similarity_score_is_symmetric_for_order_sensitive_inputs() -> None:
    left = _observation("left", "left", "tide", (0, 0, 100, 20))
    right = _observation("right", "right", "diet", (0, 0, 100, 20))

    forward = _pair_match_score(
        left,
        right,
        iou_threshold=0,
        text_similarity_threshold=0,
    )
    reverse = _pair_match_score(
        right,
        left,
        iou_threshold=0,
        text_similarity_threshold=0,
    )

    assert forward == reverse


def test_missing_provenance_identity_uses_document_but_not_page_id() -> None:
    def document(document_id: str, page_id: str) -> Document:
        return Document(
            id=document_id,
            pages=[
                Page(
                    id=page_id,
                    number=1,
                    width=100,
                    height=100,
                    elements=[_observation(None, "raw", "same", (0, 0, 80, 20))],
                )
            ],
        )

    assert document_source_identity(document("stable", "random-a")) == document_source_identity(
        document("stable", "random-b")
    )
    assert document_source_identity(document("first", "random")) != document_source_identity(
        document("second", "random")
    )


def test_assignment_maximizes_cardinality_before_score_and_is_deterministic() -> None:
    scores = [[0.90, 0.80], [0.85, None]]
    assert maximum_cardinality_score_assignment(scores) == [(0, 1), (1, 0)]
    assert maximum_cardinality_score_assignment([[0.5, 0.5], [0.5, 0.5]]) == [
        (0, 0),
        (1, 1),
    ]
    sparse, cells = maximum_cardinality_score_sparse_assignment(
        2,
        {(0, 0): 0.9, (0, 1): 0.8, (1, 0): 0.85},
        cell_budget=4,
    )
    assert sparse == [(0, 1), (1, 0)]
    assert cells == 4


def test_page_fusion_uses_cardinality_first_assignment_not_greedy_edges() -> None:
    anchor = _page(
        "anchor",
        [
            _observation("anchor", "a1", "same", (0, 0, 100, 20)),
            _observation("anchor", "a2", "same", (20, 0, 120, 20)),
        ],
    )
    challenger = _page(
        "challenger",
        [
            _observation("challenger", "b1", "same", (5, 0, 105, 20)),
            _observation("challenger", "b2", "same", (-10, 0, 90, 20)),
        ],
    )

    fused = fuse_pages([challenger, anchor], iou_threshold=0.7)

    assert len(fused.elements) == 2
    assert all(element.provenance.metadata["source_count"] == 2 for element in fused.elements)


@pytest.mark.parametrize(
    ("budgets", "reason"),
    [
        ({"candidate_budget": 2}, "candidate_budget"),
        ({"comparison_budget": 2}, "comparison_budget"),
        ({"assignment_budget": 2}, "assignment_budget"),
    ],
)
def test_work_budget_falls_back_to_deterministic_singletons(
    budgets: dict[str, int], reason: str
) -> None:
    pages = [
        _page(
            engine,
            [
                _observation(engine, f"{engine}-{index}", "same", (0, 0, 100, 20))
                for index in range(4)
            ],
        )
        for engine in ("a", "b")
    ]

    fused = fuse_pages(pages, **budgets)
    telemetry = fused.metadata["fusion"]["clustering"]

    assert len(fused.elements) == 8
    assert telemetry["budget_exhausted"] is True
    assert telemetry["fallback_reason"] == reason
    assert telemetry["over_split_elements"] == 4


def test_spatial_index_prunes_far_apart_candidates_before_matching() -> None:
    pages = []
    for engine in ("a", "b"):
        elements = [
            _observation(
                engine,
                f"{engine}-{index}",
                f"text-{index}",
                (index * 100, 0, index * 100 + 20, 20),
            )
            for index in range(64)
        ]
        pages.append(
            Page(
                id=f"{engine}-page",
                number=1,
                width=6400,
                height=100,
                elements=elements,
            )
        )

    fused = fuse_pages(pages)
    telemetry = fused.metadata["fusion"]["clustering"]

    assert len(fused.elements) == 64
    assert telemetry["budget_exhausted"] is False
    assert telemetry["spatially_pruned_pairs"] >= 3_000
    assert telemetry["candidate_pairs"] < 256
    assert telemetry["assignment_cells"] == 64


def test_budget_arguments_must_be_positive_integers() -> None:
    page = Page(id="page", number=1, width=100, height=100)
    for value in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="positive integer"):
            fuse_pages([page], candidate_budget=value)  # type: ignore[arg-type]


def test_cardinality_assignment_rejects_ragged_score_rows() -> None:
    with pytest.raises(ValueError, match="equal length"):
        maximum_cardinality_score_assignment([[0.5], [0.5, 0.4]])

    with pytest.raises(ValueError, match="finite"):
        maximum_cardinality_score_assignment([[float("nan")]])


def test_oversized_different_text_is_safely_split_without_quadratic_match() -> None:
    left = _page(
        "a",
        [_observation("a", "left", "x" * 3_000 + "a", (0, 0, 100, 20))],
    )
    right = _page(
        "b",
        [_observation("b", "right", "x" * 3_000 + "b", (0, 0, 100, 20))],
    )

    assert len(fuse_pages([left, right]).elements) == 2


def test_all_permutations_of_ambiguous_assignment_are_stable() -> None:
    pages = [
        _page("a", [_observation("a", "a", "same", (0, 0, 100, 20))]),
        _page("b", [_observation("b", "b", "same", (0, 0, 100, 20))]),
        _page("c", [_observation("c", "c", "same", (0, 0, 100, 20))]),
    ]
    expected = fuse_pages(pages).model_dump_json()
    for permutation in itertools.permutations(pages):
        assert fuse_pages(permutation).model_dump_json() == expected
