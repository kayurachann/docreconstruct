"""Build deterministic page-region and reading-order DAGs from canonical IR."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence

from docreconstruct.ir import BBox, Document, Element, ElementType, Page

from .topology_geometry import (
    cluster_page_columns,
    element_sort_key,
    horizontal_overlap_ratio,
    normalized_metadata_role,
    split_column_at_blockers,
    union_bbox,
    vertical_overlap_ratio,
)
from .topology_models import (
    PageRegion,
    PageRegionKind,
    ReadingOrderEdge,
    ReadingOrderGraph,
    ReadingOrderRelation,
    stable_topology_digest,
)

_FLOATING_ROLES = {"callout", "floating", "sidebar", "text_box", "textbox"}
_ROLE_KINDS = {
    "header": PageRegionKind.HEADER,
    "footer": PageRegionKind.FOOTER,
    "table": PageRegionKind.TABLE,
    "formula": PageRegionKind.FORMULA,
    "equation": PageRegionKind.FORMULA,
    "figure": PageRegionKind.FIGURE,
    "image": PageRegionKind.FIGURE,
    "chart": PageRegionKind.FIGURE,
    "caption": PageRegionKind.CAPTION,
    "footnote": PageRegionKind.FOOTNOTE,
}
_TYPE_KINDS = {
    ElementType.HEADER: PageRegionKind.HEADER,
    ElementType.FOOTER: PageRegionKind.FOOTER,
    ElementType.PAGE_NUMBER: PageRegionKind.FOOTER,
    ElementType.TABLE: PageRegionKind.TABLE,
    ElementType.FORMULA: PageRegionKind.FORMULA,
    ElementType.FIGURE: PageRegionKind.FIGURE,
    ElementType.IMAGE: PageRegionKind.FIGURE,
    ElementType.CHART: PageRegionKind.FIGURE,
    ElementType.CAPTION: PageRegionKind.CAPTION,
    ElementType.FOOTNOTE: PageRegionKind.FOOTNOTE,
}
_RELATION_PRIORITY = {
    ReadingOrderRelation.FLOW: 0,
    ReadingOrderRelation.PAGE_BOUNDARY: 1,
    ReadingOrderRelation.FOOTNOTE: 2,
    ReadingOrderRelation.CAPTION: 3,
}


def _make_region_id(
    page: Page,
    kind: PageRegionKind,
    elements: Sequence[Element],
    *,
    column_index: int | None = None,
    is_spanning: bool = False,
) -> str:
    identity = stable_topology_digest(
        {
            "page_id": page.id,
            "page_number": page.number,
            "kind": kind.value,
            "child_element_ids": sorted(item.id for item in elements),
            "column_index": column_index,
            "is_spanning": is_spanning,
        }
    )
    return f"region-{kind.value}-{identity[:16]}"


def _make_region(
    page: Page,
    kind: PageRegionKind,
    elements: Sequence[Element],
    *,
    detection_source: str,
    column_index: int | None = None,
    is_spanning: bool = False,
) -> PageRegion:
    ordered = tuple(elements)
    return PageRegion(
        id=_make_region_id(
            page,
            kind,
            ordered,
            column_index=column_index,
            is_spanning=is_spanning,
        ),
        kind=kind,
        bbox=union_bbox(ordered),
        child_element_ids=tuple(item.id for item in ordered),
        column_index=column_index,
        is_spanning=is_spanning,
        detection_source=detection_source,
    )


def _explicit_kind(
    page: Page,
    element: Element,
    boundary_roles: Mapping[tuple[str, str], PageRegionKind],
) -> PageRegionKind | None:
    inferred = boundary_roles.get((page.id, element.id))
    if inferred is not None:
        return inferred
    role = normalized_metadata_role(element)
    if role in _FLOATING_ROLES:
        return PageRegionKind.FLOATING
    if role in _ROLE_KINDS:
        return _ROLE_KINDS[role]
    return _TYPE_KINDS.get(element.type)


def _special_regions(
    page: Page,
    elements: Sequence[Element],
    boundary_roles: Mapping[tuple[str, str], PageRegionKind],
) -> tuple[list[PageRegion], tuple[Element, ...]]:
    grouped: dict[PageRegionKind, list[Element]] = {
        PageRegionKind.HEADER: [],
        PageRegionKind.FOOTER: [],
        PageRegionKind.FOOTNOTE: [],
    }
    individual: list[tuple[PageRegionKind, Element]] = []
    flow: list[Element] = []
    for element in sorted(elements, key=element_sort_key):
        kind = _explicit_kind(page, element, boundary_roles)
        if kind is None:
            flow.append(element)
        elif kind in grouped:
            grouped[kind].append(element)
        else:
            individual.append((kind, element))
    regions: list[PageRegion] = []
    for kind, members in grouped.items():
        if members:
            regions.append(
                _make_region(
                    page,
                    kind,
                    tuple(sorted(members, key=element_sort_key)),
                    detection_source="canonical.type_or_boundary_role",
                )
            )
    for kind, element in individual:
        role = normalized_metadata_role(element)
        source = "canonical.metadata_role" if role else "canonical.element_type"
        regions.append(_make_region(page, kind, (element,), detection_source=source))
    return regions, tuple(flow)


def _column_regions(
    page: Page, flow: Sequence[Element], special_regions: Sequence[PageRegion]
) -> tuple[list[PageRegion], int]:
    if not flow:
        return [], 1
    clustering = cluster_page_columns(flow, page)
    source = "geometry+reading_hints" if clustering.used_reading_hints else "geometry"
    spanning = [
        _make_region(
            page,
            PageRegionKind.COLUMN,
            (element,),
            detection_source=f"{source}.spanning",
            is_spanning=True,
        )
        for element in clustering.spanning
    ]
    blockers = [
        *(
            region
            for region in special_regions
            if region.kind
            in {
                PageRegionKind.TABLE,
                PageRegionKind.FORMULA,
                PageRegionKind.FIGURE,
                PageRegionKind.CAPTION,
            }
        ),
        *spanning,
    ]
    regions = list(spanning)
    for column_index, group in enumerate(clustering.groups):
        for segment in split_column_at_blockers(group, blockers):
            regions.append(
                _make_region(
                    page,
                    PageRegionKind.COLUMN,
                    segment,
                    detection_source=f"{source}.column_cluster",
                    column_index=column_index,
                )
            )
    return regions, max(1, clustering.column_count)


def _caption_target(
    caption: PageRegion,
    by_element: Mapping[str, Element],
    region_by_element: Mapping[str, PageRegion],
    figure_regions: Sequence[PageRegion],
) -> tuple[PageRegion, float, str] | None:
    element = by_element[caption.child_element_ids[0]]
    target_id = element.relationships.caption_of
    if target_id is not None:
        target = region_by_element.get(target_id)
        if target is not None and target.kind is PageRegionKind.FIGURE:
            return target, 1.0, "canonical.relationship.caption_of"
    for figure in figure_regions:
        figure_element = by_element[figure.child_element_ids[0]]
        if element.id in figure_element.relationships.children:
            return figure, 1.0, "canonical.relationship.children"
    compatible = [
        figure
        for figure in figure_regions
        if horizontal_overlap_ratio(caption.bbox, figure.bbox) >= 0.25
    ]
    if not compatible:
        return None
    nearest = min(
        compatible,
        key=lambda figure: (
            abs(caption.bbox.center_y - figure.bbox.center_y),
            abs(caption.bbox.center_x - figure.bbox.center_x),
            figure.id,
        ),
    )
    return nearest, 0.75, "geometry.nearest_figure_caption"


def _path_exists(adjacency: Mapping[str, set[str]], start: str, target: str) -> bool:
    pending = [start]
    seen: set[str] = set()
    while pending:
        node = pending.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        pending.extend(sorted(adjacency.get(node, ()), reverse=True))
    return False


def _store_edge(
    edges: dict[tuple[str, str], ReadingOrderEdge],
    adjacency: dict[str, set[str]],
    edge: ReadingOrderEdge,
) -> None:
    pair = (edge.before, edge.after)
    existing = edges.get(pair)
    if existing is not None:
        existing_rank = (
            _RELATION_PRIORITY[existing.relation],
            existing.confidence,
            existing.source,
        )
        candidate_rank = (_RELATION_PRIORITY[edge.relation], edge.confidence, edge.source)
        if candidate_rank > existing_rank:
            edges[pair] = edge
        return
    if _path_exists(adjacency, edge.after, edge.before):
        return
    edges[pair] = edge
    adjacency[edge.before].add(edge.after)


def _semantic_edges(
    regions: Sequence[PageRegion], by_element: Mapping[str, Element]
) -> tuple[dict[tuple[str, str], ReadingOrderEdge], dict[str, set[str]], set[frozenset[str]]]:
    edges: dict[tuple[str, str], ReadingOrderEdge] = {}
    adjacency = {region.id: set[str]() for region in regions}
    headers = [region for region in regions if region.kind is PageRegionKind.HEADER]
    footers = [region for region in regions if region.kind is PageRegionKind.FOOTER]
    footnotes = [region for region in regions if region.kind is PageRegionKind.FOOTNOTE]
    for header in headers:
        for region in regions:
            if region.id != header.id and region.kind is not PageRegionKind.HEADER:
                _store_edge(
                    edges,
                    adjacency,
                    ReadingOrderEdge(
                        before=header.id,
                        after=region.id,
                        confidence=1.0,
                        source="semantic.page_header",
                        relation=ReadingOrderRelation.PAGE_BOUNDARY,
                    ),
                )
    for footer in footers:
        for region in regions:
            if region.id != footer.id and region.kind is not PageRegionKind.FOOTER:
                _store_edge(
                    edges,
                    adjacency,
                    ReadingOrderEdge(
                        before=region.id,
                        after=footer.id,
                        confidence=1.0,
                        source="semantic.page_footer",
                        relation=ReadingOrderRelation.PAGE_BOUNDARY,
                    ),
                )
    for footnote in footnotes:
        for region in regions:
            if region.kind not in {
                PageRegionKind.HEADER,
                PageRegionKind.FOOTER,
                PageRegionKind.FOOTNOTE,
            }:
                _store_edge(
                    edges,
                    adjacency,
                    ReadingOrderEdge(
                        before=region.id,
                        after=footnote.id,
                        confidence=0.95,
                        source="semantic.page_footnote",
                        relation=ReadingOrderRelation.FOOTNOTE,
                    ),
                )
    region_by_element = {
        element_id: region for region in regions for element_id in region.child_element_ids
    }
    figure_regions = [region for region in regions if region.kind is PageRegionKind.FIGURE]
    caption_pairs: set[frozenset[str]] = set()
    for caption in (region for region in regions if region.kind is PageRegionKind.CAPTION):
        target = _caption_target(caption, by_element, region_by_element, figure_regions)
        if target is None:
            continue
        figure, confidence, source = target
        caption_pairs.add(frozenset((figure.id, caption.id)))
        _store_edge(
            edges,
            adjacency,
            ReadingOrderEdge(
                before=figure.id,
                after=caption.id,
                confidence=confidence,
                source=source,
                relation=ReadingOrderRelation.CAPTION,
            ),
        )
    return edges, adjacency, caption_pairs


def _geometry_edges(
    regions: Sequence[PageRegion],
    edges: dict[tuple[str, str], ReadingOrderEdge],
    adjacency: dict[str, set[str]],
    caption_pairs: set[frozenset[str]],
) -> None:
    excluded = {
        PageRegionKind.HEADER,
        PageRegionKind.FOOTER,
        PageRegionKind.FOOTNOTE,
        PageRegionKind.FLOATING,
    }
    candidates: list[tuple[float, float, str, str, float]] = []
    for index, left in enumerate(regions):
        for right in regions[index + 1 :]:
            if left.kind in excluded or right.kind in excluded:
                continue
            if frozenset((left.id, right.id)) in caption_pairs:
                continue
            if horizontal_overlap_ratio(left.bbox, right.bbox) < 0.20:
                continue
            top, bottom = sorted((left, right), key=lambda item: (item.bbox.center_y, item.id))
            overlap = vertical_overlap_ratio(top.bbox, bottom.bbox)
            if top.bbox.y1 > bottom.bbox.y0 and overlap >= 0.20:
                continue
            gap = max(0.0, bottom.bbox.y0 - top.bbox.y1)
            scale = max(top.bbox.height, bottom.bbox.height, 1.0)
            confidence = max(0.60, min(0.95, 0.80 + 0.15 * min(1.0, gap / scale)))
            candidates.append(
                (top.bbox.center_y, bottom.bbox.center_y, top.id, bottom.id, confidence)
            )
    for _, _, before, after, confidence in sorted(candidates):
        _store_edge(
            edges,
            adjacency,
            ReadingOrderEdge(
                before=before,
                after=after,
                confidence=confidence,
                source="geometry.vertical_precedence",
                relation=ReadingOrderRelation.FLOW,
            ),
        )


def build_page_reading_order_graph(
    page: Page,
    *,
    boundary_roles: Mapping[tuple[str, str], PageRegionKind] | None = None,
) -> ReadingOrderGraph:
    """Partition one page and build an acyclic, deterministic region graph."""

    roles = boundary_roles or {}
    special, flow = _special_regions(page, page.elements, roles)
    columns, column_count = _column_regions(page, flow, special)
    regions = tuple(sorted((*special, *columns), key=lambda region: region.id))
    by_element = {element.id: element for element in page.elements}
    edges, adjacency, caption_pairs = _semantic_edges(regions, by_element)
    _geometry_edges(regions, edges, adjacency, caption_pairs)
    canonical_edges = tuple(
        sorted(
            edges.values(),
            key=lambda edge: (edge.before, edge.after, edge.relation.value, edge.source),
        )
    )
    return ReadingOrderGraph(
        page_id=page.id,
        page_number=page.number,
        page_bbox=BBox(x0=0.0, y0=0.0, x1=page.width, y1=page.height),
        column_count=column_count,
        element_ids=tuple(sorted(by_element)),
        regions=regions,
        edges=canonical_edges,
    )


def _text_signature(element: Element) -> str | None:
    if element.text is None:
        return None
    normalized = re.sub(r"\s+", " ", element.text).strip().casefold()
    return normalized or None


def _infer_repeated_boundary_roles(document: Document) -> dict[tuple[str, str], PageRegionKind]:
    if len(document.pages) < 2:
        return {}
    observations: list[tuple[str, str, str, PageRegionKind]] = []
    counts: Counter[tuple[str, PageRegionKind]] = Counter()
    for page in document.pages:
        seen_on_page: set[tuple[str, PageRegionKind]] = set()
        for element in page.elements:
            if element.type not in {
                ElementType.TEXT,
                ElementType.PARAGRAPH,
                ElementType.UNKNOWN,
            }:
                continue
            signature = _text_signature(element)
            if signature is None:
                continue
            relative_y = element.bbox.center_y / page.height
            if relative_y <= 0.10:
                kind = PageRegionKind.HEADER
            elif relative_y >= 0.90:
                kind = PageRegionKind.FOOTER
            else:
                continue
            observations.append((page.id, element.id, signature, kind))
            key = (signature, kind)
            if key not in seen_on_page:
                counts[key] += 1
                seen_on_page.add(key)
    required = max(2, math.ceil(len(document.pages) / 2))
    return {
        (page_id, element_id): kind
        for page_id, element_id, signature, kind in observations
        if counts[(signature, kind)] >= required
    }


def build_document_reading_order_graphs(document: Document) -> tuple[ReadingOrderGraph, ...]:
    """Build page graphs, using repeated boundary evidence across the document."""

    roles = _infer_repeated_boundary_roles(document)
    pages = sorted(document.pages, key=lambda page: (page.number, page.id))
    return tuple(build_page_reading_order_graph(page, boundary_roles=roles) for page in pages)


__all__ = ["build_document_reading_order_graphs", "build_page_reading_order_graph"]
