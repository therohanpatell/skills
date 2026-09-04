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
    ) -> AnalysisOutcome:
        """Static analysis, then at most one Ollama call, then merge."""
        selected_skills = [s for s in skills if s.selected]
        key = cache_key(
            ddl_payloads=[t.model_dump_json() for t in ddl.tables],
            dtf_payload=dtf.model_dump_json(),
            skill_payloads=[s.text for s in selected_skills],
            model=model,
            mode="deep" if deep else "fast",
        )

        if use_cache:
            cached = self.cache.get(key)
            if cached is not None:
                cached.skills_used = [s.name for s in selected_skills]
                return AnalysisOutcome(
                    analysis=cached, from_cache=True,
                    messages=["Using cached transformation analysis."],
                )

        if progress:
            progress(0.15, "Parsing DTF and DDL...")
        _apply_qualification(dtf, ddl)
        static = analyse_static(dtf, ddl)
        static.model = model
        static.skills_used = [s.name for s in selected_skills]

        if not use_llm:
            self.cache.put(key, static)
            return AnalysisOutcome(analysis=static, messages=["Static analysis only (Ollama not used)."])

        if progress:
            progress(0.35, "Building focused prompt...")
        bundle = build_prompt(dtf, ddl, static, skills, deep=deep)
        client = OllamaClient(
            host=host or self.config.ollama.host, timeout=self.config.ollama.timeout
        )
        outcome = AnalysisOutcome(analysis=static, prompt_tokens=bundle.approx_tokens)

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
            self.cache.put(key, static)
            outcome.analysis = static
            return outcome

        if progress:
            progress(0.85, "Merging model findings...")
        merged = merge_llm(static, llm, ddl)
        merged.model = model
        merged.skills_used = static.skills_used
        self.cache.put(key, merged)
        outcome.analysis = merged
        return outcome

    # --------------------------------------------------------------- generate
    def generate(self, analysis: AnalysisResult, ddl: DDLSet, max_rows: int | None = None) -> GenerationResult:
        """Pack paths into scenarios, build rows, verify coverage, repair gaps."""
        limit = max_rows or self.config.generation.max_rows
        scenarios = build_scenarios(analysis)
        result = RowBuilder(analysis, ddl, max_rows=limit).build(scenarios)
        result = repair_coverage(
            analysis, result, ddl,
            max_rows=limit, max_passes=self.config.generation.max_retries,
        )
        return result

    # ------------------------------------------------------------------ write
    def write_output(
        self,
        analysis: AnalysisResult,
        generation: GenerationResult,
        ddl: DDLSet,
        dtf_name: str,
        directory: str | None = None,
    ) -> Path:
        """Write inserts.sql, coverage.json and analysis.json into a run folder."""
        root = Path(directory or self.config.output_directory)
        run_dir = root / datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)

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
