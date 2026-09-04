"""A small command-line runner, for driving the engine without the browser."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table as RichTable

from dtf_test_gen.config import AppConfig
from dtf_test_gen.engine import Engine
from dtf_test_gen.loaders import LoadError, discover_project, load_ddl, load_dtf, load_skills
from dtf_test_gen.loaders.skills import auto_select
from dtf_test_gen.sql.writer import render_all

console = Console()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dtf-test-gen",
        description="Generate minimal transformation-aware BigQuery test data for a DTF.",
    )
    parser.add_argument("project", help="Project directory containing ddl/, dtf/ and skills/")
    parser.add_argument("--dtf", help="DTF filename to use (defaults to the first found)")
    parser.add_argument("--model", default=None, help="Ollama model (default: from config.yaml)")
    parser.add_argument("--no-llm", action="store_true", help="Deterministic analysis only")
    parser.add_argument("--deep", action="store_true", help="Deep mode: adds a validation call")
    parser.add_argument("--no-cache", action="store_true", help="Ignore the analysis cache")
    parser.add_argument("--out", help="Write inserts.sql/coverage.json/analysis.json here")
    args = parser.parse_args(argv)

    config = AppConfig.load()
    model = args.model or config.ollama.model

    found = discover_project(args.project)
    for message in found.errors:
        console.print(f"[yellow]⚠ {message}[/]")
    if not found.dtf:
        console.print("[red]No DTF configuration found.[/]")
        return 1

    ddl, ddl_errors = load_ddl(list(found.ddl))
    for exc in ddl_errors:
        console.print(f"[red]❌ {exc.message}[/] — {exc.file}: {exc.reason}")
    if not ddl.tables:
        console.print("[red]No source schemas could be loaded.[/]")
        return 1

    dtf_path = next((p for p in found.dtf if p.name == args.dtf), found.dtf[0]) if args.dtf else found.dtf[0]
    try:
        dtf = load_dtf(dtf_path)
    except LoadError as exc:
        console.print(f"[red]❌ {exc.message}[/] — {exc.file}: {exc.reason}")
        return 1

    skills = auto_select(load_skills(list(found.skills)), dtf)
    engine = Engine(config)
    outcome = engine.analyse(
        dtf, ddl, skills, model=model, deep=args.deep,
        use_llm=not args.no_llm, use_cache=not args.no_cache,
    )
    for message in outcome.messages:
        console.print(f"[cyan]{message}[/]")
    if outcome.llm_error:
        console.print(f"[yellow]⚠ {outcome.llm_error}[/]")

    analysis = outcome.analysis
    for warning in analysis.warnings:
        console.print(f"[yellow]⚠ {warning}[/]")

    generation = engine.generate(analysis, ddl)

    table = RichTable(title=f"{dtf.name} · {analysis.source}")
    table.add_column("Transformation")
    table.add_column("Path")
    table.add_column("Covered", justify="center")
    for row in generation.coverage.rows:
        table.add_row(row.transformation, row.path, "✓" if row.covered else "✗")
    console.print(table)
    console.print(
        f"[bold]{generation.total_rows}[/] rows · "
        f"[bold]{len(generation.scenarios)}[/] scenarios · "
        f"coverage [bold]{generation.coverage.percent}%[/]"
    )
    if generation.coverage.missing:
        console.print("[red]Missing: " + ", ".join(generation.coverage.missing) + "[/]")

    if args.out:
        run_dir = engine.write_output(analysis, generation, ddl, dtf.name, directory=args.out)
        console.print(f"Wrote [bold]{run_dir}[/]")
    else:
        console.print(render_all(generation, ddl))
    return 0 if not generation.coverage.missing else 2


if __name__ == "__main__":
    sys.exit(main())
