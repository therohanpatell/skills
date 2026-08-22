from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest


def _make_pdf(path: Path, pages: list[str]) -> Path:
    doc = pymupdf.open()
    for body in pages:
        page = doc.new_page()
        page.insert_text((72, 100), body, fontsize=14)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def text_pdf(tmp_path: Path) -> Path:
    """A digitally generated PDF - it carries a real text layer."""
    return _make_pdf(
        tmp_path / "text.pdf",
        ["Invoice 2026-08 total 1234.56 EUR for consulting services rendered.",
         "Page two carries the terms and conditions of this agreement in full."],
    )


@pytest.fixture
def blank_pdf(tmp_path: Path) -> Path:
    """A PDF with no text layer, standing in for a scan."""
    doc = pymupdf.open()
    doc.new_page()
    doc.save(tmp_path / "blank.pdf")
    doc.close()
    return tmp_path / "blank.pdf"
