from __future__ import annotations

import itertools
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from docreconstruct.evaluation import (
    FidelityScore,
    evaluate,
    evaluate_editability,
    evaluate_layout,
    evaluate_structure,
    evaluate_visual,
)


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
    padded["pages"][0]["elements"].extend(  # type: ignore[index,union-attr]
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
