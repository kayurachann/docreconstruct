from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

from docreconstruct.cli import cli
from docreconstruct.evidence import (
    DetectionCandidate,
    SidecarDetection,
    SidecarEvidence,
    SidecarEvidenceBundle,
)
from docreconstruct.ir import BBox, Document, Element, ElementType, Page, Provenance
from docreconstruct.reconstruction.alignment import (
    AlignmentDecisionStatus,
    AlignmentReason,
    build_alignment_report,
)
from docreconstruct.reconstruction.alignment.candidates import source_id
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


def _content(*blocks: tuple[MarkdownBlockKind, str]) -> MarkdownContent:
    return MarkdownContent(
        source="private-authority-path.md",
        blocks=[
            MarkdownBlock(
                id=f"private-block-id-{index}",
                index=index,
                kind=kind,
                text=text,
            )
            for index, (kind, text) in enumerate(blocks)
        ],
    )


def _layout(*, pages: int = 1, width: int = 1000, height: int = 2000) -> ScanDocumentLayout:
    return ScanDocumentLayout(
        source="private-layout-path.pdf",
        pages=[
            ScanPageLayout(
                number=number,
                width=width,
                height=height,
                pdf_width=width / 2,
                pdf_height=height / 2,
                content_bbox=PixelBox(x0=0, y0=0, x1=width, y1=height),
                line_pitch=40,
                image=Image.new("RGB", (width, height), "white"),
            )
            for number in range(1, pages + 1)
        ],
    )


def _element(
    element_id: str,
    text: str,
    *,
    order: int,
    y0: float,
    kind: ElementType = ElementType.PARAGRAPH,
    confidence: float = 0.95,
) -> Element:
    return Element(
        id=element_id,
        type=kind,
        bbox=BBox(x0=100, y0=y0, x1=800, y1=y0 + 40),
        text=text,
        reading_order=order,
        confidence=confidence,
        provenance=Provenance(engine="private-provider-name", source_id=element_id),
    )


def _document(
    elements: list[Element],
    *,
    width: float = 1000,
    height: float = 2000,
    page_number: int = 1,
) -> Document:
    return Document(
        id="private-document-id",
        pages=[
            Page(
                id="private-page-id",
                number=page_number,
                width=width,
                height=height,
                elements=elements,
            )
        ],
        metadata={"provider": "private-provider-name"},
    )


def _bundle(document: Document) -> SidecarEvidenceBundle:
    detection = SidecarDetection(
        provider="json",
        confidence=1.0,
        reason="fixture",
        candidates=(DetectionCandidate(provider="json", confidence=1.0, reason="fixture"),),
        explicit=True,
    )
    return SidecarEvidenceBundle(
        items=(
            SidecarEvidence(
                path=Path("private-sidecar-path.json"),
                provider="json",
                detection=detection,
                document=document,
            ),
        )
    )


def test_reason_code_contract_is_complete_and_stable() -> None:
    assert tuple(reason.value for reason in AlignmentReason) == (
        "no_text_candidate",
        "text_below_threshold",
        "unsafe_geometry",
        "page_conflict",
        "order_conflict",
        "type_conflict",
        "ambiguous_candidates",
        "span_limit_reached",
        "candidate_budget_reached",
        "projection_invalid",
        "region_conflict",
    )


def test_report_covers_every_block_with_deterministic_decisions() -> None:
    content = _content(
        (MarkdownBlockKind.PARAGRAPH, "alpha exact authority"),
        (MarkdownBlockKind.PARAGRAPH, "beta duplicate authority"),
        (MarkdownBlockKind.PARAGRAPH, "content with no adequate evidence"),
        (MarkdownBlockKind.IMAGE, ""),
    )
    document = _document(
        [
            _element(
                "sensitive-beta-one",
                "beta duplicate authority",
                order=0,
                y0=100,
                kind=ElementType.TABLE,
                confidence=0.0,
            ),
            _element(
                "sensitive-beta-two",
                "beta duplicate authority",
                order=1,
                y0=180,
                kind=ElementType.TABLE,
                confidence=0.0,
            ),
            _element(
                "sensitive-alpha",
                "alpha exact authority",
                order=2,
                y0=260,
                confidence=1.0,
            ),
            _element("sensitive-noise", "unrelated noise", order=3, y0=340),
        ]
    )
    bundle = _bundle(document)
    matches = match_sidecar_evidence(content, _layout(), bundle)

    first = build_alignment_report(content, _layout(), bundle, matches=matches, top_n=3)
    second = build_alignment_report(content, _layout(), bundle, matches=matches, top_n=3)

    assert first == second
    assert len(first.decisions) == len(content.blocks)
    assert tuple(decision.block_id for decision in first.decisions) == (
        "block-000001",
        "block-000002",
        "block-000003",
        "block-000004",
    )
    assert first.decisions[0].status is AlignmentDecisionStatus.MATCHED
    assert first.decisions[0].selected_candidate is not None
    assert first.decisions[1].status is AlignmentDecisionStatus.AMBIGUOUS
    assert AlignmentReason.AMBIGUOUS_CANDIDATES in first.decisions[1].reason_codes
    assert first.decisions[2].status is AlignmentDecisionStatus.REJECTED
    assert AlignmentReason.TEXT_BELOW_THRESHOLD in first.decisions[2].reason_codes
    assert first.decisions[3].status is AlignmentDecisionStatus.UNMATCHED
    assert first.decisions[3].reason_codes == (AlignmentReason.NO_TEXT_CANDIDATE,)
    assert first.summary.total_blocks == 4
    assert (
        first.summary.matched
        + first.summary.ambiguous
        + first.summary.rejected
        + first.summary.unmatched
        == 4
    )


def test_report_is_invariant_to_provider_element_permutation() -> None:
    content = _content(
        (MarkdownBlockKind.PARAGRAPH, "first stable paragraph"),
        (MarkdownBlockKind.PARAGRAPH, "second stable paragraph"),
    )
    first = _element("raw-first-id", "first stable paragraph", order=0, y0=100)
    second = _element("raw-second-id", "second stable paragraph", order=1, y0=200)
    layout = _layout()

    left_bundle = _bundle(_document([first, second]))
    right_bundle = _bundle(_document([second, first]))
    left = build_alignment_report(content, layout, left_bundle)
    right = build_alignment_report(content, layout, right_bundle)

    assert left.model_dump(mode="json") == right.model_dump(mode="json")


def test_report_is_invariant_to_evidence_source_permutation() -> None:
    content = _content((MarkdownBlockKind.PARAGRAPH, "stable provider consensus"))
    first_element = _element(
        "provider-a-raw-id", "stable provider consensus", order=0, y0=100
    ).model_copy(update={"provenance": Provenance(engine="provider-a")})
    second_element = _element(
        "provider-b-raw-id", "stable provider consensus", order=0, y0=110
    ).model_copy(update={"provenance": Provenance(engine="provider-b")})
    first = _document([first_element]).model_copy(
        update={"id": "provider-a-document", "metadata": {"provider": "provider-a"}}
    )
    second = _document([second_element]).model_copy(
        update={"id": "provider-b-document", "metadata": {"provider": "provider-b"}}
    )

    left = build_alignment_report(content, _layout(), [first, second])
    right = build_alignment_report(content, _layout(), [second, first])

    assert left.model_dump(mode="json") == right.model_dump(mode="json")


def test_report_excludes_authority_content_paths_and_raw_identifiers() -> None:
    secret_text = "CONFIDENTIAL PERSON 0123456789"
    content = _content((MarkdownBlockKind.PARAGRAPH, secret_text))
    document = _document([_element("raw-secret-element-id", secret_text, order=0, y0=100)])

    report = build_alignment_report(content, _layout(), _bundle(document))
    serialized = report.model_dump_json()

    for forbidden in (
        secret_text,
        "private-authority-path.md",
        "private-layout-path.pdf",
        "private-sidecar-path.json",
        "raw-secret-element-id",
        "private-provider-name",
        "private-document-id",
        "private-page-id",
        "private-block-id",
    ):
        assert forbidden not in serialized
    assert report.privacy.content_included is False
    assert report.privacy.source_paths_included is False
    assert report.debug_artifacts.status == "disabled_for_privacy"


def test_opaque_source_identity_does_not_hash_ocr_text_or_document_metadata() -> None:
    first = _document([_element("stable-raw-id", "short secret 1234", order=0, y0=100)])
    second_element = _element("stable-raw-id", "entirely different private text", order=0, y0=100)
    second = _document([second_element]).model_copy(
        update={
            "id": "different-sensitive-document-name",
            "metadata": {"provider": "different-sensitive-provider"},
        }
    )

    assert source_id(first) == source_id(second)


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (
            _document(
                [_element("unsafe", "projection authority", order=0, y0=100)],
                width=2000,
                height=1000,
            ),
            {AlignmentReason.UNSAFE_GEOMETRY, AlignmentReason.PROJECTION_INVALID},
        ),
        (
            _document(
                [_element("wrong-page", "projection authority", order=0, y0=100)],
                page_number=9,
            ),
            {AlignmentReason.PAGE_CONFLICT},
        ),
    ],
)
def test_projection_and_page_failures_have_per_block_reason_codes(
    document: Document,
    expected: set[AlignmentReason],
) -> None:
    content = _content((MarkdownBlockKind.PARAGRAPH, "projection authority"))
    layout = _layout(pages=2 if AlignmentReason.PAGE_CONFLICT in expected else 1)

    report = build_alignment_report(content, layout, _bundle(document))

    assert report.decisions[0].status is AlignmentDecisionStatus.REJECTED
    assert expected.issubset(set(report.decisions[0].reason_codes))
    assert expected.issubset(set(report.decisions[0].alternatives[0].rejection_reasons))


def test_candidate_budget_and_span_limits_are_reported_without_changing_match() -> None:
    repeated = [
        _element(f"duplicate-{index}", "same", order=index, y0=10 + index * 5)
        for index in range(257)
    ]
    content = _content((MarkdownBlockKind.PARAGRAPH, "same"))
    bundle = _bundle(_document(repeated))
    layout = _layout()
    before = match_sidecar_evidence(content, layout, bundle)

    report = build_alignment_report(content, layout, bundle, matches=before, top_n=2)
    after = match_sidecar_evidence(content, layout, bundle)

    assert before == after
    assert AlignmentReason.CANDIDATE_BUDGET_REACHED in report.decisions[0].reason_codes

    table = MarkdownContent(
        source="table.md",
        blocks=[
            MarkdownBlock(
                id="table-private-id",
                index=0,
                kind=MarkdownBlockKind.TABLE,
                table_rows=[["one", "two", "three"]],
            )
        ],
    )
    split = _bundle(
        _document(
            [
                _element("one", "one", order=0, y0=100),
                _element("two", "two", order=1, y0=150),
                _element("three", "three", order=2, y0=200),
            ]
        )
    )
    span_report = build_alignment_report(table, layout, split)
    assert AlignmentReason.SPAN_LIMIT_REACHED in span_report.decisions[0].reason_codes


def test_cli_writes_alignment_report_before_strict_alignment_failure(tmp_path: Path) -> None:
    content = tmp_path / "secret.md"
    content.write_text("projection authority\n", encoding="utf-8")
    layout = tmp_path / "layout.png"
    Image.new("RGB", (1000, 2000), "white").save(layout)
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        _document(
            [_element("unsafe", "projection authority", order=0, y0=100)],
            width=2000,
            height=1000,
        ).model_dump_json(),
        encoding="utf-8",
    )
    report = tmp_path / "alignment.json"

    result = CliRunner().invoke(
        cli,
        [
            "hybrid",
            str(content),
            str(layout),
            "--evidence",
            str(evidence),
            "--evidence-provider",
            "json",
            "--alignment-report",
            str(report),
            "--output",
            str(tmp_path / "must-not-exist.docx"),
        ],
    )

    assert result.exit_code == 2
    assert report.is_file()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["decisions"][0]["status"] == "rejected"
    assert "projection_invalid" in payload["decisions"][0]["reason_codes"]
    assert not (tmp_path / "must-not-exist.docx").exists()
