from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

from docreconstruct.cli import cli
from docreconstruct.evaluation.omnidocbench_oracle import (
    OmniDocBenchConversionReason,
    OmniDocBenchOracleContractError,
    OmniDocBenchOracleConversionError,
    OmniDocBenchProjectionReason,
    convert_omnidocbench_oracle_dataset,
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
        "extra": {
            "relation": [
                {
                    "source_anno_id": 8,
                    "target_anno_id": 3,
                    "relation_type": "reading_order",
                }
            ]
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
    assert conversion.report.annotation_count == 3
    assert conversion.report.projected_element_count == 2
    assert conversion.report.ignored_count == 1
    assert conversion.report.audited_annotation_count == 3
    assert conversion.report.text_hash_match is True
    assert conversion.report.relation_hash_match is True
    assert conversion.report.page_geometry_valid is True
    assert conversion.report.reading_order_complete is True
    audits = page.metadata["omnidocbench_annotations"]
    assert audits[2]["projection_status"] == "ignored_audit_only"
    assert audits[2]["raw_annotation"] == record["layout_dets"][2]
    assert page.metadata["omnidocbench_extra"] == record["extra"]
    assert page.elements[0].metadata["source_annotation_id"] == 8
    assert page.elements[0].metadata["source_polygon"] == record["layout_dets"][0]["poly"]
    assert page.elements[1].metadata["latex"] == "中文 & 1"
    assert page.elements[1].metadata["attribute"] == {"language": "table_simplified_chinese"}
    warning_codes = {warning.reason_code for warning in conversion.report.warnings}
    assert OmniDocBenchConversionReason.REPORTED_DIMENSIONS_TRANSPOSED in warning_codes
    assert OmniDocBenchConversionReason.IGNORED_ANNOTATION_AUDIT_ONLY in warning_codes


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


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("anno_id", 8, OmniDocBenchConversionReason.DUPLICATE_ANNOTATION_ID),
        ("order", 1, OmniDocBenchConversionReason.DUPLICATE_READING_ORDER),
    ],
)
def test_converter_rejects_duplicate_ids_and_orders_with_reason_codes(
    tmp_path: Path,
    field: str,
    value: object,
    reason: OmniDocBenchConversionReason,
) -> None:
    image = _save_image(tmp_path / "fixture.jpg", (200, 300))
    record = _record(width=200, height=300)
    record["layout_dets"][1][field] = value  # type: ignore[index]

    with pytest.raises(OmniDocBenchOracleContractError) as caught:
        convert_omnidocbench_oracle_page(
            record,
            record_index=0,
            image_path=image,
            dataset_revision="revision",
        )

    assert caught.value.reason_code is reason


def test_converter_preserves_formula_authority_and_warns_for_unknown_category(
    tmp_path: Path,
) -> None:
    image = _save_image(tmp_path / "fixture.jpg", (200, 300))
    record = _record(width=200, height=300)
    first = record["layout_dets"][0]  # type: ignore[index]
    first["category_type"] = "equation_isolated"
    first["latex"] = r"\int_0^1 x^2\,dx"
    del first["text"]
    second = record["layout_dets"][1]  # type: ignore[index]
    second["category_type"] = "custom_table_variant"
    second["order"] = None

    conversion = convert_omnidocbench_oracle_page(
        record,
        record_index=0,
        image_path=image,
        dataset_revision="revision",
    )

    assert conversion.document.pages[0].elements[0].text == r"\int_0^1 x^2\,dx"
    assert conversion.document.pages[0].elements[0].metadata["latex"] == r"\int_0^1 x^2\,dx"
    assert conversion.document.pages[0].elements[1].type.value == "unknown"
    assert conversion.report.reading_order_complete is False
    codes = {warning.reason_code for warning in conversion.report.warnings}
    assert OmniDocBenchConversionReason.MISSING_READING_ORDER in codes
    assert OmniDocBenchConversionReason.UNSUPPORTED_CATEGORY_PROJECTED_AS_UNKNOWN in codes


def test_dataset_converter_is_deterministic_atomic_and_copies_no_sources(
    tmp_path: Path,
) -> None:
    images = tmp_path / "images"
    images.mkdir()
    image = _save_image(images / "fixture.jpg", (200, 300))
    annotation_path = tmp_path / "OmniDocBench.json"
    annotation_path.write_text(
        json.dumps([_record(width=200, height=300)], ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "fixture.md").write_text("ground truth must not be copied", encoding="utf-8")
    first_output = tmp_path / "canonical-a"
    second_output = tmp_path / "canonical-b"

    first = convert_omnidocbench_oracle_dataset(
        annotation_path,
        images_directory=images,
        output_directory=first_output,
        dataset_revision="pinned-revision",
    )
    second = convert_omnidocbench_oracle_dataset(
        annotation_path,
        images_directory=images,
        output_directory=second_output,
        dataset_revision="pinned-revision",
    )

    canonical_name = "fixture.canonical.json"
    assert (first_output / canonical_name).read_bytes() == (
        second_output / canonical_name
    ).read_bytes()
    assert (first_output / "conversion-report.json").read_bytes() == (
        second_output / "conversion-report.json"
    ).read_bytes()
    canonical_bytes = (first_output / canonical_name).read_bytes()
    assert first.pages[0].canonical_output_sha256 == hashlib.sha256(canonical_bytes).hexdigest()
    assert first.annotation_file_sha256 == hashlib.sha256(annotation_path.read_bytes()).hexdigest()
    assert first.pages[0].raster_input_sha256 == hashlib.sha256(image.read_bytes()).hexdigest()
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert not list(first_output.glob("*.jpg"))
    assert not list(first_output.glob("*.md"))
    assert not list(first_output.glob("*.tmp"))


def test_convert_omnidocbench_cli_uses_pinned_manifest_and_emits_report(
    tmp_path: Path,
) -> None:
    root = tmp_path / "private-dataset"
    dataset = root / "demo_data" / "omnidocbench_demo"
    images = dataset / "images"
    images.mkdir(parents=True)
    image = _save_image(images / "fixture.jpg", (200, 300))
    annotations = dataset / "OmniDocBench_demo.json"
    annotations.write_text(
        json.dumps([_record(width=200, height=300)], ensure_ascii=False),
        encoding="utf-8",
    )
    manifest = tmp_path / "corpus-lock.json"
    manifest.write_text(
        json.dumps(
            {
                "upstream": {
                    "revision": "immutable-revision",
                    "annotation_file": {
                        "path": "demo_data/omnidocbench_demo/OmniDocBench_demo.json",
                        "sha256": hashlib.sha256(annotations.read_bytes()).hexdigest(),
                    },
                },
                "cases": [
                    {
                        "upstream_image": "fixture.jpg",
                        "image": {"sha256": hashlib.sha256(image.read_bytes()).hexdigest()},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "canonical"

    result = CliRunner().invoke(
        cli,
        [
            "convert-omnidocbench",
            "--dataset-root",
            str(root),
            "--output",
            str(output),
            "--manifest",
            str(manifest),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads((output / "conversion-report.json").read_text(encoding="utf-8"))
    assert report["page_count"] == 1
    assert report["annotation_count"] == 3
    assert report["ignored_count"] == 1
    assert (output / "fixture.canonical.json").is_file()
    assert "ignored but audited: 1" in result.output
    assert not list(output.glob("*.jpg"))
    assert not list(output.glob("*.md"))


def test_convert_omnidocbench_cli_rejects_manifest_sha_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    images = root / "images"
    images.mkdir(parents=True)
    _save_image(images / "fixture.jpg", (200, 300))
    annotations = root / "OmniDocBench.json"
    annotations.write_text(json.dumps([_record(width=200, height=300)]), encoding="utf-8")
    manifest = tmp_path / "bad-lock.json"
    manifest.write_text(
        json.dumps(
            {
                "upstream": {
                    "revision": "revision",
                    "annotation_file": {
                        "path": "OmniDocBench.json",
                        "sha256": "0" * 64,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "output"

    result = CliRunner().invoke(
        cli,
        [
            "convert-omnidocbench",
            "--dataset-root",
            str(root),
            "--output",
            str(output),
            "--manifest",
            str(manifest),
        ],
    )

    assert result.exit_code == 2
    assert "does not match the pinned manifest" in result.output
    assert not list(output.glob("*.canonical.json")) if output.exists() else True
