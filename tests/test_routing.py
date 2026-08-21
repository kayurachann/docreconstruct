from __future__ import annotations

from docreconstruct import (
    BBox,
    Document,
    Element,
    ElementType,
    Page,
    Provenance,
    SourceType,
    TextCandidate,
)
from docreconstruct.routing import RoutingAction, RoutingReason, build_routing_plan


def test_scanned_page_routes_once_to_fast_primary_with_fallbacks() -> None:
    document = Document(
        id="scan",
        pages=[
            Page(
                id="p1",
                number=1,
                width=100,
                height=200,
                source_type=SourceType.IMAGE,
                elements=[
                    Element(
                        id="image",
                        type=ElementType.IMAGE,
                        bbox=BBox(x0=0, y0=0, x1=100, y1=200),
                    )
                ],
            )
        ],
    )

    plan = build_routing_plan(document)

    assert len(plan.tasks) == 1
    assert plan.tasks[0].primary_provider == "paddleocr"
    assert plan.tasks[0].fallback_providers == ["olmocr", "mineru"]
    assert plan.tasks[0].reasons == [RoutingReason.INITIAL_EXTRACTION]
    assert plan.tasks[0].require_consensus is False


def test_low_confidence_text_uses_secondary_and_consensus() -> None:
    element = Element(
        id="amount",
        type=ElementType.TEXT,
        bbox=BBox(x0=10, y0=10, x1=70, y1=20),
        text="$12,804,92I",
        confidence=0.61,
        provenance=Provenance(engine="paddleocr", text_confidence=0.61),
    )
    document = Document(
        id="doc",
        pages=[Page(id="p1", number=1, width=100, height=100, elements=[element])],
    )

    task = build_routing_plan(document).tasks[0]

    assert task.action is RoutingAction.RETRY
    assert task.primary_provider == "olmocr"
    assert task.require_consensus is True
    assert RoutingReason.LOW_CONFIDENCE in task.reasons


def test_disagreement_routes_only_the_questionable_region() -> None:
    stable = Element(
        id="stable",
        type=ElementType.TEXT,
        bbox=BBox(x0=0, y0=0, x1=50, y1=10),
        text="Stable",
        confidence=0.99,
    )
    disputed = Element(
        id="disputed",
        type=ElementType.TEXT,
        bbox=BBox(x0=0, y0=20, x1=80, y1=30),
        text="$12,804,921",
        confidence=0.96,
        text_candidates=[
            TextCandidate(engine="paddleocr", value="$12,804,921", confidence=0.96),
            TextCandidate(engine="mineru", value="$12,804,92I", confidence=0.81),
        ],
    )
    document = Document(
        id="doc",
        pages=[Page(id="p1", number=1, width=100, height=100, elements=[stable, disputed])],
    )

    plan = build_routing_plan(document)

    assert [task.element_id for task in plan.tasks] == ["disputed"]
    assert plan.tasks[0].action is RoutingAction.ADJUDICATE
    assert RoutingReason.PROVIDER_DISAGREEMENT in plan.tasks[0].reasons


def test_specialists_are_selected_by_region_type() -> None:
    table = Element(
        id="table",
        type=ElementType.TABLE,
        bbox=BBox(x0=0, y0=0, x1=100, y1=70),
        confidence=0.9,
    )
    handwriting = Element(
        id="hand",
        type=ElementType.TEXT,
        bbox=BBox(x0=0, y0=75, x1=100, y1=90),
        confidence=0.9,
        metadata={"handwriting": True},
    )
    document = Document(
        id="doc",
        pages=[Page(id="p1", number=1, width=100, height=100, elements=[table, handwriting])],
    )

    tasks = {task.element_id: task for task in build_routing_plan(document).tasks}

    assert tasks["table"].primary_provider == "paddleocr"
    assert tasks["hand"].primary_provider == "olmocr"


def test_already_recognized_scanned_page_still_routes_its_elements() -> None:
    """An OCR'd page must not swallow element-level work.

    ``_initial_page_task`` returned a whole-page extraction for every raster
    page, and ``plan`` then skipped the page's elements, so a scanned page that
    already carried recognized text produced no retry tasks and ignored
    ``--force-elements`` entirely.
    """

    document = Document(
        id="scan",
        pages=[
            Page(
                id="p1",
                number=1,
                width=100,
                height=200,
                source_type=SourceType.SCANNED,
                elements=[
                    Element(
                        id="amount",
                        type=ElementType.TEXT,
                        bbox=BBox(x0=10, y0=10, x1=70, y1=20),
                        text="$12,804,92I",
                        confidence=0.61,
                        provenance=Provenance(engine="paddleocr", text_confidence=0.61),
                    ),
                    Element(
                        id="tbl",
                        type=ElementType.TABLE,
                        bbox=BBox(x0=10, y0=40, x1=90, y1=120),
                        text="a b",
                        confidence=0.97,
                        provenance=Provenance(engine="paddleocr", text_confidence=0.97),
                    ),
                ],
            )
        ],
    )

    plan = build_routing_plan(document, force_element_ids=["tbl"])
    by_element = {task.element_id: task for task in plan.tasks}

    assert "amount" in by_element
    assert by_element["amount"].action is RoutingAction.RETRY
    assert RoutingReason.LOW_CONFIDENCE in by_element["amount"].reasons
    assert "tbl" in by_element
    assert RoutingReason.FORCED_REPAIR in by_element["tbl"].reasons
    # The page keeps its single whole-page extraction only when it has no text.
    assert not any(task.id.endswith("-ocr") for task in plan.tasks)
