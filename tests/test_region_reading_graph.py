from __future__ import annotations

import itertools

import pytest
from pydantic import ValidationError

from docreconstruct.ir import BBox, Document, Element, ElementType, Page, Relationship
from docreconstruct.reconstruction.alignment.topology_builder import (
    build_document_reading_order_graphs,
    build_page_reading_order_graph,
)
from docreconstruct.reconstruction.alignment.topology_models import (
    PageRegion,
    PageRegionKind,
    ReadingOrderEdge,
    ReadingOrderGraph,
    ReadingOrderRelation,
)


def _element(
    element_id: str,
    box: tuple[float, float, float, float],
    *,
    kind: ElementType = ElementType.PARAGRAPH,
    order: int | None = None,
    text: str | None = None,
    relationships: Relationship | None = None,
    metadata: dict[str, object] | None = None,
) -> Element:
    return Element(
        id=element_id,
        type=kind,
        bbox=BBox.from_sequence(box),
        reading_order=order,
        text=text,
        relationships=relationships or Relationship(),
        metadata=metadata or {},
    )


def _page(elements: list[Element], *, page_id: str = "page-1", number: int = 1) -> Page:
    return Page(id=page_id, number=number, width=600, height=800, elements=elements)


def _regions(graph: ReadingOrderGraph, kind: PageRegionKind) -> list[PageRegion]:
    return [region for region in graph.regions if region.kind is kind]


def _has_edge(
    graph: ReadingOrderGraph,
    before: PageRegion,
    after: PageRegion,
    relation: ReadingOrderRelation | None = None,
) -> bool:
    return any(
        edge.before == before.id
        and edge.after == after.id
        and (relation is None or edge.relation is relation)
        for edge in graph.edges
    )


def test_two_columns_remain_parallel_in_the_reading_order_dag() -> None:
    page = _page(
        [
            _element("left-1", (50, 100, 270, 150), order=0),
            _element("right-1", (330, 100, 550, 150), order=2),
            _element("left-2", (50, 200, 270, 250), order=1),
            _element("right-2", (330, 200, 550, 250), order=3),
        ]
    )

    graph = build_page_reading_order_graph(page)

    columns = _regions(graph, PageRegionKind.COLUMN)
    assert graph.column_count == 2
    assert len(columns) == 2
    assert {region.column_index for region in columns} == {0, 1}
    assert graph.edges == ()
    assert set(graph.topological_layers()[0]) == {region.id for region in columns}


def test_three_column_clustering_is_deterministic() -> None:
    page = _page(
        [
            _element("c1-a", (30, 100, 170, 145)),
            _element("c2-a", (230, 100, 370, 145)),
            _element("c3-a", (430, 100, 570, 145)),
            _element("c1-b", (30, 210, 170, 255)),
            _element("c2-b", (230, 210, 370, 255)),
            _element("c3-b", (430, 210, 570, 255)),
        ]
    )

    graph = build_page_reading_order_graph(page)

    columns = _regions(graph, PageRegionKind.COLUMN)
    assert graph.column_count == 3
    assert {region.column_index for region in columns} == {0, 1, 2}
    assert [
        region.child_element_ids for region in sorted(columns, key=lambda item: item.column_index)
    ] == [
        ("c1-a", "c1-b"),
        ("c2-a", "c2-b"),
        ("c3-a", "c3-b"),
    ]


def test_spanning_heading_precedes_parallel_two_column_body() -> None:
    page = _page(
        [
            _element("heading", (40, 40, 560, 90), kind=ElementType.HEADING),
            _element("left-1", (50, 150, 270, 200)),
            _element("right-1", (330, 150, 550, 200)),
            _element("left-2", (50, 250, 270, 300)),
            _element("right-2", (330, 250, 550, 300)),
        ]
    )

    graph = build_page_reading_order_graph(page)

    spanning = next(
        region for region in _regions(graph, PageRegionKind.COLUMN) if region.is_spanning
    )
    body = [region for region in _regions(graph, PageRegionKind.COLUMN) if not region.is_spanning]
    assert graph.column_count == 2
    assert spanning.child_element_ids == ("heading",)
    assert len(body) == 2
    assert all(_has_edge(graph, spanning, region, ReadingOrderRelation.FLOW) for region in body)


def test_figure_between_paragraphs_splits_flow_and_owns_caption_relation() -> None:
    page = _page(
        [
            _element("paragraph-before", (80, 100, 520, 150)),
            _element("figure", (100, 190, 500, 290), kind=ElementType.FIGURE),
            _element(
                "caption",
                (120, 305, 480, 335),
                kind=ElementType.CAPTION,
                relationships=Relationship(caption_of="figure"),
            ),
            _element("paragraph-after", (80, 390, 520, 440)),
        ]
    )

    graph = build_page_reading_order_graph(page)

    columns = _regions(graph, PageRegionKind.COLUMN)
    before = graph.region_for_element("paragraph-before")
    after = graph.region_for_element("paragraph-after")
    figure = graph.region_for_element("figure")
    caption = graph.region_for_element("caption")
    assert len(columns) == 2
    assert _has_edge(graph, before, figure, ReadingOrderRelation.FLOW)
    assert _has_edge(graph, figure, caption, ReadingOrderRelation.CAPTION)
    assert _has_edge(graph, caption, after, ReadingOrderRelation.FLOW)


def test_full_width_table_is_a_barrier_between_parallel_column_segments() -> None:
    page = _page(
        [
            _element("left-before", (50, 100, 270, 150)),
            _element("right-before", (330, 100, 550, 150)),
            _element("table", (40, 280, 560, 400), kind=ElementType.TABLE),
            _element("left-after", (50, 500, 270, 550)),
            _element("right-after", (330, 500, 550, 550)),
        ]
    )

    graph = build_page_reading_order_graph(page)

    table = graph.region_for_element("table")
    before = [graph.region_for_element(name) for name in ("left-before", "right-before")]
    after = [graph.region_for_element(name) for name in ("left-after", "right-after")]
    assert graph.column_count == 2
    assert table.kind is PageRegionKind.TABLE
    assert all(_has_edge(graph, region, table) for region in before)
    assert all(_has_edge(graph, table, region) for region in after)


def test_footnote_is_explicit_and_ordered_after_body() -> None:
    page = _page(
        [
            _element("body-1", (60, 100, 540, 150)),
            _element("body-2", (60, 200, 540, 250)),
            _element("note", (60, 700, 540, 735), kind=ElementType.FOOTNOTE),
        ]
    )

    graph = build_page_reading_order_graph(page)

    body = graph.region_for_element("body-1")
    footnote = graph.region_for_element("note")
    assert footnote.kind is PageRegionKind.FOOTNOTE
    assert _has_edge(graph, body, footnote, ReadingOrderRelation.FOOTNOTE)


def test_floating_callout_is_not_forced_into_body_sequence() -> None:
    page = _page(
        [
            _element("body-1", (60, 100, 400, 150)),
            _element("body-2", (60, 300, 400, 350)),
            _element("callout", (430, 170, 570, 270), metadata={"layout_role": "callout"}),
        ]
    )

    graph = build_page_reading_order_graph(page)

    callout = graph.region_for_element("callout")
    assert callout.kind is PageRegionKind.FLOATING
    assert all(edge.before != callout.id and edge.after != callout.id for edge in graph.edges)


def test_header_footer_formula_and_typed_regions_are_explicit() -> None:
    page = _page(
        [
            _element("header", (40, 20, 560, 50), kind=ElementType.HEADER),
            _element("body", (60, 120, 540, 170)),
            _element("formula", (200, 220, 400, 270), kind=ElementType.FORMULA),
            _element("footer", (40, 750, 560, 780), kind=ElementType.FOOTER),
        ]
    )

    graph = build_page_reading_order_graph(page)

    header = graph.region_for_element("header")
    body = graph.region_for_element("body")
    formula = graph.region_for_element("formula")
    footer = graph.region_for_element("footer")
    assert header.kind is PageRegionKind.HEADER
    assert formula.kind is PageRegionKind.FORMULA
    assert footer.kind is PageRegionKind.FOOTER
    assert _has_edge(graph, header, body, ReadingOrderRelation.PAGE_BOUNDARY)
    assert _has_edge(graph, formula, footer, ReadingOrderRelation.PAGE_BOUNDARY)


def test_repeated_untyped_boundary_text_is_detected_at_document_scope() -> None:
    pages = [
        _page(
            [
                _element(f"top-{index}", (40, 20, 560, 45), text="Annual report"),
                _element(f"body-{index}", (60, 150, 540, 220), text=f"Body {index}"),
            ],
            page_id=f"page-{index}",
            number=index,
        )
        for index in (1, 2)
    ]
    document = Document(id="document", pages=list(reversed(pages)))

    graphs = build_document_reading_order_graphs(document)

    assert [graph.page_number for graph in graphs] == [1, 2]
    assert all(
        graph.region_for_element(f"top-{index}").kind is PageRegionKind.HEADER
        for index, graph in enumerate(graphs, 1)
    )


def test_element_permutation_cannot_change_regions_edges_or_fingerprint() -> None:
    elements = [
        _element("heading", (40, 40, 560, 90), kind=ElementType.HEADING),
        _element("left-1", (50, 150, 270, 200), order=0),
        _element("right-1", (330, 150, 550, 200), order=2),
        _element("left-2", (50, 250, 270, 300), order=1),
        _element("right-2", (330, 250, 550, 300), order=3),
    ]
    expected = build_page_reading_order_graph(_page(elements))

    for permutation in itertools.permutations(elements):
        actual = build_page_reading_order_graph(_page(list(permutation)))
        assert actual == expected
        assert actual.fingerprint == expected.fingerprint


def _manual_region(region_id: str, element_id: str, x0: float) -> PageRegion:
    return PageRegion(
        id=region_id,
        kind=PageRegionKind.COLUMN,
        bbox=BBox(x0=x0, y0=10, x1=x0 + 100, y1=100),
        child_element_ids=(element_id,),
        column_index=0,
        detection_source="fixture",
    )


def test_graph_validation_rejects_cycles() -> None:
    first = _manual_region("region-column-0000000000000001", "a", 10)
    second = _manual_region("region-column-0000000000000002", "b", 200)
    edges = (
        ReadingOrderEdge(before=first.id, after=second.id, confidence=1, source="fixture"),
        ReadingOrderEdge(before=second.id, after=first.id, confidence=1, source="fixture"),
    )

    with pytest.raises(ValidationError, match="acyclic"):
        ReadingOrderGraph(
            page_id="page",
            page_number=1,
            page_bbox=BBox(x0=0, y0=0, x1=600, y1=800),
            column_count=2,
            element_ids=("a", "b"),
            regions=(first, second),
            edges=edges,
        )


def test_graph_validation_rejects_duplicate_region_membership() -> None:
    first = _manual_region("region-column-0000000000000001", "same", 10)
    second = _manual_region("region-column-0000000000000002", "same", 200)

    with pytest.raises(ValidationError, match="more than one"):
        ReadingOrderGraph(
            page_id="page",
            page_number=1,
            page_bbox=BBox(x0=0, y0=0, x1=600, y1=800),
            column_count=2,
            element_ids=("same",),
            regions=(first, second),
        )


def test_fingerprint_rejects_mutated_graph_content() -> None:
    graph = build_page_reading_order_graph(_page([_element("body", (60, 100, 540, 150))]))
    payload = graph.model_dump(mode="json")
    payload["page_number"] = 2

    with pytest.raises(ValidationError, match="fingerprint"):
        ReadingOrderGraph.model_validate(payload)
