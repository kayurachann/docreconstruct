"""Atomic dataset-level I/O for the OmniDocBench converter."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from .conversion import convert_omnidocbench_oracle_page
from .models import (
    OmniDocBenchConversionReason,
    OmniDocBenchDatasetConversionReport,
    OmniDocBenchOracleContractError,
    OmniDocBenchPageConversionReport,
)
from .projection import _pretty_json_bytes, _sha256, _sha256_bytes


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def convert_omnidocbench_oracle_dataset(
    annotations_path: str | Path,
    *,
    images_directory: str | Path,
    output_directory: str | Path,
    dataset_revision: str,
    markdown_directory: str | Path | None = None,
    report_path: str | Path | None = None,
    expected_annotations_sha256: str | None = None,
    expected_image_sha256: Mapping[str, str] | None = None,
) -> OmniDocBenchDatasetConversionReport:
    """Convert all pages; never copy source rasters or ground-truth Markdown."""

    annotations = Path(annotations_path).expanduser().resolve()
    annotations_sha256 = _sha256(annotations)
    if expected_annotations_sha256 is not None and (
        annotations_sha256 != expected_annotations_sha256.strip().casefold()
    ):
        raise ValueError(
            "annotation input SHA-256 does not match the pinned manifest: "
            f"expected {expected_annotations_sha256}, got {annotations_sha256}"
        )
    revision = dataset_revision.strip()
    if not revision:
        raise ValueError("dataset_revision must not be blank")
    payload = json.loads(annotations.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise TypeError("OmniDocBench annotations root must be a list")
    images = Path(images_directory).expanduser().resolve()
    markdown = (
        Path(markdown_directory).expanduser().resolve() if markdown_directory is not None else None
    )
    output = Path(output_directory).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination_report = (
        Path(report_path).expanduser().resolve()
        if report_path is not None
        else output / "conversion-report.json"
    )
    if destination_report == annotations:
        raise ValueError("conversion report must not overwrite the source annotation file")
    page_reports: list[OmniDocBenchPageConversionReport] = []
    output_names: dict[str, int] = {}
    for index, record in enumerate(payload):
        if not isinstance(record, Mapping):
            raise TypeError(f"annotation record {index} must be an object")
        page_info = record.get("page_info")
        if not isinstance(page_info, Mapping) or not isinstance(page_info.get("image_path"), str):
            raise ValueError(f"annotation record {index} has no page_info.image_path")
        image_name = Path(page_info["image_path"]).name
        image_path = images / image_name
        expected_image = (expected_image_sha256 or {}).get(image_name)
        if expected_image is not None:
            actual = _sha256(image_path)
            if actual != expected_image.strip().casefold():
                raise ValueError(
                    f"image input SHA-256 mismatch for {image_name}: "
                    f"expected {expected_image}, got {actual}"
                )
        markdown_path = markdown / f"{Path(image_name).stem}.md" if markdown else None
        conversion = convert_omnidocbench_oracle_page(
            record,
            record_index=index,
            image_path=image_path,
            dataset_revision=revision,
            markdown_path=markdown_path,
        )
        output_name = conversion.report.output_name
        if output_name in output_names:
            raise OmniDocBenchOracleContractError(
                OmniDocBenchConversionReason.DUPLICATE_OUTPUT_NAME,
                f"{output_name} collides with annotation record {output_names[output_name]}",
            )
        output_names[output_name] = index
        canonical_payload = _pretty_json_bytes(
            conversion.document.model_dump(mode="json", exclude_unset=True)
        )
        if _sha256_bytes(canonical_payload) != conversion.report.canonical_output_sha256:
            raise AssertionError("canonical serialization changed between validation and write")
        _atomic_write(output / output_name, canonical_payload)
        page_reports.append(conversion.report)
    report = OmniDocBenchDatasetConversionReport(
        dataset_revision=revision,
        annotation_file_name=annotations.name,
        annotation_file_sha256=annotations_sha256,
        page_count=len(page_reports),
        annotation_count=sum(page.annotation_count for page in page_reports),
        projected_element_count=sum(page.projected_element_count for page in page_reports),
        ignored_count=sum(page.ignored_count for page in page_reports),
        warning_count=sum(len(page.warnings) for page in page_reports),
        pages=page_reports,
    )
    _atomic_write(destination_report, _pretty_json_bytes(report.model_dump(mode="json")))
    return report


__all__ = ["convert_omnidocbench_oracle_dataset"]
