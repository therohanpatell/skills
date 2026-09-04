"""Pack transformation paths into the fewest possible test scenarios.

Each scenario becomes (at most) one source row per table. A path joins an
existing scenario whenever none of its constraints conflict with what that
scenario already requires -- so one row covering ACTIVE + IN + amount>100 +
join match is produced instead of four.
"""

from __future__ import annotations

from dtf_test_gen.models.analysis import (
    AnalysisResult,
    Constraint,
    ConstraintOp,
    Scenario,
    TransformationPath,
)

# Paths that describe the *default* behaviour are packed last, so the
# interesting branches get first pick of the early scenarios.
_LOW_PRIORITY_LABELS = {"SINGLE_ROW", "NON_NULL"}


def _priority(path: TransformationPath) -> tuple[int, int, str]:
    low = 1 if path.label in _LOW_PRIORITY_LABELS else 0
    # More constrained paths are harder to place, so place them first.
    return (low, -len(path.constraints), path.id)


def _excludes_row(path: TransformationPath) -> bool:
    return any(c.excludes_row for c in path.constraints)


def _scenario_excludes_row(scenario: Scenario) -> bool:
    return any(c.excludes_row for c in scenario.constraints)


def _conflicts(existing: list[Constraint], candidate: Constraint) -> bool:
    return any(c.conflicts_with(candidate) for c in existing)


def _join_incompatible(existing: list[Constraint], candidate: Constraint) -> bool:
    """A NO_MATCH row cannot also exercise a rule on the table it fails to join.

    If `customer` has no matching `order`, no `order` row is reachable from it,
    so `order.amount > 100` can never be proven in that same scenario.
    """
    orphaned = {
        (c.peer_table or "").lower()
        for c in existing if c.op == ConstraintOp.JOIN_NO_MATCH
    }
    if candidate.op not in {ConstraintOp.JOIN_MATCH, ConstraintOp.JOIN_NO_MATCH}:
        if candidate.table.lower() in orphaned:
            return True
    if candidate.op == ConstraintOp.JOIN_NO_MATCH:
        peer = (candidate.peer_table or "").lower()
        if any(
            c.table.lower() == peer and c.op not in {ConstraintOp.JOIN_MATCH, ConstraintOp.JOIN_NO_MATCH}
            for c in existing
        ):
            return True
    return False


def build_scenarios(analysis: AnalysisResult) -> list[Scenario]:
    paths = sorted(analysis.all_paths, key=_priority)
    scenarios: list[Scenario] = []

    for path in paths:
        placed = False
        excludes = _excludes_row(path)
        for scenario in scenarios:
            # A row a filter throws away cannot also demonstrate a later branch,
            # so a rejected row is never asked to carry anything else.
            if excludes or _scenario_excludes_row(scenario):
                continue
            if any(_conflicts(scenario.constraints, c) for c in path.constraints):
                continue
            if any(_join_incompatible(scenario.constraints, c) for c in path.constraints):
                continue
            scenario.constraints.extend(path.constraints)
            scenario.covers.append(path.id)
            placed = True
            break
        if not placed:
            scenarios.append(Scenario(
                id=f"TC{len(scenarios) + 1:03d}",
                description="",
                covers=[path.id],
                constraints=list(path.constraints),
            ))

    baseline = _baseline_constraints(analysis)
    for scenario in scenarios:
        _apply_baseline(scenario, baseline)
        scenario.description = describe_scenario(scenario, analysis)
    return scenarios


def _baseline_constraints(analysis: AnalysisResult) -> list[Constraint]:
    """The constraints a row must meet just to reach the transformation at all.

    Every filter's PASS value and every join's MATCH. A scenario that is not
    deliberately testing the FAIL side of one of these inherits it, so the row it
    produces actually survives the pipeline instead of being filtered out before
    the branch under test is ever evaluated.
    """
    baseline: list[Constraint] = []
    for transformation in analysis.transformations:
        if transformation.kind not in {"filter", "join"}:
            continue
        wanted = "PASS" if transformation.kind == "filter" else "MATCH"
        for path in transformation.paths:
            if path.label == wanted:
                baseline.extend(path.constraints)
    return baseline


def _apply_baseline(scenario: Scenario, baseline: list[Constraint]) -> None:
    """Fill unconstrained baseline columns, never overriding a deliberate choice."""
    if _scenario_excludes_row(scenario):
        # This row is meant to be rejected, so it needs no join partner and no
        # help passing the other filters.
        return
    constrained = {c.key.lower() for c in scenario.constraints}
    for constraint in baseline:
        if constraint.key.lower() in constrained:
            continue
        if _conflicts(scenario.constraints, constraint):
            continue
        if _join_incompatible(scenario.constraints, constraint):
            continue
        scenario.constraints.append(constraint)
        constrained.add(constraint.key.lower())


def describe_scenario(scenario: Scenario, analysis: AnalysisResult) -> str:
    """A short human sentence built from the scenario's own constraints."""
    by_id = {p.id: p for p in analysis.all_paths}
    parts: list[str] = []
    for path_id in scenario.covers:
        path = by_id.get(path_id)
        if not path:
            continue
        for constraint in path.constraints:
            parts.append(_phrase(constraint, path.label))
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    if not seen:
        return "Baseline source row"
    return ", ".join(seen[:4]) + ("..." if len(seen) > 4 else "")


def _phrase(constraint: Constraint, label: str) -> str:
    column = constraint.column
    op = constraint.op
    if op == ConstraintOp.JOIN_MATCH:
        return f"{constraint.peer_table} join match"
    if op == ConstraintOp.JOIN_NO_MATCH:
        return f"{constraint.peer_table} join miss"
    if op == ConstraintOp.IS_NULL:
        return f"{column} is NULL"
    if op == ConstraintOp.IS_NOT_NULL:
        return f"{column} present"
    if op == ConstraintOp.GROUP_DUPLICATE:
        return f"multiple rows per {column}"
    if op == ConstraintOp.ANY:
        return ""
    if op == ConstraintOp.EQ:
        return f"{column}={constraint.value}"
    if op == ConstraintOp.NE:
        return f"{column}!={constraint.value}"
    symbol = {
        ConstraintOp.GT: ">", ConstraintOp.GTE: ">=",
        ConstraintOp.LT: "<", ConstraintOp.LTE: "<=",
    }.get(op)
    if symbol:
        return f"{column}{symbol}{constraint.value}"
    if op in {ConstraintOp.IN, ConstraintOp.NOT_IN}:
        return f"{column} {'not ' if op == ConstraintOp.NOT_IN else ''}in list"
    return f"{column} {label.lower()}"
