from __future__ import annotations

from copy import deepcopy

import pytest
from PIL import Image
from pydantic import ValidationError

from docreconstruct.reconstruction.constraint_plan import (
    ConstraintPlan,
    HardConstraintKind,
    ObjectFlowMode,
    adapt_hybrid_layout_plan,
)
from docreconstruct.reconstruction.hybrid_planner import (
    HybridBlockPlacement,
    HybridLayoutPlan,
    HybridPagePlan,
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

_CONTENT_SHA = "a" * 64
_LAYOUT_SHA = "b" * 64


def _fixture(
    *, reverse: bool = False
) -> tuple[MarkdownContent, HybridLayoutPlan, ScanDocumentLayout]:
    blocks = [
        MarkdownBlock(
            id="paragraph-1",
            index=0,
            kind=MarkdownBlockKind.PARAGRAPH,
            text="Exact Markdown authority text.",
            group_id="question-1",
        ),
        MarkdownBlock(
            id="table-1",
            index=1,
            kind=MarkdownBlockKind.TABLE,
            text="A | B\n--- | ---\n1 | 2",
            table_rows=[["A", "B"], ["1", "2"]],
            group_id="question-1",
        ),
        MarkdownBlock(
            id="image-1",
            index=2,
            kind=MarkdownBlockKind.IMAGE,
            source="figure.png",
            metadata={"alignment": "center"},
        ),
    ]
    content = MarkdownContent(source="authority.md", blocks=blocks)
    first_page_placements = [
        HybridBlockPlacement(
            block_id="paragraph-1",
            block_index=0,
            page_number=1,
            source_bbox=PixelBox(x0=60, y0=80, x1=270, y1=160),
            geometry_source="json_consensus",
            evidence_providers=(
                ("provider-a", "provider-z") if reverse else ("provider-z", "provider-a")
            ),
            evidence_element_ids=(("a-1", "z-1") if reverse else ("z-1", "a-1")),
        ),
        HybridBlockPlacement(
            block_id="table-1",
            block_index=1,
            page_number=1,
            source_bbox=PixelBox(x0=330, y0=180, x1=540, y1=420),
            geometry_source="source_table",
        ),
    ]
    if reverse:
        first_page_placements.reverse()
    plan = HybridLayoutPlan(
        content_source="other-machine/authority.md" if reverse else "authority.md",
        layout_source="other-machine/layout.pdf" if reverse else "layout.pdf",
        pages=[
            HybridPagePlan(
                number=1,
                pdf_width=600,
                pdf_height=800,
                raster_width=600,
                raster_height=800,
                content_bbox=PixelBox(x0=50, y0=50, x1=550, y1=750),
                line_pitch=20,
                placements=first_page_placements,
            ),
            HybridPagePlan(
                number=2,
                pdf_width=612,
                pdf_height=792,
                raster_width=600,
                raster_height=800,
                content_bbox=PixelBox(x0=45, y0=40, x1=555, y1=760),
                line_pitch=22,
                placements=[
                    HybridBlockPlacement(
                        block_id="image-1",
                        block_index=2,
                        page_number=2,
                        source_bbox=PixelBox(x0=100, y0=120, x1=500, y1=620),
                        geometry_source="source_asset",
                    )
                ],
            ),
        ],
        warnings=(
            ["z diagnostic", "a diagnostic"] if not reverse else ["a diagnostic", "z diagnostic"]
        ),
    )
    scan = ScanDocumentLayout(
        source="layout.pdf",
        pages=[
            ScanPageLayout(
                number=1,
                width=600,
                height=800,
                pdf_width=600,
                pdf_height=800,
                content_bbox=PixelBox(x0=50, y0=50, x1=550, y1=750),
                line_pitch=20,
                image=Image.new("RGB", (600, 800), "white"),
                metadata={
                    "column_count": 2,
                    "column_boxes": [[50, 50, 280, 750], [320, 50, 550, 750]],
                },
            ),
            ScanPageLayout(
                number=2,
                width=600,
                height=800,
                pdf_width=612,
                pdf_height=792,
                content_bbox=PixelBox(x0=45, y0=40, x1=555, y1=760),
                line_pitch=22,
                image=Image.new("RGB", (600, 800), "white"),
                metadata={"column_count": 1},
            ),
        ],
    )
    return content, plan, scan


def _adapt(*, reverse: bool = False) -> ConstraintPlan:
    content, hybrid_plan, scan = _fixture(reverse=reverse)
    evidence_hashes = ["d" * 64, "c" * 64]
    if reverse:
        evidence_hashes.reverse()
    return adapt_hybrid_layout_plan(
        hybrid_plan,
        content,
        content_authority_sha256=_CONTENT_SHA,
        layout_authority_sha256=_LAYOUT_SHA,
        evidence_authority_sha256=evidence_hashes,
        source_layout=scan,
    )


def test_hybrid_mapping_is_deterministic_and_does_not_mutate_the_source_plan() -> None:
    content, hybrid_plan, scan = _fixture()
    before = hybrid_plan.model_dump(mode="json")
    first = adapt_hybrid_layout_plan(
        hybrid_plan,
        content,
        content_authority_sha256=_CONTENT_SHA,
        layout_authority_sha256=_LAYOUT_SHA,
        evidence_authority_sha256=["d" * 64, "c" * 64, "d" * 64],
        source_layout=scan,
    )
    reordered = _adapt(reverse=True)

    assert hybrid_plan.model_dump(mode="json") == before
    assert first == reordered
    assert first.fingerprint == reordered.fingerprint
    assert first.provenance.hybrid_plan_sha256 == reordered.provenance.hybrid_plan_sha256
    assert first.provenance.evidence_authority_sha256 == ("c" * 64, "d" * 64)
    round_tripped = ConstraintPlan.model_validate(first.model_dump(mode="json"))
    assert round_tripped.fingerprint == first.fingerprint


def test_hard_contract_preserves_content_objects_provenance_pages_and_editability() -> None:
    result = _adapt()
    hard = result.hard_constraints
    objects = [item for page in result.pages for item in page.objects]
    by_id = {item.object_id: item for item in objects}

    assert hard.content_authority_sha256 == _CONTENT_SHA
    assert hard.layout_authority_sha256 == _LAYOUT_SHA
    assert hard.required_object_ids == ("paragraph-1", "table-1", "image-1")
    assert hard.page_count == 2
    assert [(size.width, size.height) for size in hard.page_sizes] == [
        (600, 800),
        (612, 792),
    ]
    assert hard.source_deletion_allowed is False
    assert hard.full_page_raster_allowed is False
    assert hard.editability_downgrade_allowed is False
    assert set(hard.rules) == set(HardConstraintKind)
    assert by_id["paragraph-1"].provenance.evidence_providers == (
        "provider-a",
        "provider-z",
    )
    assert by_id["table-1"].editable_required
    assert by_id["table-1"].flow_mode is ObjectFlowMode.NATIVE_TABLE
    assert HardConstraintKind.NO_RASTER_SUBSTITUTION in by_id["table-1"].hard_constraints
    assert not by_id["image-1"].editable_required
    assert by_id["image-1"].flow_mode is ObjectFlowMode.INLINE_ASSET
    assert [len(page.columns) for page in result.pages] == [2, 1]
    assert [item.object_id for item in result.pages[0].objects] == ["paragraph-1", "table-1"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw: raw["pages"].pop(), "hard page count"),
        (
            lambda raw: raw["pages"][0]["page_size"].update({"width": 601}),
            "hard physical sizes",
        ),
        (
            lambda raw: raw["pages"][0]["objects"][0].update(
                {"authority_content_sha256": "e" * 64}
            ),
            "hard authority digest",
        ),
        (
            lambda raw: raw["pages"][0]["objects"][0]["provenance"].update(
                {"geometry_source": "rewritten"}
            ),
            "hard digest",
        ),
    ],
)
def test_tampering_with_hard_page_or_object_authority_is_rejected(mutation, message: str) -> None:
    raw = deepcopy(_adapt().model_dump(mode="json"))
    mutation(raw)

    with pytest.raises(ValidationError, match=message):
        ConstraintPlan.model_validate(raw)


def test_deletion_full_page_raster_and_editability_downgrade_cannot_be_enabled() -> None:
    for policy in (
        "source_deletion_allowed",
        "full_page_raster_allowed",
        "editability_downgrade_allowed",
    ):
        raw = deepcopy(_adapt().model_dump(mode="json"))
        raw["hard_constraints"][policy] = True
        with pytest.raises(ValidationError):
            ConstraintPlan.model_validate(raw)


def test_invalid_object_bounds_and_missing_hard_rule_are_rejected() -> None:
    raw = deepcopy(_adapt().model_dump(mode="json"))
    first = raw["pages"][0]["objects"][0]
    first["min_width"] = first["max_width"] + 1
    with pytest.raises(ValidationError, match="minimum width"):
        ConstraintPlan.model_validate(raw)

    raw = deepcopy(_adapt().model_dump(mode="json"))
    table = raw["pages"][0]["objects"][1]
    table["hard_constraints"].remove(HardConstraintKind.NO_RASTER_SUBSTITUTION.value)
    with pytest.raises(ValidationError, match="missing hard rule"):
        ConstraintPlan.model_validate(raw)


def test_adapter_rejects_missing_source_content_and_invalid_column_geometry() -> None:
    content, hybrid_plan, scan = _fixture()
    missing = hybrid_plan.model_copy(
        update={
            "pages": [
                hybrid_plan.pages[0].model_copy(
                    update={"placements": hybrid_plan.pages[0].placements[:1]}
                ),
                hybrid_plan.pages[1],
            ]
        }
    )
    with pytest.raises(ValueError, match="preserve every Markdown block"):
        adapt_hybrid_layout_plan(
            missing,
            content,
            content_authority_sha256=_CONTENT_SHA,
            layout_authority_sha256=_LAYOUT_SHA,
            source_layout=scan,
        )

    invalid_page = scan.pages[0].model_copy(
        update={
            "metadata": {
                "column_count": 2,
                "column_boxes": [[50, 50, 350, 750], [320, 50, 550, 750]],
            }
        }
    )
    invalid_scan = scan.model_copy(update={"pages": [invalid_page, scan.pages[1]]})
    with pytest.raises(ValueError, match="overlap horizontally"):
        adapt_hybrid_layout_plan(
            hybrid_plan,
            content,
            content_authority_sha256=_CONTENT_SHA,
            layout_authority_sha256=_LAYOUT_SHA,
            source_layout=invalid_scan,
        )
