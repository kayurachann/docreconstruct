from __future__ import annotations

import json

import pytest

from docreconstruct.ir import (
    BBox,
    Document,
    Element,
    ElementType,
    Page,
    Provenance,
    SourceType,
    TextCandidate,
)
from docreconstruct.normalization import EvidenceFusion, fuse_documents
from docreconstruct.providers import (
    AWSTextractProvider,
    AzureDocumentIntelligenceProvider,
    GoogleDocumentAIProvider,
    JSONProvider,
    MarkdownEvidenceProvider,
    MathpixProvider,
    MinerUProvider,
    MistralOCRProvider,
    NativePDFProvider,
    OlmOCRProvider,
    PaddleOCROfficialProvider,
    PaddleOCRProvider,
    PaddleOCRVLServerProvider,
    ProviderContext,
    ProviderInferenceUnsupportedError,
    ProviderRegistry,
    TesseractLocalProvider,
    get_registry,
    registry,
)
from docreconstruct.reconstruction.evidence_matching import _orthogonal_page_box


def test_builtin_registry_and_custom_registry() -> None:
    assert get_registry() is registry
    assert set(registry.names()) == {
        "aws_textract",
        "azure_document_intelligence",
        "google_document_ai",
        "json",
        "markdown",
        "mathpix",
        "mineru",
        "mistral_ocr",
        "native_pdf",
        "olmocr",
        "paddleocr",
        "paddleocr_official",
        "paddleocr_vl_server",
        "tesseract_local",
    }
    assert isinstance(registry.get("PaddleOCR"), PaddleOCRProvider)
    assert isinstance(registry.get("PaddleOCR-Official"), PaddleOCROfficialProvider)
    assert isinstance(registry.get("PaddleOCR-VL-Server"), PaddleOCRVLServerProvider)
    assert isinstance(registry.get("Mistral-OCR"), MistralOCRProvider)
    assert isinstance(registry.get("Mathpix"), MathpixProvider)
    assert isinstance(registry.get("AWS-Textract"), AWSTextractProvider)
    assert isinstance(registry.get("Google-Document-AI"), GoogleDocumentAIProvider)
    assert isinstance(
        registry.get("azure-document-intelligence"),
        AzureDocumentIntelligenceProvider,
    )
    assert registry.get("native-pdf").name == "native_pdf"
    assert isinstance(registry.get("tesseract-local"), TesseractLocalProvider)

    custom = ProviderRegistry()
    custom.register(JSONProvider)
    assert isinstance(custom.create("json"), JSONProvider)
    with pytest.raises(ValueError, match="already registered"):
        custom.register(JSONProvider)


def test_markdown_provider_imports_text_math_table_and_image_urls(tmp_path) -> None:
    source = tmp_path / "provider.md"
    source.write_text(
        "# Heading\n\nText body.\n\n$$x^2$$\n\n"
        "<table><tr><td>A</td></tr></table>\n\n"
        "![figure](https://example.test/figure.png)\n",
        encoding="utf-8",
    )

    result = MarkdownEvidenceProvider().parse(source)
    elements = result.document.pages[0].elements

    assert [element.type for element in elements] == [
        ElementType.HEADING,
        ElementType.PARAGRAPH,
        ElementType.FORMULA,
        ElementType.TABLE,
        ElementType.IMAGE,
    ]
    assert elements[2].metadata["latex"] == "x^2"
    assert elements[3].metadata["table"]["rows"] == [["A"]]
    assert elements[4].metadata["image_ref"] == "https://example.test/figure.png"
    assert result.warnings


def test_json_provider_validates_canonical_document() -> None:
    original = Document(
        id="canonical",
        pages=[Page(id="page-1", number=1, width=100, height=200)],
    )
    result = JSONProvider().parse(original.model_dump_json())
    assert result.provider == "json"
    assert result.document == original

    overridden = JSONProvider().normalize(
        original.model_dump(),
        context=ProviderContext(document_id="new-id", source="source.json"),
    )
    assert overridden.id == "new-id"
    assert overridden.source == "source.json"


def test_paddleocr_saved_array_shape() -> None:
    payload = {
        "page_index": 0,
        "width": 100,
        "height": 200,
        "res": {
            "rec_texts": ["Hello", "World"],
            "rec_scores": [0.99, 0.8],
            "rec_boxes": [[1, 2, 20, 12], [1, 20, 30, 35]],
        },
    }
    result = PaddleOCRProvider().parse(payload)
    page = result.document.pages[0]
    assert (page.width, page.height) == (100, 200)
    assert [element.text for element in page.elements] == ["Hello", "World"]
    assert page.elements[0].bbox == BBox(x0=1, y0=2, x1=20, y1=12)
    assert page.elements[0].provenance.engine == "paddleocr"


def test_paddleocr_ppstructure_v3_preserves_structured_evidence() -> None:
    payload = {
        "res": {
            "page_index": 0,
            "width": 1000,
            "height": 1400,
            "doc_preprocessor_res": {
                "page_index": 0,
                "model_settings": {
                    "use_doc_orientation_classify": True,
                    "use_doc_unwarping": True,
                    "unrelated_setting": "not persisted",
                },
                "angle": 90,
                "output_img": "large-raster-placeholder",
            },
            "parsing_res_list": [
                {
                    "block_bbox": [100, 80, 900, 180],
                    "block_label": "doc_title",
                    "block_content": "Structured annual report",
                    "block_id": 41,
                    "block_order": 2,
                },
                {
                    "block_bbox": [200, 260, 800, 360],
                    "block_label": "formula",
                    "block_content": r"x^2 + y^2 = 1",
                    "block_id": 42,
                    "block_order": 1,
                },
            ],
            "layout_det_res": {
                "boxes": [
                    {
                        "cls_id": 11,
                        "label": "doc_title",
                        "score": 0.91,
                        "coordinate": [100, 80, 900, 180],
                    },
                    {
                        "cls_id": 1,
                        "label": "image",
                        "score": 0.87,
                        "coordinate": [100, 500, 900, 900],
                    },
                ]
            },
            "overall_ocr_res": {
                "dt_polys": [[[110, 100], [500, 100], [500, 140], [110, 140]]],
                "dt_scores": [0.88],
                "rec_polys": [[[110, 100], [500, 100], [500, 140], [110, 140]]],
                "rec_texts": ["Structured annual report"],
                "rec_scores": [0.97],
            },
        }
    }

    page = PaddleOCRProvider().normalize(payload).pages[0]
    title = next(element for element in page.elements if element.type is ElementType.TITLE)
    formula = next(element for element in page.elements if element.type is ElementType.FORMULA)
    image = next(element for element in page.elements if element.type is ElementType.IMAGE)
    ocr_line = next(
        element
        for element in page.elements
        if element.metadata.get("paddle_section") == "overall_ocr_res"
    )

    assert title.text == "Structured annual report"
    assert title.bbox == BBox(x0=100, y0=80, x1=900, y1=180)
    assert title.reading_order == 2
    assert title.metadata["block_id"] == 41
    assert title.metadata["block_order"] == 2
    assert title.metadata["layout_detection"] == {
        "source_id": "root.layout_det_res.boxes[0]",
        "confidence": 0.91,
        "cls_id": 11,
    }
    assert title.provenance is not None
    assert title.provenance.layout_confidence == pytest.approx(0.91)

    assert formula.text == r"x^2 + y^2 = 1"
    assert formula.metadata["latex"] == r"x^2 + y^2 = 1"
    assert formula.reading_order == 1
    assert image.confidence == pytest.approx(0.87)
    assert image.metadata["reading_order_reliable"] is False

    assert ocr_line.polygon[0].x == 110
    assert ocr_line.confidence == pytest.approx(0.97)
    assert ocr_line.provenance is not None
    assert ocr_line.provenance.text_confidence == pytest.approx(0.97)
    assert ocr_line.provenance.layout_confidence == pytest.approx(0.88)
    assert ocr_line.relationships.parent == title.id
    assert ocr_line.id in title.relationships.children

    assert page.metadata["doc_preprocessor"] == {
        "angle": 90,
        "page_index": 0,
        "model_settings": {
            "use_doc_orientation_classify": True,
            "use_doc_unwarping": True,
        },
    }


def test_paddleocr_vl_ordered_page_envelopes_preserve_four_pages() -> None:
    payload = [
        {
            "prunedResult": {
                "parsing_res_list": [
                    {
                        "block_bbox": [10, 20, 100, 45],
                        "block_label": "text",
                        "block_content": f"Page {page_number}",
                        "block_id": page_number,
                        "block_order": 0,
                    }
                ],
                "layout_det_res": {"boxes": []},
            },
            "markdown": {"text": f"Page {page_number}", "images": {}},
            "outputImages": {"layout_det_res": f"page-{page_number}-layout"},
            "inputImage": f"page-{page_number}.png",
        }
        for page_number in range(1, 5)
    ]

    document = PaddleOCRProvider().normalize(payload)

    assert [page.number for page in document.pages] == [1, 2, 3, 4]
    assert [[element.text for element in page.elements] for page in document.pages] == [
        ["Page 1"],
        ["Page 2"],
        ["Page 3"],
        ["Page 4"],
    ]
    assert all(
        page.elements[0].metadata["paddle_section"] == "parsing_res_list" for page in document.pages
    )


def test_mineru_middle_json_and_content_list_shapes() -> None:
    middle_json = {
        "pdf_info": [
            {
                "page_idx": 0,
                "page_size": [100, 200],
                "para_blocks": [
                    {
                        "type": "text",
                        "bbox": [5, 10, 80, 30],
                        "score": 0.93,
                        "lines": [{"spans": [{"content": "MinerU text", "bbox": [5, 10, 80, 30]}]}],
                    },
                    {
                        "type": "table",
                        "bbox": [5, 40, 90, 100],
                        "html": "<table><tr><td>A</td></tr></table>",
                    },
                ],
            }
        ]
    }
    document = MinerUProvider().normalize(middle_json)
    assert document.pages[0].elements[0].text == "MinerU text"
    table = document.pages[0].elements[1]
    assert table.type is ElementType.TABLE
    assert table.metadata["table"]["html"].startswith("<table>")

    content_list = [
        {"page_idx": 0, "type": "text", "text": "first", "bbox": [0, 0, 10, 10]},
        {"page_idx": 1, "type": "text", "text": "second", "bbox": [0, 0, 10, 10]},
    ]
    grouped = MinerUProvider().normalize(content_list)
    assert [page.elements[0].text for page in grouped.pages] == ["first", "second"]


def test_olmocr_jsonl_and_full_page_geometry_fallback() -> None:
    jsonl = "\n".join(
        [
            json.dumps(
                {
                    "text": "Page one",
                    "metadata": {
                        "page_number": 1,
                        "width": 612,
                        "height": 792,
                        "Source-File": "input.pdf",
                    },
                }
            ),
            json.dumps(
                {
                    "natural_text": "Page two",
                    "metadata": {"page_number": 2, "width": 612, "height": 792},
                }
            ),
        ]
    )
    result = OlmOCRProvider().parse(jsonl)
    assert result.document.source == "input.pdf"
    assert [page.elements[0].text for page in result.document.pages] == ["Page one", "Page two"]
    first = result.document.pages[0].elements[0]
    assert first.bbox == BBox(x0=0, y0=0, x1=612, y1=792)
    assert first.metadata["coordinate_system"] == "full_page_fallback"


def test_native_pdf_preserves_embedded_image_bytes_when_pymupdf_is_available() -> None:
    fitz = pytest.importorskip("pymupdf")
    pdf = fitz.open()
    pdf_page = pdf.new_page(width=100, height=100)
    pdf_page.insert_text((10, 20), "Native text")
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 2, 2), False)
    pixmap.clear_with(0x336699)
    image_bytes = pixmap.tobytes("png")
    pdf_page.insert_image(fitz.Rect(0, 50, 100, 100), stream=image_bytes)
    pdf_bytes = pdf.tobytes()
    pdf.close()

    document = NativePDFProvider().parse(pdf_bytes).document
    page = document.pages[0]
    image = next(element for element in page.elements if element.type is ElementType.IMAGE)

    assert page.source_type is SourceType.HYBRID
    assert image.metadata["image"]["bytes"].startswith(b"\x89PNG")
    assert image.metadata["image"]["mime_type"] == "image/png"
    assert Document.model_validate_json(document.model_dump_json()) == document


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_native_pdf_page_frame_matches_its_span_coordinates(rotation: int) -> None:
    """Page dimensions and element boxes must share one coordinate frame.

    PyMuPDF's `page.rect` is already rotated but `get_text` reports every bbox
    in the unrotated frame, and the IR pairs unrotated page dimensions with
    `Page.rotation`. Reporting the rotated rect mixed the two, so on a quarter
    turn every box was reflected about the wrong axis and the declared page
    size was the transpose of the real one.
    """

    fitz = pytest.importorskip("pymupdf")
    pdf = fitz.open()
    pdf_page = pdf.new_page(width=200, height=400)
    pdf_page.insert_text((20, 30), "Native text", fontsize=12)
    if rotation:
        pdf_page.set_rotation(rotation)
    pdf_bytes = pdf.tobytes()
    pdf.close()

    # PyMuPDF's own transform is the reference for display space.
    with fitz.open(stream=pdf_bytes, filetype="pdf") as reference:
        reference_page = reference[0]
        raw = next(
            block["bbox"]
            for block in reference_page.get_text("dict")["blocks"]
            if block.get("type") == 0
        )
        mapped = fitz.Rect(raw) * reference_page.rotation_matrix
        expected_box = (
            min(mapped.x0, mapped.x1),
            min(mapped.y0, mapped.y1),
            max(mapped.x0, mapped.x1),
            max(mapped.y0, mapped.y1),
        )
        expected_frame = (reference_page.rect.width, reference_page.rect.height)

    page = NativePDFProvider().parse(pdf_bytes).document.pages[0]
    element = next(item for item in page.elements if item.text)
    projected = _orthogonal_page_box(page, element.bbox)

    assert projected is not None
    box, frame_width, frame_height = projected
    assert (page.width, page.height) == (200.0, 400.0)
    assert page.rotation == float(rotation)
    assert (frame_width, frame_height) == pytest.approx(expected_frame)
    assert (box.x0, box.y0, box.x1, box.y1) == pytest.approx(expected_box, abs=1e-3)


@pytest.mark.parametrize("provider", [PaddleOCRProvider(), MinerUProvider(), OlmOCRProvider()])
def test_saved_adapters_fail_clearly_for_live_inference(provider: object) -> None:
    with pytest.raises(ProviderInferenceUnsupportedError, match="not bundled"):
        provider.parse("input.pdf")


def _evidence_document(engine: str, text: str, confidence: float) -> Document:
    element = Element(
        id=f"{engine}-element",
        type=ElementType.TEXT,
        bbox=BBox(x0=10, y0=10, x1=50, y1=20),
        text=text,
        confidence=confidence,
        provenance=Provenance(
            engine=engine,
            text_confidence=confidence,
            layout_confidence=confidence,
        ),
        text_candidates=[TextCandidate(engine=engine, value=text, confidence=confidence)],
    )
    return Document(
        id=f"{engine}-doc",
        pages=[Page(id=f"{engine}-page", number=1, width=100, height=200, elements=[element])],
    )


def test_element_level_fusion_preserves_candidates_and_provenance() -> None:
    documents = [
        _evidence_document("paddleocr", "Total Revenue", 0.95),
        _evidence_document("mineru", "Total Revenue", 0.90),
        _evidence_document("olmocr", "Tota1 Revenue", 0.99),
    ]
    fused = fuse_documents(documents, document_id="ensemble")
    element = fused.pages[0].elements[0]

    assert fused.id == "ensemble"
    assert element.text == "Total Revenue"
    assert {candidate.engine for candidate in element.text_candidates} == {
        "paddleocr",
        "mineru",
        "olmocr",
    }
    assert element.provenance.engine == "ensemble"
    assert {source.engine for source in element.provenance.contributors} == {
        "paddleocr",
        "mineru",
        "olmocr",
    }
    assert element.metadata["fusion"]["source_element_ids"] == [
        "paddleocr-element",
        "mineru-element",
        "olmocr-element",
    ]

    configured = EvidenceFusion(iou_threshold=0.7, text_similarity_threshold=0.7)
    assert configured.fuse_documents(documents).pages[0].elements[0].text == "Total Revenue"
    assert configured.fuse(documents).pages[0].elements[0].text == "Total Revenue"
