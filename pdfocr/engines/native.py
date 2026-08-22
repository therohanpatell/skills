"""Reads the embedded text layer of digitally generated PDFs. No model required."""

from __future__ import annotations

from ..models import EngineInfo
from ..pdf import extract_text
from .base import Engine, PageRequest


class NativeEngine(Engine):
    name = "native"

    def run(self, request: PageRequest) -> str:
        return extract_text(request.path, request.number)

    def check(self) -> EngineInfo:
        return EngineInfo(
            name=self.name,
            available=True,
            detail="reads the PDF text layer; no OCR, only works on non-scanned PDFs",
        )
