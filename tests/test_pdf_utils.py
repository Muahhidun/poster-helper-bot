"""Tests for PDF invoice processing (pdf_utils.py)"""
import io
import pytest

from pdf_utils import extract_pdf_text, convert_pdf_to_images, prepare_pdf_for_assistant


def _make_text_pdf(lines):
    """Build a minimal one-page PDF with a real text layer (no external deps)."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject, DictionaryObject, NameObject, NumberObject, StreamObject
    )

    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)

    # Content stream printing each line with the standard Helvetica font.
    # Cyrillic needs CID fonts, so tests use Latin/digit invoice rows —
    # the extraction mechanics are identical.
    text_ops = ["BT", "/F1 12 Tf", "50 800 Td"]
    for line in lines:
        safe = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        text_ops.append(f"({safe}) Tj")
        text_ops.append("0 -20 Td")
    text_ops.append("ET")
    stream_data = "\n".join(text_ops).encode("latin-1")

    stream = StreamObject()
    stream.set_data(stream_data)
    stream_ref = writer._add_object(stream)
    page[NameObject("/Contents")] = stream_ref

    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})
    })

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_blank_pdf():
    """PDF with no text layer at all — simulates a scanned invoice."""
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_extract_text_from_digital_pdf():
    pdf_data = _make_text_pdf([
        "INVOICE Metro Cash and Carry",
        "Fri dolki 10.0 x 1200.00 = 12000.00",
        "Mozzarella 5.0 x 2680.00 = 13400.00",
        "TOTAL: 25400.00",
    ])
    text = extract_pdf_text(pdf_data)
    assert "Fri dolki" in text
    assert "12000.00" in text
    assert "25400.00" in text


def test_extract_text_scanned_pdf_returns_empty():
    """A scanned PDF (no text layer) must return '' so the Vision path kicks in"""
    assert extract_pdf_text(_make_blank_pdf()) == ""


def test_extract_text_corrupt_pdf_returns_empty():
    assert extract_pdf_text(b"not a pdf at all") == ""


def test_convert_pdf_to_images_graceful_without_poppler():
    """Must never raise even when poppler is missing — returns a list"""
    result = convert_pdf_to_images(_make_blank_pdf())
    assert isinstance(result, list)


def test_prepare_digital_pdf():
    pdf_data = _make_text_pdf(["Supplier: Kurdamir", "Mushrooms 3.5 x 1800 = 6300"])
    info = prepare_pdf_for_assistant(pdf_data, "invoice.pdf")
    assert info['is_scanned'] is False
    assert "Kurdamir" in info['text']
    assert info['images'] == []


def test_prepare_scanned_pdf():
    info = prepare_pdf_for_assistant(_make_blank_pdf(), "scan.pdf")
    assert info['is_scanned'] is True
    assert info['text'] == ""
    assert isinstance(info['images'], list)
