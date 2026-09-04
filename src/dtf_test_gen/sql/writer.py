"""Render generated rows as BigQuery INSERT statements.

Values are formatted here and only here, from Python values plus the DDL type --
the model is never asked to produce SQL.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from dtf_test_gen.generation.values import base_type
from dtf_test_gen.models.generation import GeneratedTable, GenerationResult
from dtf_test_gen.models.schema import DDLSet, Table

_QUOTED = {"STRING", "TEXT", "VARCHAR", "CHAR", "JSON"}
_TEMPORAL = {"DATE": "DATE", "DATETIME": "DATETIME", "TIME": "TIME", "TIMESTAMP": "TIMESTAMP"}


def literal(value: Any, data_type: str) -> str:
    """One BigQuery literal, typed from the DDL."""
    kind = base_type(data_type)
    if value is None:
        return "NULL"
    if kind in {"BOOL", "BOOLEAN"} or isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if kind in _TEMPORAL:
        return f"{_TEMPORAL[kind]} '{_escape(str(value))}'"
    if kind == "BYTES":
        return f"b'{_escape(str(value))}'"
    if kind in {"INT64", "INTEGER", "INT", "SMALLINT", "BIGINT", "TINYINT"}:
        try:
            return str(int(value))
        except (TypeError, ValueError):
            return f"'{_escape(str(value))}'"
    if kind in {"NUMERIC", "DECIMAL", "BIGNUMERIC", "FLOAT64", "FLOAT", "DOUBLE"}:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return f"'{_escape(str(value))}'"
        return str(int(number)) if number.is_integer() else str(number)
    if isinstance(value, (int, float)) and kind not in _QUOTED:
        return str(value)
    if isinstance(value, (_dt.date, _dt.datetime)):
        return f"'{value.isoformat()}'"
    return f"'{_escape(str(value))}'"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


def _quote_name(fq_name: str) -> str:
    return "`" + fq_name.strip("`") + "`"


def render_insert(generated: GeneratedTable, table: Table | None) -> str:
    """One INSERT ... VALUES statement carrying only the required columns."""
    if not generated.rows:
        return f"-- No rows generated for {generated.table}"

    types = {c.name.lower(): c.data_type for c in (table.columns if table else [])}
    columns = generated.columns
    header = ",\n  ".join(columns)
    lines = []
    for row in generated.rows:
        rendered = ", ".join(
            literal(row.get(name), types.get(name.lower(), "STRING")) for name in columns
        )
        lines.append(f"  ({rendered})")

    omitted = ""
    if generated.omitted_columns:
        shown = ", ".join(generated.omitted_columns[:8])
        more = f", +{len(generated.omitted_columns) - 8} more" if len(generated.omitted_columns) > 8 else ""
        omitted = f"-- Omitted (not used by the transformation): {shown}{more}\n"

    target = _quote_name(generated.fq_name or generated.table)
    return (
        f"{omitted}INSERT INTO {target}\n(\n  {header}\n)\nVALUES\n"
        + ",\n".join(lines)
        + ";"
    )


def render_all(result: GenerationResult, ddl: DDLSet, header: str | None = None) -> str:
    """Every table's INSERT, in one script."""
    blocks: list[str] = []
    if header:
        blocks.append("\n".join(f"-- {line}" for line in header.splitlines()))
    for generated in result.tables:
        blocks.append(render_insert(generated, ddl.get(generated.table)))
    return "\n\n".join(blocks) + "\n"
