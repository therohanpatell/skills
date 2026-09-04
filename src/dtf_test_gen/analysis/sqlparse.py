"""A small, dependency-free SQL reader.

This is deliberately not a full SQL parser. It only needs to answer four
questions about a DTF query: which tables are read, how they are joined, which
predicates gate rows, and which columns feed grouping/ordering/aggregation.
Anything it cannot classify is handed to the model as free text.
"""

from __future__ import annotations

import re

from dtf_test_gen.models.dtf import ColumnMapping, JoinSpec, PredicateSpec

_WS = re.compile(r"\s+")
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)

_CLAUSE_WORDS = (
    "select", "from", "where", "group by", "having", "qualify",
    "order by", "limit", "window", "union", "left join", "right join",
    "inner join", "full join", "cross join", "join", "on",
)


def normalise(sql: str) -> str:
    sql = _BLOCK_COMMENT.sub(" ", sql)
    sql = _LINE_COMMENT.sub(" ", sql)
    return _WS.sub(" ", sql).strip()


def split_top_level(text: str, separators: tuple[str, ...] = (" and ",)) -> list[str]:
    """Split on separators that sit outside parentheses and string literals."""
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    buf: list[str] = []
    i = 0
    # `BETWEEN a AND b` owns the next AND, which is not a predicate separator.
    between_pending = False
    lowered = text.lower()
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if depth == 0 and lowered.startswith("between", i) and not lowered[i - 1: i].isalnum():
            between_pending = True
        if depth == 0:
            matched = next((s for s in separators if lowered.startswith(s, i)), None)
            if matched and matched.strip() == "and" and between_pending:
                between_pending = False
                matched = None
            if matched:
                parts.append("".join(buf).strip())
                buf = []
                i += len(matched)
                continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def _depth_map(sql: str) -> list[int]:
    """Parenthesis depth at each character, ignoring string literals."""
    depths: list[int] = []
    depth = 0
    quote: str | None = None
    for ch in sql:
        if quote:
            depths.append(depth)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "(":
            depths.append(depth)
            depth += 1
            continue
        elif ch == ")":
            depth -= 1
        depths.append(depth)
    return depths


def _clause(sql: str, start_word: str) -> str:
    """Return a clause's text, stopping at its own subquery boundary.

    Depth matters: in `SELECT * FROM (SELECT .. WHERE a=1) WHERE rn=1` the inner
    WHERE must end at the closing paren, not run on into the outer one.
    """
    lowered = sql.lower()
    depths = _depth_map(sql)
    match = re.search(rf"(?<![\w.]){re.escape(start_word)}(?![\w])", lowered)
    if not match:
        return ""
    base_depth = depths[match.start()]
    start = match.end()

    end = len(sql)
    for i in range(start, len(sql)):
        if depths[i] < base_depth:
            end = i
            break
    tail_low = lowered[start:end]
    for word in _CLAUSE_WORDS:
        if word == start_word:
            continue
        for m in re.finditer(rf"(?<![\w.]){re.escape(word)}(?![\w])", tail_low):
            if depths[start + m.start()] == base_depth:
                end = min(end, start + m.start())
                break
    return sql[start:end].strip()


def extract_tables(sql: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """Return (tables, alias -> table, table -> fully-qualified name)."""
    tables: list[str] = []
    aliases: dict[str, str] = {}
    qualified: dict[str, str] = {}
    pattern = re.compile(
        r"(?:from|join)\s+`?([A-Za-z_][\w.\-]*)`?(?:\s+(?:as\s+)?([A-Za-z_]\w*))?",
        re.I,
    )
    for match in pattern.finditer(sql):
        table_ref, alias = match.group(1), match.group(2)
        short = table_ref.split(".")[-1]
        if short.lower() in {"select", "unnest"}:
            continue
        if short not in tables:
            tables.append(short)
        if "." in table_ref:
            qualified[short.lower()] = table_ref
        aliases[short.lower()] = short
        if alias and alias.lower() not in {
            "on", "where", "group", "order", "left", "right", "inner",
            "full", "cross", "join", "having", "qualify", "limit", "using",
        }:
            aliases[alias.lower()] = short
    return tables, aliases, qualified


def extract_joins(sql: str, aliases: dict[str, str]) -> list[JoinSpec]:
    joins: list[JoinSpec] = []
    pattern = re.compile(
        r"(left outer|right outer|full outer|left|right|full|inner|cross)?\s*join\s+"
        r"`?([A-Za-z_][\w.\-]*)`?(?:\s+(?:as\s+)?([A-Za-z_]\w*))?\s+on\s+(.+?)"
        r"(?=(?:\s+(?:left|right|full|inner|cross)?\s*join\s)|\s+where\s|\s+group by\s|"
        r"\s+order by\s|\s+qualify\s|\s+having\s|\s+limit\s|$)",
        re.I,
    )
    for match in pattern.finditer(sql):
        join_type = (match.group(1) or "INNER").upper().replace(" OUTER", "")
        right_table = match.group(2).split(".")[-1]
        condition = match.group(4).strip()
        eq = re.search(
            r"`?(\w+)`?\.`?(\w+)`?\s*=\s*`?(\w+)`?\.`?(\w+)`?", condition
        )
        if not eq:
            continue
        left_ref, left_col, right_ref, right_col = eq.groups()
        left_table = aliases.get(left_ref.lower(), left_ref)
        other_table = aliases.get(right_ref.lower(), right_ref)
        # Orient so `right_table` is the table introduced by this JOIN.
        if other_table.lower() == right_table.lower():
            joins.append(JoinSpec(
                left_table=left_table, left_column=left_col,
                right_table=other_table, right_column=right_col,
                join_type=join_type, raw=condition,
            ))
        else:
            joins.append(JoinSpec(
                left_table=other_table, left_column=right_col,
                right_table=left_table, right_column=left_col,
                join_type=join_type, raw=condition,
            ))
    return joins


_CASE_WHEN = re.compile(r"\bwhen\s+(.+?)\s+then\b", re.I | re.S)
_COALESCE = re.compile(r"\b(?:coalesce|ifnull)\s*\(\s*`?([\w.]+)`?\s*,", re.I)
_NULLIF = re.compile(r"\bnullif\s*\(\s*`?([\w.]+)`?\s*,", re.I)


def extract_predicates(sql: str) -> list[PredicateSpec]:
    """Collect row-gating expressions from WHERE / CASE / HAVING / QUALIFY / COALESCE."""
    specs: list[PredicateSpec] = []

    for clause_name, origin in (("where", "where"), ("having", "having"), ("qualify", "qualify")):
        clause = _clause(sql, clause_name)
        for part in split_top_level(clause, (" and ",)):
            cleaned = part.strip().rstrip(";")
            if cleaned:
                specs.append(PredicateSpec(expression=cleaned, origin=origin))

    for match in _CASE_WHEN.finditer(sql):
        for part in split_top_level(match.group(1).strip(), (" and ",)):
            specs.append(PredicateSpec(expression=part.strip(), origin="case"))

    for pattern, origin in ((_COALESCE, "coalesce"), (_NULLIF, "coalesce")):
        for match in pattern.finditer(sql):
            ref = match.group(1)
            specs.append(PredicateSpec(
                expression=f"{ref} IS NULL", origin=origin, column_hint=ref,
            ))

    seen: set[str] = set()
    unique: list[PredicateSpec] = []
    for spec in specs:
        key = f"{spec.origin}:{spec.expression.lower()}"
        if key not in seen:
            seen.add(key)
            unique.append(spec)
    return unique


def extract_group_by(sql: str) -> list[str]:
    clause = _clause(sql, "group by")
    return [p.strip().strip("`") for p in split_top_level(clause, (",",)) if p.strip()]


def extract_order_by(sql: str) -> list[str]:
    clause = _clause(sql, "order by")
    items = []
    for part in split_top_level(clause, (",",)):
        token = re.sub(r"\s+(asc|desc)\b.*$", "", part.strip(), flags=re.I).strip("`")
        if token:
            items.append(token)
    return items


_AGG = re.compile(r"\b(sum|count|avg|min|max|array_agg|string_agg|countif)\s*\(\s*(distinct\s+)?`?([\w.*]+)`?", re.I)


def extract_aggregates(sql: str) -> list[str]:
    """Return the column references that sit inside an aggregate function."""
    found: list[str] = []
    for match in _AGG.finditer(sql):
        ref = match.group(3)
        if ref and ref != "*" and ref not in found:
            found.append(ref)
    return found


_WINDOW = re.compile(r"\bover\s*\(\s*partition\s+by\s+(.+?)(?:\border\s+by\b|\))", re.I | re.S)
_ROW_NUMBER = re.compile(r"\brow_number\s*\(\s*\)\s*over", re.I)


def extract_window_partitions(sql: str) -> list[str]:
    partitions: list[str] = []
    for match in _WINDOW.finditer(sql):
        for part in split_top_level(match.group(1), (",",)):
            token = part.strip().strip("`")
            if token and token not in partitions:
                partitions.append(token)
    return partitions


def has_dedup(sql: str) -> bool:
    lowered = sql.lower()
    return bool(_ROW_NUMBER.search(sql)) or "select distinct" in lowered


def extract_select_mappings(sql: str) -> list[ColumnMapping]:
    """Read the SELECT list into target-column mappings."""
    clause = _clause(sql, "select")
    clause = re.sub(r"^\s*distinct\s+", "", clause, flags=re.I)
    mappings: list[ColumnMapping] = []
    for item in split_top_level(clause, (",",)):
        item = item.strip()
        if not item or item == "*":
            continue
        alias_match = re.search(r"\s+as\s+`?(\w+)`?$", item, re.I)
        if alias_match:
            target = alias_match.group(1)
            expression = item[: alias_match.start()].strip()
        else:
            expression = item
            target = item.split(".")[-1].strip("`")
        source_table = source_column = None
        ref = re.fullmatch(r"`?(\w+)`?\.`?(\w+)`?", expression.strip())
        if ref:
            source_table, source_column = ref.group(1), ref.group(2)
        elif re.fullmatch(r"`?\w+`?", expression.strip()):
            source_column = expression.strip().strip("`")
        mappings.append(ColumnMapping(
            target_column=target, expression=expression,
            source_table=source_table, source_column=source_column,
        ))
    return mappings


def extract_target_table(sql: str) -> str | None:
    match = re.search(
        r"(?:create\s+or\s+replace\s+table|create\s+table(?:\s+if\s+not\s+exists)?|insert\s+into|merge\s+into|merge)\s+`?([\w.\-]+)`?",
        sql, re.I,
    )
    return match.group(1) if match else None


def referenced_columns(expression: str) -> list[tuple[str | None, str]]:
    """Every `alias.column` or bare `column` reference in an expression."""
    cleaned = re.sub(r"'[^']*'", " ", expression)
    cleaned = re.sub(r'"[^"]*"', " ", cleaned)
    refs: list[tuple[str | None, str]] = []
    for match in re.finditer(r"`?([A-Za-z_]\w*)`?\.`?([A-Za-z_]\w*)`?", cleaned):
        refs.append((match.group(1), match.group(2)))
    consumed = re.sub(r"`?[A-Za-z_]\w*`?\.`?[A-Za-z_]\w*`?", " ", cleaned)
    keywords = {
        "and", "or", "not", "is", "null", "in", "like", "between", "when", "then",
        "else", "end", "case", "true", "false", "cast", "as", "coalesce", "ifnull",
        "current_date", "current_timestamp", "date", "timestamp", "interval", "day",
        "select", "from", "where", "distinct", "on", "safe_cast", "upper", "lower", "trim",
    }
    for match in re.finditer(r"(?<![\w.])([A-Za-z_]\w*)(?![\w.(])", consumed):
        token = match.group(1)
        if token.lower() not in keywords:
            refs.append((None, token))
    return refs
