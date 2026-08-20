"""Process-isolated, source-only document parser benchmark harness.

Every candidate is an external argv template that receives exactly one source path
and one destination Markdown path. Ground truth is exposed only to the separately
configured official evaluator. Operational failures always materialize as empty
``.md`` predictions so the official evaluator keeps them in its denominator.

This facade preserves the original public imports after the implementation was split
into focused configuration, corpus, process-isolation, and runner modules.
"""

from ._common import SOURCE_BENCHMARK_SCHEMA_VERSION
from .corpus import load_omnidocbench_cases
from .manifest import load_source_benchmark_manifest
from .models import (
    EvaluatorRecord,
    OfficialEvaluator,
    SourceBenchmarkCase,
    SourceBenchmarkManifest,
    SourceBenchmarkReport,
    SourceBenchmarkSystem,
    SourceRunRecord,
    SourceRunStatus,
    SourceSystemSummary,
)
from .runner import SourceBenchmarkRunner, run_source_benchmark

__all__ = [
    "EvaluatorRecord",
    "OfficialEvaluator",
    "SOURCE_BENCHMARK_SCHEMA_VERSION",
    "SourceBenchmarkCase",
    "SourceBenchmarkManifest",
    "SourceBenchmarkReport",
    "SourceBenchmarkRunner",
    "SourceBenchmarkSystem",
    "SourceRunRecord",
    "SourceRunStatus",
    "SourceSystemSummary",
    "load_omnidocbench_cases",
    "load_source_benchmark_manifest",
    "run_source_benchmark",
]
