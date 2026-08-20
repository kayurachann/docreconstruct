"""Immutable, renderer-neutral page-region and reading-order graph models."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from docreconstruct.ir import BBox

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def stable_topology_digest(value: Any) -> str:
    """Hash JSON data with a stable encoding shared by every topology object."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PageRegionKind(StrEnum):
    """Semantic or geometric partition owned by exactly one page."""

    HEADER = "header"
    FOOTER = "footer"
    COLUMN = "column"
    TABLE = "table"
    FORMULA = "formula"
    FIGURE = "figure"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    FLOATING = "floating"


class ReadingOrderRelation(StrEnum):
    """Why one region must precede another region in the page DAG."""

    FLOW = "flow"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    PAGE_BOUNDARY = "page_boundary"


class PageRegion(_FrozenModel):
    """One disjoint page partition with deterministic member reading order."""

    id: str = Field(pattern=r"^region-[a-z_]+-[0-9a-f]{16}$")
    kind: PageRegionKind
    bbox: BBox
    child_element_ids: tuple[str, ...] = Field(min_length=1)
    column_index: int | None = Field(default=None, ge=0, le=2)
    is_spanning: bool = False
    detection_source: str = Field(min_length=1)

    @field_validator("child_element_ids")
    @classmethod
    def child_ids_must_be_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("region child element IDs must not be blank")
        if len(value) != len(set(value)):
            raise ValueError("region child element IDs must be unique")
        return value

    @field_validator("detection_source")
    @classmethod
    def source_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("region detection source must not be blank")
        return value

    @model_validator(mode="after")
    def validate_column_fields(self) -> Self:
        if self.bbox.width <= 0 or self.bbox.height <= 0:
            raise ValueError("page regions must have positive-area bounding boxes")
        if self.kind is PageRegionKind.COLUMN:
            if self.is_spanning and self.column_index is not None:
                raise ValueError("a spanning column region cannot belong to one column")
        elif self.column_index is not None or self.is_spanning:
            raise ValueError("only column regions may declare column topology")
        return self


class ReadingOrderEdge(_FrozenModel):
    """A directed, evidence-labelled precedence constraint between two regions."""

    before: str = Field(pattern=r"^region-[a-z_]+-[0-9a-f]{16}$")
    after: str = Field(pattern=r"^region-[a-z_]+-[0-9a-f]{16}$")
    confidence: float = Field(ge=0.0, le=1.0)
    source: str = Field(min_length=1)
    relation: ReadingOrderRelation = ReadingOrderRelation.FLOW

    @field_validator("source")
    @classmethod
    def source_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reading-order edge source must not be blank")
        return value

    @model_validator(mode="after")
    def reject_self_loops(self) -> Self:
        if self.before == self.after:
            raise ValueError("reading-order edges cannot be self-loops")
        return self


def _region_sort_key(region: PageRegion) -> tuple[float, float, int, str, str]:
    column = -1 if region.column_index is None else region.column_index
    return (region.bbox.y0, region.bbox.x0, column, region.kind.value, region.id)


class ReadingOrderGraph(_FrozenModel):
    """A complete, disjoint page partition plus an acyclic precedence graph."""

    schema_version: Literal["1.0"] = "1.0"
    page_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    page_bbox: BBox
    column_count: int = Field(ge=1, le=3)
    element_ids: tuple[str, ...]
    regions: tuple[PageRegion, ...]
    edges: tuple[ReadingOrderEdge, ...] = ()
    fingerprint: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")

    @field_validator("page_id")
    @classmethod
    def page_id_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("graph page ID must not be blank")
        return value

    @field_validator("element_ids")
    @classmethod
    def element_ids_must_be_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("graph element IDs must not be blank")
        if value != tuple(sorted(set(value))):
            raise ValueError("graph element IDs must be unique and lexically ordered")
        return value

    @field_validator("regions")
    @classmethod
    def regions_must_be_canonical(cls, value: tuple[PageRegion, ...]) -> tuple[PageRegion, ...]:
        ordered = tuple(sorted(value, key=lambda region: region.id))
        if value != ordered:
            raise ValueError("graph regions must use canonical ID order")
        return value

    @field_validator("edges")
    @classmethod
    def edges_must_be_canonical(
        cls, value: tuple[ReadingOrderEdge, ...]
    ) -> tuple[ReadingOrderEdge, ...]:
        def edge_key(edge: ReadingOrderEdge) -> tuple[str, str, str, str]:
            return (edge.before, edge.after, edge.relation.value, edge.source)

        ordered = tuple(sorted(value, key=edge_key))
        if value != ordered:
            raise ValueError("graph edges must use canonical order")
        return value

    @model_validator(mode="after")
    def validate_partition_and_dag(self) -> Self:
        if self.page_bbox.x0 != 0 or self.page_bbox.y0 != 0:
            raise ValueError("graph page bounding box must start at the origin")
        if self.page_bbox.width <= 0 or self.page_bbox.height <= 0:
            raise ValueError("graph page bounding box must have positive area")
        region_ids = tuple(region.id for region in self.regions)
        if len(region_ids) != len(set(region_ids)):
            raise ValueError("page region IDs must be unique")
        members = [item for region in self.regions for item in region.child_element_ids]
        if len(members) != len(set(members)):
            raise ValueError("an element cannot belong to more than one page region")
        if set(members) != set(self.element_ids):
            raise ValueError("every page element must belong to exactly one page region")
        nodes = set(region_ids)
        pairs: set[tuple[str, str]] = set()
        adjacency = {node: set[str]() for node in nodes}
        indegree = dict.fromkeys(nodes, 0)
        for edge in self.edges:
            if edge.before not in nodes or edge.after not in nodes:
                raise ValueError("reading-order edge endpoint is not a page region")
            pair = (edge.before, edge.after)
            if pair in pairs:
                raise ValueError("reading-order graph cannot contain duplicate edges")
            pairs.add(pair)
            adjacency[edge.before].add(edge.after)
            indegree[edge.after] += 1
        ready = sorted(node for node, degree in indegree.items() if degree == 0)
        visited = 0
        while ready:
            node = ready.pop(0)
            visited += 1
            for successor in sorted(adjacency[node]):
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    ready.append(successor)
                    ready.sort()
        if visited != len(nodes):
            raise ValueError("reading-order graph must be acyclic")
        expected = stable_topology_digest(self.model_dump(mode="json", exclude={"fingerprint"}))
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("reading-order graph fingerprint does not match its content")
        object.__setattr__(self, "fingerprint", expected)
        return self

    def region_for_element(self, element_id: str) -> PageRegion:
        """Return the unique region owning ``element_id``."""

        for region in self.regions:
            if element_id in region.child_element_ids:
                return region
        raise KeyError(element_id)

    def topological_layers(self) -> tuple[tuple[str, ...], ...]:
        """Return stable parallel layers without flattening independent columns."""

        by_id = {region.id: region for region in self.regions}
        adjacency = {region.id: set[str]() for region in self.regions}
        indegree = {region.id: 0 for region in self.regions}
        for edge in self.edges:
            adjacency[edge.before].add(edge.after)
            indegree[edge.after] += 1
        layers: list[tuple[str, ...]] = []
        remaining = set(by_id)
        while remaining:
            layer = sorted(
                (node for node in remaining if indegree[node] == 0),
                key=lambda node: _region_sort_key(by_id[node]),
            )
            layers.append(tuple(layer))
            for node in layer:
                remaining.remove(node)
                for successor in adjacency[node]:
                    indegree[successor] -= 1
        return tuple(layers)


__all__ = [
    "PageRegion",
    "PageRegionKind",
    "ReadingOrderEdge",
    "ReadingOrderGraph",
    "ReadingOrderRelation",
    "stable_topology_digest",
]
