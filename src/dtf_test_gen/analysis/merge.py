"""Fold the model's findings into the static analysis.

The static result is authoritative for anything Python already understood; the
model may only add transformations it found in unparsed expressions and correct
column roles. It can never remove a path.
"""

from __future__ import annotations

from dtf_test_gen.analysis.predicates import ParsedPredicate, build_predicate_transformation
from dtf_test_gen.models.analysis import AnalysisResult, ColumnRole, ConstraintOp, RequiredColumn
from dtf_test_gen.models.llm import LLMAnalysis
from dtf_test_gen.models.schema import DDLSet

_OPERATOR_ALIASES = {
    "=": ConstraintOp.EQ, "==": ConstraintOp.EQ, "eq": ConstraintOp.EQ, "equals": ConstraintOp.EQ,
    "!=": ConstraintOp.NE, "<>": ConstraintOp.NE, "ne": ConstraintOp.NE, "not_equals": ConstraintOp.NE,
    ">": ConstraintOp.GT, "gt": ConstraintOp.GT, "greater_than": ConstraintOp.GT,
    ">=": ConstraintOp.GTE, "gte": ConstraintOp.GTE, "ge": ConstraintOp.GTE,
    "<": ConstraintOp.LT, "lt": ConstraintOp.LT, "less_than": ConstraintOp.LT,
    "<=": ConstraintOp.LTE, "lte": ConstraintOp.LTE, "le": ConstraintOp.LTE,
    "is_null": ConstraintOp.IS_NULL, "isnull": ConstraintOp.IS_NULL, "null": ConstraintOp.IS_NULL,
    "is_not_null": ConstraintOp.IS_NOT_NULL, "notnull": ConstraintOp.IS_NOT_NULL,
    "not_null": ConstraintOp.IS_NOT_NULL,
    "in": ConstraintOp.IN, "not_in": ConstraintOp.NOT_IN,
    "like": ConstraintOp.LIKE, "not_like": ConstraintOp.NOT_LIKE,
    "between": ConstraintOp.BETWEEN, "not_between": ConstraintOp.NOT_BETWEEN,
}
_ROLE_ALIASES = {r.value: r for r in ColumnRole} | {
    "JOIN": ColumnRole.JOIN_KEY, "JOIN_KEY": ColumnRole.JOIN_KEY, "KEY": ColumnRole.JOIN_KEY,
    "GROUP_BY": ColumnRole.GROUP_BY, "ORDER_BY": ColumnRole.ORDER_BY,
    "DEDUP": ColumnRole.DEDUP_KEY, "DEDUP_KEY": ColumnRole.DEDUP_KEY,
    "NULL_DEFAULT": ColumnRole.NULL_DEFAULT, "DEFAULT": ColumnRole.NULL_DEFAULT,
    "1:1": ColumnRole.ONE_TO_ONE, "ONE_TO_ONE": ColumnRole.ONE_TO_ONE,
    "MAPPING": ColumnRole.ONE_TO_ONE, "PASSTHROUGH": ColumnRole.ONE_TO_ONE,
}
_VALID_KINDS = {"filter", "condition", "join", "null_default", "group_by", "window", "dedup", "order_by"}
_KIND_ROLE = {
    "filter": ColumnRole.FILTER,
    "condition": ColumnRole.CONDITION,
    "join": ColumnRole.JOIN_KEY,
    "null_default": ColumnRole.NULL_DEFAULT,
    "group_by": ColumnRole.GROUP_BY,
    "order_by": ColumnRole.ORDER_BY,
    "window": ColumnRole.WINDOW,
    "dedup": ColumnRole.DEDUP_KEY,
}


def _promote(result: AnalysisResult, table, column_name: str, role: ColumnRole, reason: str) -> None:
    """Raise a column to a critical role, adding it if the static pass skipped it."""
    column = table.column(column_name)
    if column is None:
        return
    for rc in result.required_columns:
        if rc.table.lower() == table.name.lower() and rc.column.lower() == column.name.lower():
            if not rc.role.transformation_critical:
                rc.role = role
                rc.required = True
                rc.reason = reason
            return
    result.required_columns.append(RequiredColumn(
        table=table.name, column=column.name, role=role, required=True, reason=reason,
        data_type=column.data_type, nullable=not column.required,
    ))


def merge_llm(static: AnalysisResult, llm: LLMAnalysis, ddl: DDLSet) -> AnalysisResult:
    result = static.model_copy(deep=True)
    result.source = "hybrid"

    existing_rules = {
        (t.table or "").lower() + "|" + (t.column or "").lower() + "|" + t.kind
        for t in result.transformations
    }
    counter = len(result.transformations)
    added = 0

    for item in llm.transformations:
        table_obj = ddl.get(item.table)
        if not table_obj:
            result.notes.append(f"Model referenced unknown table `{item.table}`; ignored.")
            continue
        if item.column and not table_obj.column(item.column):
            result.notes.append(
                f"Model referenced unknown column `{item.table}.{item.column}`; ignored."
            )
            continue
        op = _OPERATOR_ALIASES.get(item.operator)
        if op is None:
            result.notes.append(f"Model used an unsupported operator `{item.operator}`; ignored.")
            continue
        kind = item.kind if item.kind in _VALID_KINDS else "filter"
        key = f"{table_obj.name.lower()}|{item.column.lower()}|{kind}"
        if key in existing_rules:
            continue                     # static parsing already covers this rule
        existing_rules.add(key)
        counter += 1
        parsed = ParsedPredicate(
            alias=None, column=item.column, op=op,
            value=item.value, values=item.values,
        )
        expression = item.description or f"{item.table}.{item.column} {item.operator} {item.value}"
        result.transformations.append(
            build_predicate_transformation(f"T{counter:03d}", table_obj.name, parsed, expression, kind)
        )
        # A column the model made critical must reach the INSERT, or its paths
        # can never be covered.
        _promote(result, table_obj, item.column, _KIND_ROLE.get(kind, ColumnRole.FILTER),
                 item.description or "Identified by the model")
        added += 1

    if added:
        result.notes.append(f"Model contributed {added} transformation(s) static parsing missed.")

    # Role corrections: only upgrade to a critical role, never silently downgrade
    # a column Python proved is used.
    by_key = {(rc.table.lower(), rc.column.lower()): rc for rc in result.required_columns}
    for role_item in llm.column_roles:
        table_obj = ddl.get(role_item.table)
        if not table_obj or not table_obj.column(role_item.column):
            continue
        role = _ROLE_ALIASES.get(role_item.role.replace(" ", "_")) or _ROLE_ALIASES.get(role_item.role)
        if role is None:
            continue
        key = (table_obj.name.lower(), role_item.column.lower())
        current = by_key.get(key)
        if current is None:
            column = table_obj.column(role_item.column)
            new = RequiredColumn(
                table=table_obj.name, column=column.name, role=role,
                required=role.transformation_critical, reason=role_item.reason or "Identified by the model",
                data_type=column.data_type, nullable=not column.required,
            )
            result.required_columns.append(new)
            by_key[key] = new
            continue
        if role.transformation_critical and not current.role.transformation_critical:
            current.role = role
            current.required = True
            current.reason = role_item.reason or "Identified by the model"

    # Rebuild the index so freshly promoted columns are visible below.
    by_key = {(rc.table.lower(), rc.column.lower()): rc for rc in result.required_columns}

    # Any table the model gave a role to is now in use.
    used = set(result.tables_used)
    for rc in result.required_columns:
        if rc.role.transformation_critical:
            used.add(rc.table)
    result.tables_used = sorted(used)
    result.tables_ignored = sorted(
        t.name for t in ddl.tables if t.name not in used
    )
    # Model prose is quarantined, not merged into the engine's findings.
    result.model_notes.extend(str(note)[:300] for note in llm.notes[:5])
    return result
