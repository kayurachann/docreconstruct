"""Public OmniDocBench oracle projection and conversion API."""

from .conversion import convert_omnidocbench_oracle_page
from .dataset import convert_omnidocbench_oracle_dataset
from .models import (
    OmniDocBenchConversionReason,
    OmniDocBenchConversionWarning,
    OmniDocBenchDatasetConversionReport,
    OmniDocBenchOracleContractError,
    OmniDocBenchOracleConversion,
    OmniDocBenchOracleConversionError,
    OmniDocBenchPageConversionReport,
    OmniDocBenchProjectionDiagnostic,
    OmniDocBenchProjectionReason,
)
from .projection import validate_omnidocbench_projection

__all__ = [
    "OmniDocBenchConversionReason",
    "OmniDocBenchConversionWarning",
    "OmniDocBenchDatasetConversionReport",
    "OmniDocBenchOracleContractError",
    "OmniDocBenchOracleConversion",
    "OmniDocBenchOracleConversionError",
    "OmniDocBenchPageConversionReport",
    "OmniDocBenchProjectionDiagnostic",
    "OmniDocBenchProjectionReason",
    "convert_omnidocbench_oracle_dataset",
    "convert_omnidocbench_oracle_page",
    "validate_omnidocbench_projection",
]
