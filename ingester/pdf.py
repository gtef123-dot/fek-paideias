"""Download a ΦΕΚ PDF and extract its (Greek) text with PyMuPDF.

PyMuPDF recovers the custom-encoded Greek glyphs correctly (naive pdftotext
garbles them). Best-effort: returns "" on any failure or if PyMuPDF is absent.
"""
from __future__ import annotations

from . import config
from .net import get

try:
    import fitz  # PyMuPDF
except Exception:  # noqa: BLE001
    fitz = None


def extract_text(pdf_url: str) -> str:
    if not pdf_url or fitz is None:
        return ""
    try:
        data = get(pdf_url).content
        doc = fitz.open(stream=data, filetype="pdf")
        text = "".join(page.get_text() for page in doc)
        doc.close()
        return text.strip()[: config.PDF_MAX_CHARS]
    except Exception as exc:  # noqa: BLE001
        print(f"   [pdf] extract failed ({type(exc).__name__})")
        return ""
