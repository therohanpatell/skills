"""Parse BigQuery DDL JSON in whichever of the common shapes it arrives in.

Supported shapes (auto-detected):
  1. `bq show --schema` output ......... [{"name": .., "type": .., "mode": ..}, ..]
  2. A table document ................. {"table": .., "dataset": .., "columns": [..]}
  3. A BigQuery table resource ........ {"tableReference": {..}, "schema": {"fields": [..]}}
  4. A multi-table document ........... {"tables": [ <shape 2>, .. ]}
  5. A list of table documents ........ [ <shape 2>, .. ]
  6. A name -> columns mapping ........ {"customer": [..], "order": [..]}
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dtf_test_gen.loaders.discovery import LoadError, parse_text, read_any
from dtf_test_gen.models.schema import Column, DDLSet, Table

_COLUMN_KEYS = ("columns", "fields", "schema", "column_list")
_NAME_KEYS = ("name", "column_name", "column", "field", "field_name")
_TYPE_KEYS = ("type", "data_type", "datatype", "bq_type", "field_type", "bigquery_type")
_MODE_KEYS = ("mode", "nullable", "is_nullable", "required", "constraint")
_TABLE_NAME_KEYS = ("table", "table_name", "name", "tableId", "table_id")
_DATASET_KEYS = ("dataset", "dataset_name", "datasetId", "dataset_id", "schema_name")
_PROJECT_KEYS = ("project", "project_id", "projectId")


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    lowered = {str(k).lower(): v for k, v in payload.items()}
    for key in keys:
        if key.lower() in lowered and lowered[key.lower()] not in (None, ""):
            return lowered[key.lower()]
    return None


def _normalise_mode(raw: Any, payload: dict[str, Any]) -> str:
    if raw is None:
        return "NULLABLE"
    if isinstance(raw, bool):
        key = next((k for k in payload if str(k).lower() in _MODE_KEYS), "")
        # `nullable: true` -> NULLABLE, `required: true` -> REQUIRED
        if str(key).lower() in {"required"}:
            return "REQUIRED" if raw else "NULLABLE"
        return "NULLABLE" if raw else "REQUIRED"
    text = str(raw).strip().upper()
    if text in {"REQUIRED", "NULLABLE", "REPEATED"}:
        return text
    if text in {"NOT NULL", "NOTNULL", "NO", "FALSE", "N"}:
        return "REQUIRED"
    if text in {"NULL", "YES", "TRUE", "Y"}:
        return "NULLABLE"
    if text in {"ARRAY", "REPEAT"}:
        return "REPEATED"
    return "NULLABLE"


def _parse_column(payload: Any, table_name: str, file: str | None) -> Column:
    if isinstance(payload, str):
        # `"customer_id STRING"` or just `"customer_id"`.
        parts = payload.split()
        return Column(name=parts[0], data_type=(parts[1].upper() if len(parts) > 1 else "STRING"))
    if not isinstance(payload, dict):
        raise LoadError(
            "Unable to parse DDL", file=file,
            reason=f"Column entry in table '{table_name}' is not an object: {payload!r}",
        )
    name = _first(payload, _NAME_KEYS)
    if not name:
        raise LoadError(
            "Unable to parse DDL", file=file,
            reason=f"A column in table '{table_name}' has no name.",
        )
    data_type = _first(payload, _TYPE_KEYS)
    if not data_type:
        raise LoadError(
            "Unable to parse DDL", file=file,
            reason=f"Column type is missing for {table_name}.{name}.",
        )
    mode_raw = _first(payload, _MODE_KEYS)
    return Column(
        name=str(name),
        data_type=str(data_type).strip().upper(),
        mode=_normalise_mode(mode_raw, payload),
        description=payload.get("description"),
        default_value=payload.get("default") or payload.get("default_value"),
    )


def _extract_columns(payload: dict[str, Any]) -> list[Any] | None:
    for key in _COLUMN_KEYS:
        value = _first(payload, (key,))
        if isinstance(value, list):
            return value
        if isinstance(value, dict) and isinstance(value.get("fields"), list):
            return value["fields"]
    return None


def _parse_table(payload: dict[str, Any], file: str | None, fallback_name: str) -> Table:
    ref = payload.get("tableReference") or payload.get("table_reference") or {}
    name = _first(payload, _TABLE_NAME_KEYS) or _first(ref, _TABLE_NAME_KEYS) or fallback_name
    if isinstance(name, dict):
        name = _first(name, _TABLE_NAME_KEYS) or fallback_name
    dataset = _first(payload, _DATASET_KEYS) or _first(ref, _DATASET_KEYS)
    project = _first(payload, _PROJECT_KEYS) or _first(ref, _PROJECT_KEYS)

    name_str = str(name)
    # Accept a fully-qualified name in the table field itself.
    if name_str.count(".") == 2 and not (project and dataset):
        project, dataset, name_str = name_str.split(".")
    elif name_str.count(".") == 1 and not dataset:
        dataset, name_str = name_str.split(".")

    raw_columns = _extract_columns(payload)
    if raw_columns is None:
        raise LoadError(
            "Unable to parse DDL", file=file,
            reason=f"No columns/fields found for table '{name_str}'.",
        )
    columns = [_parse_column(c, name_str, file) for c in raw_columns]
    if not columns:
        raise LoadError("Unable to parse DDL", file=file, reason=f"Table '{name_str}' has no columns.")
    return Table(
        name=name_str.strip("`"),
        dataset=str(dataset).strip("`") if dataset else None,
        project=str(project).strip("`") if project else None,
        columns=columns,
        source_file=file,
    )


def load_ddl_payload(payload: Any, file: str | None = None, fallback_name: str = "table") -> list[Table]:
    """Turn one parsed DDL document into one or more Tables."""
    if payload is None:
        raise LoadError("Unable to parse DDL", file=file, reason="File is empty.")

    if isinstance(payload, list):
        if payload and isinstance(payload[0], dict):
            keys = {str(k).lower() for k in payload[0]}
            if keys & set(_COLUMN_KEYS) or keys & {"table", "table_name"}:
                return [
                    _parse_table(item, file, f"{fallback_name}_{i}")
                    for i, item in enumerate(payload) if isinstance(item, dict)
                ]
        # Bare `bq show --schema` field list.
        return [_parse_table({"table": fallback_name, "columns": payload}, file, fallback_name)]

    if not isinstance(payload, dict):
        raise LoadError("Unable to parse DDL", file=file, reason="Top level is not an object or list.")

    tables_node = _first(payload, ("tables",))
    if isinstance(tables_node, list):
        return [
            _parse_table(item, file, f"{fallback_name}_{i}")
            for i, item in enumerate(tables_node) if isinstance(item, dict)
        ]
    if isinstance(tables_node, dict):
        return [_parse_table({**v, "table": k}, file, k) for k, v in tables_node.items() if isinstance(v, dict)]

    if _extract_columns(payload) is not None:
        return [_parse_table(payload, file, fallback_name)]

    # Shape 6: a mapping of table name -> column list / table document.
    tables: list[Table] = []
    for key, value in payload.items():
        if isinstance(value, list):
            tables.append(_parse_table({"table": key, "columns": value}, file, key))
        elif isinstance(value, dict) and _extract_columns(value) is not None:
            tables.append(_parse_table({**value, "table": value.get("table", key)}, file, key))
    if tables:
        return tables

    raise LoadError(
        "Unable to parse DDL", file=file,
        reason="No table definition found. Expected a column list, a table object, or a `tables` array.",
    )


def load_ddl(paths: list[str | Path]) -> tuple[DDLSet, list[LoadError]]:
    """Load many DDL files, collecting per-file errors instead of raising."""
    tables: list[Table] = []
    errors: list[LoadError] = []
    for raw_path in paths:
        path = Path(raw_path)
        try:
            payload = read_any(path)
            tables.extend(load_ddl_payload(payload, file=path.name, fallback_name=path.stem))
        except LoadError as exc:
            errors.append(exc)
    return DDLSet(tables=tables), errors


def load_ddl_text(text: str, filename: str) -> list[Table]:
    payload = parse_text(text, filename)
    return load_ddl_payload(payload, file=filename, fallback_name=Path(filename).stem)
