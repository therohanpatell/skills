"""Build the smallest prompt that can still answer the question.

Only columns the transformation touches are sent, only skills that matched a
real DTF feature, and only the expressions static parsing could not classify.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from dtf_test_gen.loaders.skills import Skill, dtf_signals, pack_knowledge
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
    unparsed = [w.split("`")[1] for w in static.warnings if "not understood" in w and "`" in w]
    if deep:
        unparsed += [
            m.expression for m in config.mappings
            if m.expression and not m.is_one_to_one
        ][:10]
    unparsed_columns = {
        token for expr in unparsed
        for token in re.findall(r"[A-Za-z_]\w*", expr)
    }

    tables_payload: dict[str, list[dict]] = {}
    for name in static.tables_used or config.source_tables:
        table = ddl.get(name)
        if not table:
            continue
        entries = []
        for rc in static.required_columns:
            if rc.table.lower() != table.name.lower():
                continue
            if rc.role == ColumnRole.UNUSED:
                continue        # never spend tokens describing unused columns
            entries.append({
                "column": rc.column,
                "type": rc.data_type,
                "nullable": rc.nullable,
                "static_role": rc.role.value,
            })
        # A 220-column table can still leave more used columns than the cap.
        # Anything an unparsed expression names must survive the trim, or the
        # model is asked to classify a column it was never shown.
        named = {c.lower() for c in unparsed_columns}
        entries.sort(key=lambda e: (
            e["column"].lower() not in named,
            e["static_role"] == ColumnRole.NOT_NULL_FILLER.value,
            e["static_role"] == ColumnRole.ONE_TO_ONE.value,
        ))
        trimmed = entries[:MAX_COLUMNS_PER_TABLE]
        if len(entries) > len(trimmed):
            trimmed.append({"_note": f"{len(entries) - len(trimmed)} further "
                                     "columns omitted; they are not referenced "
                                     "by any unclassified expression."})
        tables_payload[table.name] = trimmed

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

    payload = {
        "tables": tables_payload,
        "transformation": transformation_payload,
        "unparsed": unparsed,
    }

    # One shared budget across every selected knowledge file, so a deep
    # reference can contribute several sections and an irrelevant file none.
    knowledge = pack_knowledge(skills, set(dtf_signals(config)))
    if knowledge:
        payload["knowledge"] = knowledge

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
