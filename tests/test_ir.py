from __future__ import annotations

import pytest
from pydantic import ValidationError

from docreconstruct.ir import (
    BBox,
    Document,
    Element,
    ElementStyle,
    ElementType,
    Page,
    Point,
    Provenance,
    SourceType,
    TextCandidate,
)


def test_bbox_validation_properties_and_iou() -> None:
    box = BBox(x0=10, y0=20, x1=30, y1=50)
    other = BBox(x0=20, y0=35, x1=40, y1=55)

    assert box.width == 20
    assert box.height == 30
    assert box.area == 600
    assert box.center == (20, 35)
    assert box.intersection(other) == BBox(x0=20, y0=35, x1=30, y1=50)
    assert box.iou(other) == pytest.approx(150 / 850)
    assert box.iou(BBox(x0=100, y0=100, x1=110, y1=110)) == 0

    with pytest.raises(ValidationError, match="x1"):
        BBox(x0=2, y0=0, x1=1, y1=1)
    with pytest.raises(ValidationError, match="finite"):
        BBox(x0=0, y0=0, x1=float("inf"), y1=1)


def test_document_json_round_trip_and_schema() -> None:
    element = Element(
        id="el-1",
        type=ElementType.TITLE,
        bbox=BBox(x0=1, y0=2, x1=90, y1=20),
        polygon=[[1, 2], [90, 2], [90, 20], [1, 20]],
        z_index=2,
        source_crop=BBox(x0=0, y0=0, x1=95, y1=24),
        text="Quarterly report",
        reading_order=0,
        confidence=0.98,
        style=ElementStyle(font_family="Inter", font_size=18, font_weight=700),
        provenance=Provenance(
            engine="paddleocr",
            text_confidence=0.98,
            layout_confidence=0.95,
        ),
        text_candidates=[
            TextCandidate(engine="paddleocr", value="Quarterly report", confidence=0.98)
        ],
        metadata={"provider_field": {"kept": True}},
    )
    document = Document(
        id="doc-1",
        pages=[
            Page(
                id="page-1",
                number=1,
                width=100,
                height=200,
                elements=[element],
                source_type=SourceType.NATIVE,
            )
        ],
        source="sample.pdf",
    )

    encoded = document.model_dump_json()
    decoded = Document.model_validate_json(encoded)
    assert decoded == document
    assert decoded.pages[0].elements[0].polygon[0] == Point(x=1, y=2)
    assert decoded.pages[0].elements[0].z_index == 2
    assert Document.from_json(document.to_json()) == document

    schema = Document.model_json_schema()
    assert schema["properties"]["schema_version"]["default"] == "0.1"
    assert "Page" in schema["$defs"]
    assert BBox.json_schema()["required"] == ["x0", "y0", "x1", "y1"]


@pytest.mark.parametrize(
    "binary",
    [
        b"\x89PNG\r\n\x1a\n\x00\xff\x80",
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\xfe\x80",
    ],
)
def test_arbitrary_image_bytes_round_trip_inside_metadata(binary: bytes) -> None:
    document = Document(
        id="binary-doc",
        pages=[
            Page(
                id="page-1",
                number=1,
                width=10,
                height=10,
                elements=[
                    Element(
                        id="image-1",
                        type=ElementType.IMAGE,
                        bbox=BBox(x0=0, y0=0, x1=10, y1=10),
                        metadata={
                            "image": {
                                "bytes": binary,
                                "mime_type": "image/png",
                            }
                        },
                    )
                ],
            )
        ],
    )

    payload = document.model_dump_json()
    restored = Document.model_validate_json(payload)
    restored_bytes = restored.pages[0].elements[0].metadata["image"]["bytes"]
    assert restored_bytes == binary
    assert isinstance(restored_bytes, bytes)


def test_page_and_document_reject_duplicate_identifiers() -> None:
    element = Element(id="duplicate", bbox=BBox(x0=0, y0=0, x1=1, y1=1))
    with pytest.raises(ValidationError, match="element IDs"):
        Page(
            id="page-1",
            number=1,
            width=1,
            height=1,
            elements=[element, element.model_copy(deep=True)],
        )

    page = Page(id="page-1", number=1, width=1, height=1)
    with pytest.raises(ValidationError, match="page IDs"):
        Document(id="doc", pages=[page, page.model_copy(update={"number": 2})])


def test_mutable_defaults_are_not_shared() -> None:
    left = Document(id="left")
    right = Document(id="right")
    left.metadata["changed"] = True
    left.pages.append(Page(id="page-1", number=1, width=1, height=1))
    assert right.metadata == {}
    assert right.pages == []
