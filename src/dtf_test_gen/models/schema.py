"""Normalised representation of BigQuery DDL, whatever shape the JSON arrived in."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Column(BaseModel):
    name: str
    data_type: str = "STRING"
    mode: str = "NULLABLE"          # NULLABLE | REQUIRED | REPEATED
    description: str | None = None
    default_value: Any | None = None

    @property
    def required(self) -> bool:
        """True when BigQuery will reject an INSERT that omits this column."""
        return self.mode.upper() == "REQUIRED" and self.default_value is None

    @property
    def is_repeated(self) -> bool:
        return self.mode.upper() == "REPEATED"


class Table(BaseModel):
    name: str
    project: str | None = None
    dataset: str | None = None
    columns: list[Column] = Field(default_factory=list)
    source_file: str | None = None

    @property
    def fq_name(self) -> str:
        parts = [p for p in (self.project, self.dataset, self.name) if p]
        return ".".join(parts)

    def column(self, name: str) -> Column | None:
        lowered = name.lower()
        for col in self.columns:
            if col.name.lower() == lowered:
                return col
        return None

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


class DDLSet(BaseModel):
    """All tables the user selected, addressable by any alias BigQuery allows."""

    tables: list[Table] = Field(default_factory=list)

    def get(self, name: str) -> Table | None:
        """Resolve `t`, `dataset.t` or `project.dataset.t` to a table."""
        if not name:
            return None
        key = name.strip().strip("`").lower()
        short = key.split(".")[-1]
        for table in self.tables:
            if table.name.lower() == key or table.fq_name.lower() == key:
                return table
        for table in self.tables:
            if table.name.lower() == short:
                return table
        return None

    def names(self) -> list[str]:
        return [t.name for t in self.tables]
