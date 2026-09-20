"""The strict JSON contract the local model must return.

The model is asked for reasoning about *requirements only* -- never for SQL,
never for values, never for row layouts. Python expands this into paths,
values and INSERT statements deterministically.
"""

from __future__ import annotations

from typing import Any

from dataclasses import dataclass, field
from dtf_test_gen.models.base import Model


@dataclass(kw_only=True)
class LLMTransformation(Model):
    kind: str = "filter"
    table: str = ""
    column: str = ""
    operator: str = "eq"
    value: Any = None
    values: list[Any] = field(default_factory=list)
    description: str = ""
    # The expression this was read from, quoted from the input. A rule that
    # cites nothing was not found in the config -- it was invented.
    source_expression: str = ""

    def __post_init__(self):
        self.kind = self.kind.strip().lower()
        self.operator = self.operator.strip().lower()
        self.table = self.table.strip().strip("`")
        self.column = self.column.strip().strip("`")


@dataclass(kw_only=True)
class LLMColumnRole(Model):
    table: str = ""
    column: str = ""
    role: str = "UNUSED"
    reason: str = ""

    def __post_init__(self):
        self.table = self.table.strip().strip("`")
        self.column = self.column.strip().strip("`")
        self.role = self.role.strip().upper()


@dataclass(kw_only=True)
class LLMAnalysis(Model):
    transformations: list[LLMTransformation] = field(default_factory=list)
    column_roles: list[LLMColumnRole] = field(default_factory=list)
    scenario_hints: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
