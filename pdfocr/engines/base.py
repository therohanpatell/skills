"""Engine interface shared by every OCR backend."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from ..models import EngineInfo
from ..pdf import PageImage, render_page


@dataclass
class PageRequest:
    """One page of work handed to an engine.

    The rasterised image is produced on first use so text-layer engines never pay for it.
    """

    path: Path
    number: int
    dpi: int = 200

    @cached_property
    def image(self) -> PageImage:
        return render_page(self.path, self.number, self.dpi)


class OcrError(RuntimeError):
    """Raised when an engine cannot transcribe a page."""


class Engine(ABC):
    name: str = "base"

    @abstractmethod
    def run(self, request: PageRequest) -> str:
        """Return the text of a single page."""

    def check(self) -> EngineInfo:
        """Report whether this engine can run right now, and why not if it can't."""
        return EngineInfo(name=self.name, available=True)
