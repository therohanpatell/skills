"""Normalised representation of a DTF transformation configuration.

DTF configs come in many shapes in the wild: a raw SQL string, a structured
mapping document, or a mixture. The loader flattens all of them into this model
so the analyser never has to care about the on-disk format.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PredicateSpec(BaseModel):
    """A raw predicate string plus where it came from (WHERE, CASE, HAVING...)."""

    expression: str
    origin: str = "where"           # where | case | having | qualify | coalesce | filter
    column_hint: str | None = None


class JoinSpec(BaseModel):
    left_table: str
    right_table: str
    left_column: str
    right_column: str
    join_type: str = "INNER"        # INNER | LEFT | RIGHT | FULL | CROSS
    raw: str | None = None

    @property
    def optional_side(self) -> bool:
        """True when a non-matching row still survives the join (LEFT/RIGHT/FULL)."""
        return self.join_type.upper() in {"LEFT", "RIGHT", "FULL", "LEFT OUTER", "RIGHT OUTER", "FULL OUTER"}


class ColumnMapping(BaseModel):
    target_column: str
    expression: str | None = None
    source_table: str | None = None
    source_column: str | None = None

    @property
    def is_one_to_one(self) -> bool:
        """A mapping is 1:1 when the target is a bare copy of a source column."""
        if self.expression is None:
            return self.source_column is not None
        expr = self.expression.strip().strip("`")
        if not expr:
            return False
        bare = expr.split(".")[-1]
        return bare.replace("_", "").isalnum() and "(" not in expr and " " not in expr


class DTFConfig(BaseModel):
    name: str
    source_file: str | None = None
    raw_sql: str | None = None
    target_table: str | None = None
    source_tables: list[str] = Field(default_factory=list)
    table_aliases: dict[str, str] = Field(default_factory=dict)   # alias -> table
    source_table_fq: dict[str, str] = Field(default_factory=dict)  # table -> project.dataset.table
    joins: list[JoinSpec] = Field(default_factory=list)
    predicates: list[PredicateSpec] = Field(default_factory=list)
    mappings: list[ColumnMapping] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    aggregates: list[str] = Field(default_factory=list)
    dedup_keys: list[str] = Field(default_factory=list)
    window_partitions: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    def resolve_alias(self, alias: str | None) -> str | None:
        if not alias:
            return None
        return self.table_aliases.get(alias.lower(), alias)
