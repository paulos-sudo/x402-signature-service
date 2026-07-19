"""PDF-Validierung & Seitenberechnung (pypdf).

Wird genutzt, um (a) sicherzustellen, dass nur valide, unverschluesselte PDFs
angenommen werden und (b) die letzte Seitenzahl fuer die Platzierung des
Signaturfelds (unten rechts, letzte Seite) zu bestimmen.
"""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader


class InvalidPdfError(Exception):
    pass


def validate_pdf(data: bytes) -> int:
    """Prueft das PDF und liefert die Seitenanzahl (>=1)."""
    if not data.startswith(b"%PDF-"):
        raise InvalidPdfError("Document is not a valid PDF (missing %PDF header)")
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise InvalidPdfError("Encrypted/password-protected PDFs are not supported")
        num_pages = len(reader.pages)
    except InvalidPdfError:
        raise
    except Exception as exc:  # pypdf wirft diverse Exception-Typen
        raise InvalidPdfError("Document could not be parsed as a PDF") from exc
    if num_pages < 1:
        raise InvalidPdfError("PDF contains no pages")
    return num_pages


# Position des Signaturfelds: unten rechts auf der letzten Seite,
# in Prozent der Seitenmasse (Documenso-Konvention).
SIGNATURE_FIELD_POSITION = {
    "page_x": 62.0,   # % vom linken Rand
    "page_y": 88.0,   # % vom oberen Rand
    "page_width": 30.0,
    "page_height": 6.0,
}
