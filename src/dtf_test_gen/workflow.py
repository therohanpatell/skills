"""Shared upload validation, stable run identities, and downloadable artifacts."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections import Counter

from dtf_test_gen.generation.values import base_type
from dtf_test_gen.loaders.discovery import LoadError


def decode_upload(data: bytes, filename: str) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LoadError("Invalid file encoding", file=filename,
                        reason="Save this file as UTF-8 JSON or Markdown.") from exc


def validate_inputs(dtf, ddl) -> list[str]:
    errors = []
    names = Counter(t.name.lower() for t in ddl.tables)
    for name, count in names.items():
        if count > 1:
            errors.append(f"Multiple schemas use table name '{name}'. This engine requires unique short table names; remove duplicates or use distinct test table names.")
    for source in dtf.source_tables:
        expected = dtf.source_table_fq.get(source, source)
        table = ddl.get(expected)
        if table is None:
            errors.append(f"Missing source DDL: {expected}. Upload its JSON schema.")
        elif "." in expected and table.dataset:
            if not table.fq_name.lower().endswith(expected.lower()) and not expected.lower().endswith(table.fq_name.lower()):
                errors.append(f"Source {expected} does not match uploaded schema {table.fq_name}.")
    supported = {"STRING", "TEXT", "VARCHAR", "CHAR", "BYTES", "BOOL", "BOOLEAN",
                 "INT64", "INTEGER", "INT", "SMALLINT", "BIGINT", "TINYINT",
                 "NUMERIC", "DECIMAL", "BIGNUMERIC", "FLOAT64", "FLOAT", "DOUBLE",
                 "DATE", "TIME", "DATETIME", "TIMESTAMP", "JSON"}
    for table in ddl.tables:
        identifiers = [table.name, *table.column_names()]
        identifiers.extend(part for part in (table.project, table.dataset) if part is not None)
        if any(not part or any(ch in part for ch in "`\n\r\x00") for part in identifiers):
            errors.append(f"Invalid table or column identifier in {table.source_file or table.name}.")
        columns = Counter(c.name.lower() for c in table.columns)
        if any(n > 1 for n in columns.values()):
            errors.append(f"Duplicate column names in {table.fq_name}.")
        for column in table.columns:
            if column.is_repeated or column.data_type.startswith("ARRAY") or base_type(column.data_type) not in supported:
                errors.append(f"Unsupported column {table.fq_name}.{column.name}: {column.mode} {column.data_type}. Nested/array and non-scalar fixtures need explicit generation support.")
    for join in dtf.joins:
        for name, column in ((join.left_table, join.left_column), (join.right_table, join.right_column)):
            table = ddl.get(dtf.resolve_alias(name))
            if table and not table.column(column):
                errors.append(f"Join column {name}.{column} is missing from the supplied DDL.")
    return list(dict.fromkeys(errors))


def validate_generation(generation, ddl) -> list[str]:
    from dtf_test_gen.sql.writer import literal
    errors = []
    for generated in generation.tables:
        table = ddl.get(generated.table)
        for index, row in enumerate(generated.rows, 1):
            for column in table.columns if table else []:
                if column.name not in generated.columns:
                    continue
                value = row.get(column.name)
                if column.required and value is None:
                    errors.append(f"{table.name}.{column.name}, row {index}: NULL violates REQUIRED. This test path cannot be inserted into the supplied schema.")
                try:
                    literal(value, column.data_type)
                except (ValueError, TypeError) as exc:
                    errors.append(f"{table.name}.{column.name}, row {index}: {exc}")
    return errors


def run_fingerprint(dtf, ddl, skills, settings) -> str:
    payload = [dtf.model_dump() if dtf else None, ddl.model_dump(),
               [(s.name, s.text, s.selected) for s in skills], settings]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def download_bundle(sql, analysis, generation, outcome) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("inserts.sql", sql)
        archive.writestr("analysis.json", analysis.model_dump_json(indent=2))
        archive.writestr("coverage.json", json.dumps({
            "recognized_paths": generation.coverage.total,
            "covered": generation.coverage.covered,
            "missing": generation.coverage.missing,
            "warnings": generation.warnings,
            "paths": generation.coverage.model_dump(),
        }, indent=2))
        archive.writestr("run.json", json.dumps({
            "model_error": outcome.llm_error if outcome else None,
            "knowledge_used": outcome.knowledge_used if outcome else {},
            "knowledge_skipped": outcome.knowledge_skipped if outcome else [],
            "tables": [{"table": t.fq_name, "rows": t.row_count} for t in generation.tables],
            "note": "Input-scenario coverage only; execute the DTF and assert expected target results separately.",
        }, indent=2))
    return buffer.getvalue()
