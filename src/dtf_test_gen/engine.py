"""The engine facade the UI talks to.

Streamlit calls exactly two methods here -- `analyse` and `generate` -- and does
no transformation reasoning of its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dtf_test_gen.analysis.merge import merge_llm
from dtf_test_gen.analysis.static import analyse_static
from dtf_test_gen.cache import AnalysisCache, cache_key
from dtf_test_gen.config import AppConfig
from dtf_test_gen.generation.rows import RowBuilder
from dtf_test_gen.generation.scenarios import build_scenarios
from dtf_test_gen.llm.client import OllamaClient, OllamaError
from dtf_test_gen.llm.parse import REPAIR_INSTRUCTION, LLMParseError, parse_llm_json
from dtf_test_gen.llm.prompt import build_prompt
from dtf_test_gen.loaders.skills import Skill
from dtf_test_gen.models.analysis import AnalysisResult
from dtf_test_gen.models.dtf import DTFConfig
from dtf_test_gen.models.generation import GenerationResult
from dtf_test_gen.models.schema import DDLSet
from dtf_test_gen.sql.writer import render_all
from dtf_test_gen.validation.repair import repair_coverage
from dtf_test_gen.workflow import validate_inputs
from dtf_test_gen.generation.values import default_value
from dtf_test_gen.models.generation import GeneratedTable
from dtf_test_gen.llm.interpret import interpret_dtf, InterpretationError


def _apply_qualification(dtf: DTFConfig, ddl: DDLSet) -> None:
    """Fill in project/dataset from the DTF when the DDL file omitted them.

    A bare `bq show --schema` export has no dataset, but the DTF's SQL names one,
    and the generated INSERT has to target a table that actually exists.
    """
    for short, fq in dtf.source_table_fq.items():
        table = ddl.get(short)
        if table is None or (table.dataset and table.project):
            continue
        parts = fq.strip("`").split(".")
        if len(parts) == 3:
            table.project = table.project or parts[0]
            table.dataset = table.dataset or parts[1]
        elif len(parts) == 2:
            table.dataset = table.dataset or parts[0]


@dataclass
class AnalysisOutcome:
    analysis: AnalysisResult
    from_cache: bool = False
    llm_called: bool = False
    llm_error: str | None = None
    prompt_tokens: int = 0
    messages: list[str] = field(default_factory=list)
    # Which knowledge files actually contributed, and how many characters each.
    knowledge_used: dict[str, int] = field(default_factory=dict)
    knowledge_skipped: list[str] = field(default_factory=list)
    interpretation: dict = field(default_factory=dict)
    interpreted_dtf: DTFConfig | None = None


class Engine:
    def __init__(self, config: AppConfig | None = None):
        self.config = config or AppConfig()
        self.cache = AnalysisCache(
            directory=self.config.cache.directory, enabled=self.config.cache.enabled
        )

    # ---------------------------------------------------------------- analyse
    def analyse(
        self,
        dtf: DTFConfig,
        ddl: DDLSet,
        skills: list[Skill],
        model: str,
        deep: bool = False,
        use_llm: bool = True,
        use_cache: bool = True,
        temperature: float | None = None,
        host: str | None = None,
        progress=None,
        full_knowledge: bool = False,
        runtime_interpretation: bool = False,
    ) -> AnalysisOutcome:
        """Static analysis, model review (with one repair retry), then merge."""
        if dtf.needs_interpretation or (runtime_interpretation and use_llm and not dtf.is_sql_file):
            if not use_llm:
                raise InterpretationError("This DTF needs runtime interpretation. Enable Use Ollama and select your framework knowledge files; the built-in parser cannot interpret this layout.")
            schema_errors = validate_inputs(dtf, ddl, check_sources=False)
            if schema_errors:
                raise InterpretationError("\n".join(schema_errors))
            if progress:
                progress(0.2, "Reading original DTF JSON with framework knowledge in Ollama...")
            client = OllamaClient(host=host or self.config.ollama.host, timeout=self.config.ollama.timeout)
            try:
                interpreted = interpret_dtf(dtf, ddl, skills, client, model,
                                            self.config.ollama.temperature if temperature is None else temperature)
            except OllamaError as exc:
                raise InterpretationError(f"Runtime interpretation failed: {exc}. No static fallback or INSERTs were generated for this unverified DTF.") from exc
            _apply_qualification(interpreted.dtf, ddl)
            analysis = analyse_static(interpreted.dtf, ddl)
            analysis.source = "llm-interpreted"
            analysis.model = model
            analysis.skills_used = [s.name for s in skills if s.selected]
            analysis.model_notes = [str(note) for note in interpreted.report.get("notes", [])]
            analysis.warnings.append("DTF semantics were interpreted by the local model. Review the normalized DTF and source evidence; schema validation does not prove semantic equivalence.")
            return AnalysisOutcome(
                analysis=analysis, llm_called=True, prompt_tokens=interpreted.prompt_tokens,
                knowledge_used=interpreted.knowledge_used, interpretation=interpreted.report,
                interpreted_dtf=interpreted.dtf,
                messages=["Interpreted the original DTF at runtime using complete selected knowledge files, then validated source requirements before generation."],
            )
        errors = validate_inputs(dtf, ddl)
        if errors:
            raise ValueError("\n".join(errors))
        _apply_qualification(dtf, ddl)
        selected_skills = [s for s in skills if s.selected]
        key = cache_key(
            ddl_payloads=[t.model_dump_json() for t in ddl.tables],
            dtf_payload=dtf.model_dump_json(),
            skill_payloads=[s.text for s in selected_skills],
            model=model,
            mode=json.dumps(["v3", deep, use_llm, full_knowledge, host or self.config.ollama.host,
                             self.config.ollama.temperature if temperature is None else temperature]),
        )

        if use_cache:
            cached = self.cache.get(key)
            if cached is not None:
                cached.skills_used = [s.name for s in selected_skills]
                bundle = build_prompt(dtf, ddl, cached, skills, deep=deep, full_knowledge=full_knowledge)
                knowledge = bundle.payload.get("knowledge", {}) if use_llm else {}
                return AnalysisOutcome(
                    analysis=cached, from_cache=True,
                    knowledge_used={name: len(body) for name, body in knowledge.items()},
                    knowledge_skipped=[s.name for s in selected_skills if s.name not in knowledge] if use_llm else [],
                    messages=["Using cached transformation analysis."],
                )

        if progress:
            progress(0.15, "Parsing DTF and DDL...")
        static = analyse_static(dtf, ddl)
        static.model = model
        static.skills_used = [s.name for s in selected_skills]

        if not use_llm:
            self.cache.put(key, static)
            return AnalysisOutcome(analysis=static, messages=["Static analysis only (Ollama not used)."])

        if progress:
            progress(0.35, "Building focused prompt...")
        bundle = build_prompt(dtf, ddl, static, skills, deep=deep, full_knowledge=full_knowledge)
        client = OllamaClient(
            host=host or self.config.ollama.host, timeout=self.config.ollama.timeout
        )
        knowledge = bundle.payload.get("knowledge", {})
        outcome = AnalysisOutcome(
            analysis=static,
            prompt_tokens=bundle.approx_tokens,
            knowledge_used={name: len(body) for name, body in knowledge.items()},
            knowledge_skipped=[
                s.name for s in skills if s.selected and s.name not in knowledge
            ],
        )

        try:
            if progress:
                progress(0.5, f"Asking {model} to review the transformation...")
            raw = client.generate(
                model=model,
                prompt=bundle.user,
                system=bundle.system,
                temperature=self.config.ollama.temperature if temperature is None else temperature,
            )
            outcome.llm_called = True
            try:
                llm = parse_llm_json(raw)
            except LLMParseError as exc:
                outcome.messages.append(
                    f"Ollama returned invalid structured output ({exc}). Attempting repair..."
                )
                if progress:
                    progress(0.7, "Repairing model output...")
                raw = client.generate(
                    model=model,
                    prompt=f"{bundle.user}\n\n{REPAIR_INSTRUCTION}",
                    system=bundle.system,
                    temperature=0.0,
                )
                llm = parse_llm_json(raw)
                outcome.messages.append("Repair succeeded.")
        except (OllamaError, LLMParseError) as exc:
            outcome.llm_error = str(exc)
            outcome.messages.append("Falling back to static analysis only.")
            static.warnings.append(f"Model step skipped: {exc}")
            # Do not cache failed model calls: the next run should retry Ollama.
            outcome.analysis = static
            return outcome

        if progress:
            progress(0.85, "Merging model findings...")
        merged = merge_llm(static, llm, ddl, bundle.payload.get("unparsed", []))
        merged.model = model
        merged.skills_used = static.skills_used
        self.cache.put(key, merged)
        outcome.analysis = merged
        return outcome

    # --------------------------------------------------------------- generate
    def generate(
        self,
        analysis: AnalysisResult,
        ddl: DDLSet,
        max_rows: int | None = None,
        include_not_null: bool = True,
        include_all_sources: bool = False,
        include_all_columns: bool = False,
    ) -> GenerationResult:
        """Pack paths into scenarios, build rows, verify coverage, repair gaps."""
        limit = max_rows or self.config.generation.max_rows
        scenarios = build_scenarios(analysis)
        result = RowBuilder(
            analysis, ddl, max_rows=limit, include_not_null=include_not_null,
        ).build(scenarios)
        result = repair_coverage(
            analysis, result, ddl,
            max_rows=limit, max_passes=self.config.generation.max_retries,
            include_not_null=include_not_null,
        )
        # Preserve rule-bearing rows, then fill requested schema columns and
        # create baseline fixtures for sources with no recognized scenarios.
        existing = {table.table.lower(): table for table in result.tables}
        wanted = {name.lower() for name in analysis.tables_used}
        for table in ddl.tables:
            if not include_all_sources and table.name.lower() not in wanted:
                continue
            generated = existing.get(table.name.lower())
            if generated is None:
                generated = GeneratedTable(table=table.name, fq_name=table.fq_name)
                result.tables.append(generated)
                generated.rows = [{}]
                result.warnings.append(
                    f"{table.fq_name}: baseline fixture only; no generated transformation scenario for this table."
                )
            emitted = set(generated.columns)
            if include_all_columns:
                emitted.update(table.column_names())
            emitted.update(c.name for c in table.columns if c.required and include_not_null)
            if not emitted and table.columns:
                emitted.add(table.columns[0].name)
            generated.columns = [c.name for c in table.columns if c.name in emitted]
            generated.omitted_columns = [c.name for c in table.columns if c.name not in emitted]
            for index, row in enumerate(generated.rows, 1):
                for column in table.columns:
                    if column.name in emitted and column.name not in row:
                        row[column.name] = default_value(column.data_type, column.name, index)
        if not analysis.all_paths:
            result.warnings.append("No recognized transformation paths: these are baseline fixtures, not verified transformation tests.")
        return result

    # ------------------------------------------------------------------ write
    def write_output(
        self,
        analysis: AnalysisResult,
        generation: GenerationResult,
        ddl: DDLSet,
        dtf_name: str,
        directory: str | None = None,
        outcome: AnalysisOutcome | None = None,
    ) -> Path:
        """Write inserts.sql, coverage.json and analysis.json into a run folder."""
        root = Path(directory or self.config.output_directory)
        run_dir = root / datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        if outcome and outcome.interpretation:
            (run_dir / "interpretation.json").write_text(json.dumps(outcome.interpretation, indent=2), encoding="utf-8")

        header = (
            f"DTF: {dtf_name}\n"
            f"Generated: {datetime.now().isoformat(timespec='seconds')}\n"
            f"Rows: {generation.total_rows}  Coverage: {generation.coverage.percent}%"
        )
        (run_dir / "inserts.sql").write_text(render_all(generation, ddl, header=header), encoding="utf-8")
        (run_dir / "coverage.json").write_text(
            json.dumps(
                {
                    "dtf": dtf_name,
                    "total_paths": generation.coverage.total,
                    "covered": generation.coverage.covered,
                    "percent": generation.coverage.percent,
                    "missing": generation.coverage.missing,
                    "repaired": generation.coverage.repaired,
                    "paths": [r.model_dump() for r in generation.coverage.rows],
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        (run_dir / "analysis.json").write_text(
            json.dumps(
                {
                    "dtf": dtf_name,
                    "model": analysis.model,
                    "source": analysis.source,
                    "warnings": analysis.warnings + generation.warnings,
                    "notes": analysis.notes,
                    "model_notes": analysis.model_notes,
                    "tables_used": analysis.tables_used,
                    "tables_ignored": analysis.tables_ignored,
                    "transformations": [
                        {
                            "id": t.id, "kind": t.kind, "rule": t.description,
                            "paths": [p.id for p in t.paths],
                        }
                        for t in analysis.transformations
                    ],
                    "columns": [
                        {"table": c.table, "column": c.column, "role": c.role.value, "required": c.required}
                        for c in analysis.required_columns
                    ],
                    "rows": {t.table: t.row_count for t in generation.tables},
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        return run_dir
