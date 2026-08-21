from __future__ import annotations

from docreconstruct import BBox, Document, Element, ElementType, Page
from docreconstruct.profiles import ReconstructionProfile, settings_for
from docreconstruct.reconstruction import (
    CriticResult,
    LayoutCorrection,
    apply_layout_corrections,
    build_plan,
    refine_document,
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
                        id="e1",
                        type=ElementType.PARAGRAPH,
                        bbox=BBox(x0=10, y0=10, x1=40, y1=30),
                        text="immutable source text",
                    )
                ],
            )
        ],
    )


def test_profile_aliases_and_plan_source_traceability() -> None:
    profile, settings = settings_for("visual")
    plan = build_plan(_document(), target="html", profile="semantic")

    assert profile is ReconstructionProfile.PIXEL_PERFECT
    assert settings.layout_strategy == "fixed"
    assert plan.layout_strategy == "flow"
    assert plan.elements[0].source_id == "e1"
    assert plan.elements[0].constraints["preserve_text"] is True


def test_layout_corrections_cannot_rewrite_text() -> None:
    original = _document()
    corrected = apply_layout_corrections(
        original,
        [LayoutCorrection(element_id="e1", page_number=1, reason="move left", dx=-5)],
    )

    assert original.pages[0].elements[0].bbox.x0 == 10
    assert corrected.pages[0].elements[0].bbox.x0 == 5
    assert corrected.pages[0].elements[0].text == "immutable source text"


def test_refinement_accepts_only_improving_passes() -> None:
    def critic(document: Document) -> CriticResult:
        x0 = document.pages[0].elements[0].bbox.x0
        if x0 == 5:
            return CriticResult(score=1.0)
        return CriticResult(
            score=0.5,
            corrections=[
                LayoutCorrection(
                    element_id="e1", page_number=1, reason="align with reference", dx=-5
                )
            ],
        )

    result = refine_document(_document(), critic, maximum_passes=3)

    assert result.initial_score == 0.5
    assert result.final_score == 1.0
    assert result.passes[0].accepted is True
    assert result.document.pages[0].elements[0].bbox.x0 == 5
    assert result.document.pages[0].elements[0].text == "immutable source text"


def _edge_document() -> Document:
    return Document(
        id="edge",
        pages=[
            Page(
                id="p1",
                number=1,
                width=600,
                height=800,
                elements=[
                    Element(
                        id="e1",
                        type=ElementType.TEXT,
                        bbox=BBox(x0=500, y0=700, x1=580, y1=740),
                        text="near the corner",
                    )
                ],
            )
        ],
    )


def test_a_move_that_reaches_a_page_edge_stays_a_move() -> None:
    """Clamping the corner and the far edge separately resized the element.

    An 80x40 box pushed 60pt right came back 40pt wide, and a large enough
    translation collapsed it to zero area, silently turning a reposition into a
    destructive resize.
    """

    for correction, expected in (
        (LayoutCorrection(element_id="e1", page_number=1, reason="right", dx=60), (520.0, 700.0)),
        (LayoutCorrection(element_id="e1", page_number=1, reason="down", dy=80), (500.0, 760.0)),
        (LayoutCorrection(element_id="e1", page_number=1, reason="far", dx=400), (520.0, 700.0)),
    ):
        box = apply_layout_corrections(_edge_document(), [correction]).pages[0].elements[0].bbox

        assert (box.width, box.height) == (80.0, 40.0)
        assert (box.x0, box.y0) == expected
        assert box.x1 <= 600.0 and box.y1 <= 800.0


def test_a_resize_that_collapses_the_box_is_rejected() -> None:
    import pytest

    for delta in (-80.0, -200.0):
        correction = LayoutCorrection(
            element_id="e1", page_number=1, reason="shrink", width_delta=delta
        )
        with pytest.raises(ValueError, match="collapses its box"):
            apply_layout_corrections(_edge_document(), [correction])


def test_growing_an_element_still_works() -> None:
    correction = LayoutCorrection(element_id="e1", page_number=1, reason="widen", width_delta=40)

    box = apply_layout_corrections(_edge_document(), [correction]).pages[0].elements[0].bbox

    assert box.width == 120.0
    assert box.x1 <= 600.0
