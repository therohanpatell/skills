"""config.yaml handling. UI settings always override the file."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_MODEL = "qwen3:8b"
# Fallback list shown when Ollama is unreachable; installed models are otherwise
# detected from the server. Qwen3 ships 0.6b/1.7b/4b/8b/14b/30b/32b -- there is
# no 7b tag, so 8b is the small-model tier.
KNOWN_MODELS = ["qwen3:8b", "qwen3:4b", "qwen3:14b", "qwen3:30b", "qwen3:32b"]


@dataclass
class OllamaSettings:
    host: str = "http://localhost:11434"
    model: str = DEFAULT_MODEL
    temperature: float = 0.1
    timeout: float = 300.0


@dataclass
class GenerationSettings:
    max_rows: int = 50
    max_retries: int = 2
    mode: str = "fast"


@dataclass
class CacheSettings:
    enabled: bool = True
    directory: str = ".dtf_cache"


@dataclass
class AppConfig:
    ollama: OllamaSettings = field(default_factory=OllamaSettings)
    generation: GenerationSettings = field(default_factory=GenerationSettings)
    cache: CacheSettings = field(default_factory=CacheSettings)
    skills_directory: str = "./skills"
    output_directory: str = "./output"
    source: str | None = None

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> "AppConfig":
        """Read config.yaml if present; fall back to defaults silently."""
        config = cls()
        p = Path(path)
        if not p.is_file():
            for candidate in (Path("config.yaml.example"),):
                if candidate.is_file():
                    p = candidate
                    break
            else:
                return config
        try:
            payload = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return config
        config.source = str(p)

        ollama = payload.get("ollama") or {}
        config.ollama = OllamaSettings(
            host=str(ollama.get("host", config.ollama.host)),
            model=str(ollama.get("model", config.ollama.model)),
            temperature=float(ollama.get("temperature", config.ollama.temperature)),
            timeout=float(ollama.get("timeout", config.ollama.timeout)),
        )
        generation = payload.get("generation") or {}
        config.generation = GenerationSettings(
            max_rows=int(generation.get("max_rows", config.generation.max_rows)),
            max_retries=int(generation.get("max_retries", config.generation.max_retries)),
            mode=str(generation.get("mode", config.generation.mode)),
        )
        cache = payload.get("cache") or {}
        config.cache = CacheSettings(
            enabled=bool(cache.get("enabled", config.cache.enabled)),
            directory=str(cache.get("directory", config.cache.directory)),
        )
        config.skills_directory = str((payload.get("skills") or {}).get("directory", config.skills_directory))
        config.output_directory = str((payload.get("output") or {}).get("directory", config.output_directory))
        return config
