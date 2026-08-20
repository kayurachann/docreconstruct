from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from docreconstruct.evaluation.omnidocbench_oracle import (
    OmniDocBenchOracleConversionError,
    OmniDocBenchProjectionReason,
    convert_omnidocbench_oracle_page,
    validate_omnidocbench_projection,
)


def _save_image(path: Path, size: tuple[int, int]) -> Path:
    Image.new("RGB", size, "white").save(path)
    return path


def _record(*, width: int, height: int) -> dict[str, object]:
    return {
        "layout_dets": [
            {
                "category_type": "text_block",
                "poly": [10, 20, 90, 20, 90, 50, 10, 50],
                "ignore": False,
                "order": 1,
                "anno_id": 8,
                "text": "Exact English authority",
                "attribute": {"text_language": "text_english"},
            },
            {
                "category_type": "table",
                "poly": [10, 60, 90, 60, 90, 150, 10, 150],
                "ignore": False,
                "order": 2,
                "anno_id": 3,
                "latex": "中文 & 1",
                "html": "<table><tr><td>中文</td><td>1</td></tr></table>",
                "attribute": {"language": "table_simplified_chinese"},
            },
            {
                "category_type": "figure",
                "poly": [100, 60, 180, 60, 180, 150, 100, 150],
                "ignore": True,
                "order": 3,
                "anno_id": 9,
            },
        ],
        "page_info": {
            "width": width,
            "height": height,
            "page_no": 7,
            "image_path": "fixture.jpg",
            "page_attribute": {
                "data_source": "mixed_fixture",
                "language": "mixed",
                "layout": "single_column",
            },
        },
    }


def test_projection_corrects_proven_transposed_metadata_without_rotating_gt(
    tmp_path: Path,
) -> None:
    image = _save_image(tmp_path / "fixture.jpg", (200, 300))
    record = _record(width=300, height=200)

    conversion = convert_omnidocbench_oracle_page(
        record,
        record_index=4,
        image_path=image,
        dataset_revision="pinned-revision",
    )

    assert conversion.diagnostic.reason_code is (
        OmniDocBenchProjectionReason.REPORTED_DIMENSIONS_TRANSPOSED
    )
    assert conversion.diagnostic.corrected
    assert conversion.diagnostic.annotation_count == 3
    assert conversion.diagnostic.retained_annotation_count == 2
    assert conversion.diagnostic.ignored_annotation_count == 1
    page = conversion.document.pages[0]
    assert (page.width, page.height) == (200.0, 300.0)
    assert len(page.elements) == 2
    assert page.elements[0].text == "Exact English authority"
    assert page.elements[0].bbox.model_dump() == {
        "x0": 10.0,
        "y0": 20.0,
        "x1": 90.0,
        "y1": 50.0,
    }
    assert page.elements[1].metadata["html"].startswith("<table>")
    assert page.metadata["projection_validation"]["corrected"] is True


def test_projection_keeps_matching_dimensions(tmp_path: Path) -> None:
    image = _save_image(tmp_path / "fixture.jpg", (200, 300))

    diagnostic = validate_omnidocbench_projection(
        _record(width=200, height=300),
        image,
    )

    assert diagnostic.reason_code is OmniDocBenchProjectionReason.DIMENSIONS_MATCH
    assert not diagnostic.corrected


def test_projection_rejects_unknown_scaling_instead_of_guessing(tmp_path: Path) -> None:
    image = _save_image(tmp_path / "fixture.jpg", (200, 300))

    with pytest.raises(OmniDocBenchOracleConversionError) as caught:
        validate_omnidocbench_projection(_record(width=400, height=600), image)

    assert caught.value.diagnostic.reason_code is (
        OmniDocBenchProjectionReason.REPORTED_DIMENSIONS_INCOMPATIBLE
    )


def test_projection_rejects_annotations_outside_decoded_raster(tmp_path: Path) -> None:
    image = _save_image(tmp_path / "fixture.jpg", (100, 100))
    record = _record(width=100, height=100)

    with pytest.raises(OmniDocBenchOracleConversionError) as caught:
        validate_omnidocbench_projection(record, image)

    assert caught.value.diagnostic.reason_code is (
        OmniDocBenchProjectionReason.ANNOTATION_OUT_OF_BOUNDS
    )
