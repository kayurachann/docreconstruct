"""Canonical, provider-independent document intermediate representation.

The models in this module deliberately contain only portable data.  Provider
payloads that do not yet have a canonical field can be retained in ``metadata``
without weakening validation of the geometry and confidence fields used by the
reconstruction pipeline.
"""

from __future__ import annotations

import base64
import math
from enum import StrEnum
from typing import Annotated, Any, ClassVar, TypeVar

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

_ModelT = TypeVar("_ModelT", bound="CanonicalModel")
_BYTES_TAG = "$docreconstruct_base64"


def _encode_metadata_bytes(value: Any) -> Any:
    """Tag arbitrary bytes so untyped metadata can round-trip through JSON."""

    if isinstance(value, bytes):
        return {_BYTES_TAG: base64.urlsafe_b64encode(value).decode("ascii")}
    if isinstance(value, bytearray):
        return {_BYTES_TAG: base64.urlsafe_b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dict):
        return {key: _encode_metadata_bytes(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode_metadata_bytes(item) for item in value]
    return value


def _decode_metadata_bytes(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {_BYTES_TAG} and isinstance(value[_BYTES_TAG], str):
            try:
                return base64.urlsafe_b64decode(value[_BYTES_TAG].encode("ascii"))
            except (ValueError, UnicodeEncodeError) as exc:
                raise ValueError("invalid base64 byte payload in metadata") from exc
        return {key: _decode_metadata_bytes(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_metadata_bytes(item) for item in value]
    return value


Metadata = Annotated[
    dict[str, Any],
    BeforeValidator(_decode_metadata_bytes),
    PlainSerializer(_encode_metadata_bytes, return_type=Any, when_used="json"),
]


class CanonicalModel(BaseModel):
    """Base class with strict input validation and explicit JSON helpers."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    def to_json(self, **kwargs: Any) -> str:
        """Serialize the model to canonical JSON using Pydantic's JSON mode."""

        return self.model_dump_json(**kwargs)

    @classmethod
    def from_json(cls: type[_ModelT], value: str | bytes | bytearray) -> _ModelT:
        """Validate a model from a JSON string or bytes."""

        return cls.model_validate_json(value)

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        """Return the JSON Schema for this model."""

        return cls.model_json_schema()


class BBox(CanonicalModel):
    """Axis-aligned bounding box in source-page coordinates."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        frozen=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    x0: float
    y0: float
    x1: float
    y1: float

    @field_validator("x0", "y0", "x1", "y1")
    @classmethod
    def coordinates_must_be_finite(cls, value: float) -> float:
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("bounding-box coordinates must be finite")
        return value

    @model_validator(mode="after")
    def coordinates_must_be_ordered(self) -> BBox:
        if self.x1 < self.x0:
            raise ValueError("x1 must be greater than or equal to x0")
        if self.y1 < self.y0:
            raise ValueError("y1 must be greater than or equal to y0")
        return self

    @classmethod
    def from_sequence(cls, value: list[float] | tuple[float, float, float, float]) -> BBox:
        """Build a box from ``[x0, y0, x1, y1]``."""

        if len(value) != 4:
            raise ValueError("a bounding box requires exactly four coordinates")
        return cls(x0=value[0], y0=value[1], x1=value[2], y1=value[3])

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def center(self) -> tuple[float, float]:
        return (self.center_x, self.center_y)

    def intersection(self, other: BBox) -> BBox | None:
        """Return the geometric intersection, or ``None`` when disjoint."""

        x0 = max(self.x0, other.x0)
        y0 = max(self.y0, other.y0)
        x1 = min(self.x1, other.x1)
        y1 = min(self.y1, other.y1)
        if x1 <= x0 or y1 <= y0:
            return None
        return BBox(x0=x0, y0=y0, x1=x1, y1=y1)

    def iou(self, other: BBox) -> float:
        """Return intersection-over-union in the inclusive range ``[0, 1]``."""

        overlap = self.intersection(other)
        if overlap is None:
            return 0.0
        union = self.area + other.area - overlap.area
        return overlap.area / union if union > 0 else 0.0


class Point(CanonicalModel):
    """Finite two-dimensional point in source-page coordinates."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        frozen=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    x: float
    y: float

    @model_validator(mode="before")
    @classmethod
    def accept_coordinate_pair(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return {"x": value[0], "y": value[1]}
        return value

    @field_validator("x", "y")
    @classmethod
    def coordinates_must_be_finite(cls, value: float) -> float:
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("point coordinates must be finite")
        return value


class ElementType(StrEnum):
    TEXT = "text"
    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    FIGURE = "figure"
    IMAGE = "image"
    CAPTION = "caption"
    FORMULA = "formula"
    CHART = "chart"
    HEADER = "header"
    FOOTER = "footer"
    FOOTNOTE = "footnote"
    PAGE_NUMBER = "page_number"
    SIGNATURE = "signature"
    STAMP = "stamp"
    CHECKBOX = "checkbox"
    UNKNOWN = "unknown"


class SourceType(StrEnum):
    NATIVE = "native"
    SCANNED = "scanned"
    HYBRID = "hybrid"
    IMAGE = "image"
    UNKNOWN = "unknown"


class TextAlignment(StrEnum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


class ElementStyle(CanonicalModel):
    """Visual evidence inferred or extracted for a document element."""

    font_family: str | None = None
    font_size: float | None = Field(default=None, gt=0)
    font_weight: int | None = Field(default=None, ge=1, le=1000)
    italic: bool | None = None
    underline: bool | None = None
    alignment: TextAlignment | None = None
    line_height: float | None = Field(default=None, gt=0)
    color: str | None = None
    background_color: str | None = None
    rotation: float | None = None
    opacity: float | None = Field(default=None, ge=0, le=1)

    @field_validator("rotation")
    @classmethod
    def rotation_must_be_finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("rotation must be finite")
        return value


class Provenance(CanonicalModel):
    """Origin and reliability of an observation.

    A fused observation uses ``engine='ensemble'`` and retains the original
    records recursively in ``contributors``.
    """

    engine: str = Field(min_length=1)
    source_id: str | None = None
    text_confidence: float | None = Field(default=None, ge=0, le=1)
    layout_confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: Metadata = Field(default_factory=dict)
    contributors: list[Provenance] = Field(default_factory=list)

    @field_validator("engine")
    @classmethod
    def engine_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("engine must not be blank")
        return value


class TextCandidate(CanonicalModel):
    """One provider's hypothesis for an element's exact text."""

    engine: str = Field(min_length=1)
    value: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_element_id: str | None = None
    metadata: Metadata = Field(default_factory=dict)


class Relationship(CanonicalModel):
    """Canonical relationships between this element and other element IDs."""

    parent: str | None = None
    caption_of: str | None = None
    continued_from: str | None = None
    continued_to: str | None = None
    children: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    metadata: Metadata = Field(default_factory=dict)


class Element(CanonicalModel):
    """Smallest independently positioned unit in the canonical representation."""

    id: str = Field(min_length=1)
    type: ElementType = ElementType.UNKNOWN
    bbox: BBox
    polygon: list[Point] = Field(default_factory=list)
    z_index: int = 0
    source_crop: BBox | None = None
    text: str | None = None
    reading_order: int | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    style: ElementStyle = Field(default_factory=ElementStyle)
    relationships: Relationship = Field(default_factory=Relationship)
    provenance: Provenance | None = None
    text_candidates: list[TextCandidate] = Field(default_factory=list)
    metadata: Metadata = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def id_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("element id must not be blank")
        return value


class Page(CanonicalModel):
    """One source page and all canonical elements located on it."""

    id: str = Field(min_length=1)
    number: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: float = 0.0
    elements: list[Element] = Field(default_factory=list)
    source_type: SourceType = SourceType.UNKNOWN
    metadata: Metadata = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def id_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("page id must not be blank")
        return value

    @field_validator("width", "height", "rotation")
    @classmethod
    def dimensions_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("page dimensions and rotation must be finite")
        return value

    @model_validator(mode="after")
    def element_ids_must_be_unique(self) -> Page:
        ids = [element.id for element in self.elements]
        if len(ids) != len(set(ids)):
            raise ValueError("element IDs must be unique within a page")
        return self


class Document(CanonicalModel):
    """Canonical document exchanged between providers and reconstruction stages."""

    CURRENT_SCHEMA_VERSION: ClassVar[str] = "0.1"

    id: str = Field(min_length=1)
    pages: list[Page] = Field(default_factory=list)
    source: str | None = None
    metadata: Metadata = Field(default_factory=dict)
    schema_version: str = Field(default=CURRENT_SCHEMA_VERSION, min_length=1)

    @field_validator("id")
    @classmethod
    def id_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("document id must not be blank")
        return value

    @model_validator(mode="after")
    def page_identifiers_must_be_unique(self) -> Document:
        ids = [page.id for page in self.pages]
        numbers = [page.number for page in self.pages]
        if len(ids) != len(set(ids)):
            raise ValueError("page IDs must be unique within a document")
        if len(numbers) != len(set(numbers)):
            raise ValueError("page numbers must be unique within a document")
        return self


Provenance.model_rebuild()
