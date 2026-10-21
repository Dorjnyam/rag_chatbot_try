"""Actual parser/native-tool checks. Missing tools are reported as skips."""
import shutil
import subprocess
from pathlib import Path
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from PIL import Image, ImageDraw, ImageFont
from app.config import settings
from app.services.parsing import parse_bounded, parse_direct
from app.services.preview_worker import _extract_map, _normalized, _boxes_for_range, _ocr, _convert


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


def test_preview_docx_conversion_uses_requested_output_name(tmp_path, monkeypatch):
    source = tmp_path / "company-policy.docx"
    source.write_bytes(b"synthetic docx placeholder")
    output = tmp_path / "preview-work"
    requested_pdf = output / "preview.pdf"

    def fake_convert(args, **kwargs):
        outdir = Path(args[args.index("--outdir") + 1])
        (outdir / "company-policy.pdf").write_bytes(b"%PDF-1.7 synthetic")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_convert)
    assert _convert(source, requested_pdf, "soffice", 10) == requested_pdf
    assert requested_pdf.read_bytes() == b"%PDF-1.7 synthetic"
    assert not (output / "company-policy.pdf").exists()


def test_preview_maps_unique_pdf_text_to_pdfium_coordinates(tmp_path):
    file = tmp_path / "preview.pdf"
    native_pdf(file, "Policy permits five working days.")
    mapping = _extract_map(file, [dict(block=1, page=1, text="Policy permits five working days.")],
        "source-hash", "extraction-hash", "converter-hash")
    assert mapping["page_count"] == 1
    block = mapping["pages"][0]["mapped"][0]
    assert block["status"] == "mapped"
    assert block["rects"]
    left, bottom, right, top = block["rects"][0]
    assert 0 <= left < right <= mapping["pages"][0]["width"]
    assert 0 <= bottom < top <= mapping["pages"][0]["height"]


def test_preview_ambiguous_duplicate_text_is_not_given_false_exact_bounds(tmp_path):
    file = tmp_path / "duplicate.pdf"
    native_pdf(file, "Policy applies. Policy applies.")
    mapping = _extract_map(file, [dict(block=1, page=1, text="Policy applies.")],
        "source-hash", "extraction-hash", "converter-hash")
    block = mapping["pages"][0]["mapped"][0]
    assert block["status"] == "page_only"
    assert block["rects"] == []


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_preview_rotated_pages_keep_bounds_inside_pdf_canvas(tmp_path, rotation):
    source = tmp_path / "source.pdf"
    native_pdf(source, "Rotation preserves the policy text coordinates.")
    reader = PdfReader(source)
    writer = PdfWriter()
    page = reader.pages[0]
    page.rotate(rotation)
    writer.add_page(page)
    rotated = tmp_path / f"rotated-{rotation}.pdf"
    writer.write(rotated)
    mapping = _extract_map(rotated, [dict(block=1, page=1, text="Rotation preserves the policy text coordinates.")],
        "source-hash", "extraction-hash", "converter-hash")
    page_map = mapping["pages"][0]
    assert page_map["width"] > 0 and page_map["height"] > 0
    match = page_map["mapped"][0]
    assert match["status"] == "mapped"
    for left, bottom, right, top in match["rects"]:
        assert 0 <= left < right <= page_map["width"]
        assert 0 <= bottom < top <= page_map["height"]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_preview_ocr_crop_uses_unrotated_pdf_coordinates(tmp_path, monkeypatch, rotation):
    source = tmp_path / "source.pdf"
    native_pdf(source, "Crop coordinates remain stable after rotation.")
    reader = PdfReader(source)
    writer = PdfWriter()
    page = reader.pages[0]
    if rotation:
        page.rotate(rotation)
    writer.add_page(page)
    rotated = tmp_path / f"crop-{rotation}.pdf"
    writer.write(rotated)
    captured = {}

    def fake_tesseract(image, **kwargs):
        captured["size"] = image.size
        return {"text":["synthetic"],"conf":["88"],"left":[0],"top":[0],"width":[10],"height":[10]}

    monkeypatch.setattr("pytesseract.image_to_data", fake_tesseract)
    result = _ocr(rotated, 1, [50, 680, 250, 720], "mon+eng", "tesseract", "", 60)
    width, height = captured["size"]
    assert abs(width - 200 * 300 / 72) < 5
    assert abs(height - 40 * 300 / 72) < 5
    assert result["text"] == "synthetic"
    left, bottom, right, top = result["words"][0]["rect"]
    assert left >= 50 and bottom < top <= 720 and right > left


def test_preview_unicode_normalization_and_line_rectangle_union():
    value, positions = _normalized("  Өдөр \u04aeүрэг 2026 ")
    assert value == "өдөр үүрэг 2026"
    assert positions
    boxes = _boxes_for_range([
        {"index":0,"rect":[10,100,15,110]}, {"index":1,"rect":[15,100,20,110]},
        {"index":2,"rect":[10,80,15,90]},
    ], 0, 3)
    assert boxes == [[10, 100, 20, 110], [10, 80, 15, 90]]
