from __future__ import annotations

from pathlib import Path
from typing import get_type_hints

import pytest
from PIL import Image
from pydantic import ValidationError

import docreconstruct.reconstruction.evidence_matching as evidence_matching
from docreconstruct.evidence import (
    DetectionCandidate,
    SidecarDetection,
    SidecarEvidence,
    SidecarEvidenceBundle,
)
from docreconstruct.ir import (
    BBox,
    Document,
    Element,
    ElementStyle,
    ElementType,
    Page,
    Provenance,
)
from docreconstruct.providers.markdown import MarkdownEvidenceProvider
from docreconstruct.reconstruction.evidence_matching import (
    EvidenceMatch,
    match_sidecar_evidence,
)
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


def _content(*blocks: tuple[MarkdownBlockKind, str, list[list[str]] | None]) -> MarkdownContent:
    return MarkdownContent(
        source="authority.md",
        blocks=[
            MarkdownBlock(
                id=f"md-{index + 1}",
                index=index,
                kind=kind,
                text=text,
                table_rows=rows or [],
            )
            for index, (kind, text, rows) in enumerate(blocks)
        ],
    )


def _layout(width: int = 1000, height: int = 2000) -> ScanDocumentLayout:
    page = ScanPageLayout(
        number=1,
        width=width,
        height=height,
        pdf_width=500,
        pdf_height=1000,
        content_bbox=PixelBox(x0=0, y0=0, x1=width, y1=height),
        line_pitch=40,
        image=Image.new("RGB", (width, height), "white"),
    )
    return ScanDocumentLayout(source="scan.png", pages=[page])


def _multi_page_layout(
    *, pages: int = 2, width: int = 1000, height: int = 2000
) -> ScanDocumentLayout:
    return ScanDocumentLayout(
        source="scan.pdf",
        pages=[
            ScanPageLayout(
                number=number,
                width=width,
                height=height,
                pdf_width=500,
                pdf_height=1000,
                content_bbox=PixelBox(x0=0, y0=0, x1=width, y1=height),
                line_pitch=40,
                image=Image.new("RGB", (width, height), "white"),
            )
            for number in range(1, pages + 1)
        ],
    )


def _document(
    provider: str,
    elements: list[Element],
    *,
    width: float = 1000,
    height: float = 2000,
    rotation: float = 0,
    page_metadata: dict[str, object] | None = None,
) -> Document:
    return Document(
        id=f"{provider}-document",
        pages=[
            Page(
                id="page-1",
                number=1,
                width=width,
                height=height,
                rotation=rotation,
                elements=elements,
                metadata=page_metadata or {},
            )
        ],
        metadata={"provider": provider},
    )


def _element(
    element_id: str,
    text: str | None,
    bbox: tuple[float, float, float, float],
    *,
    provider: str,
    kind: ElementType = ElementType.TEXT,
    order: int = 0,
    confidence: float = 0.9,
    style: ElementStyle | None = None,
    metadata: dict[str, object] | None = None,
) -> Element:
    return Element(
        id=element_id,
        type=kind,
        bbox=BBox(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3]),
        text=text,
        reading_order=order,
        confidence=confidence,
        style=style or ElementStyle(),
        provenance=Provenance(
            engine=provider,
            source_id=element_id,
            text_confidence=confidence,
            layout_confidence=confidence,
        ),
        metadata=metadata or {},
    )


def _bundle(provider: str, document: Document) -> SidecarEvidenceBundle:
    candidate = DetectionCandidate(provider=provider, confidence=1.0, reason="test")
    detection = SidecarDetection(
        provider=provider,
        confidence=1.0,
        reason="test",
        candidates=(candidate,),
        explicit=True,
    )
    return SidecarEvidenceBundle(
        items=(
            SidecarEvidence(
                path=Path(f"{provider}.json"),
                provider=provider,
                detection=detection,
                document=document,
            ),
        )
    )


def test_bundle_geometry_maps_and_clips_to_scan_pixels_with_typed_style() -> None:
    content = _content((MarkdownBlockKind.PARAGRAPH, "Quarterly report", None))
    document = _document(
        "azure_document_intelligence",
        [
            _element(
                "paragraph-1",
                "Quarterly report",
                (-10, 20, 50, 40),
                provider="azure_document_intelligence",
                kind=ElementType.PARAGRAPH,
                style=ElementStyle(font_family="Arial", font_size=12, font_weight=700),
            )
        ],
        width=100,
        height=200,
    )

    matches = match_sidecar_evidence(
        content,
        _layout(),
        _bundle("azure_document_intelligence", document),
    )

    assert len(matches) == 1
    match = matches[0]
    assert match.source_bbox == PixelBox(x0=0, y0=200, x1=500, y1=400)
    assert match.bbox == match.source_bbox
    assert match.source_rows == [match.source_bbox]
    assert match.providers == ("azure_document_intelligence",)
    assert match.element_ids == ("paragraph-1",)
    assert match.style == ElementStyle(font_family="Arial", font_size=12, font_weight=700)
    assert match.geometry_source == "json_consensus"
    assert match.score == match.match_score
    assert match.confidence == pytest.approx(0.9)
    assert "geometry clipped" in match.warnings[0]
    with pytest.raises(ValidationError):
        EvidenceMatch.model_validate({**match.model_dump(), "unexpected": True})


def test_multi_provider_consensus_rejects_outlier_but_retains_disagreement() -> None:
    content = _content((MarkdownBlockKind.HEADING, "Results", None))
    documents = [
        _document(
            "provider_a",
            [
                _element(
                    "a-title",
                    "Results",
                    (100, 200, 500, 300),
                    provider="provider_a",
                    kind=ElementType.TITLE,
                    confidence=0.9,
                    style=ElementStyle(font_family="Arial", font_weight=700),
                )
            ],
        ),
        _document(
            "provider_b",
            [
                _element(
                    "b-title",
                    "Results",
                    (105, 205, 505, 305),
                    provider="provider_b",
                    kind=ElementType.HEADING,
                    confidence=0.95,
                    style=ElementStyle(font_family="Arial", font_weight=700),
                )
            ],
        ),
        _document(
            "provider_c",
            [
                _element(
                    "c-title",
                    "Results",
                    (600, 1000, 900, 1100),
                    provider="provider_c",
                    kind=ElementType.TITLE,
                    confidence=0.99,
                    style=ElementStyle(font_family="Courier New", font_weight=400),
                )
            ],
        ),
    ]

    match = match_sidecar_evidence(content, _layout(), documents)[0]

    assert 100 <= match.source_bbox.x0 <= 105
    assert 200 <= match.source_bbox.y0 <= 205
    assert match.source_bbox.x1 <= 505
    assert match.providers == ("provider_a", "provider_b", "provider_c")
    assert set(match.element_ids) == {"a-title", "b-title", "c-title"}
    assert match.style is not None
    assert match.style.font_family == "Arial"
    assert match.conflict is True
    assert any("outlier" in warning for warning in match.warnings)
    contributors = match.style_metadata["contributors"]
    assert sum(item["selected_for_geometry"] for item in contributors) == 2


def test_mismatched_text_and_incompatible_page_dimensions_are_rejected() -> None:
    content = _content((MarkdownBlockKind.PARAGRAPH, "Expected authority text", None))
    mismatch = _document(
        "provider",
        [
            _element(
                "wrong-text",
                "Completely unrelated material",
                (100, 200, 500, 300),
                provider="provider",
            )
        ],
    )
    wrong_dimensions = _document(
        "provider",
        [
            _element(
                "wrong-shape",
                "Expected authority text",
                (10, 10, 90, 30),
                provider="provider",
            )
        ],
        width=100,
        height=100,
    )

    assert match_sidecar_evidence(content, _layout(), mismatch) == []
    assert match_sidecar_evidence(content, _layout(), wrong_dimensions) == []


def test_synthetic_markdown_geometry_is_ignored_when_real_evidence_exists() -> None:
    content = _content((MarkdownBlockKind.PARAGRAPH, "Authoritative text", None))
    synthetic = MarkdownEvidenceProvider().normalize(content)
    real = _document(
        "provider",
        [
            _element(
                "real",
                "Authoritative text",
                (100, 200, 500, 300),
                provider="provider",
            )
        ],
    )

    match = match_sidecar_evidence(content, _layout(), [synthetic, real])[0]

    assert match.providers == ("provider",)
    assert match.element_ids == ("real",)


def test_contiguous_provider_lines_form_one_markdown_block_and_two_source_rows() -> None:
    content = _content((MarkdownBlockKind.PARAGRAPH, "First half second half", None))
    document = _document(
        "line_provider",
        [
            _element(
                "line-1",
                "First half",
                (100, 200, 500, 250),
                provider="line_provider",
                order=0,
            ),
            _element(
                "line-2",
                "second half",
                (100, 270, 520, 320),
                provider="line_provider",
                order=1,
            ),
        ],
    )

    match = match_sidecar_evidence(content, _layout(), document)[0]

    assert match.element_ids == ("line-1", "line-2")
    assert match.source_bbox == PixelBox(x0=100, y0=200, x1=520, y1=320)
    assert match.source_rows == [
        PixelBox(x0=100, y0=200, x1=500, y1=250),
        PixelBox(x0=100, y0=270, x1=520, y1=320),
    ]
    assert match.match_score > 0.9


def test_complete_offset_page_sequence_maps_to_layout_pages_by_ordinal() -> None:
    content = _content(
        (MarkdownBlockKind.PARAGRAPH, "First cropped page", None),
        (MarkdownBlockKind.PARAGRAPH, "Second cropped page", None),
    )
    document = Document(
        id="offset-pages",
        pages=[
            Page(
                id="source-page-5",
                number=5,
                width=1000,
                height=2000,
                elements=[
                    _element(
                        "page-5-text",
                        "First cropped page",
                        (100, 200, 500, 300),
                        provider="provider",
                    )
                ],
            ),
            Page(
                id="source-page-6",
                number=6,
                width=1000,
                height=2000,
                elements=[
                    _element(
                        "page-6-text",
                        "Second cropped page",
                        (100, 400, 500, 500),
                        provider="provider",
                    )
                ],
            ),
        ],
        metadata={"provider": "provider"},
    )

    matches = match_sidecar_evidence(content, _multi_page_layout(), document)

    assert [match.page_number for match in matches] == [1, 2]
    assert [match.element_ids for match in matches] == [("page-5-text",), ("page-6-text",)]
    assert all(
        any("mapped by complete ordinal sequence" in warning for warning in match.warnings)
        for match in matches
    )


def test_irregular_or_partial_page_sequences_are_not_remapped_by_ordinal() -> None:
    content = _content(
        (MarkdownBlockKind.PARAGRAPH, "Irregular exact page", None),
        (MarkdownBlockKind.PARAGRAPH, "Ambiguous missing page", None),
    )
    document = Document(
        id="irregular-pages",
        pages=[
            Page(
                id="source-page-1",
                number=1,
                width=1000,
                height=2000,
                elements=[
                    _element(
                        "page-1-text",
                        "Irregular exact page",
                        (100, 200, 500, 300),
                        provider="provider",
                    )
                ],
            ),
            Page(
                id="source-page-3",
                number=3,
                width=1000,
                height=2000,
                elements=[
                    _element(
                        "page-3-text",
                        "Ambiguous missing page",
                        (100, 400, 500, 500),
                        provider="provider",
                    )
                ],
            ),
        ],
        metadata={"provider": "provider"},
    )

    matches = match_sidecar_evidence(content, _multi_page_layout(), document)

    assert [match.block_id for match in matches] == ["md-1"]
    assert matches[0].page_number == 1
    assert not any(
        "mapped by complete ordinal sequence" in warning for warning in matches[0].warnings
    )


def test_monotonic_matching_supports_headings_equations_and_tables() -> None:
    content = _content(
        (MarkdownBlockKind.HEADING, "Results", None),
        (MarkdownBlockKind.EQUATION, r"\frac{1}{2}", None),
        (MarkdownBlockKind.TABLE, "", [["A", "B"], ["1", "2"]]),
    )
    document = _document(
        "structured_provider",
        [
            _element(
                "heading",
                "Results",
                (100, 100, 500, 170),
                provider="structured_provider",
                kind=ElementType.TITLE,
                order=0,
            ),
            _element(
                "formula",
                None,
                (200, 220, 400, 300),
                provider="structured_provider",
                kind=ElementType.FORMULA,
                order=1,
                metadata={"latex": r"\frac{1}{2}"},
            ),
            _element(
                "table",
                None,
                (100, 350, 900, 700),
                provider="structured_provider",
                kind=ElementType.TABLE,
                order=2,
                metadata={"rows": [["A", "B"], ["1", "2"]]},
            ),
        ],
    )

    matches = match_sidecar_evidence(content, _layout(), document)

    assert [match.block_id for match in matches] == ["md-1", "md-2", "md-3"]
    assert [match.element_ids for match in matches] == [
        ("heading",),
        ("formula",),
        ("table",),
    ]
    assert [match.source_bbox.y0 for match in matches] == [100, 220, 350]


def test_textless_json_image_geometry_matches_markdown_asset_deterministically() -> None:
    content = MarkdownContent(
        source="authority.md",
        blocks=[
            MarkdownBlock(
                id="md-image",
                index=0,
                kind=MarkdownBlockKind.IMAGE,
                source="https://expired.example/figures/chart-2026.png?signature=expired",
            )
        ],
    )
    document = _document(
        "json_provider",
        [
            _element(
                "unrelated-image",
                None,
                (50, 100, 300, 300),
                provider="json_provider",
                kind=ElementType.IMAGE,
                order=0,
                metadata={"image": {"path": "assets/photo.png"}},
            ),
            _element(
                "same-filename",
                None,
                (50, 350, 600, 450),
                provider="json_provider",
                kind=ElementType.FIGURE,
                order=1,
                metadata={"image": {"path": "other/chart-2026.png"}},
            ),
            _element(
                "chart-image",
                None,
                (100, 500, 900, 1200),
                provider="json_provider",
                kind=ElementType.CHART,
                order=2,
                metadata={"image": {"path": "figures/chart-2026.png"}},
            ),
        ],
    )

    match = match_sidecar_evidence(content, _layout(), document)[0]

    assert match.block_id == "md-image"
    assert match.element_ids == ("chart-image",)
    assert match.source_bbox == PixelBox(x0=100, y0=500, x1=900, y1=1200)
    assert match.source_rows == [match.source_bbox]
    assert match.match_score > 0.95


def test_html_chart_src_matches_markdown_image_on_its_normalized_page() -> None:
    filename = "img_in_chart_box_838_409_1121_630.jpg"
    content = MarkdownContent(
        source="authority.md",
        blocks=[
            MarkdownBlock(
                id="md-chart",
                index=0,
                kind=MarkdownBlockKind.IMAGE,
                source=(
                    "https://assets.invalid/export/markdown_2/imgs/"
                    f"{filename}?authorization=expired"
                ),
            )
        ],
    )
    document = Document(
        id="paddle-pages",
        pages=[
            Page(id="page-1", number=1, width=500, height=1000, elements=[]),
            Page(id="page-2", number=2, width=500, height=1000, elements=[]),
            Page(
                id="page-3",
                number=3,
                width=500,
                height=1000,
                elements=[
                    _element(
                        "unrelated-figure",
                        '<div><img src="imgs/other-figure.jpg" alt="Image" /></div>',
                        (20, 40, 120, 140),
                        provider="paddleocr",
                        kind=ElementType.FIGURE,
                        order=0,
                    ),
                    _element(
                        "chart-12",
                        f'<div><img src="imgs/{filename}" alt="Image" /></div>',
                        (100, 200, 300, 400),
                        provider="paddleocr",
                        kind=ElementType.CHART,
                        order=1,
                    ),
                ],
            ),
        ],
        metadata={"provider": "paddleocr"},
    )

    matches = match_sidecar_evidence(
        content,
        _multi_page_layout(pages=3),
        document,
    )

    assert len(matches) == 1
    match = matches[0]
    assert match.block_id == "md-chart"
    assert match.page_number == 3
    assert match.element_ids == ("chart-12",)
    assert match.source_bbox == PixelBox(x0=200, y0=400, x1=600, y1=800)
    assert match.source_rows == [match.source_bbox]
    assert match.match_score > 0.95


def test_section_heading_shares_exact_provider_line_with_next_group() -> None:
    heading = "PART II. True or false questions."
    question = (
        "Question 1: A sufficiently long prompt follows the section heading "
        "on the same source line and remains the editable content authority."
    )
    content = MarkdownContent(
        source="authority.md",
        blocks=[
            MarkdownBlock(
                id="page-one",
                index=0,
                kind=MarkdownBlockKind.PARAGRAPH,
                text="End of page one",
            ),
            MarkdownBlock(
                id="section-heading",
                index=1,
                kind=MarkdownBlockKind.HEADING,
                text=heading,
                level=1,
            ),
            MarkdownBlock(
                id="question-one",
                index=2,
                kind=MarkdownBlockKind.PARAGRAPH,
                text=question,
                group_id="section-1:question 1:",
                starts_group=True,
            ),
        ],
    )
    document = Document(
        id="combined-heading-line",
        pages=[
            Page(
                id="provider-page-1",
                number=1,
                width=1000,
                height=2000,
                elements=[
                    _element(
                        "page-one-text",
                        "End of page one",
                        (100, 1700, 500, 1750),
                        provider="paddleocr",
                    )
                ],
            ),
            Page(
                id="provider-page-2",
                number=2,
                width=1000,
                height=2000,
                elements=[
                    _element(
                        "combined-line",
                        f"{heading} {question}",
                        (100, 100, 900, 180),
                        provider="paddleocr",
                    )
                ],
            ),
        ],
        metadata={"provider": "paddleocr"},
    )

    matches = match_sidecar_evidence(content, _multi_page_layout(), document)
    by_block = {match.block_id: match for match in matches}

    assert set(by_block) == {"page-one", "section-heading", "question-one"}
    heading_match = by_block["section-heading"]
    question_match = by_block["question-one"]
    assert heading_match.page_number == question_match.page_number == 2
    assert heading_match.element_ids == question_match.element_ids == ("combined-line",)
    assert heading_match.source_bbox == question_match.source_bbox
    assert any("shares one exact provider line" in item for item in heading_match.warnings)


def test_exact_combined_provider_units_recover_only_leading_text_owners() -> None:
    header_blocks = [
        MarkdownBlock(
            id="teacher",
            index=1,
            kind=MarkdownBlockKind.HEADING,
            text="TEACHER NGUYEN VAN A",
            level=1,
        ),
        MarkdownBlock(
            id="test-title",
            index=2,
            kind=MarkdownBlockKind.HEADING,
            text="PRACTICE TEST 05",
            level=1,
        ),
        MarkdownBlock(
            id="page-note",
            index=3,
            kind=MarkdownBlockKind.PARAGRAPH,
            text="This test contains four pages",
        ),
    ]
    question = MarkdownBlock(
        id="question",
        index=4,
        kind=MarkdownBlockKind.PARAGRAPH,
        text="Question 2: Choose the correct statement.",
        group_id="question-2",
        starts_group=True,
    )
    options = [
        MarkdownBlock(
            id=f"option-{label.casefold()}",
            index=5 + offset,
            kind=MarkdownBlockKind.OPTION,
            text=f"{label}. " + phrase,
            group_id="question-2",
        )
        for offset, (label, phrase) in enumerate(
            (
                ("A", "The first deliberately long answer remains editable text."),
                ("B", "The second deliberately long answer remains editable text."),
                ("C", "The third deliberately long answer remains editable text."),
                ("D", "The fourth deliberately long answer remains editable text."),
            )
        )
    ]
    content = MarkdownContent(
        source="authority.md",
        blocks=[
            MarkdownBlock(
                id="before",
                index=0,
                kind=MarkdownBlockKind.PARAGRAPH,
                text="Top anchor",
            ),
            *header_blocks,
            question,
            *options,
            MarkdownBlock(
                id="after",
                index=9,
                kind=MarkdownBlockKind.PARAGRAPH,
                text="Bottom anchor",
            ),
        ],
    )
    header_text = " ".join(block.text for block in header_blocks)
    question_text = " ".join(block.text for block in (question, *options))
    document = _document(
        "paddleocr",
        [
            _element("before-unit", "Top anchor", (100, 100, 400, 150), provider="paddleocr"),
            _element(
                "header-stack",
                header_text,
                (100, 200, 500, 380),
                provider="paddleocr",
            ),
            _element(
                "question-stack",
                question_text,
                (100, 450, 900, 850),
                provider="paddleocr",
            ),
            _element(
                "after-unit",
                "Bottom anchor",
                (100, 900, 400, 950),
                provider="paddleocr",
            ),
        ],
    )

    matches = match_sidecar_evidence(content, _layout(), document)
    by_block = {match.block_id: match for match in matches}

    assert set(by_block) == {
        "before",
        "teacher",
        "test-title",
        "page-note",
        "question",
        "after",
    }
    for block in header_blocks:
        assert by_block[block.id].element_ids == ("header-stack",)
        assert by_block[block.id].source_bbox == PixelBox(x0=100, y0=200, x1=500, y1=380)
    assert by_block["question"].element_ids == ("question-stack",)
    assert all(option.id not in by_block for option in options)


def test_combined_provider_unit_recovers_exact_trailing_byline_suffix() -> None:
    body_text = (
        "The final editable stanza keeps every long authority line in order, "
        "including one deliberately different spelling near the ending."
    )
    byline = "PHI-YEN"
    content = MarkdownContent(
        source="authority.md",
        blocks=[
            MarkdownBlock(
                id="body",
                index=0,
                kind=MarkdownBlockKind.PARAGRAPH,
                text=body_text,
            ),
            MarkdownBlock(
                id="byline",
                index=1,
                kind=MarkdownBlockKind.HEADING,
                text=byline,
                level=2,
            ),
        ],
    )
    provider_text = (
        "The final editable stanza keeps every long authority line in order, "
        "including one deliberately divergent spelling near the ending. "
        f"{byline}"
    )
    document = _document(
        "mineru",
        [
            _element(
                "combined-final-stanza",
                provider_text,
                (700, 1400, 950, 1900),
                provider="mineru",
            )
        ],
    )

    matches = match_sidecar_evidence(content, _layout(), document)
    by_block = {match.block_id: match for match in matches}

    assert set(by_block) == {"body", "byline"}
    assert (
        by_block["body"].element_ids == by_block["byline"].element_ids == ("combined-final-stanza",)
    )
    assert by_block["byline"].source_bbox == PixelBox(
        x0=700,
        y0=1400,
        x1=950,
        y1=1900,
    )


def test_combined_provider_unit_rejects_repeated_location_ambiguity() -> None:
    content = MarkdownContent(
        source="authority.md",
        blocks=[
            MarkdownBlock(
                id="before",
                index=0,
                kind=MarkdownBlockKind.PARAGRAPH,
                text="Before repeated stack",
            ),
            MarkdownBlock(
                id="heading-a",
                index=1,
                kind=MarkdownBlockKind.HEADING,
                text="UNIQUE HEADING ALPHA",
                level=1,
            ),
            MarkdownBlock(
                id="heading-b",
                index=2,
                kind=MarkdownBlockKind.HEADING,
                text="UNIQUE HEADING BETA",
                level=1,
            ),
            MarkdownBlock(
                id="heading-c",
                index=3,
                kind=MarkdownBlockKind.HEADING,
                text="UNIQUE HEADING GAMMA",
                level=1,
            ),
            MarkdownBlock(
                id="after",
                index=4,
                kind=MarkdownBlockKind.PARAGRAPH,
                text="After repeated stack",
            ),
        ],
    )
    combined = "UNIQUE HEADING ALPHA UNIQUE HEADING BETA UNIQUE HEADING GAMMA"
    document = _document(
        "provider",
        [
            _element(
                "before-unit",
                "Before repeated stack",
                (100, 100, 500, 150),
                provider="provider",
            ),
            _element(
                "stack-one",
                combined,
                (100, 250, 600, 350),
                provider="provider",
            ),
            _element(
                "stack-two",
                combined,
                (100, 450, 600, 550),
                provider="provider",
            ),
            _element(
                "after-unit",
                "After repeated stack",
                (100, 650, 500, 700),
                provider="provider",
            ),
        ],
    )

    matches = match_sidecar_evidence(content, _layout(), document)

    assert [match.block_id for match in matches] == ["before", "after"]


def test_combined_provider_unit_rejects_reversed_anchor_order() -> None:
    content = MarkdownContent(
        source="authority.md",
        blocks=[
            MarkdownBlock(
                id="before",
                index=0,
                kind=MarkdownBlockKind.PARAGRAPH,
                text="Before combined stack",
            ),
            MarkdownBlock(
                id="heading-a",
                index=1,
                kind=MarkdownBlockKind.HEADING,
                text="ORDERED HEADING ALPHA",
                level=1,
            ),
            MarkdownBlock(
                id="heading-b",
                index=2,
                kind=MarkdownBlockKind.HEADING,
                text="ORDERED HEADING BETA",
                level=1,
            ),
            MarkdownBlock(
                id="after",
                index=3,
                kind=MarkdownBlockKind.PARAGRAPH,
                text="After combined stack",
            ),
        ],
    )
    document = _document(
        "provider",
        [
            _element(
                "reversed-stack",
                "ORDERED HEADING ALPHA ORDERED HEADING BETA",
                (100, 50, 600, 100),
                provider="provider",
            ),
            _element(
                "before-unit",
                "Before combined stack",
                (100, 150, 500, 200),
                provider="provider",
            ),
            _element(
                "after-unit",
                "After combined stack",
                (100, 650, 500, 700),
                provider="provider",
            ),
        ],
    )

    matches = match_sidecar_evidence(content, _layout(), document)

    assert [match.block_id for match in matches] == ["before", "after"]


def test_textless_visual_order_fallback_is_unique_or_rejected_as_ambiguous() -> None:
    content = MarkdownContent(
        source="authority.md",
        blocks=[
            MarkdownBlock(
                id="md-image",
                index=0,
                kind=MarkdownBlockKind.IMAGE,
                source="expired-asset.png",
            )
        ],
    )
    one_visual = _document(
        "json_provider",
        [
            _element(
                "only-figure",
                None,
                (100, 400, 900, 1000),
                provider="json_provider",
                kind=ElementType.FIGURE,
            )
        ],
    )
    ambiguous_visuals = _document(
        "json_provider",
        [
            _element(
                "figure-a",
                None,
                (100, 100, 900, 500),
                provider="json_provider",
                kind=ElementType.FIGURE,
                order=0,
            ),
            _element(
                "figure-b",
                None,
                (100, 700, 900, 1100),
                provider="json_provider",
                kind=ElementType.FIGURE,
                order=1,
            ),
        ],
    )

    match = match_sidecar_evidence(content, _layout(), one_visual)[0]

    assert match.element_ids == ("only-figure",)
    assert match.match_score < 0.8
    assert any("unique monotonic position" in warning for warning in match.warnings)
    assert match_sidecar_evidence(content, _layout(), ambiguous_visuals) == []


def test_out_of_order_exact_visual_does_not_displace_text_matches() -> None:
    content = MarkdownContent(
        source="authority.md",
        blocks=[
            MarkdownBlock(
                id="before",
                index=0,
                kind=MarkdownBlockKind.PARAGRAPH,
                text="Before image",
            ),
            MarkdownBlock(
                id="image",
                index=1,
                kind=MarkdownBlockKind.IMAGE,
                source="figure.png",
            ),
            MarkdownBlock(
                id="after",
                index=2,
                kind=MarkdownBlockKind.PARAGRAPH,
                text="After image",
            ),
        ],
    )
    out_of_order = _document(
        "json_provider",
        [
            _element(
                "figure",
                None,
                (100, 100, 900, 500),
                provider="json_provider",
                kind=ElementType.FIGURE,
                order=0,
                metadata={"image": {"path": "figure.png"}},
            ),
            _element(
                "before-text",
                "Before image",
                (100, 600, 900, 700),
                provider="json_provider",
                order=1,
            ),
            _element(
                "after-text",
                "After image",
                (100, 800, 900, 900),
                provider="json_provider",
                order=2,
            ),
        ],
    )

    matches = match_sidecar_evidence(content, _layout(), out_of_order)

    assert [match.block_id for match in matches] == ["before", "after"]
    assert [match.element_ids for match in matches] == [
        ("before-text",),
        ("after-text",),
    ]


def test_exact_visual_identity_overrides_explicitly_unreliable_order_on_same_page() -> None:
    content = MarkdownContent(
        source="authority.md",
        blocks=[
            MarkdownBlock(
                id="before",
                index=0,
                kind=MarkdownBlockKind.PARAGRAPH,
                text="Before side chart",
            ),
            MarkdownBlock(
                id="chart",
                index=1,
                kind=MarkdownBlockKind.IMAGE,
                source="https://assets.invalid/markdown_2/imgs/side-chart.jpg?expired=1",
            ),
            MarkdownBlock(
                id="after",
                index=2,
                kind=MarkdownBlockKind.PARAGRAPH,
                text="After side chart",
            ),
        ],
    )
    document = _document(
        "paddleocr",
        [
            _element(
                "side-chart",
                '<div><img src="imgs/side-chart.jpg" alt="Image" /></div>',
                (600, 100, 900, 500),
                provider="paddleocr",
                kind=ElementType.CHART,
                order=0,
                metadata={"reading_order_reliable": False},
            ),
            _element(
                "before-text",
                "Before side chart",
                (100, 200, 500, 300),
                provider="paddleocr",
                order=1,
            ),
            _element(
                "after-text",
                "After side chart",
                (100, 400, 500, 500),
                provider="paddleocr",
                order=2,
            ),
        ],
    )

    matches = match_sidecar_evidence(content, _layout(), document)
    by_block = {match.block_id: match for match in matches}

    assert set(by_block) == {"before", "chart", "after"}
    assert by_block["chart"].element_ids == ("side-chart",)
    assert by_block["chart"].source_bbox == PixelBox(x0=600, y0=100, x1=900, y1=500)
    assert any(
        "overrides provider fallback order" in warning for warning in by_block["chart"].warnings
    )


@pytest.mark.parametrize(
    ("rotation", "width", "height", "bbox", "expected"),
    [
        (90, 200, 100, (20, 10, 60, 40), PixelBox(x0=600, y0=200, x1=900, y1=600)),
        (
            180,
            100,
            200,
            (10, 20, 40, 60),
            PixelBox(x0=600, y0=1400, x1=900, y1=1800),
        ),
        (
            270,
            200,
            100,
            (20, 10, 60, 40),
            PixelBox(x0=100, y0=1400, x1=400, y1=1800),
        ),
    ],
)
def test_explicit_orthogonal_page_rotation_maps_to_scan_pixels(
    rotation: float,
    width: float,
    height: float,
    bbox: tuple[float, float, float, float],
    expected: PixelBox,
) -> None:
    content = _content((MarkdownBlockKind.PARAGRAPH, "Rotated text", None))
    document = _document(
        "rotated_provider",
        [
            _element(
                "rotated-text",
                "Rotated text",
                bbox,
                provider="rotated_provider",
            )
        ],
        width=width,
        height=height,
        rotation=rotation,
    )

    match = match_sidecar_evidence(content, _layout(), document)[0]

    assert match.source_bbox == expected


def test_ambiguous_nonorthogonal_page_rotation_is_not_guessed() -> None:
    content = _content((MarkdownBlockKind.PARAGRAPH, "Skewed text", None))
    document = _document(
        "rotated_provider",
        [
            _element(
                "skewed-text",
                "Skewed text",
                (100, 200, 500, 300),
                provider="rotated_provider",
            )
        ],
        rotation=45,
    )

    assert match_sidecar_evidence(content, _layout(), document) == []


def test_evidence_match_rejects_coerced_or_nonpositive_nested_pixel_boxes() -> None:
    content = _content((MarkdownBlockKind.PARAGRAPH, "Strict geometry", None))
    document = _document(
        "provider",
        [
            _element(
                "strict-text",
                "Strict geometry",
                (100, 200, 500, 300),
                provider="provider",
            )
        ],
    )
    match = match_sidecar_evidence(content, _layout(), document)[0]
    payload = match.model_dump()

    with pytest.raises(ValidationError):
        EvidenceMatch.model_validate(
            {**payload, "source_bbox": {"x0": "100", "y0": 200, "x1": 500, "y1": 300}}
        )
    with pytest.raises(ValidationError):
        EvidenceMatch.model_validate(
            {**payload, "source_bbox": PixelBox(x0=10, y0=10, x1=5, y1=20)}
        )
    with pytest.raises(ValidationError):
        EvidenceMatch.model_validate(
            {**payload, "source_rows": [PixelBox(x0=10, y0=10, x1=20, y1=5)]}
        )


def test_public_evidence_type_hints_resolve_without_import_cycles() -> None:
    hints = get_type_hints(match_sidecar_evidence)

    assert "EvidenceBundleLike" in str(hints["evidence"])
    assert hints["return"] == list[EvidenceMatch]


def test_unverified_exact_anchors_retry_with_exhaustive_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _content(
        (MarkdownBlockKind.PARAGRAPH, "first exact anchor", None),
        (MarkdownBlockKind.PARAGRAPH, "second exact anchor", None),
    )
    document = _document(
        "fallback-provider",
        [
            _element(
                "fallback-1",
                "first exact anchor",
                (100, 100, 700, 150),
                provider="fallback-provider",
            ),
            _element(
                "fallback-2",
                "second exact anchor",
                (100, 200, 700, 250),
                provider="fallback-provider",
            ),
        ],
    )
    alignment_modes: list[bool] = []
    original_align_source = evidence_matching._align_source

    def counting_align_source(*args: object, **kwargs: object) -> list[object]:
        alignment_modes.append(bool(kwargs.get("exhaustive", False)))
        return original_align_source(*args, **kwargs)  # type: ignore[arg-type,return-value]

    monkeypatch.setattr(evidence_matching, "_align_source", counting_align_source)
    monkeypatch.setattr(
        evidence_matching._TextCandidateIndex,
        "alignment_respects_anchors",
        lambda _self, _aligned: False,
    )

    matches = match_sidecar_evidence(content, _layout(), document)

    assert [match.block_id for match in matches] == ["md-1", "md-2"]
    assert alignment_modes == [False, True]


def test_indexed_candidates_match_exhaustive_output_and_bound_similarity_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocks: list[MarkdownBlock] = []
    elements: list[Element] = []
    authority_index = 0
    reading_order = 0
    for text_index in range(12):
        blocks.append(
            MarkdownBlock(
                id=f"latency-block-{text_index}",
                index=authority_index,
                kind=MarkdownBlockKind.PARAGRAPH,
                text=f"authority phrase {text_index:02d} alpha beta",
            )
        )
        authority_index += 1
        for fragment in (
            f"zzzz qqqq vvvv {text_index:02d}",
            f"authority phrase {text_index:02d} alpha beta",
            f"yyyy xxxx wwww {text_index:02d}",
            f"kkkk jjjj hhhh {text_index:02d}",
        ):
            y0 = reading_order * 50 + 10
            elements.append(
                _element(
                    f"latency-unit-{reading_order}",
                    fragment,
                    (100, y0, 800, y0 + 30),
                    provider="latency-provider",
                    order=reading_order,
                )
            )
            reading_order += 1
        if text_index == 5:
            blocks.append(
                MarkdownBlock(
                    id="latency-visual",
                    index=authority_index,
                    kind=MarkdownBlockKind.IMAGE,
                    source="assets/latency-figure.png",
                )
            )
            authority_index += 1
            y0 = reading_order * 50 + 10
            elements.append(
                _element(
                    "latency-figure",
                    None,
                    (100, y0, 800, y0 + 30),
                    provider="latency-provider",
                    kind=ElementType.FIGURE,
                    order=reading_order,
                    metadata={"path": "assets/latency-figure.png"},
                )
            )
            reading_order += 1

    content = MarkdownContent(source="latency.md", blocks=blocks)
    document = _document(
        "latency-provider",
        elements,
        height=3000,
    )
    original_sequence_matcher = evidence_matching.difflib.SequenceMatcher
    ratio_calls = 0
    span_candidate_calls = 0

    def counting_sequence_matcher(*args: object, **kwargs: object) -> object:
        nonlocal ratio_calls
        ratio_calls += 1
        return original_sequence_matcher(*args, **kwargs)  # type: ignore[call-overload]

    original_span_candidate = evidence_matching._span_candidate

    def counting_span_candidate(*args: object, **kwargs: object) -> object:
        nonlocal span_candidate_calls
        span_candidate_calls += 1
        return original_span_candidate(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        evidence_matching.difflib,
        "SequenceMatcher",
        counting_sequence_matcher,
    )
    monkeypatch.setattr(
        evidence_matching,
        "_span_candidate",
        counting_span_candidate,
    )

    matches = match_sidecar_evidence(content, _layout(height=3000), document)
    indexed_ratio_calls = ratio_calls
    indexed_span_calls = span_candidate_calls

    # The exhaustive path is retained as a deterministic oracle/fallback.  It
    # must produce byte-for-byte equivalent public models, while demonstrating
    # that exact anchors and monotonic windows structurally reduce fuzzy work.
    monkeypatch.setattr(
        evidence_matching._TextCandidateIndex,
        "spans_for",
        evidence_matching._TextCandidateIndex.exhaustive_spans_for,
    )
    exhaustive = match_sidecar_evidence(content, _layout(height=3000), document)
    exhaustive_ratio_calls = ratio_calls - indexed_ratio_calls
    exhaustive_span_calls = span_candidate_calls - indexed_span_calls

    assert len(matches) == len(blocks)
    assert [match.model_dump(mode="json") for match in matches] == [
        match.model_dump(mode="json") for match in exhaustive
    ]
    assert indexed_span_calls < exhaustive_span_calls // 3
    assert indexed_ratio_calls < exhaustive_ratio_calls
    assert indexed_ratio_calls < 100
    # Similarity and normalization caches are job-scoped; raw document text is
    # not retained by a module-level LRU after this invocation returns.
    assert not hasattr(evidence_matching._normalize_text, "cache_info")
    assert not hasattr(evidence_matching._cached_text_similarity, "cache_info")
