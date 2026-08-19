from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path

import pytest

from docreconstruct.ir import BBox, Document, Element, ElementStyle, ElementType, Page
from docreconstruct.renderers import (
    DOCXRenderer,
    HTMLRenderer,
    JSONRenderer,
    MarkdownRenderer,
    registry,
    render,
)


def _document(*elements: Element) -> Document:
    return Document(
        id="test-document",
        pages=[Page(id="page-1", number=1, width=600, height=800, elements=list(elements))],
    )


def _png_bytes() -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format="PNG")
    return output.getvalue()


def test_json_is_deterministic_and_round_trips_canonical_ir() -> None:
    document = _document(
        Element(
            id="paragraph-1",
            type=ElementType.PARAGRAPH,
            bbox=BBox(x0=10, y0=20, x1=200, y1=60),
            text="Exact text Ω",
        )
    )
    renderer = JSONRenderer()

    first = renderer.render(document)
    second = renderer.render(document)

    assert first == second
    assert Document.model_validate_json(first) == document
    assert json.loads(first)["pages"][0]["elements"][0]["text"] == "Exact text Ω"


def test_html_is_fixed_page_escaped_and_self_contained() -> None:
    dangerous = '<script>alert("x")</script> & exact'
    document = _document(
        Element(
            id="text-1",
            type=ElementType.PARAGRAPH,
            bbox=BBox(x0=10, y0=20, x1=250, y1=70),
            text=dangerous,
            style=ElementStyle(font_size=12, font_weight=700),
        ),
        Element(
            id="table-1",
            type=ElementType.TABLE,
            bbox=BBox(x0=10, y0=100, x1=400, y1=220),
            metadata={"rows": [["A", "B"], ["<one>", "two"]]},
        ),
        Element(
            id="image-1",
            type=ElementType.IMAGE,
            bbox=BBox(x0=10, y0=240, x1=110, y1=340),
            metadata={
                "image_data": base64.b64encode(_png_bytes()).decode(),
                "mime_type": "image/png",
            },
        ),
    )

    output = HTMLRenderer().render(document)

    assert "width:600px;height:800px" in output
    assert dangerous not in output
    assert "&lt;script&gt;" in output
    assert "&lt;one&gt;" in output
    assert "data:image/png;base64," in output
    assert "http://" not in output and "https://" not in output


def test_local_image_dereference_is_disabled_by_default(tmp_path: Path) -> None:
    image_path = tmp_path / "private.png"
    image_bytes = _png_bytes()
    image_path.write_bytes(image_bytes)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    document = _document(
        Element(
            id="private-image",
            type=ElementType.IMAGE,
            bbox=BBox(x0=0, y0=0, x1=20, y1=20),
            metadata={"image_ref": str(image_path)},
        )
    )

    safe_html = HTMLRenderer().render(document)
    opted_in_html = HTMLRenderer(
        allow_local_files=True,
        local_file_root=tmp_path,
    ).render(document)

    assert encoded not in safe_html
    assert "dr-image-placeholder" in safe_html
    assert encoded in opted_in_html

    if DOCXRenderer.is_available():
        safe_docx = DOCXRenderer().render(document)
        with zipfile.ZipFile(io.BytesIO(safe_docx)) as archive:
            assert not any(name.startswith("word/media/") for name in archive.namelist())


@pytest.mark.skipif(not DOCXRenderer.is_available(), reason="python-docx is optional")
def test_docx_groups_native_spans_and_preserves_styled_runs() -> None:
    from docx import Document as WordDocument

    document = _document(
        Element(
            id="page-1-text-1-1-1",
            type=ElementType.TEXT,
            bbox=BBox(x0=10, y0=10, x1=45, y1=20),
            text="Hello",
            reading_order=0,
            style=ElementStyle(font_size=10, font_weight=400),
        ),
        Element(
            id="page-1-text-1-1-2",
            type=ElementType.TEXT,
            bbox=BBox(x0=50, y0=10, x1=85, y1=20),
            text="world",
            reading_order=1,
            style=ElementStyle(font_size=10, font_weight=700),
        ),
        Element(
            id="page-1-text-1-2-1",
            type=ElementType.TEXT,
            bbox=BBox(x0=10, y0=24, x1=45, y1=34),
            text="again",
            reading_order=2,
            style=ElementStyle(font_size=10, font_weight=400),
        ),
        Element(
            id="heading-1",
            type=ElementType.HEADING,
            bbox=BBox(x0=10, y0=60, x1=200, y1=90),
            text="Heading",
            reading_order=3,
            metadata={"level": 2},
        ),
    )

    output = DOCXRenderer().render(document)
    reconstructed = WordDocument(io.BytesIO(output))
    nonempty = [paragraph for paragraph in reconstructed.paragraphs if paragraph.text]

    assert [paragraph.text for paragraph in nonempty] == ["Hello world again", "Heading"]
    world_run = next(run for run in nonempty[0].runs if run.text == "world")
    assert world_run.bold is True
    assert nonempty[1].style.name == "Heading 2"


@pytest.mark.skipif(not DOCXRenderer.is_available(), reason="python-docx is optional")
def test_docx_preserves_source_page_geometry_margins_and_boundaries() -> None:
    from docx import Document as WordDocument
    from docx.enum.section import WD_SECTION

    a4_width, a4_height = 595.2756, 841.8898
    source = Document(
        id="two-page-pdf",
        pages=[
            Page(
                id="page-1",
                number=1,
                width=a4_width,
                height=a4_height,
                source_type="scanned",
                metadata={"provider": "native_pdf"},
                elements=[
                    Element(
                        id="scan-1",
                        type=ElementType.IMAGE,
                        bbox=BBox(x0=0, y0=72, x1=a4_width, y1=a4_height - 72),
                        metadata={"image_data": base64.b64encode(_png_bytes()).decode()},
                    )
                ],
            ),
            Page(
                id="page-2",
                number=2,
                width=612,
                height=792,
                metadata={
                    "coordinate_unit": "pt",
                    "page_margins": {"left": 36, "top": 54, "right": 45, "bottom": 63},
                },
                elements=[
                    Element(
                        id="paragraph-2",
                        type=ElementType.PARAGRAPH,
                        bbox=BBox(x0=36, y0=54, x1=567, y1=80),
                        text="Second page",
                    )
                ],
            ),
        ],
    )

    reconstructed = WordDocument(io.BytesIO(DOCXRenderer().render(source)))

    assert len(reconstructed.sections) == 2
    first, second = reconstructed.sections
    assert first.page_width.pt == pytest.approx(a4_width, abs=0.1)
    assert first.page_height.pt == pytest.approx(a4_height, abs=0.1)
    assert first.left_margin.pt == pytest.approx(0, abs=0.1)
    assert first.top_margin.pt == pytest.approx(72, abs=0.1)
    assert first.right_margin.pt == pytest.approx(0, abs=0.1)
    assert first.bottom_margin.pt == pytest.approx(72, abs=0.1)
    assert second.page_width.pt == pytest.approx(612, abs=0.1)
    assert second.page_height.pt == pytest.approx(792, abs=0.1)
    assert second.left_margin.pt == pytest.approx(36, abs=0.1)
    assert second.top_margin.pt == pytest.approx(54, abs=0.1)
    assert second.right_margin.pt == pytest.approx(45, abs=0.1)
    assert second.bottom_margin.pt == pytest.approx(63, abs=0.1)
    assert second.start_type == WD_SECTION.NEW_PAGE

    picture = reconstructed.inline_shapes[0]
    assert picture.width.inches == pytest.approx(a4_width / 72, abs=0.01)


@pytest.mark.skipif(not DOCXRenderer.is_available(), reason="python-docx is optional")
def test_docx_image_line_box_is_not_clipped_by_text_line_height() -> None:
    from docx import Document as WordDocument

    source = _document(
        Element(
            id="figure-with-text-line-height",
            type=ElementType.FIGURE,
            bbox=BBox(x0=10, y0=20, x1=210, y1=120),
            style=ElementStyle(line_height=1),
            metadata={"image_data": base64.b64encode(_png_bytes()).decode()},
        )
    )

    reconstructed = WordDocument(io.BytesIO(DOCXRenderer().render(source)))

    assert len(reconstructed.inline_shapes) == 1
    picture_paragraph = next(
        paragraph for paragraph in reconstructed.paragraphs if paragraph._p.xpath(".//w:drawing")
    )
    assert picture_paragraph.paragraph_format.line_spacing is None
    assert picture_paragraph.paragraph_format.line_spacing_rule is None


def test_markdown_and_render_facade(tmp_path: Path) -> None:
    document = _document(
        Element(
            id="title-1",
            type=ElementType.TITLE,
            bbox=BBox(x0=0, y0=0, x1=100, y1=20),
            text="A title",
        ),
        Element(
            id="table-1",
            type=ElementType.TABLE,
            bbox=BBox(x0=0, y0=30, x1=100, y1=80),
            metadata={"rows": [["Name", "Value"], ["A", "1"]]},
        ),
    )

    markdown = MarkdownRenderer().render(document)
    destination = render(document, tmp_path / "artifact.json")

    assert markdown.startswith("# A title")
    assert "| Name | Value |" in markdown
    assert destination.is_file()
    assert registry.get("json").format == "json"


def test_table_html_is_converted_to_native_rows_with_conservative_spans() -> None:
    document = _document(
        Element(
            id="table-html",
            type=ElementType.TABLE,
            bbox=BBox(x0=0, y0=0, x1=300, y1=100),
            metadata={
                "table_html": (
                    "<table><tr><th rowspan='2'>A</th><th colspan='2'>B</th></tr>"
                    "<tr><td>C</td><td>D<script>ignored()</script></td></tr></table>"
                )
            },
        )
    )

    markdown = MarkdownRenderer().render(document)
    html_output = HTMLRenderer().render(document)

    assert "| A | B |  |" in markdown
    assert "|  | C | D |" in markdown
    assert "ignored" not in markdown
    assert "<th>A</th><th>B</th><th></th>" in html_output
