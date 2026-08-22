"""Classic Tesseract OCR, used as an offline fallback when no vision model is available."""

from __future__ import annotations

import shutil
import subprocess

from ..config import Settings
from ..config import settings as default_settings
from ..models import EngineInfo
from .base import Engine, OcrError, PageRequest


class TesseractEngine(Engine):
    name = "tesseract"

    def __init__(self, config: Settings | None = None) -> None:
        self.config = config or default_settings

    def run(self, request: PageRequest) -> str:
        binary = shutil.which(self.config.tesseract_cmd)
        if binary is None:
            raise OcrError(f"{self.config.tesseract_cmd} is not installed")
        try:
            proc = subprocess.run(
                [binary, "stdin", "stdout", "-l", self.config.tesseract_lang],
                input=request.image.png,
                capture_output=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrError("tesseract timed out") from exc
        if proc.returncode != 0:
            raise OcrError(f"tesseract failed: {proc.stderr.decode('utf-8', 'replace')[:300]}")
        return proc.stdout.decode("utf-8", "replace").strip()

    def check(self) -> EngineInfo:
        binary = shutil.which(self.config.tesseract_cmd)
        if binary is None:
            return EngineInfo(
                name=self.name, available=False, detail=f"{self.config.tesseract_cmd} not on PATH"
            )
        return EngineInfo(
            name=self.name, available=True, detail=f"{binary} ({self.config.tesseract_lang})"
        )
