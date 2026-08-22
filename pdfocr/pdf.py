"""PDF inspection and page rasterisation, backed by PyMuPDF."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:  # PyMuPDF renamed its module; keep working on older wheels too
    import pymupdf
except ImportError:  # pragma: no cover - PyMuPDF < 1.24.3
    import fitz as pymupdf


class PdfError(RuntimeError):
    """Raised when a file cannot be opened or read as a PDF."""


@dataclass(frozen=True)
class PageImage:
    number: int
    png: bytes
    width: int
    height: int


def page_count(path: Path) -> int:
    try:
        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                raise PdfError("PDF is password protected")
            return doc.page_count
    except PdfError:
        raise
    except Exception as exc:  # pragma: no cover - depends on the broken file supplied
        raise PdfError(f"could not open PDF: {exc}") from exc


def extract_text(path: Path, number: int) -> str:
    """Return the embedded text layer of a 1-based page, if the PDF has one."""
    with pymupdf.open(path) as doc:
        return doc[number - 1].get_text("text").strip()


def has_text_layer(path: Path, sample_pages: int = 3, min_chars: int = 40) -> bool:
    """Cheap heuristic: does this PDF already carry a usable text layer?

    Digitally generated PDFs do, and running a vision model over them would be slower and
    less accurate than simply reading the text out.
    """
    with pymupdf.open(path) as doc:
        pages = min(sample_pages, doc.page_count)
        if pages == 0:
            return False
        chars = sum(len(doc[index].get_text("text").strip()) for index in range(pages))
        return chars / pages >= min_chars


def render_page(path: Path, number: int, dpi: int = 200) -> PageImage:
    """Rasterise a single 1-based page to PNG bytes."""
    zoom = dpi / 72.0
    with pymupdf.open(path) as doc:
        if not 1 <= number <= doc.page_count:
            raise PdfError(f"page {number} out of range (1-{doc.page_count})")
        pix = doc[number - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return PageImage(number=number, png=pix.tobytes("png"), width=pix.width, height=pix.height)
