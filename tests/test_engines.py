from __future__ import annotations

import httpx
import pytest

from pdfocr.config import Settings
from pdfocr.engines import PageRequest, build, resolve
from pdfocr.engines.base import OcrError
from pdfocr.engines.vlm import VlmEngine


def test_native_engine_reads_text_layer(text_pdf):
    engine = build("native")
    assert "Invoice 2026-08" in engine.run(PageRequest(path=text_pdf, number=1))


def test_auto_prefers_native_for_digital_pdfs(text_pdf):
    assert resolve("auto", text_pdf).name == "native"


def test_auto_reports_when_nothing_is_available(blank_pdf):
    config = Settings(
        vlm_base_url="http://127.0.0.1:1/v1", tesseract_cmd="tesseract-does-not-exist"
    )
    with pytest.raises(OcrError, match="no OCR engine available"):
        resolve("auto", blank_pdf, config)


def test_unknown_engine():
    with pytest.raises(ValueError, match="unknown engine"):
        build("magic")


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, content: str) -> None:
        self._content = content

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def test_vlm_engine_strips_code_fences(monkeypatch, text_pdf):
    engine = VlmEngine(Settings())
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse("```markdown\n# Title\n```"))
    assert engine.run(PageRequest(path=text_pdf, number=1)) == "# Title"


def test_vlm_engine_reports_unreachable_backend(monkeypatch, text_pdf):
    def boom(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(OcrError, match="unreachable"):
        VlmEngine(Settings()).run(PageRequest(path=text_pdf, number=1))


def test_vlm_check_marks_backend_down(monkeypatch):
    def boom(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", boom)
    info = VlmEngine(Settings()).check()
    assert info.available is False and "unreachable" in info.detail
