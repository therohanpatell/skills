"""The analysis contract: what the transformation needs from the source data."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ColumnRole(str, Enum):
    JOIN_KEY = "JOIN KEY"
    FILTER = "FILTER"
    CONDITION = "CONDITION"
    GROUP_BY = "GROUP BY"
    ORDER_BY = "ORDER BY"
    DEDUP_KEY = "DEDUP KEY"
    WINDOW = "WINDOW"
    NULL_DEFAULT = "NULL/DEFAULT"
    AGGREGATE = "AGGREGATE"
    ONE_TO_ONE = "1:1 MAPPING"
    NOT_NULL_FILLER = "NOT NULL FILLER"
    UNUSED = "UNUSED"

    @property
    def transformation_critical(self) -> bool:
        return self in {
            ColumnRole.JOIN_KEY,
            ColumnRole.FILTER,
            ColumnRole.CONDITION,
            ColumnRole.GROUP_BY,
            ColumnRole.ORDER_BY,
            ColumnRole.DEDUP_KEY,
            ColumnRole.WINDOW,
            ColumnRole.NULL_DEFAULT,
            ColumnRole.AGGREGATE,
        }


class ConstraintOp(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"
    IN = "in"
    NOT_IN = "not_in"
    LIKE = "like"
    NOT_LIKE = "not_like"
    BETWEEN = "between"
    NOT_BETWEEN = "not_between"
    JOIN_MATCH = "join_match"
    JOIN_NO_MATCH = "join_no_match"
    GROUP_DUPLICATE = "group_duplicate"
    GROUP_SINGLE = "group_single"
    ANY = "any"


class Constraint(BaseModel):
    """One requirement placed on a single source column by one path."""

    table: str
    column: str
    op: ConstraintOp
    value: Any = None
    values: list[Any] = Field(default_factory=list)
    peer_table: str | None = None       # for joins: the table on the other side
    peer_column: str | None = None
    # True when meeting this constraint means the row is discarded by a filter,
    # so nothing downstream of that filter can be proven by the same row.
    excludes_row: bool = False

    @property
    def key(self) -> str:
        return f"{self.table}.{self.column}"

    def conflicts_with(self, other: "Constraint") -> bool:
        """Two constraints conflict when no single value can satisfy both."""
        if self.key != other.key:
            return False
        a, b = self, other
        if a.op == ConstraintOp.ANY or b.op == ConstraintOp.ANY:
            return False
        nulls = {ConstraintOp.IS_NULL}
        non_null_ops = {
            ConstraintOp.IS_NOT_NULL, ConstraintOp.EQ, ConstraintOp.GT, ConstraintOp.GTE,
            ConstraintOp.LT, ConstraintOp.LTE, ConstraintOp.IN, ConstraintOp.LIKE,
            ConstraintOp.BETWEEN, ConstraintOp.JOIN_MATCH,
        }
        if (a.op in nulls) != (b.op in nulls):
            return (a.op in nulls and b.op in non_null_ops) or (b.op in nulls and a.op in non_null_ops)
        if a.op in nulls and b.op in nulls:
            return False
        if a.op == ConstraintOp.EQ and b.op == ConstraintOp.EQ:
            return a.value != b.value
        if a.op == ConstraintOp.EQ and b.op == ConstraintOp.NE:
            return a.value == b.value
        if b.op == ConstraintOp.EQ and a.op == ConstraintOp.NE:
            return a.value == b.value
        join_ops = {ConstraintOp.JOIN_MATCH, ConstraintOp.JOIN_NO_MATCH}
        if a.op in join_ops and b.op in join_ops:
            return a.op != b.op
        group_ops = {ConstraintOp.GROUP_DUPLICATE, ConstraintOp.GROUP_SINGLE}
        if a.op in group_ops and b.op in group_ops:
            return a.op != b.op
        numeric_pairs = {
            (ConstraintOp.GT, ConstraintOp.LT), (ConstraintOp.GT, ConstraintOp.LTE),
            (ConstraintOp.GTE, ConstraintOp.LT), (ConstraintOp.GTE, ConstraintOp.LTE),
        }
        pair = (a.op, b.op)
        if pair in numeric_pairs or pair[::-1] in numeric_pairs:
            try:
                lo = float(a.value if a.op in {ConstraintOp.GT, ConstraintOp.GTE} else b.value)
                hi = float(b.value if a.op in {ConstraintOp.GT, ConstraintOp.GTE} else a.value)
            except (TypeError, ValueError):
                return True
            return lo >= hi
        if a.op == b.op and a.op in {ConstraintOp.GT, ConstraintOp.GTE, ConstraintOp.LT, ConstraintOp.LTE}:
            return False
        # Anything else that isn't provably compatible is treated as conflicting,
        # which only ever costs an extra row -- never a missed path.
        return a.op != b.op or a.value != b.value


class TransformationPath(BaseModel):
    id: str                          # e.g. T001.PASS
    transformation_id: str
    label: str                       # PASS / FAIL / NULL / MATCH ...
    description: str
    constraints: list[Constraint] = Field(default_factory=list)


class Transformation(BaseModel):
    id: str                          # T001
    kind: str                        # filter | condition | join | null_default | group_by | order_by | dedup | window
    expression: str
    description: str
    table: str | None = None
    column: str | None = None
    paths: list[TransformationPath] = Field(default_factory=list)


class RequiredColumn(BaseModel):
    table: str
    column: str
    role: ColumnRole = ColumnRole.UNUSED
    required: bool = False
    reason: str = ""
    data_type: str = "STRING"
    nullable: bool = True


class Scenario(BaseModel):
    id: str                          # TC001
    description: str
    covers: list[str] = Field(default_factory=list)      # path ids
    constraints: list[Constraint] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    tables_used: list[str] = Field(default_factory=list)
    tables_ignored: list[str] = Field(default_factory=list)
    required_columns: list[RequiredColumn] = Field(default_factory=list)
    transformations: list[Transformation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source: str = "static"           # static | llm | hybrid | cache
    model: str | None = None
    skills_used: list[str] = Field(default_factory=list)

    @property
    def all_paths(self) -> list[TransformationPath]:
        return [p for t in self.transformations for p in t.paths]

    def columns_by_role(self, *roles: ColumnRole) -> list[RequiredColumn]:
        wanted = set(roles)
        return [c for c in self.required_columns if c.role in wanted]

    @property
    def transformation_columns(self) -> list[RequiredColumn]:
        return [c for c in self.required_columns if c.role.transformation_critical]
