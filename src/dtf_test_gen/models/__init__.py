"""Pydantic models shared by every layer of the engine."""

from dtf_test_gen.models.schema import Column, Table, DDLSet
from dtf_test_gen.models.dtf import (
    DTFConfig,
    JoinSpec,
    ColumnMapping,
    PredicateSpec,
)
from dtf_test_gen.models.analysis import (
    ColumnRole,
    Constraint,
    ConstraintOp,
    TransformationPath,
    Transformation,
    RequiredColumn,
    AnalysisResult,
    Scenario,
)
from dtf_test_gen.models.generation import (
    GeneratedTable,
    GenerationResult,
    CoverageRow,
    CoverageReport,
)
from dtf_test_gen.models.llm import LLMAnalysis, LLMTransformation, LLMColumnRole

__all__ = [
    "Column",
    "Table",
    "DDLSet",
    "DTFConfig",
    "JoinSpec",
    "ColumnMapping",
    "PredicateSpec",
    "ColumnRole",
    "Constraint",
    "ConstraintOp",
    "TransformationPath",
    "Transformation",
    "RequiredColumn",
    "AnalysisResult",
    "Scenario",
    "GeneratedTable",
    "GenerationResult",
    "CoverageRow",
    "CoverageReport",
    "LLMAnalysis",
    "LLMTransformation",
    "LLMColumnRole",
]
