from __future__ import annotations

import itertools
from copy import deepcopy
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from PIL import Image, ImageDraw

from docreconstruct.evaluation import (
    FidelityScore,
    evaluate,
    evaluate_editability,
    evaluate_layout,
    evaluate_structure,
    evaluate_visual,
)
from docreconstruct.evaluation.metrics import _dense_distance, _distance


def _element(
    identifier: str,
    *,
    kind: str = "paragraph",
    text: str = "text",
    bbox: tuple[float, float, float, float] = (0, 0, 100, 20),
    order: int = 0,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": identifier,
        "type": kind,
        "text": text,
        "bbox": list(bbox),
        "reading_order": order,
        "metadata": metadata or {},
    }


def _document(elements: list[dict[str, object]]) -> dict[str, object]:
    return {
        "id": "document",
        "pages": [
            {
                "id": "page-1",
                "number": 1,
                "width": 600,
                "height": 800,
                "elements": elements,
            }
        ],
    }


@st.composite
def _layout_elements(
    draw: st.DrawFn,
    *,
    min_size: int = 1,
    max_size: int = 8,
) -> list[dict[str, object]]:
    count = draw(st.integers(min_value=min_size, max_value=max_size))
    kinds = draw(
        st.lists(
            st.sampled_from(("paragraph", "heading", "formula", "table")),
            min_size=count,
            max_size=count,
        )
    )
    widths = draw(
        st.lists(st.integers(min_value=20, max_value=120), min_size=count, max_size=count)
    )
    heights = draw(
        st.lists(st.integers(min_value=10, max_value=60), min_size=count, max_size=count)
    )
    return [
        _element(
            f"element-{index}",
            kind=kinds[index],
            text=f"content {index}",
            bbox=(
                float((index % 4) * 140 + 10),
                float((index // 4) * 100 + 10),
                float((index % 4) * 140 + 10 + widths[index]),
                float((index // 4) * 100 + 10 + heights[index]),
            ),
            order=index,
        )
        for index in range(count)
    ]


@given(elements=_layout_elements(), data=st.data())
def test_layout_score_is_property_invariant_under_both_permutations(
    elements: list[dict[str, object]],
    data: st.DataObject,
) -> None:
    candidate_elements = deepcopy(elements)
    for index, element in enumerate(candidate_elements):
        element["id"] = f"candidate-{index}"
    reference_permutation = data.draw(st.permutations(elements), label="reference permutation")
    candidate_permutation = data.draw(
        st.permutations(candidate_elements), label="candidate permutation"
    )

    baseline = evaluate_layout(_document(elements), _document(candidate_elements)).score
    permuted = evaluate_layout(
        _document(list(reference_permutation)),
        _document(list(candidate_permutation)),
    ).score

    assert permuted == pytest.approx(baseline)


@given(elements=_layout_elements())
def test_exact_layout_score_is_never_lower_than_damaged(
    elements: list[dict[str, object]],
) -> None:
    damaged = deepcopy(elements)
    damaged_bbox = damaged[0]["bbox"]
    assert isinstance(damaged_bbox, list)
    damaged_bbox[0] = float(damaged_bbox[0]) + 175.0
    damaged_bbox[2] = float(damaged_bbox[2]) + 175.0

    exact_score = evaluate_layout(_document(elements), _document(deepcopy(elements))).score
    damaged_score = evaluate_layout(_document(elements), _document(damaged)).score

    assert exact_score >= damaged_score


@given(
    editable_count=st.integers(min_value=0, max_value=20),
    flattened_count=st.integers(min_value=0, max_value=20),
    empty_count=st.integers(min_value=1, max_value=20),
)
def test_adding_empty_objects_never_improves_editability(
    editable_count: int,
    flattened_count: int,
    empty_count: int,
) -> None:
    elements = [
        _element(f"editable-{index}", text=f"editable {index}") for index in range(editable_count)
    ]
    elements.extend(
        _element(
            f"flattened-{index}",
            kind="image",
            text="",
            metadata={"flattened": True},
        )
        for index in range(flattened_count)
    )
    padded = deepcopy(elements)
    padded.extend(_element(f"empty-{index}", text="") for index in range(empty_count))

    assert (
        evaluate_editability(_document(padded)).score
        <= evaluate_editability(_document(elements)).score
    )


def test_layout_score_is_candidate_permutation_invariant() -> None:
    reference = _document(
        [
            _element("r-left", text="reference A", bbox=(10, 10, 110, 40), order=0),
            _element("r-right", text="reference B", bbox=(300, 10, 400, 40), order=1),
        ]
    )
    candidates = [
        _element("c-left", text="candidate X", bbox=(10, 10, 110, 40), order=0),
        _element("c-right", text="candidate Y", bbox=(300, 10, 400, 40), order=1),
    ]

    scores = {
        evaluate_layout(reference, _document(list(order))).score
        for order in itertools.permutations(candidates)
    }

    assert len(scores) == 1
    assert next(iter(scores)) == pytest.approx(1.0)


def test_layout_score_is_reference_permutation_invariant() -> None:
    references = [
        _element("r-left", text="reference A", bbox=(10, 10, 110, 40), order=0),
        _element("r-right", text="reference B", bbox=(300, 10, 400, 40), order=1),
    ]
    candidate = _document(
        [
            _element("c-left", text="candidate X", bbox=(10, 10, 110, 40), order=0),
            _element("c-right", text="candidate Y", bbox=(300, 10, 400, 40), order=1),
        ]
    )

    scores = {
        evaluate_layout(_document(list(order)), candidate).score
        for order in itertools.permutations(references)
    }

    assert len(scores) == 1
    assert next(iter(scores)) == pytest.approx(1.0)


def test_large_page_assignment_remains_deterministic_and_exact() -> None:
    reference_elements = [
        _element(
            f"ref-{index}",
            text=f"line {index}",
            bbox=(
                (index % 12) * 45,
                (index // 12) * 35,
                (index % 12) * 45 + 40,
                (index // 12) * 35 + 25,
            ),
            order=index,
        )
        for index in range(120)
    ]
    candidate_elements = [
        _element(
            f"candidate-{index}",
            text=f"line {index}",
            bbox=(
                (index % 12) * 45,
                (index // 12) * 35,
                (index % 12) * 45 + 40,
                (index // 12) * 35 + 25,
            ),
            order=index,
        )
        for index in reversed(range(120))
    ]

    metrics = evaluate_layout(_document(reference_elements), _document(candidate_elements))

    assert metrics.matched_elements == 120
    assert metrics.score == pytest.approx(1.0)


def test_duplicate_ids_do_not_force_invalid_match() -> None:
    reference = _document(
        [
            _element("duplicate", kind="table", text="table", bbox=(300, 300, 500, 500)),
            _element("duplicate", kind="paragraph", text="body", bbox=(10, 10, 110, 40)),
        ]
    )
    candidate = _document(
        [_element("duplicate", kind="paragraph", text="body", bbox=(10, 10, 110, 40))]
    )

    metrics = evaluate_layout(reference, candidate)

    assert metrics.matched_elements == 1
    assert metrics.mean_iou == pytest.approx(1.0)


def _table_document(
    rows: list[list[str]] | None,
    *,
    cells: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if rows is not None:
        metadata["table"] = {"rows": rows}
    if cells is not None:
        metadata["cells"] = cells
    return _document([_element("table-1", kind="table", text="", metadata=metadata)])


def test_wrong_table_text_lowers_table_score() -> None:
    reference = _table_document([["A", "B"], ["C", "D"]])
    exact = _table_document([["A", "B"], ["C", "D"]])
    wrong = _table_document([["W", "X"], ["Y", "Z"]])

    exact_score = evaluate_structure(reference, exact).table_structure_accuracy
    wrong_score = evaluate_structure(reference, wrong).table_structure_accuracy

    assert exact_score == pytest.approx(1.0)
    assert wrong_score < exact_score


def test_wrong_rowspan_lowers_table_score() -> None:
    reference = _table_document(
        None,
        cells=[
            {"row": 0, "column": 0, "row_span": 2, "column_span": 1, "text": "A"},
            {"row": 0, "column": 1, "row_span": 1, "column_span": 1, "text": "B"},
            {"row": 1, "column": 1, "row_span": 1, "column_span": 1, "text": "C"},
        ],
    )
    wrong = deepcopy(reference)
    wrong["pages"][0]["elements"][0]["metadata"]["cells"][0]["row_span"] = 1  # type: ignore[index]

    score = evaluate_structure(reference, wrong).table_structure_accuracy

    assert score < 1.0


def test_missing_reference_table_scores_zero() -> None:
    reference = _table_document([["A", "B"]])
    candidate = _document([_element("body", text="A B")])

    assert evaluate_structure(reference, candidate).table_structure_accuracy == 0.0


def test_extra_empty_paragraphs_do_not_improve_editability() -> None:
    base = _document([_element("body", text="editable")])
    padded = deepcopy(base)
    padded["pages"][0]["elements"].extend(  # type: ignore[index]
        _element(f"empty-{index}", text="") for index in range(20)
    )

    assert evaluate_editability(padded).score <= evaluate_editability(base).score


def test_full_page_image_with_empty_paragraphs_is_not_editable() -> None:
    candidate = _document(
        [
            _element(
                "scan",
                kind="image",
                text="",
                bbox=(0, 0, 600, 800),
                metadata={"flattened": True, "full_page": True},
            ),
            *[_element(f"empty-{index}", text="") for index in range(100)],
        ]
    )

    assert evaluate_editability(candidate).score == 0.0


def test_docx_full_page_image_with_empty_paragraphs_is_not_editable(tmp_path: Path) -> None:
    docx = pytest.importorskip("docx")
    image_path = tmp_path / "page.png"
    Image.new("RGB", (600, 800), "white").save(image_path)
    document = docx.Document()
    for _ in range(100):
        document.add_paragraph("")
    document.add_picture(str(image_path))
    output = tmp_path / "flattened.docx"
    document.save(output)

    metrics = evaluate_editability(output)

    assert metrics.editable_elements == 0
    assert metrics.flattened_elements >= 1
    assert metrics.score == 0.0


def test_missing_visual_is_not_visual_pass() -> None:
    report = evaluate(_document([_element("r")]), _document([_element("c")]))
    payload = report.to_dict()

    assert report.visual is None
    assert payload["component_statuses"]["visual"] == "not_measured"
    assert payload["accepted"] is False


def test_missing_component_does_not_inflate_strict_overall() -> None:
    fidelity = FidelityScore(
        text=1.0,
        visual=None,
        custom_weights={"text": 0.5, "visual": 0.5},
    )

    assert fidelity.overall_measured == pytest.approx(1.0)
    assert fidelity.overall_strict == pytest.approx(0.5)
    assert fidelity.measurement_coverage == pytest.approx(0.5)
    assert fidelity.overall == fidelity.overall_measured


def test_color_swap_lowers_color_profile_score() -> None:
    reference = Image.new("RGB", (100, 100), "white")
    swapped = reference.copy()
    ImageDraw.Draw(reference).rectangle((10, 10, 90, 90), fill="red")
    ImageDraw.Draw(swapped).rectangle((10, 10, 90, 90), fill="blue")

    exact = evaluate_visual(reference, reference, profile="document_color")
    changed = evaluate_visual(reference, swapped, profile="document_color")

    assert exact.score == pytest.approx(1.0)
    assert changed.color_similarity < 0.5
    assert changed.score < exact.score - 0.1


def test_metric_report_contains_versions_and_measurement_coverage() -> None:
    report = evaluate(_document([_element("r")]), _document([_element("c")]))
    payload = report.to_dict()

    assert payload["schema_version"]
    assert payload["metric_version"]
    assert 0.0 <= payload["measurement_coverage"] <= 1.0
    for name in ("text", "layout", "structure", "editability"):
        assert payload[name]["metric_version"]


@given(
    left=st.text(alphabet="abcABC 12àệ", max_size=45),
    right=st.text(alphabet="abcABC 12àệ", max_size=45),
)
def test_bit_vector_distance_equals_the_dense_dynamic_program(left: str, right: str) -> None:
    """The fast edit distance must be exact, not an approximation.

    `_distance` runs once per candidate pair inside the O(n^2) element matcher,
    so it is replaced by Myers' bit-vector formulation — but every layout and
    structure score is derived from its value, so any disagreement with the
    dense dynamic program would silently move the metrics.
    """

    assert _distance(left, right) == _dense_distance(*sorted((left, right), key=len, reverse=True))


@given(
    left=st.lists(st.sampled_from(["alpha", "beta", "gamma", "delta"]), max_size=25),
    right=st.lists(st.sampled_from(["alpha", "beta", "gamma", "delta"]), max_size=25),
)
def test_bit_vector_distance_matches_for_word_sequences(
    left: list[str],
    right: list[str],
) -> None:
    ordered = sorted((left, right), key=len, reverse=True)
    assert _distance(left, right) == _dense_distance(ordered[0], ordered[1])


def test_distance_falls_back_for_unhashable_items() -> None:
    """Word and character sequences hash, but the contract must not assume it."""

    left = [["a"], ["b"], ["c"]]
    right = [["a"], ["x"], ["c"]]

    assert _distance(left, right) == 1


def test_content_free_docx_is_not_editable(tmp_path: Path) -> None:
    """An empty body scored a perfect 1.0 on all three editability ratios.

    It even claimed ``native_structure_ratio == 1.0`` — that the document was
    100% native tables — which contradicts the raster branch's explicit zeros
    and the hand-written text/markdown constant's ``structure = 0.0``.
    """

    import zipfile

    output = tmp_path / "empty.docx"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:sectPr/></w:body></w:document>',
        )

    metrics = evaluate_editability(output)

    assert metrics.total_elements == 0
    assert metrics.score == 0.0
    assert metrics.native_structure_ratio == 0.0


@pytest.mark.parametrize("candidate", [{"pages": []}, {"pages": [{"elements": []}]}])
def test_element_free_ir_is_not_editable(candidate: dict[str, object]) -> None:
    metrics = evaluate_editability(candidate)

    assert metrics.total_elements == 0
    assert metrics.score == 0.0
