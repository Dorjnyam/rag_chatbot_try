"""Isolated PDFium/LibreOffice/OCR worker for document preview artifacts."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

from .parsing import normalize

MAX_RENDER_PIXELS = 25_000_000
MAX_LINES_PER_PAGE = 5000
MAX_TEXT_CHARACTERS = 2_000_000


def _profile():
    directory = tempfile.TemporaryDirectory(prefix="rag-preview-lo-")
    user = Path(directory.name) / "user"
    user.mkdir()
    (user / "registrymodifications.xcu").write_text(
        '<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
        '</item></oor:items>', encoding="utf8")
    return directory, f"-env:UserInstallation={user.as_uri()}"


def _convert(source, output_file, executable, timeout):
    output_file = Path(output_file)
    output = output_file.parent
    output.mkdir(parents=True, exist_ok=True)
    profile, user = _profile()
    try:
        result = subprocess.run([
            executable, user, "--headless", "--norestore", "--nodefault", "--convert-to", "pdf",
            "--outdir", str(output), str(source),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        target = output / f"{source.stem}.pdf"
        if result.returncode or not target.is_file():
            raise ValueError("Preview conversion failed")
        # LibreOffice names the PDF after the DOCX/DOC input. The preview
        # service expects the explicit temporary path supplied in its request.
        # Normalize the name before returning so both PDF and Word flows share
        # the same artifact contract.
        if target.resolve() != output_file.resolve():
            target.replace(output_file)
        return output_file
    finally:
        profile.cleanup()


def _normalized(text):
    normalized, indexes = [], []
    for index, char in enumerate(unicodedata.normalize("NFC", text).casefold()):
        if char.isalnum():
            normalized.append(char)
            indexes.append(index)
        elif normalized and normalized[-1] != " ":
            normalized.append(" ")
            indexes.append(index)
    while normalized and normalized[0] == " ":
        normalized.pop(0)
        indexes.pop(0)
    while normalized and normalized[-1] == " ":
        normalized.pop()
        indexes.pop()
    return "".join(normalized), indexes


def _boxes_for_range(chars, start, end):
    selected = []
    for item in chars:
        if start <= item["index"] < end:
            selected.append(item["rect"])
    if not selected:
        return []
    selected.sort(key=lambda rect: (-((rect[1] + rect[3]) / 2), rect[0]))
    lines = []
    for rect in selected:
        center = (rect[1] + rect[3]) / 2
        if not lines or abs(lines[-1]["center"] - center) > 4:
            lines.append({"center": center, "rect": list(rect)})
        else:
            current = lines[-1]["rect"]
            current[0] = min(current[0], rect[0])
            current[1] = min(current[1], rect[1])
            current[2] = max(current[2], rect[2])
            current[3] = max(current[3], rect[3])
    return [line["rect"] for line in lines]


def _clip_rect(rect, view_box):
    left, bottom, right, top = rect
    x0, y0, x1, y1 = view_box
    clipped = [max(left, x0), max(bottom, y0), min(right, x1), min(top, y1)]
    return clipped if clipped[2] > clipped[0] and clipped[3] > clipped[1] else None


def _extract_map(pdf_path, blocks, source_sha, extraction_sha, converter, minimum_native_chars=40):
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_path)
    try:
        pages = []
        page_texts = []
        page_chars = []
        total_characters = 0
        for page_number in range(1, len(document) + 1):
            page = document[page_number - 1]
            try:
                x0, y0, x1, y1 = page.get_bbox()
                width, height = x1 - x0, y1 - y0
                text_page = page.get_textpage()
                try:
                    text = text_page.get_text_range()
                    total_characters += len(text)
                    if total_characters > MAX_TEXT_CHARACTERS:
                        raise ValueError("Preview text mapping exceeds its size limit")
                    chars = []
                    for index, char in enumerate(text):
                        if char.isspace():
                            continue
                        try:
                            rect = _clip_rect(text_page.get_charbox(index), (x0, y0, x1, y1))
                            if rect:
                                chars.append({"index": index, "char": char,
                                    "rect": [float(value) for value in rect]})
                        except (TypeError, ValueError, RuntimeError):
                            continue
                    # Keep accessible text segments with their per-line rectangles.
                    sorted_chars = sorted(chars, key=lambda item: (-((item["rect"][1] + item["rect"][3]) / 2), item["rect"][0]))
                    lines = []
                    for item in sorted_chars:
                        center = (item["rect"][1] + item["rect"][3]) / 2
                        if not lines or abs(lines[-1]["center"] - center) > 4:
                            lines.append({"center": center, "text": item["char"], "rect": list(item["rect"]),
                                "start": item["index"], "end": item["index"] + 1})
                        else:
                            line = lines[-1]
                            line["text"] += item["char"]
                            line["start"] = min(line["start"], item["index"])
                            line["end"] = max(line["end"], item["index"] + 1)
                            line["rect"][0] = min(line["rect"][0], item["rect"][0])
                            line["rect"][1] = min(line["rect"][1], item["rect"][1])
                            line["rect"][2] = max(line["rect"][2], item["rect"][2])
                            line["rect"][3] = max(line["rect"][3], item["rect"][3])
                    lines = lines[:MAX_LINES_PER_PAGE]
                    image_regions = []
                    try:
                        for object_number, obj in enumerate(page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE])):
                            try:
                                bounds = _clip_rect(obj.get_bounds(), (x0, y0, x1, y1))
                                if bounds:
                                    image_regions.append({"id": f"p{page_number}-img{object_number}",
                                        "rect": [float(value) for value in bounds], "kind": "image_candidate"})
                            except (AttributeError, RuntimeError, ValueError):
                                continue
                    except (AttributeError, RuntimeError, TypeError):
                        pass
                    if sum(char.isalnum() for char in text) < minimum_native_chars and not image_regions:
                        image_regions.append({"id": f"p{page_number}-sparse-text", "rect":[float(x0),float(y0),float(x1),float(y1)],
                            "kind":"page_candidate"})
                    pages.append({"page": page_number, "width": width, "height": height,
                        "view_box": [float(x0), float(y0), float(x1), float(y1)],
                        "lines": [{"text": line["text"], "rect": line["rect"]} for line in lines],
                        "regions": image_regions, "mapped": []})
                    page_texts.append(text)
                    page_chars.append(chars)
                finally:
                    text_page.close()
            finally:
                page.close()
        if not pages or len(pages) > 100:
            raise ValueError("Preview page count is outside the supported range")

        # PDF extraction already has one canonical page block. Word blocks have no
        # page numbers, so only a single unique, ordered text match gets a rectangle.
        last_word_page = 0
        for block_index, block in enumerate(blocks):
            text = str(block.get("text") or "")
            needle, _ = _normalized(text)
            if not needle:
                continue
            candidate_pages = []
            if block.get("page"):
                page_num = int(block["page"])
                candidate_pages = [page_num] if 1 <= page_num <= len(pages) else []
            else:
                candidate_pages = list(range(max(1, last_word_page), len(pages) + 1))
            matches = []
            for page_num in candidate_pages:
                content = page_texts[page_num - 1]
                haystack, indices = _normalized(content)
                offset = 0
                while len(matches) < 3:
                    found = haystack.find(needle, offset)
                    if found < 0:
                        break
                    if indices:
                        matches.append((page_num, indices[found], indices[found + len(needle) - 1] + 1))
                    offset = found + len(needle)
            status = "unmapped"
            rects = []
            page_num = int(block.get("page") or 0)
            if len(matches) == 1:
                page_num, start, end = matches[0]
                rects = _boxes_for_range(page_chars[page_num - 1], start, end)
                status = "mapped" if rects else "page_only"
                if not block.get("page"):
                    last_word_page = page_num
            elif len(candidate_pages) == 1:
                page_num = candidate_pages[0]
                status = "page_only"
            mapping = {"block": int(block.get("block", block_index + 1)), "page": page_num or None,
                "status": status, "rects": rects}
            if page_num and 1 <= page_num <= len(pages):
                pages[page_num - 1]["mapped"].append(mapping)
        return {"revision": 1, "source_sha": source_sha, "extraction_sha": extraction_sha,
            "converter": converter, "page_count": len(pages), "pages": pages}
    finally:
        document.close()


def _ocr(pdf_path, page_number, rect, language, tess_cmd, tessdata, timeout):
    import pypdfium2 as pdfium
    import pytesseract
    from PIL import Image
    from pytesseract import Output

    pdfium_document = pdfium.PdfDocument(pdf_path)
    try:
        if page_number < 1 or page_number > len(pdfium_document):
            raise ValueError("Invalid page")
        page = pdfium_document[page_number - 1]
        try:
            x0, y0, x1, y1 = page.get_bbox()
            width, height = x1 - x0, y1 - y0
            left, bottom, right, top = rect
            if not (x0 <= left < right <= x1 and y0 <= bottom < top <= y1):
                raise ValueError("The selected region is outside the page")
            if right - left < 8 or top - bottom < 8 or (right - left) * (top - bottom) * (300 / 72) ** 2 > MAX_RENDER_PIXELS:
                raise ValueError("Selected OCR region is too small or exceeds the render limit")
            rotation = (360 - page.get_rotation()) % 360
            crop = (left - x0, bottom - y0, x1 - right, y1 - top)
            bitmap = page.render(scale=300 / 72, rotation=rotation, crop=crop)
            image = bitmap.to_pil()
            try:
                pytesseract.pytesseract.tesseract_cmd = tess_cmd
                old_prefix = os.environ.get("TESSDATA_PREFIX")
                try:
                    if tessdata:
                        os.environ["TESSDATA_PREFIX"] = str(Path(tessdata).resolve())
                    data = pytesseract.image_to_data(image, lang=language, output_type=Output.DICT, timeout=timeout)
                finally:
                    if old_prefix is None:
                        os.environ.pop("TESSDATA_PREFIX", None)
                    else:
                        os.environ["TESSDATA_PREFIX"] = old_prefix
                scale = 300 / 72
                words = []
                text_parts = []
                for i, word in enumerate(data["text"]):
                    word = str(word).strip()
                    try:
                        confidence = float(data["conf"][i])
                        x, y, w, h = (int(data[key][i]) for key in ("left", "top", "width", "height"))
                    except (TypeError, ValueError, KeyError):
                        continue
                    if not word or confidence < 0 or w <= 0 or h <= 0:
                        continue
                    words.append({"text": word, "confidence": confidence,
                        "rect": [left + x / scale, top - (y + h) / scale, left + (x + w) / scale, top - y / scale]})
                    text_parts.append(word)
                return {"text": normalize(" ".join(text_parts)), "words": words,
                    "mean_confidence": round(sum(item["confidence"] for item in words) / len(words), 2) if words else None}
            finally:
                image.close()
                bitmap.close()
        finally:
            page.close()
    finally:
        pdfium_document.close()


def main(request_path, output_path):
    request = json.loads(Path(request_path).read_text(encoding="utf8"))
    try:
        if request["action"] == "prepare":
            source = Path(request["source"])
            pdf_path = Path(request["pdf"])
            if request["media_type"] == "pdf":
                shutil.copyfile(source, pdf_path)
                converter = request.get("converter_fingerprint", "source-pdf")
            else:
                converter = request.get("converter_fingerprint", "unavailable")
                pdf_path = _convert(source, pdf_path, request["libreoffice"], request["timeout"])
            if pdf_path.stat().st_size > request["max_bytes"]:
                raise ValueError("Preview PDF exceeds the source size limit")
            mapping = _extract_map(pdf_path, request["blocks"], request["source_sha"], request["extraction_sha"],
                converter, request.get("minimum_native_chars", 40))
            mapping["pdf_sha"] = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
            Path(request["map"]).write_text(json.dumps(mapping, ensure_ascii=False, separators=(",", ":")), encoding="utf8")
            result = {"status": "ready", "page_count": mapping["page_count"], "mapped": sum(len(page["mapped"]) for page in mapping["pages"])}
        elif request["action"] == "ocr":
            result = _ocr(request["pdf"], request["page"], request["rect"], request["language"],
                request["tesseract"], request.get("tessdata", ""), request["timeout"])
        else:
            raise ValueError("Unknown preview action")
        result["status"] = result.get("status", "ok")
    except Exception as error:
        result = {"error": str(error)[:300]}
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False), encoding="utf8")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
