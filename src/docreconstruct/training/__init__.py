"""License-aware datasets and reproducible trainer plans.

The core package deliberately plans and validates training without importing a
specific deep-learning stack.  Actual fine-tuning is supplied by an opt-in
``docreconstruct.trainers`` entry point so a normal installation stays small
and no model or dataset is downloaded implicitly.
"""

from .models import (
    DatasetManifest,
    DatasetSample,
    DatasetValidationReport,
    DataUsageLane,
    SplitName,
    TrainerDescriptor,
    TrainingPlan,
)
from .planner import (
    TRAINER_CATALOG,
    build_training_plan,
    load_dataset_manifest,
    validate_dataset,
)

__all__ = [
    "TRAINER_CATALOG",
    "DatasetManifest",
    "DatasetSample",
    "DatasetValidationReport",
    "DataUsageLane",
    "SplitName",
    "TrainerDescriptor",
    "TrainingPlan",
    "build_training_plan",
    "load_dataset_manifest",
    "validate_dataset",
]
