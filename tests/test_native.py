"""Actual parser/native-tool checks. Missing tools are reported as skips."""
import shutil
import subprocess
from pathlib import Path
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from PIL import Image, ImageDraw, ImageFont
from app.config import settings
from app.services.parsing import parse_bounded, parse_direct


def native_pdf(file, text="Synthetic policy: request leave five working days in advance."):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"):NameObject("/Font"), NameObject("/Subtype"):NameObject("/Type1"), NameObject("/BaseFont"):NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):DictionaryObject({NameObject("/F1"):writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(file)


def test_native_pdf_bounded_extraction(tmp_path):
    file = tmp_path / "native.pdf"
    native_pdf(file)
    result = parse_bounded(file, "pdf", settings(tmp_path))
    assert "five working days" in result["blocks"][0]["text"]
    assert result["blocks"][0]["page"] == 1
    assert result["candidates"][0]["ocr"] is None


def test_encrypted_and_page_limit(tmp_path):
    file = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("password")
    writer.write(file)
    with pytest.raises(ValueError, match="Encrypted"):
        parse_direct(file, "pdf", settings(tmp_path))
    native_pdf(file)
    config = settings(tmp_path)
    config["PDF_MAX_PAGES"] = 0
    with pytest.raises(ValueError, match="page limit"):
        parse_direct(file, "pdf", config)


def test_real_bilingual_ocr(tmp_path):
    config = settings(tmp_path)
    if not Path(config["TESSERACT_CMD"]).exists() and not shutil.which(config["TESSERACT_CMD"]):
        pytest.skip("Tesseract missing")
    tessdata = Path(__file__).resolve().parents[1] / "instance/tessdata"
    if not (tessdata / "mon.traineddata").exists():
        pytest.skip("Mongolian OCR data missing; see README")
    config["TESSDATA_PREFIX"] = str(tessdata)
    font_file = Path("C:/Windows/Fonts/arial.ttf")
    if not font_file.exists():
        pytest.skip("Test font missing")
    image = Image.new("RGB", (1600, 500), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_file), 52)
    draw.text((60, 80), "Leave request: 5 working days.", font=font, fill="black")
    draw.text((60, 200), "Өдөр бүр Үүрэг 5 хоног.", font=font, fill="black")
    file = tmp_path / "scan.pdf"
    image.save(file, resolution=150)
    image.close()
    result = parse_bounded(file, "pdf", config)
    text = result["blocks"][0]["text"]
    assert "5" in text and "working" in text
    assert "Өдөр" in text and "Үүрэг" in text, text
    assert result["blocks"][0]["method"] == "ocr"
    assert result["warnings"]


def test_real_doc_conversion(tmp_path):
    from conftest import docx_file
    config = settings(tmp_path)
    if not Path(config["LIBREOFFICE_CMD"]).exists() and not shutil.which(config["LIBREOFFICE_CMD"]):
        pytest.skip("LibreOffice missing")
    source = tmp_path / "original.docx"
    source.write_bytes(docx_file("Өдөр Үүрэг legacy conversion preserves text.").read())
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    subprocess.run([config["LIBREOFFICE_CMD"], f"-env:UserInstallation={(tmp_path/'lo-profile').as_uri()}", "--headless", "--convert-to", "doc:MS Word 97", "--outdir", str(uploads), str(source)],
        timeout=120, check=True, capture_output=True, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    result = parse_bounded(uploads / "original.doc", "doc", config)
    assert "Өдөр Үүрэг" in result["blocks"][1]["text"]
    assert any("legacy DOC" in warning for warning in result["warnings"])
