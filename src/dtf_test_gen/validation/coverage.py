"""Check every transformation path against the rows actually generated.

Coverage is verified against the real row values, not against the intent that
produced them -- so a bug in value generation shows up as missing coverage
rather than silently passing.
"""

from __future__ import annotations

from typing import Any

from dtf_test_gen.generation.values import base_type, is_numeric
from dtf_test_gen.models.analysis import AnalysisResult, Constraint, ConstraintOp, Scenario
from dtf_test_gen.models.generation import CoverageReport, CoverageRow, GenerationResult

_INTEGER_LIKE = {"INT64", "INTEGER", "INT", "SMALLINT", "BIGINT", "TINYINT",
                 "NUMERIC", "DECIMAL", "BIGNUMERIC", "FLOAT64", "FLOAT", "DOUBLE"}


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def satisfies(constraint: Constraint, row: dict[str, Any], all_rows: dict[str, list[dict]]) -> bool:
    """Does this row meet this constraint?"""
    column = _lookup(row, constraint.column)
    op = constraint.op
    present = _has_column(row, constraint.column)

    if op == ConstraintOp.ANY:
        return True
    if op == ConstraintOp.IS_NULL:
        return present and column is None
    if op == ConstraintOp.IS_NOT_NULL:
        return present and column is not None
    if op in {ConstraintOp.JOIN_MATCH, ConstraintOp.JOIN_NO_MATCH}:
        peer_rows = all_rows.get((constraint.peer_table or "").lower(), [])
        peer_values = {_lookup(r, constraint.peer_column or "") for r in peer_rows}
        matched = column in peer_values
        return matched if op == ConstraintOp.JOIN_MATCH else not matched
    if op in {ConstraintOp.GROUP_DUPLICATE, ConstraintOp.GROUP_SINGLE}:
        siblings = [
            r for r in all_rows.get(constraint.table.lower(), [])
            if _lookup(r, constraint.column) == column
        ]
        return len(siblings) > 1 if op == ConstraintOp.GROUP_DUPLICATE else len(siblings) == 1
    if column is None:
        return False

    if op == ConstraintOp.EQ:
        return _equal(column, constraint.value)
    if op == ConstraintOp.NE:
        return not _equal(column, constraint.value)
    if op in {ConstraintOp.GT, ConstraintOp.GTE, ConstraintOp.LT, ConstraintOp.LTE}:
        left, right = _as_number(column), _as_number(constraint.value)
        if left is None or right is None:
            left, right = str(column), str(constraint.value)
        return {
            ConstraintOp.GT: left > right, ConstraintOp.GTE: left >= right,
            ConstraintOp.LT: left < right, ConstraintOp.LTE: left <= right,
        }[op]
    if op == ConstraintOp.IN:
        return any(_equal(column, v) for v in constraint.values)
    if op == ConstraintOp.NOT_IN:
        return not any(_equal(column, v) for v in constraint.values)
    if op == ConstraintOp.LIKE:
        return _like(str(column), str(constraint.value or ""))
    if op == ConstraintOp.NOT_LIKE:
        return not _like(str(column), str(constraint.value or ""))
    if op in {ConstraintOp.BETWEEN, ConstraintOp.NOT_BETWEEN}:
        lo, hi = (list(constraint.values) + [None, None])[:2]
        left, low, high = _as_number(column), _as_number(lo), _as_number(hi)
        if None in (left, low, high):
            inside = str(lo) <= str(column) <= str(hi)
        else:
            inside = low <= left <= high
        return inside if op == ConstraintOp.BETWEEN else not inside
    return True


def _equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    na, nb = _as_number(a), _as_number(b)
    if na is not None and nb is not None:
        return abs(na - nb) < 1e-9
    return str(a) == str(b)


def _like(value: str, pattern: str) -> bool:
    import re
    regex = "^" + re.escape(pattern).replace(r"\%", ".*").replace("_", ".") + "$"
    return re.match(regex, value, re.I) is not None


def _lookup(row: dict[str, Any], column: str) -> Any:
    lowered = column.lower()
    for key, value in row.items():
        if key.lower() == lowered:
            return value
    return None


def _has_column(row: dict[str, Any], column: str) -> bool:
    lowered = column.lower()
    return any(k.lower() == lowered for k in row)


def evaluate_coverage(analysis: AnalysisResult, generation: GenerationResult) -> CoverageReport:
    rows_by_table = {t.table.lower(): t.rows for t in generation.tables}
    report = CoverageReport()

    for transformation in analysis.transformations:
        for path in transformation.paths:
            row = CoverageRow(
                transformation=transformation.description,
                transformation_id=transformation.id,
                path=path.label,
                path_id=path.id,
            )
            for scenario in generation.scenarios:
                scenario_id = scenario.id if isinstance(scenario, Scenario) else scenario.get("id")
                if path.id not in (scenario.covers if isinstance(scenario, Scenario) else scenario.get("covers", [])):
                    continue
                if _path_met(path.constraints, rows_by_table):
                    row.covered = True
                    row.covered_by.append(scenario_id)
            if not row.covered and _path_met(path.constraints, rows_by_table):
                # Covered incidentally by a row built for another scenario.
                row.covered = True
                row.covered_by.append("incidental")
            report.rows.append(row)
    return report


def _path_met(constraints: list[Constraint], rows_by_table: dict[str, list[dict]]) -> bool:
    """A path is met when one row satisfies all of its constraints for its table."""
    if not constraints:
        return True
    by_table: dict[str, list[Constraint]] = {}
    for constraint in constraints:
        by_table.setdefault(constraint.table.lower(), []).append(constraint)
    for table, items in by_table.items():
        rows = rows_by_table.get(table, [])
        if not any(all(satisfies(c, row, rows_by_table) for c in items) for row in rows):
            return False
    return True
