"""The strict JSON contract the local model must return.

The model is asked for reasoning about *requirements only* -- never for SQL,
never for values, never for row layouts. Python expands this into paths,
values and INSERT statements deterministically.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class LLMTransformation(BaseModel):
    kind: str = "filter"
    table: str = ""
    column: str = ""
    operator: str = "eq"
    value: Any = None
    values: list[Any] = Field(default_factory=list)
    description: str = ""
    # The expression this was read from, quoted from the input. A rule that
    # cites nothing was not found in the config -- it was invented.
    source_expression: str = ""

    @field_validator("kind", "operator", mode="before")
    @classmethod
    def _lower(cls, v: Any) -> str:
        return str(v or "").strip().lower()

    @field_validator("table", "column", mode="before")
    @classmethod
    def _clean_ident(cls, v: Any) -> str:
        return str(v or "").strip().strip("`")


class LLMColumnRole(BaseModel):
    table: str = ""
    column: str = ""
    role: str = "UNUSED"
    reason: str = ""

    @field_validator("table", "column", mode="before")
    @classmethod
    def _clean_ident(cls, v: Any) -> str:
        return str(v or "").strip().strip("`")

    @field_validator("role", mode="before")
    @classmethod
    def _upper(cls, v: Any) -> str:
        return str(v or "UNUSED").strip().upper()


class LLMAnalysis(BaseModel):
    transformations: list[LLMTransformation] = Field(default_factory=list)
    column_roles: list[LLMColumnRole] = Field(default_factory=list)
    scenario_hints: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
