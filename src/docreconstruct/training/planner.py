"""Deterministic dataset validation and model-agnostic training plans."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from .models import (
    DatasetManifest,
    DatasetSample,
    DatasetValidationReport,
    DataUsageLane,
    SplitName,
    TrainerDescriptor,
    TrainingPlan,
)

_SOURCE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
_GROUND_TRUTH_SUFFIXES = {".md", ".markdown", ".json", ".jsonl"}

TRAINER_CATALOG: dict[str, TrainerDescriptor] = {
    "olmocr": TrainerDescriptor(
        id="olmocr",
        title="olmOCR supervised/RL document-to-Markdown training",
        project_url="https://github.com/allenai/olmocr",
        tasks=["page-to-markdown", "handwriting", "formula", "table", "reading-order"],
        execution_modes=["local-gpu", "cluster"],
        entry_point="olmocr",
        license_notes="Apache-2.0 code; every training sample keeps its own license lane.",
    ),
    "paddleocr": TrainerDescriptor(
        id="paddleocr",
        title="PaddleOCR detection/recognition/layout specialists",
        project_url="https://github.com/PaddlePaddle/PaddleOCR",
        tasks=["text-detection", "text-recognition", "layout", "table", "formula"],
        execution_modes=["local-cpu", "local-gpu", "cluster"],
        entry_point="paddleocr",
        license_notes="Apache-2.0 code; model and dataset terms remain independently binding.",
    ),
    "doctr": TrainerDescriptor(
        id="doctr",
        title="docTR text detection and recognition training",
        project_url="https://github.com/mindee/doctr",
        tasks=["text-detection", "text-recognition", "rotated-text"],
        execution_modes=["local-gpu", "cluster"],
        entry_point="doctr",
        license_notes="Apache-2.0 code; it is not a full semantic Markdown trainer.",
    ),
    "tesseract": TrainerDescriptor(
        id="tesseract",
        title="Tesseract language/script recognition training",
        project_url="https://github.com/tesseract-ocr/tesstrain",
        tasks=["printed-text", "language-pack"],
        execution_modes=["local-cpu"],
        entry_point="tesseract",
        license_notes="Apache-2.0 code; intended as a deterministic baseline/specialist.",
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_tree_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _dataset_fingerprint(samples: list[DatasetSample]) -> str:
    rows = []
    for sample in sorted(samples, key=lambda item: item.id):
        rows.append(
            {
                "id": sample.id,
                "document_id": sample.document_id,
                "source": sample.source_sha256,
                "ground_truth": sample.ground_truth_sha256,
                "split": sample.split,
                "lane": sample.usage_lane,
                "license": sample.license_id,
                "groups": sample.group_ids,
            }
        )
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_dataset_manifest(source: str | Path) -> DatasetManifest:
    """Load JSON and resolve sample paths relative to the manifest."""

    path = Path(source).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("training dataset manifest must be a JSON object")
    samples = payload.get("samples")
    if not isinstance(samples, list):
        raise ValueError("training dataset manifest requires a samples array")
    normalized: list[dict[str, Any]] = []
    for raw in samples:
        if not isinstance(raw, dict):
            raise ValueError("every training sample must be an object")
        item = dict(raw)
        for field in ("source", "ground_truth"):
            value = Path(str(item.get(field, ""))).expanduser()
            item[field] = value if value.is_absolute() else (path.parent / value).resolve()
        normalized.append(item)
    return DatasetManifest.model_validate({**payload, "samples": normalized})


def _assigned_split(sample: DatasetSample, seed: int) -> SplitName:
    if sample.split is not None:
        return sample.split
    group = sample.group_ids.get("source_document", sample.document_id)
    value = int(hashlib.sha256(f"{seed}:{group}".encode()).hexdigest()[:8], 16) % 100
    if value < 80:
        return SplitName.TRAIN
    if value < 90:
        return SplitName.VALIDATION
    return SplitName.TEST


def _rights_error(sample: DatasetSample, lane: DataUsageLane) -> str | None:
    if sample.usage_lane is DataUsageLane.UNKNOWN:
        return f"{sample.id}: usage_lane is unknown"
    if lane is DataUsageLane.COMMERCIAL_PERMISSIVE:
        if sample.usage_lane is not DataUsageLane.COMMERCIAL_PERMISSIVE:
            return f"{sample.id}: {sample.usage_lane.value} is excluded from commercial training"
        if sample.commercial_use is not True or not sample.license_id:
            return f"{sample.id}: commercial rights are not explicit"
    if (
        sample.pii_status.casefold() in {"contains-pii", "pii", "secret"}
        and not sample.consent_scope
    ):
        return f"{sample.id}: PII/secret data requires explicit consent_scope"
    return None


def validate_dataset(
    manifest: DatasetManifest,
    *,
    lane: DataUsageLane = DataUsageLane.RESEARCH_ONLY,
    verify_hashes: bool = True,
) -> DatasetValidationReport:
    """Validate rights, hashes, file types, duplicates, and split leakage."""

    missing: list[str] = []
    mismatches: list[str] = []
    rights: list[str] = []
    warnings: list[str] = []
    source_hashes: dict[str, list[str]] = defaultdict(list)
    group_splits: dict[str, set[str]] = defaultdict(set)
    split_counts: Counter[str] = Counter()
    lane_counts: Counter[str] = Counter()
    normalized: list[DatasetSample] = []

    for sample in manifest.samples:
        split = _assigned_split(sample, manifest.seed)
        source = sample.source.resolve()
        truth = sample.ground_truth.resolve()
        if source.suffix.casefold() not in _SOURCE_SUFFIXES:
            warnings.append(f"{sample.id}: unusual source suffix {source.suffix!r}")
        if truth.suffix.casefold() not in _GROUND_TRUTH_SUFFIXES:
            warnings.append(f"{sample.id}: unusual ground-truth suffix {truth.suffix!r}")
        if not source.is_file():
            missing.append(str(source))
        if not truth.is_file():
            missing.append(str(truth))
        source_digest = _sha256(source) if source.is_file() else None
        truth_digest = _sha256(truth) if truth.is_file() else None
        if verify_hashes and sample.source_sha256 and source_digest != sample.source_sha256:
            mismatches.append(f"{sample.id}: source_sha256")
        if (
            verify_hashes
            and sample.ground_truth_sha256
            and truth_digest != sample.ground_truth_sha256
        ):
            mismatches.append(f"{sample.id}: ground_truth_sha256")
        effective = sample.model_copy(
            update={
                "split": split,
                "source": source,
                "ground_truth": truth,
                "source_sha256": source_digest or sample.source_sha256,
                "ground_truth_sha256": truth_digest or sample.ground_truth_sha256,
            }
        )
        normalized.append(effective)
        if source_digest:
            source_hashes[source_digest].append(sample.id)
        split_counts[split.value] += 1
        lane_counts[sample.usage_lane.value] += 1
        for key, value in {"document_id": sample.document_id, **sample.group_ids}.items():
            group_splits[f"{key}:{value}"].add(split.value)
        error = _rights_error(sample, lane)
        if error:
            rights.append(error)

    duplicate_sources = [
        f"{digest}:{','.join(sorted(ids))}" for digest, ids in source_hashes.items() if len(ids) > 1
    ]
    leakage = [group for group, splits in group_splits.items() if len(splits) > 1]
    valid = not (missing or mismatches or leakage or duplicate_sources or rights)
    return DatasetValidationReport(
        valid=valid,
        dataset_fingerprint=_dataset_fingerprint(normalized),
        sample_count=len(normalized),
        split_counts=dict(sorted(split_counts.items())),
        lane_counts=dict(sorted(lane_counts.items())),
        missing_files=sorted(set(missing)),
        hash_mismatches=sorted(mismatches),
        leakage_groups=sorted(leakage),
        duplicate_sources=sorted(duplicate_sources),
        rights_errors=sorted(rights),
        warnings=sorted(warnings),
    )


def _plugin_available(name: str) -> bool:
    entry_points = importlib_metadata.entry_points(group="docreconstruct.trainers")
    return any(point.name == name for point in entry_points)


def build_training_plan(
    manifest: DatasetManifest,
    *,
    backend: str,
    lane: DataUsageLane = DataUsageLane.RESEARCH_ONLY,
) -> TrainingPlan:
    """Build a deterministic dry-run plan; never starts training implicitly."""

    try:
        descriptor = TRAINER_CATALOG[backend.strip().casefold()]
    except KeyError as exc:
        choices = ", ".join(TRAINER_CATALOG)
        raise ValueError(f"unknown trainer {backend!r}; choose {choices}") from exc
    report = validate_dataset(manifest, lane=lane)
    if not report.valid:
        raise ValueError("dataset validation failed; inspect dataset-validate report")
    assigned: dict[str, list[str]] = {split.value: [] for split in SplitName}
    slices: set[str] = set()
    for sample in manifest.samples:
        split = _assigned_split(sample, manifest.seed)
        assigned[split.value].append(sample.id)
        slices.update(f"language:{value}" for value in sample.languages)
        slices.update(f"script:{value}" for value in sample.scripts)
        slices.update(f"type:{value}" for value in sample.document_types)
        slices.update(f"content:{value}" for value in sample.content_kinds)
        slices.update(f"degradation:{value}" for value in sample.degradations)
    executable = _plugin_available(descriptor.entry_point)
    warnings = []
    if not executable:
        warnings.append(
            f"Trainer plugin {descriptor.entry_point!r} is not installed; this is a dry-run plan."
        )
    if lane is DataUsageLane.RESEARCH_ONLY:
        warnings.append(
            "Weights produced from research-only data must not enter commercial releases."
        )
    return TrainingPlan(
        backend=descriptor,
        dataset_name=manifest.name,
        dataset_version=manifest.version,
        dataset_fingerprint=report.dataset_fingerprint,
        seed=manifest.seed,
        usage_lane=lane,
        split_counts=report.split_counts,
        sample_ids={key: sorted(value) for key, value in assigned.items()},
        evaluation_slices=sorted(slices),
        required_metrics=[
            "grapheme-cer",
            "word-error-rate",
            "layout-map",
            "reading-order-edit",
            "table-teds",
            "formula-structure",
            "markdown-ast-validity",
            "hallucination-rate",
            "confidence-calibration",
        ],
        source_tree_fingerprint=_source_tree_fingerprint(),
        executable=executable,
        warnings=warnings,
    )
