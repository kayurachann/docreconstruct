from __future__ import annotations

import json
from pathlib import Path

import pytest

from docreconstruct.ir import BBox, Document, ElementType
from docreconstruct.providers import (
    AmazonTextractProvider,
    AWSTextractProvider,
    AwsTextractProvider,
    ProviderContext,
    ProviderCredentialRequirement,
    ProviderExecutionMode,
    ProviderInferenceUnsupportedError,
    ProviderInputError,
    ProviderPrivacy,
    TextractProvider,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_detect_document_text_preserves_text_geometry_and_block_graph() -> None:
    provider = AWSTextractProvider()
    document = provider.normalize(
        _fixture("aws_textract_detect_document_text.json"),
        context=ProviderContext(
            document_id="textract-detect",
            source="scan.png",
            page_width=1000,
            page_height=2000,
        ),
    )

    assert document.id == "textract-detect"
    assert document.source == "scan.png"
    assert document.metadata == {
        "provider": "aws_textract",
        "operation": "DetectDocumentText",
        "model_version": "1.0-test",
        "document_metadata": {"Pages": 1},
    }
    page = document.pages[0]
    assert (page.width, page.height) == (1000, 2000)
    assert page.metadata["coordinate_system"] == "context_scaled_normalized"
    assert [element.reading_order for element in page.elements] == list(range(len(page.elements)))

    line = next(element for element in page.elements if element.id == "detect-line-1")
    assert line.type is ElementType.TEXT
    assert line.text == "Hello AWS"
    assert line.bbox == BBox(x0=100, y0=400, x1=500, y1=500)
    assert [(point.x, point.y) for point in line.polygon] == [
        (100, 400),
        (500, 400),
        (500, 500),
        (100, 500),
    ]
    assert line.confidence == pytest.approx(0.985)
    assert line.relationships.children == ["detect-word-1", "detect-word-2"]
    assert line.metadata["text_types"] == ["PRINTED", "HANDWRITING"]
    assert line.metadata["printed"] is True
    assert line.metadata["handwriting"] is True

    printed = next(element for element in page.elements if element.id == "detect-word-1")
    handwritten = next(element for element in page.elements if element.id == "detect-word-2")
    assert printed.confidence == pytest.approx(0.995)
    assert printed.metadata["text_type"] == "PRINTED"
    assert printed.relationships.parent == line.id
    assert printed.provenance is not None
    assert printed.provenance.engine == "aws_textract"
    assert printed.provenance.source_id == "detect-word-1"
    assert printed.text_candidates[0].source_element_id == "detect-word-1"
    assert handwritten.metadata["text_type"] == "HANDWRITING"
    assert handwritten.metadata["handwriting"] is True


def test_analyze_document_normalizes_tables_forms_queries_signatures_and_layout() -> None:
    document = AWSTextractProvider().normalize(_fixture("aws_textract_analyze_document.json"))
    page = document.pages[0]
    elements = {element.id: element for element in page.elements}

    assert document.metadata["operation"] == "AnalyzeDocument"
    assert document.metadata["model_version"] == "1.0-test"
    assert (page.width, page.height) == (1, 1)
    assert page.metadata["coordinate_system"] == "normalized"

    table = elements["table-1"]
    assert table.type is ElementType.TABLE
    assert table.text is None
    assert table.bbox == BBox(x0=0.1, y0=0.2, x1=0.9, y1=0.5)
    assert table.metadata["rows"] == [["Item", "Amount"], ["Widget", "42"]]
    assert table.metadata["row_count"] == 2
    assert table.metadata["column_count"] == 2
    assert table.metadata["header_rows"] == 1
    assert table.metadata["table_entity_types"] == ["STRUCTURED_TABLE"]
    assert table.metadata["merged_cells"][0]["text"] == "Item Amount"
    assert table.relationships.children == ["cell-11", "cell-12", "cell-21", "cell-22"]
    assert table.relationships.references == [
        "merged-cell-1",
        "table-title-1",
        "table-footer-1",
    ]

    cell = elements["cell-22"]
    merged = elements["merged-cell-1"]
    assert cell.text == "42"
    assert cell.relationships.parent == "table-1"
    assert cell.metadata["row_index"] == 2
    assert cell.metadata["entity_types"] == ["TABLE_SUMMARY"]
    assert cell.metadata["handwriting"] is True
    assert merged.text == "Item Amount"
    assert merged.relationships.parent == "table-1"
    assert merged.relationships.children == ["cell-11", "cell-12"]
    assert merged.metadata["column_span"] == 2

    title = elements["table-title-1"]
    footer = elements["table-footer-1"]
    assert title.type is ElementType.TITLE
    assert title.text == "Charges"
    assert title.relationships.caption_of == "table-1"
    assert footer.type is ElementType.FOOTER
    assert footer.relationships.caption_of == "table-1"

    key = elements["key-name"]
    value = elements["value-name"]
    assert key.text == "Name:"
    assert key.metadata["key_value_role"] == "key"
    assert key.metadata["value_text"] == "Ada"
    assert key.relationships.references == ["value-name"]
    assert value.text == "Ada"
    assert value.metadata["key_value_role"] == "value"
    assert value.metadata["key_text"] == "Name:"
    assert value.relationships.references == ["key-name"]

    signature = elements["signature-1"]
    assert signature.type is ElementType.SIGNATURE
    assert signature.text is None
    assert signature.confidence == pytest.approx(0.88)

    query = elements["query-1"]
    answer = elements["query-result-1"]
    assert query.text == "Who is the customer?"
    assert query.bbox == BBox(x0=0, y0=0, x1=0, y1=0)
    assert query.metadata["coordinate_system"] == "unavailable"
    assert query.metadata["query_alias"] == "CUSTOMER"
    assert query.metadata["answers"] == [
        {"id": "query-result-1", "text": "Ada", "confidence": 0.97}
    ]
    assert query.relationships.references == ["query-result-1"]
    assert answer.metadata["query_text"] == "Who is the customer?"
    assert answer.metadata["query_alias"] == "CUSTOMER"
    assert answer.relationships.parent == "query-1"
    assert answer.relationships.references == ["query-1"]

    selection = elements["selection-1"]
    assert selection.type is ElementType.CHECKBOX
    assert selection.text == "☒"
    assert selection.metadata["selected"] is True

    expected_layout_types = {
        "layout-title": (ElementType.TITLE, "title"),
        "layout-header": (ElementType.HEADER, "header"),
        "layout-section": (ElementType.HEADING, "section_header"),
        "layout-text": (ElementType.PARAGRAPH, "text"),
        "layout-list": (ElementType.LIST_ITEM, "list"),
        "layout-figure": (ElementType.FIGURE, "figure"),
        "layout-table": (ElementType.TABLE, "table"),
        "layout-key-value": (ElementType.TEXT, "key_value"),
        "layout-footer": (ElementType.FOOTER, "footer"),
        "layout-page-number": (ElementType.PAGE_NUMBER, "page_number"),
    }
    for element_id, (expected_type, layout_type) in expected_layout_types.items():
        assert elements[element_id].type is expected_type
        assert elements[element_id].metadata["layout_type"] == layout_type
    layout_elements = [
        element for element in page.elements if element.metadata["block_type"].startswith("LAYOUT_")
    ]
    assert [element.reading_order for element in layout_elements] == sorted(
        element.reading_order for element in layout_elements if element.reading_order is not None
    )
    assert elements["layout-title"].text == "Sample Invoice"
    assert elements["layout-figure"].text is None
    assert elements["layout-page-number"].text == "1"

    assert Document.model_validate_json(document.model_dump_json()) == document


def test_textract_provider_is_saved_only_and_exports_common_aliases(tmp_path: Path) -> None:
    assert AWSTextractProvider is AwsTextractProvider
    assert AWSTextractProvider is AmazonTextractProvider
    assert AWSTextractProvider is TextractProvider

    capabilities = AWSTextractProvider().capabilities
    assert capabilities.execution_modes == [ProviderExecutionMode.SAVED]
    assert capabilities.saved_json is True
    assert capabilities.live_inference is False
    assert capabilities.credentials is ProviderCredentialRequirement.NONE
    assert capabilities.privacy is ProviderPrivacy.NO_TRANSFER
    assert capabilities.tables is True
    assert capabilities.layout is True
    assert capabilities.handwriting is True

    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF-test")
    with pytest.raises(ProviderInferenceUnsupportedError, match="not bundled"):
        AWSTextractProvider().parse(source)


def test_textract_parse_reads_offline_fixture_and_uses_aws_percentage_confidence() -> None:
    result = AWSTextractProvider().parse(FIXTURES / "aws_textract_detect_document_text.json")
    assert result.provider == "aws_textract"
    assert result.document.source == str(FIXTURES / "aws_textract_detect_document_text.json")

    one_percent = AWSTextractProvider().normalize(
        {
            "Blocks": [
                {"BlockType": "PAGE", "Id": "page", "Page": 1},
                {
                    "BlockType": "WORD",
                    "Id": "word",
                    "Page": 1,
                    "Text": "low confidence",
                    "Confidence": 1.0,
                },
            ]
        }
    )
    assert one_percent.pages[0].elements[0].confidence == pytest.approx(0.01)


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"Blocks": "not-an-array"}, {"Blocks": ["not-a-block"]}],
)
def test_textract_rejects_non_response_shapes(payload: object) -> None:
    with pytest.raises(ProviderInputError):
        AWSTextractProvider().normalize(payload)
