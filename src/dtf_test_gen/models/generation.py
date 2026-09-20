"""Generated rows, coverage and the final artefacts."""

from __future__ import annotations

from typing import Any

from dataclasses import dataclass, field
from dtf_test_gen.models.base import Model


@dataclass(kw_only=True)
class GeneratedTable(Model):
    table: str
    fq_name: str
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    omitted_columns: list[str] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass(kw_only=True)
class CoverageRow(Model):
    transformation: str
    transformation_id: str
    path: str
    path_id: str
    covered: bool = False
    covered_by: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class CoverageReport(Model):
    rows: list[CoverageRow] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def covered(self) -> int:
        return sum(1 for r in self.rows if r.covered)

    @property
    def missing(self) -> list[str]:
        return [r.path_id for r in self.rows if not r.covered]

    @property
    def percent(self) -> float:
        if not self.rows:
            return 100.0
        return round(100.0 * self.covered / self.total, 1)


@dataclass(kw_only=True)
class GenerationResult(Model):
    tables: list[GeneratedTable] = field(default_factory=list)
    scenarios: list[Any] = field(default_factory=list)         # list[Scenario]
    coverage: CoverageReport = field(default_factory=CoverageReport)
    naive_row_estimate: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(t.row_count for t in self.tables)

    def table(self, name: str) -> GeneratedTable | None:
        for t in self.tables:
            if t.table == name:
                return t
        return None
