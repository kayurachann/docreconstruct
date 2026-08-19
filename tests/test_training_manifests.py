from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docreconstruct.cli import cli
from docreconstruct.training import (
    DatasetManifest,
    DatasetSample,
    DataUsageLane,
    SplitName,
    build_training_plan,
    validate_dataset,
)


def _sample(
    tmp_path: Path,
    sample_id: str,
    *,
    document_id: str | None = None,
    split: SplitName | None = None,
    lane: DataUsageLane = DataUsageLane.COMMERCIAL_PERMISSIVE,
) -> DatasetSample:
    image = tmp_path / f"{sample_id}.png"
    truth = tmp_path / f"{sample_id}.md"
    image.write_bytes(f"not-a-real-image:{sample_id}".encode())
    truth.write_text(f"# {sample_id}\n", encoding="utf-8")
    return DatasetSample(
        id=sample_id,
        document_id=document_id or sample_id,
        source=image,
        ground_truth=truth,
        split=split,
        usage_lane=lane,
        license_id="CDLA-Permissive-1.0",
        commercial_use=True,
        redistribution=True,
        pii_status="none",
        languages=["en"],
        content_kinds=["printed", "layout"],
        degradations=["skew"],
    )


def test_dataset_validation_and_plan_are_deterministic(tmp_path: Path) -> None:
    manifest = DatasetManifest(
        name="licensed-fixture",
        version="1",
        seed=42,
        samples=[_sample(tmp_path, f"page-{index}") for index in range(12)],
    )

    first = validate_dataset(manifest, lane=DataUsageLane.COMMERCIAL_PERMISSIVE)
    second = validate_dataset(manifest, lane=DataUsageLane.COMMERCIAL_PERMISSIVE)
    plan = build_training_plan(
        manifest,
        backend="olmocr",
        lane=DataUsageLane.COMMERCIAL_PERMISSIVE,
    )

    assert first.valid
    assert first.dataset_fingerprint == second.dataset_fingerprint
    assert sum(plan.split_counts.values()) == 12
    assert "degradation:skew" in plan.evaluation_slices
    assert "hallucination-rate" in plan.required_metrics
    assert len(plan.source_tree_fingerprint) == 64


def test_dataset_rejects_group_leakage_and_research_data_in_commercial_lane(
    tmp_path: Path,
) -> None:
    train = _sample(tmp_path, "train", document_id="same", split=SplitName.TRAIN)
    test = _sample(tmp_path, "test", document_id="same", split=SplitName.TEST)
    research = _sample(
        tmp_path,
        "research",
        lane=DataUsageLane.RESEARCH_ONLY,
        split=SplitName.TRAIN,
    )
    manifest = DatasetManifest(name="unsafe", samples=[train, test, research])

    report = validate_dataset(manifest, lane=DataUsageLane.COMMERCIAL_PERMISSIVE)

    assert not report.valid
    assert "document_id:same" in report.leakage_groups
    assert any("research-only" in error for error in report.rights_errors)


def test_private_opt_in_requires_consent_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="consent_scope"):
        _sample(tmp_path, "private", lane=DataUsageLane.PRIVATE_OPT_IN)


def test_training_cli_validates_relative_manifest_and_writes_dry_run(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    truth = tmp_path / "page.md"
    source.write_bytes(b"fixture")
    truth.write_text("content\n", encoding="utf-8")
    manifest = tmp_path / "dataset.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "cli-fixture",
                "samples": [
                    {
                        "id": "one",
                        "document_id": "one",
                        "source": "page.png",
                        "ground_truth": "page.md",
                        "usage_lane": "commercial-permissive",
                        "license_id": "CC0-1.0",
                        "commercial_use": True,
                        "redistribution": True,
                        "pii_status": "none",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "validation.json"
    plan_path = tmp_path / "plan.json"
    runner = CliRunner()

    validation = runner.invoke(
        cli,
        [
            "dataset-validate",
            str(manifest),
            "--lane",
            "commercial-permissive",
            "--output",
            str(report_path),
        ],
    )
    planning = runner.invoke(
        cli,
        [
            "train-plan",
            str(manifest),
            "--lane",
            "commercial-permissive",
            "--output",
            str(plan_path),
        ],
    )

    assert validation.exit_code == 0, validation.output
    assert planning.exit_code == 0, planning.output
    assert json.loads(report_path.read_text(encoding="utf-8"))["valid"] is True
    assert json.loads(plan_path.read_text(encoding="utf-8"))["executable"] is False
