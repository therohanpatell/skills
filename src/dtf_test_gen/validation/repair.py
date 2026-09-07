"""Add the fewest extra rows needed to close a coverage gap.

Repair only ever appends dedicated rows for paths that verification proved
missing -- it never rewrites rows that already carry coverage.
"""

from __future__ import annotations

from dtf_test_gen.generation.rows import RowBuilder
from dtf_test_gen.models.analysis import AnalysisResult, Scenario
from dtf_test_gen.models.generation import GenerationResult
from dtf_test_gen.validation.coverage import evaluate_coverage
from dtf_test_gen.models.schema import DDLSet


def repair_coverage(
    analysis: AnalysisResult,
    generation: GenerationResult,
    ddl: DDLSet,
    max_rows: int = 50,
    max_passes: int = 2,
    include_not_null: bool = True,
) -> GenerationResult:
    """Re-run generation with a dedicated scenario per missing path."""
    report = evaluate_coverage(analysis, generation)
    if not report.missing:
        generation.coverage = report
        return generation

    paths_by_id = {p.id: p for p in analysis.all_paths}
    scenarios: list[Scenario] = list(generation.scenarios)
    repaired: list[str] = []

    for _ in range(max_passes):
        missing = report.missing
        if not missing:
            break
        for path_id in missing:
            path = paths_by_id.get(path_id)
            if path is None or path_id in repaired:
                continue
            scenarios.append(Scenario(
                id=f"TC{len(scenarios) + 1:03d}",
                description=f"Dedicated row for {path_id}: {path.description}",
                covers=[path_id],
                constraints=list(path.constraints),
            ))
            repaired.append(path_id)

        builder = RowBuilder(
            analysis, ddl, max_rows=max_rows, include_not_null=include_not_null,
        )
        generation = builder.build(scenarios)
        report = evaluate_coverage(analysis, generation)

    report.repaired = repaired
    generation.coverage = report
    if report.missing:
        generation.warnings.append(
            "Coverage incomplete after automatic repair. Missing: " + ", ".join(report.missing)
        )
    return generation
