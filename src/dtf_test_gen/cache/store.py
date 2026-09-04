"""On-disk cache of analysis results, keyed by everything that can change one."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dtf_test_gen.models.analysis import AnalysisResult


def cache_key(
    ddl_payloads: list[str],
    dtf_payload: str,
    skill_payloads: list[str],
    model: str,
    mode: str,
) -> str:
    digest = hashlib.sha256()
    for payload in sorted(ddl_payloads):
        digest.update(payload.encode("utf-8", "replace"))
        digest.update(b"\x00")
    digest.update(dtf_payload.encode("utf-8", "replace"))
    digest.update(b"\x01")
    for payload in sorted(skill_payloads):
        digest.update(payload.encode("utf-8", "replace"))
        digest.update(b"\x02")
    digest.update(f"{model}|{mode}".encode())
    return digest.hexdigest()[:32]


class AnalysisCache:
    def __init__(self, directory: str | Path = ".dtf_cache", enabled: bool = True):
        self.directory = Path(directory)
        self.enabled = enabled

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> AnalysisResult | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            return AnalysisResult.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:                       # noqa: BLE001 - a stale cache is never fatal
            return None

    def put(self, key: str, result: AnalysisResult) -> None:
        if not self.enabled:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._path(key).write_text(result.model_dump_json(indent=1), encoding="utf-8")
        except OSError:
            pass

    def clear(self) -> int:
        if not self.directory.is_dir():
            return 0
        removed = 0
        for path in self.directory.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def count(self) -> int:
        if not self.directory.is_dir():
            return 0
        return len(list(self.directory.glob("*.json")))
