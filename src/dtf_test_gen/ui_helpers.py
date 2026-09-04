"""Presentation helpers. Kept out of app.py so the UI file stays declarative."""

from __future__ import annotations

from typing import Any

import pandas as pd

from dtf_test_gen.models.analysis import AnalysisResult, ColumnRole, Scenario
from dtf_test_gen.models.generation import GenerationResult

ROLE_BADGES: dict[ColumnRole, str] = {
    ColumnRole.JOIN_KEY: "🔗",
    ColumnRole.FILTER: "🔍",
    ColumnRole.CONDITION: "🔀",
    ColumnRole.GROUP_BY: "🧮",
    ColumnRole.ORDER_BY: "↕️",
    ColumnRole.DEDUP_KEY: "🧹",
    ColumnRole.WINDOW: "🪟",
    ColumnRole.NULL_DEFAULT: "␀",
    ColumnRole.AGGREGATE: "Σ",
    ColumnRole.ONE_TO_ONE: "➡️",
    ColumnRole.NOT_NULL_FILLER: "📌",
    ColumnRole.UNUSED: "·",
}


def columns_dataframe(analysis: AnalysisResult, include_unused: bool = True) -> pd.DataFrame:
    rows = []
    for rc in analysis.required_columns:
        if not include_unused and rc.role == ColumnRole.UNUSED:
            continue
        rows.append({
            "Table": rc.table,
            "Source Column": rc.column,
            "Type": rc.data_type,
            "Role": f"{ROLE_BADGES.get(rc.role, '')} {rc.role.value}".strip(),
            "Required": "Yes" if rc.required else "No",
            "Why": rc.reason,
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    order = {r.value: i for i, r in enumerate(ColumnRole)}
    frame["_sort"] = frame["Role"].map(lambda v: order.get(v.split(" ", 1)[-1], 99))
    return frame.sort_values(["_sort", "Table", "Source Column"]).drop(columns="_sort").reset_index(drop=True)


def coverage_dataframe(generation: GenerationResult) -> pd.DataFrame:
    rows = [
        {
            "Transformation": row.transformation,
            "Path": row.path,
            "Path ID": row.path_id,
            "Covered": "✓" if row.covered else "✗",
            "Covered By": ", ".join(row.covered_by) or "-",
        }
        for row in generation.coverage.rows
    ]
    return pd.DataFrame(rows)


def rows_dataframe(generation: GenerationResult, table_name: str) -> pd.DataFrame:
    table = generation.table(table_name)
    if table is None or not table.rows:
        return pd.DataFrame()
    # Display only: show SQL NULL as "NULL" rather than pandas' "None".
    return pd.DataFrame(
        [
            {col: ("NULL" if row.get(col) is None else row.get(col)) for col in table.columns}
            for row in table.rows
        ]
    )


def scenario_cards(generation: GenerationResult, analysis: AnalysisResult) -> list[dict[str, Any]]:
    by_id = {p.id: p for p in analysis.all_paths}
    covered = {r.path_id for r in generation.coverage.rows if r.covered}
    cards = []
    for scenario in generation.scenarios:
        if not isinstance(scenario, Scenario):
            continue
        cards.append({
            "id": scenario.id,
            "description": scenario.description or "Baseline source row",
            "covers": [
                {
                    "id": path_id,
                    "label": by_id[path_id].label if path_id in by_id else path_id,
                    "description": by_id[path_id].description if path_id in by_id else "",
                    "covered": path_id in covered,
                }
                for path_id in scenario.covers
            ],
        })
    return cards


def counts(analysis: AnalysisResult) -> dict[str, int]:
    return {
        "tables_used": len(analysis.tables_used),
        "tables_ignored": len(analysis.tables_ignored),
        "transformation_columns": len(analysis.transformation_columns),
        "one_to_one_columns": len(analysis.columns_by_role(ColumnRole.ONE_TO_ONE)),
        "unused_columns": len(analysis.columns_by_role(ColumnRole.UNUSED)),
        "not_null_fillers": len(analysis.columns_by_role(ColumnRole.NOT_NULL_FILLER)),
        "transformations": len(analysis.transformations),
        "paths": len(analysis.all_paths),
    }
