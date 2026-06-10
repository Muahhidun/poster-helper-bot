"""Utilities for parsing PDF invoices in the assistant chat.

Electronic invoices (PDF) often contain a real text layer — extracting it
gives 100% accurate numbers without OCR. Scanned PDFs have no text layer,
so we convert pages to images and let the Vision model read them.
"""
import io
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

# Minimum meaningful characters per page to consider the PDF "digital"
# (scanned PDFs usually yield 0 or just a few garbage chars)
MIN_TEXT_CHARS_PER_PAGE = 30

# Limit pages to keep prompts and image payloads bounded
MAX_PDF_PAGES = 10


def extract_pdf_text(pdf_data: bytes) -> str:
    """Extract the text layer from a PDF. Returns '' if no usable text."""
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("pypdf is not installed — PDF text extraction unavailable")
        return ""

    try:
        reader = PdfReader(io.BytesIO(pdf_data))
        pages_text = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            try:
                pages_text.append(page.extract_text() or "")
            except Exception as page_err:
                logger.warning(f"PDF page text extraction failed: {page_err}")
                pages_text.append("")
        full_text = "\n".join(pages_text).strip()

        # Heuristic: treat as scanned if too little text was found
        num_pages = max(len(reader.pages[:MAX_PDF_PAGES]), 1)
        if len(full_text) < MIN_TEXT_CHARS_PER_PAGE * 1 and num_pages >= 1:
            logger.info(f"PDF looks scanned: only {len(full_text)} chars of text extracted")
            return ""
        return full_text
    except Exception as e:
        logger.error(f"Failed to read PDF: {e}")
        return ""


def convert_pdf_to_images(pdf_data: bytes, max_pages: int = MAX_PDF_PAGES) -> List[bytes]:
    """Convert PDF pages to JPEG images (for scanned PDFs).

    Requires pdf2image + poppler. Returns [] gracefully when unavailable,
    in which case the raw PDF should be sent to a model with native PDF
    support (Gemini accepts application/pdf inline).
    """
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        logger.warning("pdf2image is not installed — scanned PDF conversion unavailable")
        return []

    try:
        pil_pages = convert_from_bytes(pdf_data, dpi=200, fmt='jpeg',
                                       first_page=1, last_page=max_pages)
        images = []
        for pil_page in pil_pages:
            buf = io.BytesIO()
            pil_page.save(buf, format='JPEG', quality=90)
            images.append(buf.getvalue())
        return images
    except Exception as e:
        # poppler binary missing or corrupt PDF — degrade gracefully
        logger.warning(f"PDF→image conversion failed (poppler missing?): {e}")
        return []


def prepare_pdf_for_assistant(pdf_data: bytes, filename: str = "document.pdf") -> Dict:
    """Prepare a PDF for the assistant pipeline.

    Returns dict:
      text       — extracted text layer ('' if scanned)
      images     — list of JPEG bytes for scanned PDFs ([] if conversion unavailable)
      is_scanned — True when no usable text layer was found
    """
    text = extract_pdf_text(pdf_data)
    is_scanned = not text

    images: List[bytes] = []
    if is_scanned:
        images = convert_pdf_to_images(pdf_data)
        if images:
            logger.info(f"Scanned PDF '{filename}': converted {len(images)} page(s) to images")
        else:
            logger.info(f"Scanned PDF '{filename}': sending raw PDF to model (no converter)")
    else:
        logger.info(f"Digital PDF '{filename}': extracted {len(text)} chars of text")

    return {
        'text': text,
        'images': images,
        'is_scanned': is_scanned,
    }
