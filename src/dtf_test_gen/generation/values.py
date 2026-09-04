"""Deterministic value generation.

Given a BigQuery type and (optionally) a constraint, produce a value. The same
input always produces the same output -- no randomness anywhere, so two runs of
the same DTF give byte-identical SQL.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from dtf_test_gen.models.analysis import Constraint, ConstraintOp

_BASE_DATE = _dt.date(2026, 1, 1)
_NUMERIC = {"INT64", "INTEGER", "INT", "SMALLINT", "BIGINT", "TINYINT",
            "NUMERIC", "DECIMAL", "BIGNUMERIC", "FLOAT64", "FLOAT", "DOUBLE"}
_INTEGERS = {"INT64", "INTEGER", "INT", "SMALLINT", "BIGINT", "TINYINT"}
_STRINGS = {"STRING", "TEXT", "VARCHAR", "CHAR", "BYTES"}
_STEP = 50          # how far past a boundary to sit, for `>` / `<`


def base_type(data_type: str) -> str:
    """Strip parameters and array wrappers: `NUMERIC(10,2)` -> `NUMERIC`."""
    text = (data_type or "STRING").strip().upper()
    text = re.sub(r"^ARRAY\s*<\s*(.+?)\s*>$", r"\1", text)
    return re.sub(r"\(.*\)$", "", text).strip()


def is_numeric(data_type: str) -> bool:
    return base_type(data_type) in _NUMERIC


def default_value(data_type: str, column: str, index: int) -> Any:
    """A simple, deterministic value for a column with no transformation opinion."""
    kind = base_type(data_type)
    if kind in _INTEGERS:
        return index
    if kind in {"NUMERIC", "DECIMAL", "BIGNUMERIC", "FLOAT64", "FLOAT", "DOUBLE"}:
        return float(index * 10)
    if kind == "BOOL" or kind == "BOOLEAN":
        return index % 2 == 1
    if kind == "DATE":
        return (_BASE_DATE + _dt.timedelta(days=index - 1)).isoformat()
    if kind in {"TIMESTAMP", "DATETIME"}:
        stamp = _dt.datetime.combine(_BASE_DATE + _dt.timedelta(days=index - 1), _dt.time())
        return stamp.strftime("%Y-%m-%d %H:%M:%S")
    if kind == "TIME":
        return f"{index % 24:02d}:00:00"
    if kind in {"JSON", "STRUCT", "RECORD"}:
        return "{}"
    prefix = re.sub(r"[^A-Za-z0-9]", "", column).upper()[:12] or "TEST"
    return f"{prefix}_{index:03d}"


def _shift_numeric(value: Any, delta: float, data_type: str) -> Any:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default_value(data_type, "value", 1)
    shifted = number + delta
    if base_type(data_type) in _INTEGERS or (isinstance(value, int) and not isinstance(value, bool)):
        return int(shifted)
    return round(shifted, 4)


def _other_string(value: Any, column: str) -> str:
    text = str(value) if value is not None else ""
    candidate = f"NOT_{text}"[:60]
    return candidate if candidate != text else f"OTHER_{column.upper()[:10]}"


def value_for(constraint: Constraint, data_type: str, index: int = 1) -> Any:
    """The value that satisfies one constraint, chosen deterministically."""
    column = constraint.column
    op = constraint.op
    value = constraint.value
    values = constraint.values

    if op == ConstraintOp.IS_NULL:
        return None
    if op in {ConstraintOp.IS_NOT_NULL, ConstraintOp.ANY}:
        return default_value(data_type, column, index)
    if op == ConstraintOp.EQ:
        return value
    if op == ConstraintOp.NE:
        if is_numeric(data_type):
            return _shift_numeric(value if value is not None else 0, _STEP, data_type)
        if base_type(data_type) in {"BOOL", "BOOLEAN"}:
            return not bool(value)
        return _other_string(value, column)
    if op == ConstraintOp.GT:
        return _shift_numeric(value, _STEP, data_type) if is_numeric(data_type) else _date_shift(value, 1, data_type)
    if op == ConstraintOp.GTE:
        return value if is_numeric(data_type) or _is_temporal(data_type) else value
    if op == ConstraintOp.LT:
        return _shift_numeric(value, -_STEP, data_type) if is_numeric(data_type) else _date_shift(value, -1, data_type)
    if op == ConstraintOp.LTE:
        return value
    if op == ConstraintOp.IN:
        return values[0] if values else value
    if op == ConstraintOp.NOT_IN:
        if is_numeric(data_type):
            largest = max((v for v in values if isinstance(v, (int, float))), default=0)
            return _shift_numeric(largest, _STEP, data_type)
        return _other_string(values[0] if values else value, column)
    if op == ConstraintOp.LIKE:
        return _from_like_pattern(str(value or ""), column)
    if op == ConstraintOp.NOT_LIKE:
        return f"NO_MATCH_{column.upper()[:10]}"
    if op == ConstraintOp.BETWEEN:
        lo, hi = (list(values) + [None, None])[:2]
        if is_numeric(data_type):
            try:
                mid = (float(lo) + float(hi)) / 2
                return int(mid) if base_type(data_type) in _INTEGERS else round(mid, 4)
            except (TypeError, ValueError):
                return lo
        return lo
    if op == ConstraintOp.NOT_BETWEEN:
        hi = (list(values) + [None, None])[1]
        if is_numeric(data_type):
            return _shift_numeric(hi, _STEP, data_type)
        return _other_string(hi, column)
    if op in {ConstraintOp.GROUP_DUPLICATE, ConstraintOp.GROUP_SINGLE}:
        return default_value(data_type, column, index)
    # Join constraints are resolved by the row builder, which knows both sides.
    return default_value(data_type, column, index)


def _is_temporal(data_type: str) -> bool:
    return base_type(data_type) in {"DATE", "TIMESTAMP", "DATETIME", "TIME"}


def _date_shift(value: Any, days: int, data_type: str) -> Any:
    text = str(value or "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = _dt.datetime.strptime(text[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
        shifted = parsed + _dt.timedelta(days=days)
        return shifted.strftime(fmt)
    return default_value(data_type, "value", 1 if days > 0 else 2)


def _from_like_pattern(pattern: str, column: str) -> str:
    """Build the shortest string matching a LIKE pattern."""
    out: list[str] = []
    for ch in pattern:
        if ch == "%":
            out.append("X")
        elif ch == "_":
            out.append("A")
        else:
            out.append(ch)
    text = "".join(out)
    return text or f"{column.upper()[:10]}_001"
