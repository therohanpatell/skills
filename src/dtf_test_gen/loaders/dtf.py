"""Parse a DTF configuration into the normalised DTFConfig model.

DTF configs are not standardised, so the loader accepts three broad families and
merges whatever it finds:

  A. SQL-carrying config .... {"name": .., "sql": "SELECT .. FROM .. WHERE .."}
  B. Structured config ...... {"source": .., "target": .., "mappings": [..],
                               "filters": [..], "joins": [..], "group_by": [..]}
  C. Nested config .......... {"transformation": { <A or B> }}

Anything unrecognised is preserved in `raw` and shown to the model as context.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dtf_test_gen.analysis import sqlparse
from dtf_test_gen.loaders.discovery import LoadError, parse_text, read_any
from dtf_test_gen.models.dtf import ColumnMapping, DTFConfig, JoinSpec, PredicateSpec

_SQL_KEYS = ("sql", "query", "statement", "transformation_sql", "select", "sql_text")
_NAME_KEYS = ("name", "id", "dtf_name", "pipeline", "pipeline_name", "job", "job_name")
_TARGET_KEYS = ("target", "target_table", "destination", "output", "sink", "target_bq_table")
_SOURCE_KEYS = ("source", "sources", "source_table", "source_tables", "input", "inputs", "from")
_MAPPING_KEYS = ("mappings", "mapping", "columns", "fields", "column_mappings", "select")
_FILTER_KEYS = ("filters", "filter", "where", "conditions", "predicates", "criteria")
_JOIN_KEYS = ("joins", "join", "lookups", "lookup")
_GROUP_KEYS = ("group_by", "groupby", "group", "aggregation_keys")
_ORDER_KEYS = ("order_by", "orderby", "sort", "sort_by")
_AGG_KEYS = ("aggregates", "aggregations", "metrics", "measures")
_DEDUP_KEYS = ("dedup", "dedup_keys", "deduplicate", "distinct_on", "primary_key")
_WINDOW_KEYS = ("partition_by", "window_partition", "partition")
_NESTED_KEYS = ("transformation", "transformations", "dtf", "config", "spec", "definition")


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    lowered = {str(k).lower(): v for k, v in payload.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, "", [], {}):
            return value
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _table_name(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("table", "table_name", "name", "id", "fq_name", "full_name"):
            if value.get(key):
                return str(value[key]).strip("`").split(".")[-1]
        return None
    return str(value).strip("`").split(".")[-1]


def _fq_table(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        project = value.get("project") or value.get("project_id")
        dataset = value.get("dataset") or value.get("dataset_id")
        table = value.get("table") or value.get("table_name") or value.get("name")
        parts = [str(p) for p in (project, dataset, table) if p]
        return ".".join(parts) if parts else None
    return str(value).strip("`")


def _parse_structured_filters(payload: Any) -> list[PredicateSpec]:
    specs: list[PredicateSpec] = []
    for item in _as_list(payload):
        if isinstance(item, str):
            for part in sqlparse.split_top_level(item, (" and ",)):
                specs.append(PredicateSpec(expression=part.strip(), origin="filter"))
        elif isinstance(item, dict):
            expr = _first(item, ("expression", "expr", "condition", "sql", "predicate", "clause"))
            if expr:
                specs.append(PredicateSpec(expression=str(expr), origin="filter"))
                continue
            column = _first(item, ("column", "field", "name", "left"))
            operator = _first(item, ("operator", "op", "comparison")) or "="
            value = item.get("value", item.get("values", item.get("right")))
            if column is None:
                continue
            op = str(operator).strip().upper()
            if isinstance(value, list):
                rendered = ", ".join(_render_literal(v) for v in value)
                specs.append(PredicateSpec(expression=f"{column} {op} ({rendered})", origin="filter"))
            elif value is None and op in {"IS NULL", "IS NOT NULL", "ISNULL", "NOTNULL"}:
                specs.append(PredicateSpec(expression=f"{column} {op}", origin="filter"))
            else:
                specs.append(PredicateSpec(expression=f"{column} {op} {_render_literal(value)}", origin="filter"))
    return specs


def _render_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _parse_structured_joins(payload: Any, default_left: str | None) -> list[JoinSpec]:
    joins: list[JoinSpec] = []
    for item in _as_list(payload):
        if isinstance(item, str):
            joins.extend(sqlparse.extract_joins("FROM x " + item, {}))
            continue
        if not isinstance(item, dict):
            continue
        join_type = str(_first(item, ("type", "join_type", "kind")) or "INNER").upper().replace("_", " ")
        right = _table_name(_first(item, ("table", "right_table", "right", "with", "target", "lookup_table")))
        left = _table_name(_first(item, ("left_table", "left", "from", "base"))) or default_left
        on = _first(item, ("on", "condition", "clause"))
        left_col = _table_name(_first(item, ("left_column", "left_key", "source_column", "left_on")))
        right_col = _table_name(_first(item, ("right_column", "right_key", "target_column", "right_on")))
        keys = _first(item, ("keys", "on_columns", "using"))
        if keys and not (left_col and right_col):
            key_list = _as_list(keys)
            if key_list:
                first_key = key_list[0]
                if isinstance(first_key, dict):
                    left_col = left_col or _table_name(_first(first_key, ("left", "source", "left_column")))
                    right_col = right_col or _table_name(_first(first_key, ("right", "target", "right_column")))
                else:
                    left_col = left_col or str(first_key)
                    right_col = right_col or str(first_key)
        if on and not (left_col and right_col) and left and right:
            parsed = sqlparse.extract_joins(f"FROM {left} {join_type} JOIN {right} ON {on}", {})
            if parsed:
                joins.append(parsed[0])
                continue
        if left and right and left_col and right_col:
            joins.append(JoinSpec(
                left_table=left, left_column=left_col,
                right_table=right, right_column=right_col,
                join_type=join_type, raw=str(on) if on else None,
            ))
    return joins


def _parse_structured_mappings(payload: Any) -> list[ColumnMapping]:
    mappings: list[ColumnMapping] = []
    items = payload
    if isinstance(payload, dict):
        items = [{"target": k, "source": v} for k, v in payload.items()]
    for item in _as_list(items):
        if isinstance(item, str):
            mappings.append(ColumnMapping(target_column=item, source_column=item))
            continue
        if not isinstance(item, dict):
            continue
        target = _first(item, ("target", "target_column", "name", "column", "field", "to", "output"))
        expression = _first(item, ("expression", "expr", "transform", "transformation", "sql", "formula", "logic"))
        source = _first(item, ("source", "source_column", "from", "input", "src"))
        source_table = _table_name(_first(item, ("source_table", "table")))
        source_column = None
        if isinstance(source, str):
            if "." in source:
                source_table = source_table or source.split(".")[0]
                source_column = source.split(".")[-1]
            else:
                source_column = source
        elif isinstance(source, dict):
            source_table = source_table or _table_name(_first(source, ("table",)))
            source_column = _table_name(_first(source, ("column", "name", "field")))
        if not target:
            target = source_column or "unnamed"
        mappings.append(ColumnMapping(
            target_column=str(target).strip("`"),
            expression=str(expression) if expression else (source_column or None),
            source_table=source_table,
            source_column=source_column,
        ))
    return mappings


def _names(payload: Any) -> list[str]:
    out: list[str] = []
    for item in _as_list(payload):
        name = _table_name(item) if isinstance(item, dict) else str(item).strip("`").split(".")[-1]
        if name and name not in out:
            out.append(name)
    return out


def load_dtf_payload(payload: Any, file: str | None = None, fallback_name: str = "dtf") -> DTFConfig:
    if payload is None:
        raise LoadError("Unable to parse DTF configuration", file=file, reason="File is empty.")
    if isinstance(payload, str):
        payload = {"sql": payload}
    if isinstance(payload, list):
        if len(payload) == 1 and isinstance(payload[0], dict):
            payload = payload[0]
        else:
            raise LoadError(
                "Unable to parse DTF configuration", file=file,
                reason="Top level is a list; expected a single transformation object.",
            )
    if not isinstance(payload, dict):
        raise LoadError("Unable to parse DTF configuration", file=file, reason="Top level is not an object.")

    # Unwrap one level of nesting if the real config lives under a wrapper key.
    nested = _first(payload, _NESTED_KEYS)
    if isinstance(nested, dict) and not (set(payload) & set(_SQL_KEYS + _MAPPING_KEYS)):
        merged = {**payload, **nested}
        payload = merged
    elif isinstance(nested, list) and nested and isinstance(nested[0], dict):
        payload = {**payload, **nested[0]}

    name = _first(payload, _NAME_KEYS) or fallback_name
    raw_sql = _first(payload, _SQL_KEYS)
    raw_sql = str(raw_sql) if isinstance(raw_sql, str) else None

    config = DTFConfig(name=str(name), source_file=file, raw_sql=raw_sql, raw=payload)

    target = _first(payload, _TARGET_KEYS)
    config.target_table = _fq_table(target)

    config.source_tables = _names(_first(payload, _SOURCE_KEYS))
    config.mappings = _parse_structured_mappings(_first(payload, _MAPPING_KEYS))
    config.predicates = _parse_structured_filters(_first(payload, _FILTER_KEYS))
    config.joins = _parse_structured_joins(
        _first(payload, _JOIN_KEYS),
        config.source_tables[0] if config.source_tables else None,
    )
    config.group_by = [str(g).strip("`") for g in _as_list(_first(payload, _GROUP_KEYS))]
    config.order_by = [str(g).strip("`") for g in _as_list(_first(payload, _ORDER_KEYS))]
    config.aggregates = [str(g) for g in _as_list(_first(payload, _AGG_KEYS))]
    config.dedup_keys = [str(g).strip("`") for g in _as_list(_first(payload, _DEDUP_KEYS))]
    config.window_partitions = [str(g).strip("`") for g in _as_list(_first(payload, _WINDOW_KEYS))]

    if raw_sql:
        _merge_sql(config, raw_sql)

    for join in config.joins:
        for table in (join.left_table, join.right_table):
            short = table.split(".")[-1]
            if short and short not in config.source_tables:
                config.source_tables.append(short)

    if not config.source_tables and not config.raw_sql:
        raise LoadError(
            "Unable to parse DTF configuration", file=file,
            reason="No source tables and no SQL found. Provide `sql`, `source`, or `joins`.",
        )
    return config


def _merge_sql(config: DTFConfig, raw_sql: str) -> None:
    """Fold everything the SQL reader finds into the config, without clobbering."""
    sql = sqlparse.normalise(raw_sql)
    tables, aliases, qualified = sqlparse.extract_tables(sql)
    config.table_aliases.update(aliases)
    config.source_table_fq.update(qualified)
    for table in tables:
        if table not in config.source_tables:
            config.source_tables.append(table)

    target = sqlparse.extract_target_table(sql)
    if target and not config.target_table:
        config.target_table = target
    if config.target_table:
        short = config.target_table.split(".")[-1]
        config.source_tables = [t for t in config.source_tables if t.lower() != short.lower()]

    config.joins.extend(sqlparse.extract_joins(sql, aliases))
    existing = {p.expression.lower() for p in config.predicates}
    for spec in sqlparse.extract_predicates(sql):
        if spec.expression.lower() not in existing:
            config.predicates.append(spec)
    if not config.mappings:
        config.mappings = sqlparse.extract_select_mappings(sql)
    config.group_by = config.group_by or sqlparse.extract_group_by(sql)
    config.order_by = config.order_by or sqlparse.extract_order_by(sql)
    config.aggregates = config.aggregates or sqlparse.extract_aggregates(sql)
    config.window_partitions = config.window_partitions or sqlparse.extract_window_partitions(sql)
    if sqlparse.has_dedup(sql) and not config.dedup_keys:
        config.dedup_keys = list(config.window_partitions)


def load_dtf(path: str | Path) -> DTFConfig:
    p = Path(path)
    payload = read_any(p)
    return load_dtf_payload(payload, file=p.name, fallback_name=p.stem)


def load_dtf_text(text: str, filename: str) -> DTFConfig:
    suffix = Path(filename).suffix.lower()
    if suffix in {".sql", ".txt"}:
        return load_dtf_payload({"name": Path(filename).stem, "sql": text}, file=filename)
    payload = parse_text(text, filename)
    return load_dtf_payload(payload, file=filename, fallback_name=Path(filename).stem)
