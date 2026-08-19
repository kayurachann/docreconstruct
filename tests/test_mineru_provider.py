from __future__ import annotations

from PIL import Image

from docreconstruct.evidence import detect_sidecar_provider
from docreconstruct.ir import BBox, ElementType
from docreconstruct.providers import MinerUProvider, ProviderContext
from docreconstruct.reconstruction.evidence_matching import match_sidecar_evidence
from docreconstruct.reconstruction.markdown_content import (
    MarkdownBlock,
    MarkdownBlockKind,
    MarkdownContent,
)
from docreconstruct.reconstruction.scan_layout import (
    PixelBox,
    ScanDocumentLayout,
    ScanPageLayout,
)


def test_content_list_uses_unit_geometry_and_preserves_visual_assets() -> None:
    payload = {
        "_backend": "pipeline",
        "_version_name": "3.0.0",
        "content_list": [
            {
                "page_idx": 4,
                "type": "equation",
                "text": "$$x^2 + y^2 = 1$$",
                "text_format": "latex",
                "img_path": "images/equation.jpg",
                "bbox": [100, 120, 900, 240],
            },
            {
                "page_idx": 4,
                "type": "table",
                "img_path": "images/table.jpg",
                "table_caption": ["Table 1"],
                "table_body": "<table><tr><td>A</td></tr></table>",
                "bbox": [100, 300, 900, 600],
            },
            {
                "page_idx": 4,
                "type": "image",
                "img_path": "images/figure.jpg",
                "image_caption": ["Figure 1"],
                "bbox": [100, 650, 900, 950],
            },
        ],
    }

    document = MinerUProvider().normalize(
        payload,
        context=ProviderContext(page_width=1200, page_height=1800),
    )
    page = document.pages[0]

    # content_list geometry is defined by MinerU in a 0-1000 frame.  The
    # canonical unit square retains those semantics instead of guessing pixels.
    assert (page.number, page.width, page.height) == (5, 1.0, 1.0)
    assert page.metadata["source_page_index"] == 4
    assert page.metadata["coordinate_system"] == "mineru_content_list_normalized"
    assert page.metadata["source_coordinate_system"] == "mineru_content_list_0_1000"
    assert page.metadata["source_coordinate_extent"] == [1000.0, 1000.0]
    assert document.metadata["mineru"] == {
        "_backend": "pipeline",
        "_version_name": "3.0.0",
    }

    formula, table, image = page.elements
    assert formula.type is ElementType.FORMULA
    assert formula.bbox == BBox(x0=0.1, y0=0.12, x1=0.9, y1=0.24)
    assert formula.metadata["latex"] == "$$x^2 + y^2 = 1$$"
    assert formula.metadata["image_ref"] == "images/equation.jpg"
    assert formula.metadata["asset"]["kind"] == "formula"

    assert table.type is ElementType.TABLE
    assert table.metadata["table"]["html"].startswith("<table>")
    assert table.metadata["image_ref"] == "images/table.jpg"
    assert image.type is ElementType.IMAGE
    assert image.metadata["image_caption"] == ["Figure 1"]
    assert image.metadata["image"] == {
        "path": "images/figure.jpg",
        "src": "images/figure.jpg",
    }

    for element in page.elements:
        assert element.metadata["page_idx"] == 4
        assert element.metadata["source_coordinate_system"] == ("mineru_content_list_0_1000")
        assert element.provenance is not None
        assert element.provenance.metadata["page_idx"] == 4
        assert element.provenance.metadata["output_format"] == "content_list"
        assert element.provenance.source_id.startswith("content_list.page[4]")


def test_content_list_v2_preserves_structured_text_and_formula_provenance() -> None:
    payload = [
        [
            {
                "type": "title",
                "content": {
                    "title_content": [
                        {"type": "text", "content": "1 "},
                        {"type": "text", "content": "Introduction"},
                    ],
                    "level": 1,
                },
                "bbox": [83, 121, 917, 156],
            },
            {
                "type": "equation_interline",
                "content": {
                    "math_content": "x^2 + 1",
                    "math_type": "latex",
                    "image_path": "images/math.png",
                },
                "bbox": [100, 300, 900, 420],
            },
        ]
    ]

    detection = detect_sidecar_provider(payload)
    assert detection.provider == "mineru"
    assert detection.confidence == 0.93

    page = MinerUProvider().normalize(payload).pages[0]
    title, formula = page.elements

    assert page.metadata["output_format"] == "content_list_v2"
    assert title.type is ElementType.TITLE
    assert title.text == "1 Introduction"
    assert formula.type is ElementType.FORMULA
    assert formula.text == "x^2 + 1"
    assert formula.metadata["latex"] == "x^2 + 1"
    assert formula.metadata["image_ref"] == "images/math.png"
    assert formula.metadata["mineru_content"]["math_type"] == "latex"
    assert formula.provenance is not None
    assert formula.provenance.source_id == "content_list_v2[0][1]"


def test_multipage_content_list_projects_to_portrait_scan_without_pixel_guessing() -> None:
    document = MinerUProvider().normalize(
        [
            {
                "page_idx": 0,
                "type": "text",
                "text": "First page evidence",
                "bbox": [100, 200, 900, 300],
            },
            {
                "page_idx": 1,
                "type": "text",
                "text": "Second page evidence",
                "bbox": [100, 700, 900, 800],
            },
        ]
    )
    markdown = MarkdownContent(
        source="content.md",
        blocks=[
            MarkdownBlock(
                id="md-1",
                index=0,
                kind=MarkdownBlockKind.PARAGRAPH,
                text="First page evidence",
            ),
            MarkdownBlock(
                id="md-2",
                index=1,
                kind=MarkdownBlockKind.PARAGRAPH,
                text="Second page evidence",
            ),
        ],
    )
    scan = ScanDocumentLayout(
        source="portrait.pdf",
        pages=[
            ScanPageLayout(
                number=number,
                width=1000,
                height=1600,
                pdf_width=500,
                pdf_height=800,
                content_bbox=PixelBox(x0=0, y0=0, x1=1000, y1=1600),
                line_pitch=32,
                image=Image.new("RGB", (1000, 1600), "white"),
            )
            for number in (1, 2)
        ],
    )

    matches = match_sidecar_evidence(markdown, scan, document)

    assert [match.page_number for match in matches] == [1, 2]
    assert [match.source_bbox for match in matches] == [
        PixelBox(x0=100, y0=320, x1=900, y1=480),
        PixelBox(x0=100, y0=1120, x1=900, y1=1280),
    ]
