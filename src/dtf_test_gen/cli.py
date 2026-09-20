"""A small command-line runner, for driving the engine without the browser."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dtf_test_gen.config import AppConfig
from dtf_test_gen.engine import Engine
from dtf_test_gen.loaders import LoadError, discover_project, load_ddl, load_dtf, load_skills
from dtf_test_gen.loaders.skills import auto_select
from dtf_test_gen.sql.writer import render_all
from dtf_test_gen.workflow import validate_inputs, validate_generation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dtf-test-gen",
        description="Generate minimal transformation-aware BigQuery test data for a DTF.",
    )
    parser.add_argument("project", help="Project directory containing ddl/, dtf/ and skills/")
    parser.add_argument("--dtf", help="DTF filename to use (defaults to the first found)")
    parser.add_argument("--model", default=None, help="Ollama model (default: from config.json)")
    parser.add_argument("--interpret-runtime", action="store_true", help="Interpret JSON using framework knowledge before deterministic analysis")
    parser.add_argument("--no-llm", action="store_true", help="Deterministic analysis only")
    parser.add_argument("--deep", action="store_true", help="Include mappings and review notes in the model prompt")
    parser.add_argument("--no-cache", action="store_true", help="Ignore the analysis cache")
    parser.add_argument("--out", help="Write inserts.sql/coverage.json/analysis.json here")
    parser.add_argument("--knowledge", action="append", default=[], help="Knowledge Markdown file or folder (repeatable)")
    parser.add_argument("--host", help="Ollama host, default http://localhost:11434")
    args = parser.parse_args(argv)

    config = AppConfig.load()
    if args.host:
        config.ollama.host = args.host
    model = args.model or config.ollama.model

    found = discover_project(args.project)
    for message in found.errors:
        print(f"WARNING: {message}")
    if not found.dtf:
        print("No DTF configuration found.")
        return 1

    ddl, ddl_errors = load_ddl(list(found.ddl))
    for exc in ddl_errors:
        print(f"ERROR: {exc.message} - {exc.file}: {exc.reason}")
    if not ddl.tables:
        print("No source schemas could be loaded.")
        return 1

    if args.dtf and not any(p.name == args.dtf for p in found.dtf):
        print(f"DTF file not found: {args.dtf}")
        return 1
    dtf_path = next((p for p in found.dtf if p.name == args.dtf), found.dtf[0]) if args.dtf else found.dtf[0]
    try:
        dtf = load_dtf(dtf_path, allow_runtime=True)
    except LoadError as exc:
        print(f"ERROR: {exc.message} - {exc.file}: {exc.reason}")
        return 1

    paths = list(found.skills)
    explicit = set()
    for location in args.knowledge:
        path = Path(location)
        if not path.exists():
            print(f"Knowledge path not found: {path}")
            return 1
        additions = sorted(path.rglob("*.md")) if path.is_dir() else [path]
        paths.extend(additions)
        explicit.update(str(p) for p in additions)
    errors = validate_inputs(dtf, ddl, check_sources=not (dtf.needs_interpretation or (args.interpret_runtime and not args.no_llm)))
    if errors:
        for error in errors:
            print(error)
        return 1
    skills = auto_select(load_skills(list(dict.fromkeys(paths))), dtf)
    for skill in skills:
        if skill.path in explicit:
            skill.selected = True

    engine = Engine(config)
    try:
        outcome = engine.analyse(
            dtf, ddl, skills, model=model, deep=args.deep,
            use_llm=not args.no_llm, use_cache=not args.no_cache,
            runtime_interpretation=args.interpret_runtime,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    if outcome.interpreted_dtf:
        dtf = outcome.interpreted_dtf
    for message in outcome.messages:
        print(message)
    if outcome.llm_error:
        print(f"WARNING: {outcome.llm_error}")

    for name, size in outcome.knowledge_used.items():
        print(f"Knowledge sent: {name} ({size} characters)")
    for name in outcome.knowledge_skipped:
        print(f"Knowledge omitted by relevance/budget: {name}")

    analysis = outcome.analysis
    if not analysis.transformations:
        print("No supported transformation paths were found. Check the DTF format; no test coverage can be claimed.")
        return 2
    for warning in analysis.warnings:
        print(f"WARNING: {warning}")
    for note in analysis.notes:
        print(f"- {note}")
    for note in analysis.model_notes:
        print(f"model remark (unverified): {note}")

    generation = engine.generate(analysis, ddl)
    errors = validate_generation(generation, ddl)
    if errors:
        for error in errors:
            print(error)
        return 2

    print(f"{dtf.name} | {analysis.source}")
    for row in generation.coverage.rows:
        print(f"{'PASS' if row.covered else 'MISSING'} | {row.transformation} | {row.path}")
    for warning in generation.warnings:
        print(f"WARNING: {warning}")
    print(
        f"{generation.total_rows} rows | "
        f"{len(generation.scenarios)} scenarios | "
        f"coverage {generation.coverage.percent}%"
    )
    if generation.coverage.missing:
        print("Missing: " + ", ".join(generation.coverage.missing))

    if args.out:
        run_dir = engine.write_output(analysis, generation, ddl, dtf.name, directory=args.out, outcome=outcome)
        print(f"Wrote {run_dir}")
    else:
        print(render_all(generation, ddl))
    return 0 if not generation.coverage.missing else 2


if __name__ == "__main__":
    sys.exit(main())
