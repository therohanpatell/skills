"""Deterministic analysis: everything Python can work out without the model.

This runs first and does most of the work. The model only ever adds what static
parsing could not classify -- which keeps the prompt (and the token bill) small.
"""

from __future__ import annotations

import re

from dtf_test_gen.analysis import sqlparse
from dtf_test_gen.analysis.predicates import build_predicate_transformation, parse_predicate
from dtf_test_gen.models.analysis import (
    AnalysisResult,
    ColumnRole,
    Constraint,
    ConstraintOp,
    RequiredColumn,
    Transformation,
    TransformationPath,
)
from dtf_test_gen.models.dtf import DTFConfig
from dtf_test_gen.models.schema import DDLSet

_ROLE_PRIORITY = [
    ColumnRole.JOIN_KEY, ColumnRole.FILTER, ColumnRole.CONDITION, ColumnRole.NULL_DEFAULT,
    ColumnRole.GROUP_BY, ColumnRole.DEDUP_KEY, ColumnRole.WINDOW, ColumnRole.AGGREGATE,
    ColumnRole.ORDER_BY, ColumnRole.ONE_TO_ONE, ColumnRole.NOT_NULL_FILLER, ColumnRole.UNUSED,
]
_ORIGIN_KIND = {
    "where": "filter", "filter": "filter", "having": "filter",
    "qualify": "filter", "case": "condition", "coalesce": "null_default",
}
_ORIGIN_ROLE = {
    "where": ColumnRole.FILTER, "filter": ColumnRole.FILTER, "having": ColumnRole.FILTER,
    "qualify": ColumnRole.FILTER, "case": ColumnRole.CONDITION, "coalesce": ColumnRole.NULL_DEFAULT,
}


class _RoleTable:
    """Accumulates column roles, keeping the strongest role seen for each column."""

    def __init__(self) -> None:
        self._roles: dict[tuple[str, str], RequiredColumn] = {}

    def add(self, table: str, column: str, role: ColumnRole, reason: str) -> None:
        key = (table.lower(), column.lower())
        existing = self._roles.get(key)
        if existing and _ROLE_PRIORITY.index(existing.role) <= _ROLE_PRIORITY.index(role):
            return
        self._roles[key] = RequiredColumn(
            table=table, column=column, role=role,
            required=role.transformation_critical, reason=reason,
        )

    def values(self) -> list[RequiredColumn]:
        return list(self._roles.values())

    def has(self, table: str, column: str) -> bool:
        return (table.lower(), column.lower()) in self._roles


def _resolve_table(
    alias: str | None, config: DTFConfig, ddl: DDLSet, default: str | None
) -> str | None:
    """Map an alias (or nothing) to a real table name, preferring the DDL."""
    if alias:
        resolved = config.table_aliases.get(alias.lower(), alias)
        table = ddl.get(resolved)
        if table:
            return table.name
        if resolved.lower() in {t.lower() for t in config.source_tables}:
            return resolved
    return default


def _owner_of_column(column: str, config: DTFConfig, ddl: DDLSet) -> str | None:
    """Find which selected source table actually declares this column."""
    candidates = [t for t in config.source_tables if (tbl := ddl.get(t)) and tbl.column(column)]
    if len(candidates) == 1:
        return ddl.get(candidates[0]).name
    if candidates:
        return ddl.get(candidates[0]).name
    return None


def analyse_static(config: DTFConfig, ddl: DDLSet) -> AnalysisResult:
    roles = _RoleTable()
    transformations: list[Transformation] = []
    warnings: list[str] = []
    notes: list[str] = []
    counter = 0

    known_tables = [t.name for t in ddl.tables]

    # A config read straight from a *.json.template still carries placeholders,
    # which look like table names but resolve to nothing. Say so plainly.
    placeholders = [
        name for name in config.source_tables
        if any(marker in name for marker in ("{{", "${", "<%", "}}"))
    ]
    if placeholders:
        warnings.append(
            "Unresolved template placeholder(s) in the DTF source tables: "
            + ", ".join(f"`{p}`" for p in placeholders)
            + ". Fill the template in (or point at a rendered config) before generating."
        )
    default_table = None
    for name in config.source_tables:
        table = ddl.get(name)
        if table:
            default_table = table.name
            break

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"T{counter:03d}"

    # --- joins -----------------------------------------------------------
    seen_joins: set[str] = set()
    for join in config.joins:
        left = _resolve_table(join.left_table, config, ddl, default_table) or join.left_table
        right = _resolve_table(join.right_table, config, ddl, default_table) or join.right_table
        key = f"{left}.{join.left_column}={right}.{join.right_column}".lower()
        if key in seen_joins:
            continue
        seen_joins.add(key)

        tid = next_id()
        roles.add(left, join.left_column, ColumnRole.JOIN_KEY,
                  f"{join.join_type} JOIN key to {right}.{join.right_column}")
        roles.add(right, join.right_column, ColumnRole.JOIN_KEY,
                  f"{join.join_type} JOIN key from {left}.{join.left_column}")

        description = (
            f"{join.join_type} JOIN {left}.{join.left_column} = {right}.{join.right_column}"
        )
        paths = [
            TransformationPath(
                id=f"{tid}.MATCH", transformation_id=tid, label="MATCH",
                description=f"{left} row has a matching {right} row",
                constraints=[Constraint(
                    table=left, column=join.left_column, op=ConstraintOp.JOIN_MATCH,
                    peer_table=right, peer_column=join.right_column,
                )],
            ),
        ]
        # A NO_MATCH row only survives an outer join, so it is only a real path there.
        if join.optional_side:
            paths.append(TransformationPath(
                id=f"{tid}.NO_MATCH", transformation_id=tid, label="NO_MATCH",
                description=f"{left} row has no matching {right} row",
                constraints=[Constraint(
                    table=left, column=join.left_column, op=ConstraintOp.JOIN_NO_MATCH,
                    peer_table=right, peer_column=join.right_column,
                )],
            ))
        else:
            notes.append(
                f"{join.join_type} JOIN on {left}.{join.left_column}: NO_MATCH drops the row, "
                "so only the MATCH path is generated."
            )
        transformations.append(Transformation(
            id=tid, kind="join", expression=join.raw or description,
            description=description, table=left, column=join.left_column, paths=paths,
        ))

    # --- predicates ------------------------------------------------------
    seen_predicates: set[str] = set()
    for spec in config.predicates:
        expression = spec.expression.strip()
        if expression.lower() in seen_predicates:
            continue
        seen_predicates.add(expression.lower())

        parsed = parse_predicate(expression)
        if parsed is None:
            if re.search(r"\w+\.\w+\s*=\s*\w+\.\w+", expression):
                continue        # a join condition already handled above
            warnings.append(f"Predicate not understood by static parsing: `{expression}`")
            for alias, column in sqlparse.referenced_columns(expression):
                table = _resolve_table(alias, config, ddl, None) or _owner_of_column(column, config, ddl)
                if table:
                    roles.add(table, column, ColumnRole.CONDITION, f"Referenced in `{expression}`")
            continue

        table = (
            _resolve_table(parsed.alias, config, ddl, None)
            or _owner_of_column(parsed.column, config, ddl)
            or default_table
        )
        if table is None:
            warnings.append(f"Could not attribute `{expression}` to a selected table.")
            continue
        ddl_table = ddl.get(table)
        if ddl_table and not ddl_table.column(parsed.column):
            warnings.append(
                f"Column `{parsed.column}` in `{expression}` is not in the DDL for `{table}`."
            )

        kind = _ORIGIN_KIND.get(spec.origin, "filter")
        role = _ORIGIN_ROLE.get(spec.origin, ColumnRole.FILTER)
        roles.add(table, parsed.column, role, f"Used in `{expression}` ({spec.origin})")
        transformations.append(
            build_predicate_transformation(next_id(), table, parsed, expression, kind)
        )

    # --- grouping / aggregation -------------------------------------------
    for ref in config.group_by:
        alias, column = (ref.split(".")[-2] if "." in ref else None), ref.split(".")[-1]
        table = _resolve_table(alias, config, ddl, None) or _owner_of_column(column, config, ddl) or default_table
        if not table:
            continue
        roles.add(table, column, ColumnRole.GROUP_BY, f"GROUP BY {ref}")
        tid = next_id()
        transformations.append(Transformation(
            id=tid, kind="group_by", expression=f"GROUP BY {ref}",
            description=f"GROUP BY {table}.{column}", table=table, column=column,
            paths=[
                TransformationPath(
                    id=f"{tid}.MULTI_ROW", transformation_id=tid, label="MULTI_ROW",
                    description=f"A group of {table}.{column} holds more than one source row",
                    constraints=[Constraint(
                        table=table, column=column, op=ConstraintOp.GROUP_DUPLICATE,
                    )],
                ),
                TransformationPath(
                    id=f"{tid}.SINGLE_ROW", transformation_id=tid, label="SINGLE_ROW",
                    description=f"A group of {table}.{column} holds exactly one source row",
                    constraints=[Constraint(
                        table=table, column=column, op=ConstraintOp.GROUP_SINGLE,
                    )],
                ),
            ],
        ))

    for ref in config.aggregates:
        tokens = [tok for tok in re.split(r"[().,\s]+", str(ref).strip()) if tok]
        if not tokens:
            continue
        # `SUM(amount)` -> `amount`; `amount` -> `amount`. Skip the function name.
        column = tokens[-1].split(".")[-1].strip("`")
        if column.upper() in {"DISTINCT", "SUM", "COUNT", "AVG", "MIN", "MAX"} and len(tokens) > 1:
            column = tokens[-2].split(".")[-1].strip("`")
        if not column or column == "*":
            continue
        table = _owner_of_column(column, config, ddl) or default_table
        if table:
            roles.add(table, column, ColumnRole.AGGREGATE, f"Aggregated: {ref}")

    for ref in config.window_partitions:
        column = ref.split(".")[-1].strip("`")
        alias = ref.split(".")[-2] if "." in ref else None
        table = _resolve_table(alias, config, ddl, None) or _owner_of_column(column, config, ddl) or default_table
        if table:
            roles.add(table, column, ColumnRole.WINDOW, f"PARTITION BY {ref}")

    for ref in config.dedup_keys:
        column = ref.split(".")[-1].strip("`")
        table = _owner_of_column(column, config, ddl) or default_table
        if not table or (ddl.get(table) and not ddl.get(table).column(column)):
            continue
        roles.add(table, column, ColumnRole.DEDUP_KEY, f"Deduplication key: {ref}")
        if any(t.kind == "group_by" and (t.column or "").lower() == column.lower()
               for t in transformations):
            continue        # GROUP BY already produced the duplicate-row paths
        tid = next_id()
        transformations.append(Transformation(
            id=tid, kind="dedup", expression=f"DEDUP ON {ref}",
            description=f"Deduplicate on {table}.{column}", table=table, column=column,
            paths=[
                TransformationPath(
                    id=f"{tid}.DUPLICATE", transformation_id=tid, label="DUPLICATE",
                    description=f"Two source rows share {table}.{column}, so one must be dropped",
                    constraints=[Constraint(
                        table=table, column=column, op=ConstraintOp.GROUP_DUPLICATE,
                    )],
                ),
                TransformationPath(
                    id=f"{tid}.UNIQUE", transformation_id=tid, label="UNIQUE",
                    description=f"{table}.{column} appears once, so the row is kept",
                    constraints=[Constraint(
                        table=table, column=column, op=ConstraintOp.GROUP_SINGLE,
                    )],
                ),
            ],
        ))

    for ref in config.order_by:
        column = ref.split(".")[-1].strip("`")
        alias = ref.split(".")[-2] if "." in ref else None
        table = _resolve_table(alias, config, ddl, None) or _owner_of_column(column, config, ddl) or default_table
        if table:
            roles.add(table, column, ColumnRole.ORDER_BY, f"ORDER BY {ref}")

    # --- mappings ---------------------------------------------------------
    for mapping in config.mappings:
        if mapping.is_one_to_one and mapping.source_column:
            table = (
                _resolve_table(mapping.source_table, config, ddl, None)
                or _owner_of_column(mapping.source_column, config, ddl)
                or default_table
            )
            if table:
                roles.add(table, mapping.source_column, ColumnRole.ONE_TO_ONE,
                          f"Copied straight to target.{mapping.target_column}")
            continue
        if mapping.expression:
            for alias, column in sqlparse.referenced_columns(mapping.expression):
                table = _resolve_table(alias, config, ddl, None) or _owner_of_column(column, config, ddl)
                if not table:
                    continue
                ddl_table = ddl.get(table)
                if ddl_table and not ddl_table.column(column):
                    continue
                if not roles.has(table, column):
                    roles.add(table, column, ColumnRole.ONE_TO_ONE,
                              f"Feeds target.{mapping.target_column} via `{mapping.expression}`")

    # --- everything else in the DDL is unused -----------------------------
    used_tables = {
        t.name for t in ddl.tables
        if any(rc.table.lower() == t.name.lower() for rc in roles.values())
    }
    for table in ddl.tables:
        for column in table.columns:
            if not roles.has(table.name, column.name):
                if table.name in used_tables and column.required:
                    roles.add(table.name, column.name, ColumnRole.NOT_NULL_FILLER,
                              "REQUIRED in the DDL, so BigQuery needs a value on INSERT")
                else:
                    roles.add(table.name, column.name, ColumnRole.UNUSED,
                              "Not referenced anywhere in the DTF")

    # A reference the parser could not attribute (an alias like `rn` from a
    # subquery) must not survive as a phantom column.
    required_columns = []
    for rc in roles.values():
        ddl_table = ddl.get(rc.table)
        if ddl_table is None or ddl_table.column(rc.column) is None:
            warnings.append(
                f"Ignored `{rc.table}.{rc.column}`: not a column in the loaded DDL."
            )
            continue
        required_columns.append(rc)
    for rc in required_columns:
        ddl_table = ddl.get(rc.table)
        col = ddl_table.column(rc.column) if ddl_table else None
        if col:
            rc.data_type = col.data_type
            rc.nullable = not col.required
        if rc.role == ColumnRole.NOT_NULL_FILLER:
            rc.required = True

    ignored = [name for name in known_tables if name not in used_tables]
    if not transformations:
        warnings.append(
            "No transformation branches were detected. The DTF may be a pure 1:1 copy, "
            "or its format was not recognised."
        )

    return AnalysisResult(
        tables_used=sorted(used_tables),
        tables_ignored=sorted(ignored),
        required_columns=required_columns,
        transformations=transformations,
        notes=notes,
        warnings=warnings,
        source="static",
    )
