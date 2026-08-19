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
