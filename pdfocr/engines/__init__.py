"""Engine registry and the `auto` selection policy."""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from ..config import settings as default_settings
from ..models import EngineInfo
from ..pdf import has_text_layer
from .base import Engine, OcrError, PageRequest
from .native import NativeEngine
from .tesseract import TesseractEngine
from .vlm import VlmEngine

ENGINE_NAMES = ("auto", "vlm", "native", "tesseract")


def build(name: str, config: Settings | None = None) -> Engine:
    config = config or default_settings
    if name == "vlm":
        return VlmEngine(config)
    if name == "native":
        return NativeEngine()
    if name == "tesseract":
        return TesseractEngine(config)
    raise ValueError(f"unknown engine {name!r}; expected one of {', '.join(ENGINE_NAMES)}")


def resolve(name: str, pdf_path: Path, config: Settings | None = None) -> Engine:
    """Turn the requested engine name into a concrete engine for this document.

    `auto` prefers the PDF's own text layer when it has one (instant and exact), then a
    vision model, then Tesseract.
    """
    config = config or default_settings
    if name != "auto":
        return build(name, config)

    if has_text_layer(pdf_path):
        return NativeEngine()

    vlm = VlmEngine(config)
    if vlm.check().available:
        return vlm

    tesseract = TesseractEngine(config)
    if tesseract.check().available:
        return tesseract

    raise OcrError(
        "no OCR engine available: the PDF has no text layer, the vision model at "
        f"{config.vlm_base_url} is unreachable and tesseract is not installed"
    )


def status(config: Settings | None = None) -> list[EngineInfo]:
    config = config or default_settings
    return [build(name, config).check() for name in ("vlm", "native", "tesseract")]


__all__ = [
    "ENGINE_NAMES",
    "Engine",
    "OcrError",
    "PageRequest",
    "build",
    "resolve",
    "status",
]
