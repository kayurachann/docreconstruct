"""Adversarial and metamorphic contracts for cross-provider evidence fusion."""

from __future__ import annotations

import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st

from docreconstruct.ir import (
    BBox,
    Document,
    Element,
    ElementStyle,
    ElementType,
    Page,
    Provenance,
    Relationship,
    TextCandidate,
)
from docreconstruct.normalization import fuse_documents, fuse_element_evidence, fuse_pages


def _element(
    engine: str,
    element_id: str,
    text: str | None,
    box: tuple[float, float, float, float],
    *,
    kind: ElementType = ElementType.TEXT,
    confidence: float = 0.9,
    reading_order: int | None = None,
) -> Element:
    return Element(
        id=element_id,
        type=kind,
        bbox=BBox.from_sequence(box),
        text=text,
        reading_order=reading_order,
        confidence=confidence,
        style=ElementStyle(font_family=f"{engine} Sans", font_size=10 + confidence),
        relationships=Relationship(references=[f"ref-{engine}"], metadata={"engine": engine}),
        provenance=Provenance(
            engine=engine,
            source_id=f"source-{element_id}",
            text_confidence=confidence,
            layout_confidence=confidence,
            metadata={"engine": engine},
        ),
        text_candidates=[
            TextCandidate(
                engine=engine,
                value=text or "",
                confidence=confidence,
                source_element_id=element_id,
            )
        ],
        metadata={"provider": engine},
    )


def _document(engine: str, *, reverse_elements: bool = False) -> Document:
    elements = [
        _element(
            engine,
            f"{engine}-title",
            "Quarterly report",
            (10, 10, 90, 24),
            kind=ElementType.TITLE,
            confidence={"paddleocr": 0.95, "mineru": 0.92, "olmocr": 0.89}[engine],
            reading_order=0,
        ),
        _element(
            engine,
            f"{engine}-body",
            "Revenue increased",
            (10, 40, 90, 55),
            confidence={"paddleocr": 0.91, "mineru": 0.94, "olmocr": 0.88}[engine],
            reading_order=1,
        ),
    ]
    if reverse_elements:
        elements.reverse()
    return Document(
        id=f"{engine}-document",
        pages=[
            Page(
                id=f"{engine}-page",
                number=1,
                width=100,
                height=200,
                elements=elements,
                metadata={"provider": engine},
            )
        ],
        metadata={"provider": engine},
    )


def _json(document: Document) -> str:
    return document.model_dump_json()


def test_element_reduction_is_invariant_to_source_permutation() -> None:
    elements = [
        _element("paddleocr", "paddle", "same text", (0, 0, 100, 20), confidence=0.8),
        _element("mineru", "miner", "same text", (1, 0, 101, 20), confidence=0.8),
        _element("olmocr", "olm", "same text", (2, 0, 102, 20), confidence=0.8),
    ]
    expected = fuse_element_evidence(elements, element_id="stable").model_dump_json()

    for permutation in itertools.permutations(elements):
        assert fuse_element_evidence(permutation, element_id="stable").model_dump_json() == expected


@given(
    provider_order=st.permutations(["paddleocr", "mineru", "olmocr"]),
    reverse_flags=st.tuples(st.booleans(), st.booleans(), st.booleans()),
)
def test_document_fusion_is_invariant_to_provider_and_element_permutations(
    provider_order: list[str],
    reverse_flags: tuple[bool, bool, bool],
) -> None:
    baseline = fuse_documents(
        [_document("paddleocr"), _document("mineru"), _document("olmocr")],
        document_id="ensemble",
    )
    flags = dict(zip(("paddleocr", "mineru", "olmocr"), reverse_flags, strict=True))
    permuted = fuse_documents(
        [_document(engine, reverse_elements=flags[engine]) for engine in provider_order],
        document_id="ensemble",
    )

    assert _json(permuted) == _json(baseline)


def test_duplicate_elements_from_one_provider_never_share_a_cluster() -> None:
    paddle_page = Page(
        id="paddle-page",
        number=1,
        width=120,
        height=200,
        elements=[
            _element("paddleocr", "duplicate-a", "shared", (0, 0, 100, 20)),
            _element("paddleocr", "duplicate-b", "shared", (0, 0, 100, 20)),
        ],
    )
    mineru_page = Page(
        id="mineru-page",
        number=1,
        width=120,
        height=200,
        elements=[_element("mineru", "counterpart", "shared", (0, 0, 100, 20))],
    )

    fused = fuse_pages([paddle_page, mineru_page])

    assert len(fused.elements) == 2
    for element in fused.elements:
        contributors = element.provenance.contributors if element.provenance else []
        engines = [contributor.engine for contributor in contributors]
        assert engines.count("paddleocr") <= 1
        assert engines.count("mineru") <= 1


def test_complete_link_constraint_rejects_chain_only_merge() -> None:
    pages = [
        Page(
            id=f"{engine}-page",
            number=1,
            width=160,
            height=100,
            elements=[_element(engine, engine, "chain", box)],
        )
        for engine, box in (
            ("a", (0, 0, 100, 20)),
            ("b", (20, 0, 120, 20)),
            ("c", (40, 0, 140, 20)),
        )
    ]

    fused = fuse_pages(pages, iou_threshold=0.5)

    assert len(fused.elements) == 2
    contributor_sets = [
        {contributor.engine for contributor in element.provenance.contributors}
        for element in fused.elements
        if element.provenance is not None
    ]
    assert {"a", "c"} not in contributor_sets
    assert all(len(contributors) <= 2 for contributors in contributor_sets)


def test_type_incompatibility_prevents_geometric_merge() -> None:
    pages = [
        Page(
            id="table-page",
            number=1,
            width=100,
            height=100,
            elements=[
                _element(
                    "table-engine",
                    "table",
                    None,
                    (0, 0, 80, 80),
                    kind=ElementType.TABLE,
                )
            ],
        ),
        Page(
            id="formula-page",
            number=1,
            width=100,
            height=100,
            elements=[
                _element(
                    "formula-engine",
                    "formula",
                    None,
                    (0, 0, 80, 80),
                    kind=ElementType.FORMULA,
                )
            ],
        ),
    ]

    assert len(fuse_pages(pages).elements) == 2


def test_pages_with_different_numbers_are_not_fused() -> None:
    pages = [
        Page(id="one", number=1, width=100, height=100),
        Page(id="two", number=2, width=100, height=100),
    ]

    with pytest.raises(ValueError, match="same page number"):
        fuse_pages(pages)
