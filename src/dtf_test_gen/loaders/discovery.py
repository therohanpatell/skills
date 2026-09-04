"""Find DDL / DTF / skill files in a project directory without assuming filenames."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DATA_SUFFIXES = {".json", ".yaml", ".yml"}
DOC_SUFFIXES = {".md", ".markdown"}
# Config files are often shipped as templates: `feature.json.template`.
TEMPLATE_SUFFIXES = {".template", ".tmpl", ".j2", ".jinja"}
SKIP_DIRS = {
    ".git", ".github", "__pycache__", ".venv", "venv", "node_modules",
    "output", ".dtf_cache", ".idea", "target",
}

DDL_HINTS = ("ddl", "schema", "schemas", "tables", "table")
DTF_HINTS = ("dtf", "transform", "transformation", "pipeline", "mapping", "job")
SKILL_HINTS = ("skill", "skills", "knowledge", "docs", "reference")


class LoadError(Exception):
    """Raised with a human-readable reason the UI can show verbatim."""

    def __init__(self, message: str, file: str | None = None, reason: str | None = None):
        super().__init__(message)
        self.message = message
        self.file = file
        self.reason = reason


def read_any(path: str | Path) -> Any:
    """Read a JSON or YAML file, raising LoadError with a readable reason."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise LoadError("Unable to read file", file=p.name, reason=str(exc)) from exc
    return parse_text(text, p.name)


def effective_suffix(path: Path) -> str:
    """The suffix that decides the format, seeing through a template wrapper.

    `npw_details.json.template` is a JSON file; its literal suffix is not.
    """
    if path.suffix.lower() in TEMPLATE_SUFFIXES:
        return Path(path.stem).suffix.lower()
    return path.suffix.lower()


def _one_line(exc: Exception) -> str:
    return " ".join(str(exc).split())[:200]


def parse_text(text: str, filename: str = "<uploaded>") -> Any:
    suffix = effective_suffix(Path(filename))
    try:
        if suffix in {".yaml", ".yml"}:
            return yaml.safe_load(text)
        return json.loads(text)
    except json.JSONDecodeError:
        # Some "*.json" exports are really YAML; try the tolerant parser once.
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise LoadError("Unable to parse file", file=filename, reason=_one_line(exc)) from exc
    except yaml.YAMLError as exc:
        raise LoadError("Unable to parse file", file=filename, reason=_one_line(exc)) from exc


def _looks_like_ddl(payload: Any) -> bool:
    if isinstance(payload, list):
        return bool(payload) and isinstance(payload[0], dict) and (
            {"name", "type"} <= set(k.lower() for k in payload[0])
            or "columns" in {k.lower() for k in payload[0]}
            or "fields" in {k.lower() for k in payload[0]}
        )
    if isinstance(payload, dict):
        keys = {k.lower() for k in payload}
        if keys & {"columns", "fields"}:
            return True
        if "schema" in keys and isinstance(payload.get("schema"), (dict, list)):
            return True
        if "tables" in keys:
            return True
    return False


def _looks_like_dtf(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    keys = {k.lower() for k in payload}
    return bool(keys & {
        "sql", "query", "statement", "transformation", "transformations",
        "mappings", "mapping", "target", "target_table", "source", "sources",
        "joins", "filters", "where", "transformation_sql",
    })


@dataclass
class ProjectFiles:
    root: Path
    ddl: list[Path] = field(default_factory=list)
    dtf: list[Path] = field(default_factory=list)
    skills: list[Path] = field(default_factory=list)
    unclassified: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.ddl or self.dtf or self.skills)


def discover_project(root: str | Path) -> ProjectFiles:
    """Walk a project directory and classify every data/doc file it contains.

    Classification uses directory names first (ddl/, dtf/, skills/), then falls
    back to inspecting the parsed payload -- so unusual layouts still work.
    """
    root_path = Path(root).expanduser()
    result = ProjectFiles(root=root_path)
    if not root_path.is_dir():
        result.errors.append(f"Not a directory: {root_path}")
        return result

    for path in sorted(root_path.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        suffix = effective_suffix(path)
        rel_parts = {p.lower() for p in path.relative_to(root_path).parts[:-1]}

        if suffix in DOC_SUFFIXES:
            # Every markdown file is a candidate skill; the UI decides which to send.
            result.skills.append(path)
            continue
        if suffix not in DATA_SUFFIXES:
            continue

        by_dir_ddl = bool(rel_parts & set(DDL_HINTS))
        by_dir_dtf = bool(rel_parts & set(DTF_HINTS))
        try:
            payload = read_any(path)
        except LoadError as exc:
            result.errors.append(f"{path.name}: {exc.reason or exc.message}")
            continue

        if by_dir_dtf and not by_dir_ddl:
            result.dtf.append(path)
        elif by_dir_ddl and not by_dir_dtf:
            result.ddl.append(path)
        elif _looks_like_dtf(payload):
            result.dtf.append(path)
        elif _looks_like_ddl(payload):
            result.ddl.append(path)
        else:
            result.unclassified.append(path)

    return result
