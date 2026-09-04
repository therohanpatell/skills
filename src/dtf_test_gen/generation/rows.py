"""Turn packed scenarios into actual source rows, table by table.

Column selection is the point of this module: a table's INSERT carries only the
columns the transformation reads, plus any REQUIRED column BigQuery would reject
the insert without. Everything else is deliberately omitted.
"""

from __future__ import annotations

from typing import Any

from dtf_test_gen.generation.values import default_value, value_for
from dtf_test_gen.models.analysis import (
    AnalysisResult,
    ColumnRole,
    Constraint,
    ConstraintOp,
    Scenario,
)
from dtf_test_gen.models.generation import GeneratedTable, GenerationResult
from dtf_test_gen.models.schema import DDLSet, Table

# A join key that is guaranteed not to exist on the other side.
ORPHAN_INT = 999999
ORPHAN_STR = "NO_MATCH_KEY"


def _emitted_columns(table: Table, analysis: AnalysisResult) -> tuple[list[str], list[str]]:
    """(columns to emit, columns deliberately omitted)."""
    roles = {
        rc.column.lower(): rc for rc in analysis.required_columns
        if rc.table.lower() == table.name.lower()
    }
    emit: list[str] = []
    omit: list[str] = []
    for column in table.columns:
        rc = roles.get(column.name.lower())
        role = rc.role if rc else ColumnRole.UNUSED
        if role.transformation_critical:
            emit.append(column.name)
        elif column.required:
            # BigQuery rejects the INSERT without it, so a simple value goes in.
            emit.append(column.name)
        else:
            omit.append(column.name)
    return emit, omit


def _constraints_for(scenario: Scenario, table_name: str) -> dict[str, list[Constraint]]:
    grouped: dict[str, list[Constraint]] = {}
    for constraint in scenario.constraints:
        if constraint.table.lower() != table_name.lower():
            continue
        grouped.setdefault(constraint.column.lower(), []).append(constraint)
    return grouped


def _pick_constraint(constraints: list[Constraint]) -> Constraint:
    """When several constraints land on one column, the most specific one wins."""
    order = {
        ConstraintOp.EQ: 0, ConstraintOp.IS_NULL: 1, ConstraintOp.IN: 2,
        ConstraintOp.BETWEEN: 3, ConstraintOp.LIKE: 4,
        ConstraintOp.GT: 5, ConstraintOp.GTE: 5, ConstraintOp.LT: 5, ConstraintOp.LTE: 5,
        ConstraintOp.NE: 6, ConstraintOp.NOT_IN: 6, ConstraintOp.NOT_LIKE: 6,
        ConstraintOp.NOT_BETWEEN: 6, ConstraintOp.IS_NOT_NULL: 7,
        ConstraintOp.JOIN_MATCH: 8, ConstraintOp.JOIN_NO_MATCH: 8,
        ConstraintOp.GROUP_DUPLICATE: 9, ConstraintOp.ANY: 10,
    }
    return sorted(constraints, key=lambda c: order.get(c.op, 5))[0]


class RowBuilder:
    """Builds rows for every table, keeping join keys consistent across tables."""

    def __init__(self, analysis: AnalysisResult, ddl: DDLSet, max_rows: int = 50):
        self.analysis = analysis
        self.ddl = ddl
        self.max_rows = max_rows
        self.warnings: list[str] = []
        self._join_seq = 0

    def build(self, scenarios: list[Scenario]) -> GenerationResult:
        tables = [t for t in self.ddl.tables if t.name in set(self.analysis.tables_used)]
        if not tables:
            tables = list(self.ddl.tables)

        generated: dict[str, GeneratedTable] = {}
        for table in tables:
            emit, omit = _emitted_columns(table, self.analysis)
            generated[table.name] = GeneratedTable(
                table=table.name, fq_name=table.fq_name, columns=emit, omitted_columns=omit,
            )

        row_index: dict[str, int] = {t.name: 0 for t in tables}

        for scenario in scenarios:
            # Resolve join keys first, so both sides of a MATCH get the same value.
            join_keys = self._scenario_join_keys(scenario, row_index)
            for table in tables:
                constraints = _constraints_for(scenario, table.name)
                pinned = {
                    col: value for (tbl, col), value in join_keys.items()
                    if tbl == table.name.lower()
                }
                if not constraints and not pinned and row_index[table.name] > 0:
                    continue        # this scenario says nothing about this table
                target = generated[table.name]
                if len(target.rows) >= self.max_rows:
                    self.warnings.append(
                        f"Row cap of {self.max_rows} reached for `{table.name}`; "
                        "some scenarios were not materialised."
                    )
                    continue
                row_index[table.name] += 1
                rows = self._build_rows(
                    table, target.columns, constraints, row_index[table.name], pinned,
                )
                target.rows.extend(rows)
                row_index[table.name] += len(rows) - 1

        result = GenerationResult(
            tables=[t for t in generated.values() if t.rows],
            scenarios=scenarios,
            warnings=self.warnings,
        )
        result.naive_row_estimate = self._naive_estimate()
        return result

    def _scenario_join_keys(
        self, scenario: Scenario, row_index: dict[str, int]
    ) -> dict[tuple[str, str], object]:
        """Pin the join key on both sides of every MATCH in this scenario.

        Without this, the driving row and the row that is supposed to join to it
        get independent values and the MATCH path is only nominally covered.
        """
        pinned: dict[tuple[str, str], object] = {}
        for constraint in scenario.constraints:
            if constraint.op not in {ConstraintOp.JOIN_MATCH, ConstraintOp.JOIN_NO_MATCH}:
                continue
            table = self.ddl.get(constraint.table)
            column = table.column(constraint.column) if table else None
            if table is None or column is None:
                continue
            if constraint.op == ConstraintOp.JOIN_NO_MATCH:
                key = ORPHAN_INT if _numeric_column(column.data_type) else ORPHAN_STR
                pinned[(table.name.lower(), column.name.lower())] = key
                continue
            self._join_seq += 1
            key = default_value(column.data_type, column.name, self._join_seq)
            pinned[(table.name.lower(), column.name.lower())] = key
            peer = self.ddl.get(constraint.peer_table or "")
            peer_column = peer.column(constraint.peer_column or "") if peer else None
            if peer and peer_column:
                pinned[(peer.name.lower(), peer_column.name.lower())] = key
        return pinned

    def _build_rows(
        self,
        table: Table,
        columns: list[str],
        constraints: dict[str, list[Constraint]],
        index: int,
        pinned: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        row = self._build_row(table, columns, constraints, index, pinned)
        rows = [row]

        # GROUP BY MULTI_ROW needs a second row sharing the same group key.
        duplicate_columns = [
            col for col, items in constraints.items()
            if any(c.op == ConstraintOp.GROUP_DUPLICATE for c in items)
        ]
        if duplicate_columns:
            sibling = dict(row)
            for column in table.columns:
                if column.name not in columns:
                    continue
                if column.name.lower() in duplicate_columns:
                    continue        # keep the group/dedup key identical
                rc = self._role_of(table.name, column.name)
                role = rc.role if rc else None
                if role == ColumnRole.AGGREGATE:
                    # Differing measures make a wrong SUM visible in the output.
                    sibling[column.name] = self._vary(row.get(column.name), column.data_type, index)
                elif role == ColumnRole.ORDER_BY:
                    # The tiebreaker must differ, or which row survives the
                    # dedup is undefined and the test proves nothing.
                    sibling[column.name] = default_value(column.data_type, column.name, index + 1)
                elif column.required or self._is_unique_key(table, column.name):
                    sibling[column.name] = default_value(column.data_type, column.name, index + 1000)
            rows.append(sibling)
        return rows

    def _build_row(
        self,
        table: Table,
        columns: list[str],
        constraints: dict[str, list[Constraint]],
        index: int,
        pinned: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pinned = pinned or {}
        row: dict[str, Any] = {}
        for name in columns:
            column = table.column(name)
            if column is None:
                continue
            if name.lower() in pinned:
                row[name] = pinned[name.lower()]
                continue
            items = constraints.get(name.lower())
            if not items:
                row[name] = default_value(column.data_type, name, index)
                continue
            constraint = _pick_constraint(items)
            row[name] = value_for(constraint, column.data_type, index)
        return row

    def _role_of(self, table: str, column: str):
        for rc in self.analysis.required_columns:
            if rc.table.lower() == table.lower() and rc.column.lower() == column.lower():
                return rc
        return None

    def _is_unique_key(self, table: Table, column: str) -> bool:
        rc = self._role_of(table.name, column)
        if rc and rc.role in {ColumnRole.DEDUP_KEY}:
            return True
        return column.lower() in {"id", f"{table.name.lower()}_id"}

    @staticmethod
    def _vary(value: Any, data_type: str, index: int) -> Any:
        if isinstance(value, bool):
            return not value
        if isinstance(value, (int, float)):
            return type(value)(value + 100)
        return default_value(data_type, "value", index + 1)

    def _naive_estimate(self) -> int | None:
        """How many rows a naive 'one row per path per table' run would need.

        Only reported when it is a like-for-like comparison, so the UI never
        shows a misleading saving.
        """
        paths = self.analysis.all_paths
        if not paths:
            return None
        tables = len({c.table for p in paths for c in p.constraints})
        if tables == 0:
            return None
        return len(paths) * max(1, tables)


def _numeric_column(data_type: str) -> bool:
    from dtf_test_gen.generation.values import is_numeric
    return is_numeric(data_type)
