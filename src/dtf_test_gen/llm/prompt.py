"""Build the smallest prompt that can still answer the question.

Only columns the transformation touches are sent, only skills that matched a
real DTF feature, and only the expressions static parsing could not classify.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from dtf_test_gen.loaders.skills import Skill, dtf_signals
from dtf_test_gen.models.analysis import AnalysisResult, ColumnRole
from dtf_test_gen.models.dtf import DTFConfig
from dtf_test_gen.models.schema import DDLSet

PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "analyzer.md"
_FALLBACK_SYSTEM = (
    "You analyse a BigQuery transformation and report what the source data must "
    "look like, as JSON only. You never write SQL and never invent row values."
)
MAX_COLUMNS_PER_TABLE = 25
MAX_SKILLS = 4


@dataclass
class PromptBundle:
    system: str
    user: str
    payload: dict

    @property
    def approx_tokens(self) -> int:
        return (len(self.system) + len(self.user)) // 4


def load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return _FALLBACK_SYSTEM


def build_prompt(
    config: DTFConfig,
    ddl: DDLSet,
    static: AnalysisResult,
    skills: list[Skill],
    deep: bool = False,
) -> PromptBundle:
    interesting_roles = {
        rc.role for rc in static.required_columns
    }
    del interesting_roles       # roles are read per-column below

    tables_payload: dict[str, list[dict]] = {}
    for name in static.tables_used or config.source_tables:
        table = ddl.get(name)
        if not table:
            continue
        entries = []
        for rc in static.required_columns:
            if rc.table.lower() != table.name.lower():
                continue
            if rc.role == ColumnRole.UNUSED and not deep:
                continue        # never spend tokens describing unused columns
            entries.append({
                "column": rc.column,
                "type": rc.data_type,
                "nullable": rc.nullable,
                "static_role": rc.role.value,
            })
        tables_payload[table.name] = entries[:MAX_COLUMNS_PER_TABLE]

    transformation_payload = {
        "target": config.target_table,
        "joins": [
            {
                "type": j.join_type,
                "left": f"{j.left_table}.{j.left_column}",
                "right": f"{j.right_table}.{j.right_column}",
            }
            for j in config.joins
        ],
        "understood_predicates": [
            {"id": t.id, "kind": t.kind, "rule": t.description}
            for t in static.transformations if t.kind in {"filter", "condition", "null_default"}
        ],
        "group_by": config.group_by,
        "aggregates": config.aggregates,
        "window_partitions": config.window_partitions,
        "dedup_keys": config.dedup_keys,
    }

    unparsed = [w.split("`")[1] for w in static.warnings if "not understood" in w and "`" in w]
    if deep:
        unparsed += [
            m.expression for m in config.mappings
            if m.expression and not m.is_one_to_one
        ][:10]

    payload = {
        "tables": tables_payload,
        "transformation": transformation_payload,
        "unparsed": unparsed,
    }

    selected_skills = [s for s in skills if s.selected][:MAX_SKILLS]
    if selected_skills:
        # Send the part of each document that matches what this DTF actually
        # does, rather than whatever happens to sit at the top of the file.
        wanted = set(dtf_signals(config))
        payload["skills"] = {
            s.name: s.relevant_excerpt(wanted) for s in selected_skills
        }

    instruction = (
        "Analyse this transformation and return the JSON object described in your "
        "instructions. Return JSON only."
    )
    if deep:
        instruction += (
            " Deep mode: also validate the understood predicates against the skills "
            "and flag anything inconsistent in `notes`."
        )
    user = f"{instruction}\n\n{json.dumps(payload, indent=1, default=str)}"
    return PromptBundle(system=load_system_prompt(), user=user, payload=payload)
