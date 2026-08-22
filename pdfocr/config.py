"""Runtime configuration, read from environment variables (or a .env file)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PDFOCR_", env_file=".env", extra="ignore")

    # --- server ---
    host: str = "0.0.0.0"  # noqa: S104 - binding to the LAN is the point of this service
    port: int = 8000
    api_key: str | None = None  # when set, clients must send X-API-Key

    # --- storage ---
    storage_dir: Path = Path("storage")
    job_ttl_seconds: int = 24 * 3600
    max_upload_mb: int = 200

    # --- OCR ---
    engine: str = "auto"  # auto | vlm | native | tesseract
    dpi: int = 200
    max_pages: int = 0  # 0 = no limit
    page_concurrency: int = 2
    job_concurrency: int = 2

    # --- vision LLM backend (OpenAI-compatible: Ollama, vLLM, LM Studio, llama.cpp) ---
    vlm_base_url: str = "http://localhost:11434/v1"
    vlm_model: str = "qwen2.5vl:7b"
    vlm_api_key: str = "not-needed"
    vlm_timeout: float = 300.0
    vlm_max_tokens: int = 4096
    vlm_temperature: float = 0.0
    vlm_prompt: str = (
        "You are an OCR engine. Transcribe every character of text visible in this page "
        "image into Markdown. Preserve the original reading order, headings, lists and line "
        "breaks. Render tables as Markdown tables. Do not translate, summarise, explain or add "
        "commentary. If the page contains no text, reply with an empty response."
    )

    # --- tesseract fallback ---
    tesseract_cmd: str = "tesseract"
    tesseract_lang: str = "eng"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


settings = Settings()
