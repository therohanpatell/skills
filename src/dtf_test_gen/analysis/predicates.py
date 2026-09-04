"""Turn one predicate expression into a transformation with its coverage paths.

Every branch a DTF can take becomes an explicit pair of paths, so a missed path
is impossible by construction rather than by luck.
"""

from __future__ import annotations

import re
from typing import Any

from dtf_test_gen.models.analysis import Constraint, ConstraintOp, Transformation, TransformationPath

_OP_TEXT = {
    ConstraintOp.EQ: "=", ConstraintOp.NE: "!=", ConstraintOp.GT: ">",
    ConstraintOp.GTE: ">=", ConstraintOp.LT: "<", ConstraintOp.LTE: "<=",
}

_NEGATION = {
    ConstraintOp.EQ: ConstraintOp.NE,
    ConstraintOp.NE: ConstraintOp.EQ,
    ConstraintOp.GT: ConstraintOp.LTE,
    ConstraintOp.GTE: ConstraintOp.LT,
    ConstraintOp.LT: ConstraintOp.GTE,
    ConstraintOp.LTE: ConstraintOp.GT,
    ConstraintOp.IS_NULL: ConstraintOp.IS_NOT_NULL,
    ConstraintOp.IS_NOT_NULL: ConstraintOp.IS_NULL,
    ConstraintOp.IN: ConstraintOp.NOT_IN,
    ConstraintOp.NOT_IN: ConstraintOp.IN,
    ConstraintOp.LIKE: ConstraintOp.NOT_LIKE,
    ConstraintOp.NOT_LIKE: ConstraintOp.LIKE,
    ConstraintOp.BETWEEN: ConstraintOp.NOT_BETWEEN,
    ConstraintOp.NOT_BETWEEN: ConstraintOp.BETWEEN,
}

_SYMBOL_OPS = [
    (">=", ConstraintOp.GTE), ("<=", ConstraintOp.LTE), ("<>", ConstraintOp.NE),
    ("!=", ConstraintOp.NE), (">", ConstraintOp.GT), ("<", ConstraintOp.LT),
    ("=", ConstraintOp.EQ),
]


def parse_literal(text: str) -> Any:
    token = text.strip()
    if not token:
        return None
    if token.upper() == "NULL":
        return None
    if token.upper() == "TRUE":
        return True
    if token.upper() == "FALSE":
        return False
    if (token.startswith("'") and token.endswith("'")) or (token.startswith('"') and token.endswith('"')):
        return token[1:-1].replace("''", "'")
    cast = re.fullmatch(r"(?:DATE|TIMESTAMP|DATETIME|TIME)\s+'([^']*)'", token, re.I)
    if cast:
        return cast.group(1)
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token.strip("`")


def _split_ref(ref: str) -> tuple[str | None, str]:
    ref = ref.strip().strip("`")
    if "." in ref:
        parts = [p.strip("`") for p in ref.split(".")]
        return parts[-2], parts[-1]
    return None, ref


_KEYWORD_LITERALS = {"TRUE", "FALSE", "NULL"}


def _is_column_ref(token: str) -> bool:
    return bool(re.fullmatch(r"`?[A-Za-z_]\w*`?(\.`?[A-Za-z_]\w*`?)?", token.strip()))


def _looks_like_column(token: str) -> bool:
    """A bare identifier that is not a keyword literal, so `flag = TRUE` parses."""
    text = token.strip()
    if text.upper() in _KEYWORD_LITERALS:
        return False
    if re.fullmatch(r"(?:DATE|TIMESTAMP|DATETIME|TIME)\s+'[^']*'", text, re.I):
        return False
    return _is_column_ref(text) and not re.fullmatch(r"'[^']*'|\d+(\.\d+)?", text)


class ParsedPredicate:
    """A single comparison lifted out of a predicate expression."""

    def __init__(self, alias: str | None, column: str, op: ConstraintOp,
                 value: Any = None, values: list[Any] | None = None):
        self.alias = alias
        self.column = column
        self.op = op
        self.value = value
        self.values = values or []


def _unwrap_parens(expr: str) -> str:
    """Remove parentheses that wrap the whole expression, and only those.

    A blanket `strip("()")` would turn `x IN ('A','B')` into `x IN ('A','B'`.
    """
    while expr.startswith("(") and expr.endswith(")"):
        depth = 0
        for i, ch in enumerate(expr):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i < len(expr) - 1:
                    return expr          # the parens close early: not a wrapper
        expr = expr[1:-1].strip()
    return expr


def parse_predicate(expression: str) -> ParsedPredicate | None:
    """Parse `col OP value` shapes. Returns None for anything too complex."""
    expr = _unwrap_parens(expression.strip())
    if not expr:
        return None

    m = re.fullmatch(r"(.+?)\s+IS\s+NOT\s+NULL", expr, re.I)
    if m and _is_column_ref(m.group(1)):
        alias, col = _split_ref(m.group(1))
        return ParsedPredicate(alias, col, ConstraintOp.IS_NOT_NULL)

    m = re.fullmatch(r"(.+?)\s+IS\s+NULL", expr, re.I)
    if m and _is_column_ref(m.group(1)):
        alias, col = _split_ref(m.group(1))
        return ParsedPredicate(alias, col, ConstraintOp.IS_NULL)

    m = re.fullmatch(r"(.+?)\s+(NOT\s+)?IN\s*\((.*)\)", expr, re.I | re.S)
    if m and _is_column_ref(m.group(1)):
        alias, col = _split_ref(m.group(1))
        values = [parse_literal(v) for v in re.split(r",(?=(?:[^']*'[^']*')*[^']*$)", m.group(3))]
        op = ConstraintOp.NOT_IN if m.group(2) else ConstraintOp.IN
        return ParsedPredicate(alias, col, op, values[0] if values else None, values)

    m = re.fullmatch(r"(.+?)\s+(NOT\s+)?BETWEEN\s+(.+?)\s+AND\s+(.+)", expr, re.I | re.S)
    if m and _is_column_ref(m.group(1)):
        alias, col = _split_ref(m.group(1))
        lo, hi = parse_literal(m.group(3)), parse_literal(m.group(4))
        op = ConstraintOp.NOT_BETWEEN if m.group(2) else ConstraintOp.BETWEEN
        return ParsedPredicate(alias, col, op, lo, [lo, hi])

    m = re.fullmatch(r"(.+?)\s+(NOT\s+)?LIKE\s+(.+)", expr, re.I | re.S)
    if m and _is_column_ref(m.group(1)):
        alias, col = _split_ref(m.group(1))
        op = ConstraintOp.NOT_LIKE if m.group(2) else ConstraintOp.LIKE
        return ParsedPredicate(alias, col, op, parse_literal(m.group(3)))

    for symbol, op in _SYMBOL_OPS:
        idx = _find_top_level(expr, symbol)
        if idx < 0:
            continue
        left, right = expr[:idx].strip(), expr[idx + len(symbol):].strip()
        if not _is_column_ref(left):
            # `100 < amount` -- flip it so the column is always on the left.
            if _is_column_ref(right) and not _is_column_ref(left):
                flipped = {
                    ConstraintOp.GT: ConstraintOp.LT, ConstraintOp.LT: ConstraintOp.GT,
                    ConstraintOp.GTE: ConstraintOp.LTE, ConstraintOp.LTE: ConstraintOp.GTE,
                }.get(op, op)
                alias, col = _split_ref(right)
                return ParsedPredicate(alias, col, flipped, parse_literal(left))
            return None
        if _looks_like_column(right):
            # column = column is a join condition, handled separately.
            return None
        alias, col = _split_ref(left)
        return ParsedPredicate(alias, col, op, parse_literal(right))
    return None


def _find_top_level(text: str, symbol: str) -> int:
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and text.startswith(symbol, i):
            # Don't treat `>=` as `>`.
            if symbol in {">", "<"} and i + 1 < len(text) and text[i + 1] in "=>":
                i += 1
                continue
            if symbol == "=" and i > 0 and text[i - 1] in "<>!=":
                i += 1
                continue
            return i
        i += 1
    return -1


def describe(op: ConstraintOp, column: str, value: Any, values: list[Any]) -> str:
    if op == ConstraintOp.IS_NULL:
        return f"{column} IS NULL"
    if op == ConstraintOp.IS_NOT_NULL:
        return f"{column} IS NOT NULL"
    if op in {ConstraintOp.IN, ConstraintOp.NOT_IN}:
        rendered = ", ".join(repr(v) for v in values)
        return f"{column} {'NOT IN' if op == ConstraintOp.NOT_IN else 'IN'} ({rendered})"
    if op in {ConstraintOp.BETWEEN, ConstraintOp.NOT_BETWEEN}:
        lo, hi = (values + [None, None])[:2]
        return f"{column} {'NOT BETWEEN' if op == ConstraintOp.NOT_BETWEEN else 'BETWEEN'} {lo} AND {hi}"
    if op in {ConstraintOp.LIKE, ConstraintOp.NOT_LIKE}:
        return f"{column} {'NOT LIKE' if op == ConstraintOp.NOT_LIKE else 'LIKE'} {value!r}"
    return f"{column} {_OP_TEXT.get(op, op.value)} {value!r}"


def _path_label(op: ConstraintOp, positive: bool) -> str:
    if op in {ConstraintOp.IS_NULL, ConstraintOp.IS_NOT_NULL}:
        return "NULL" if (op == ConstraintOp.IS_NULL) == positive else "NON_NULL"
    return "PASS" if positive else "FAIL"


def build_predicate_transformation(
    tid: str,
    table: str,
    parsed: ParsedPredicate,
    expression: str,
    kind: str,
) -> Transformation:
    """Expand one predicate into its PASS and FAIL (or NULL / NON_NULL) paths."""
    positive = Constraint(
        table=table, column=parsed.column, op=parsed.op,
        value=parsed.value, values=parsed.values,
    )
    negated_op = _NEGATION.get(parsed.op, ConstraintOp.NE)
    negative = Constraint(
        table=table, column=parsed.column, op=negated_op,
        value=parsed.value, values=parsed.values,
        # Failing a WHERE clause drops the row before any later branch runs.
        excludes_row=(kind == "filter"),
    )
    pass_label = _path_label(parsed.op, True)
    fail_label = _path_label(parsed.op, False)
    description = describe(parsed.op, f"{table}.{parsed.column}", parsed.value, parsed.values)

    return Transformation(
        id=tid,
        kind=kind,
        expression=expression,
        description=description,
        table=table,
        column=parsed.column,
        paths=[
            TransformationPath(
                id=f"{tid}.{pass_label}", transformation_id=tid, label=pass_label,
                description=description, constraints=[positive],
            ),
            TransformationPath(
                id=f"{tid}.{fail_label}", transformation_id=tid, label=fail_label,
                description=describe(negated_op, f"{table}.{parsed.column}", parsed.value, parsed.values),
                constraints=[negative],
            ),
        ],
    )
