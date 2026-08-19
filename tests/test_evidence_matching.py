from __future__ import annotations

from pathlib import Path
from typing import get_type_hints

import pytest
from PIL import Image
from pydantic import ValidationError

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
