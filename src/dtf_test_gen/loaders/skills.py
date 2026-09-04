"""Load DTF skill/knowledge markdown and pick only the ones the DTF actually needs.

Sending every skill file to the model is the single biggest token waster, so a
skill is only selected when a signal for it is present in the parsed DTF.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from dtf_test_gen.models.dtf import DTFConfig

# topic -> (filename keywords, human label)
_TOPICS: dict[str, tuple[tuple[str, ...], str]] = {
    "filter": (("filter", "where", "predicate"), "row filtering"),
    "join": (("join", "lookup", "enrich"), "joins"),
    "conditional": (("conditional", "case", "when", "if", "branch"), "conditional logic"),
    "aggregation": (("aggregation", "aggregate", "groupby", "group_by", "sum", "metric"), "aggregation"),
    "window": (("window", "partition", "analytic", "rank"), "window functions"),
    "null": (("null", "default", "coalesce", "missing"), "NULL/default handling"),
    "dedup": (("dedup", "duplicate", "distinct", "unique"), "deduplication"),
    "scd": (("scd", "history", "slowly"), "SCD history"),
    "mapping": (("mapping", "map", "rename", "passthrough"), "column mapping"),
}

MAX_EXCERPT_CHARS = 900


@dataclass
class Skill:
    name: str
    path: str | None
    text: str
    topics: list[str] = field(default_factory=list)
    selected: bool = False
    reason: str = ""

    def excerpt(self, limit: int = MAX_EXCERPT_CHARS) -> str:
        """A compact excerpt: the rule-bearing lines, not the whole document."""
        lines = [ln.rstrip() for ln in self.text.splitlines()]
        keep: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#") or stripped.startswith(("- ", "* ", "1.", "2.", "3.")):
                keep.append(stripped)
            elif len(keep) < 4:
                keep.append(stripped)
        body = "\n".join(keep) if keep else self.text.strip()
        if len(body) <= limit:
            return body
        return body[:limit].rsplit("\n", 1)[0] + "\n..."


def _topics_for(name: str, text: str) -> list[str]:
    """Match on the filename first; fall back to whole-word matches in the text.

    Substring matching is deliberately avoided -- "if" inside "verify" used to
    select the conditional skill for every document in the directory.
    """
    stem = Path(name).stem.lower()
    filename_topics = [
        topic for topic, (keywords, _label) in _TOPICS.items()
        if any(kw in stem for kw in keywords)
    ]
    if filename_topics:
        return filename_topics

    head = text[:2000].lower()
    found = []
    for topic, (keywords, _label) in _TOPICS.items():
        if any(re.search(rf"\b{re.escape(kw)}\b", head) for kw in keywords):
            found.append(topic)
    return found


def load_skill_payload(name: str, text: str, path: str | None = None) -> Skill:
    return Skill(name=name, path=path, text=text, topics=_topics_for(name, text))


def load_skills(paths: list[str | Path]) -> list[Skill]:
    skills: list[Skill] = []
    for raw in paths:
        p = Path(raw)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        skills.append(load_skill_payload(p.name, text, str(p)))
    return skills


def dtf_signals(config: DTFConfig) -> dict[str, str]:
    """Which transformation features the DTF exhibits, and the evidence for each."""
    signals: dict[str, str] = {}
    sql = (config.raw_sql or "").lower()

    if config.predicates:
        origins = {p.origin for p in config.predicates}
        if origins & {"where", "filter", "having", "qualify"}:
            example = next(p.expression for p in config.predicates)
            signals["filter"] = f"DTF filters rows on `{example}`."
        if "case" in origins or "case when" in sql:
            signals["conditional"] = "DTF contains CASE WHEN branching."
    if config.joins:
        join = config.joins[0]
        signals["join"] = f"DTF contains {join.join_type} JOIN on {join.left_table}.{join.left_column}."
    if config.group_by or config.aggregates:
        signals["aggregation"] = (
            f"DTF groups by {', '.join(config.group_by) or 'a key'} with aggregates."
        )
    if config.window_partitions:
        signals["window"] = f"DTF uses OVER (PARTITION BY {', '.join(config.window_partitions)})."
    if config.dedup_keys or "distinct" in sql or "row_number" in sql:
        signals["dedup"] = "DTF deduplicates rows."
    if any(p.origin == "coalesce" for p in config.predicates) or "coalesce" in sql or "ifnull" in sql:
        signals["null"] = "DTF uses COALESCE/IFNULL for NULL defaulting."
    if any(m.is_one_to_one for m in config.mappings):
        signals["mapping"] = "DTF contains 1:1 column mappings."
    if re.search(r"\b(is_current|effective_from|effective_to|scd)\b", sql):
        signals["scd"] = "DTF references SCD history columns."
    return signals


def auto_select(skills: list[Skill], config: DTFConfig) -> list[Skill]:
    """Mark the skills whose topic matches a signal actually present in the DTF."""
    signals = dtf_signals(config)
    for skill in skills:
        matched = [t for t in skill.topics if t in signals]
        if matched:
            skill.selected = True
            skill.reason = signals[matched[0]]
        else:
            skill.selected = False
            skill.reason = "No matching transformation feature found in the DTF."
    return skills
