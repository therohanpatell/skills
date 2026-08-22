from __future__ import annotations

import pytest

from pdfocr import pdf


def test_page_count(text_pdf):
    assert pdf.page_count(text_pdf) == 2


def test_extract_text(text_pdf):
    assert "Invoice 2026-08" in pdf.extract_text(text_pdf, 1)
    assert "terms and conditions" in pdf.extract_text(text_pdf, 2)


def test_has_text_layer(text_pdf, blank_pdf):
    assert pdf.has_text_layer(text_pdf) is True
    assert pdf.has_text_layer(blank_pdf) is False


def test_render_page(text_pdf):
    image = pdf.render_page(text_pdf, 1, dpi=100)
    assert image.png.startswith(b"\x89PNG")
    assert image.width > 0 and image.height > 0


def test_render_page_out_of_range(text_pdf):
    with pytest.raises(pdf.PdfError):
        pdf.render_page(text_pdf, 99)


def test_not_a_pdf(tmp_path):
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"definitely not a pdf")
    with pytest.raises(pdf.PdfError):
        pdf.page_count(junk)
