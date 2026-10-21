"""Version-scoped, local preview maps and user-requested OCR proposals."""
import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from flask import current_app
from sqlalchemy import func, select

from ..extensions import db
from ..models import Document, DocumentVersion, uid
from .storage import fingerprint, mutation_lock, path, read_json, root, write_json

LAYOUT_REVISION = "preview-map-v1"
MAX_ROI_FRACTION = 0.95


def _bytes_hash(file_path):
    digest = hashlib.sha256()
    with Path(file_path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _converter_fingerprint(config):
    executable = Path(config.get("LIBREOFFICE_CMD", ""))
    if not executable.is_file():
        import shutil as shutil_module
        located = shutil_module.which(str(executable))
        executable = Path(located) if located else executable
    if executable.is_file():
        return _bytes_hash(executable)
    return "unavailable"


def _paths(version, extraction_sha, converter_sha):
    cache_id = fingerprint({"version": version.id, "source": version.sha256,
        "extraction": extraction_sha, "converter": converter_sha, "layout": LAYOUT_REVISION})
    base = f"previews/{version.id}/{cache_id}"
    return cache_id, f"{base}/preview.pdf", f"{base}/map.json"


def _cleanup_stale(version_id, current_cache_id, source_sha, extraction_sha):
    directory = root() / "previews" / str(version_id)
    if not directory.is_dir() or not directory.resolve().is_relative_to(root()):
        return
    for child in directory.iterdir():
        if child.is_dir() and child.name != current_cache_id:
            resolved = child.resolve()
            if resolved.is_relative_to(directory.resolve()):
                shutil.rmtree(resolved, ignore_errors=True)
    proposals = directory / "proposals.json"
    if proposals.is_file():
        try:
            data = json.loads(proposals.read_text(encoding="utf8"))
            if data.get("source_sha") != source_sha or data.get("extraction_sha") != extraction_sha:
                proposals.unlink(missing_ok=True)
        except (OSError, ValueError, AttributeError):
            proposals.unlink(missing_ok=True)


def _run_worker(spec, timeout):
    with tempfile.TemporaryDirectory(prefix="rag-preview-request-") as temporary:
        request_file = Path(temporary) / "request.json"
        result_file = Path(temporary) / "result.json"
        request_file.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf8")
        try:
            result = subprocess.run([sys.executable, "-m", "app.services.preview_worker", str(request_file), str(result_file)],
                cwd=str(Path(__file__).resolve().parents[2]), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=timeout, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as error:
            raise ValueError("Баримтын preview бэлтгэх хугацаа дууслаа.") from error
        if not result_file.is_file():
            raise ValueError("Баримтын preview боловсруулагч ажилласангүй.")
        data = json.loads(result_file.read_text(encoding="utf8"))
        if result.returncode or data.get("error"):
            raise ValueError("Preview боловсруулах боломжгүй. PDF эсвэл LibreOffice тохиргоог шалгана уу.")
        return data


def prepare(version):
    if version.state == "deleted":
        raise ValueError("Устгасан хувилбарын preview боломжгүй.")
    source = path(version.file_path)
    extraction = path(version.extraction_path) if version.extraction_path else None
    if not source.is_file() or not extraction or not extraction.is_file():
        raise ValueError("Preview үүсгэхийн тулд эх файл болон танилтын текст шаардлагатай.")
    if _bytes_hash(source) != version.sha256:
        raise ValueError("Эх баримтын fingerprint өөрчлөгдсөн байна. Энэ хувилбарыг preview хийх боломжгүй.")
    extraction_sha = _bytes_hash(extraction)
    config = current_app.config
    converter_sha = _converter_fingerprint(config)
    cache_id, pdf_relative, map_relative = _paths(version, extraction_sha, converter_sha)
    pdf_file, map_file = path(pdf_relative), path(map_relative)
    with mutation_lock():
        if pdf_file.is_file() and map_file.is_file():
            try:
                cached = read_json(map_relative)
                if (cached.get("source_sha") == version.sha256 and cached.get("extraction_sha") == extraction_sha
                        and cached.get("converter") == converter_sha and cached.get("revision") == 1):
                    _cleanup_stale(version.id, cache_id, version.sha256, extraction_sha)
                    return dict(status="ready", cache_id=cache_id, page_count=cached["page_count"], cached=True)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        artifact = read_json(version.extraction_path)
        pdf_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="rag-preview-build-") as temporary:
            temp_pdf = Path(temporary) / "preview.pdf"
            temp_map = Path(temporary) / "map.json"
            spec = {"action": "prepare", "source": str(source), "pdf": str(temp_pdf), "map": str(temp_map),
                "blocks": artifact.get("blocks", []), "source_sha": version.sha256,
                "extraction_sha": extraction_sha, "media_type": version.media_type,
                "libreoffice": config["LIBREOFFICE_CMD"], "converter_fingerprint": converter_sha,
                "timeout": config["CONVERT_TIMEOUT"], "minimum_native_chars": config["OCR_MIN_CHARACTERS"],
                "max_bytes": config["MAX_CONTENT_LENGTH"]}
            data = _run_worker(spec, config["PROCESS_TIMEOUT"])
            if not temp_pdf.is_file() or not temp_map.is_file():
                raise ValueError("Preview артефакт бүрэн үүссэнгүй.")
            pdf_file.parent.mkdir(parents=True, exist_ok=True)
            temporary_pdf, temporary_map = pdf_file.with_suffix(".pdf.tmp"), map_file.with_suffix(".json.tmp")
            shutil.copyfile(temp_pdf, temporary_pdf)
            shutil.copyfile(temp_map, temporary_map)
            temporary_pdf.replace(pdf_file)
            temporary_map.replace(map_file)
        _cleanup_stale(version.id, cache_id, version.sha256, extraction_sha)
        return dict(status=data.get("status", "ready"), cache_id=cache_id,
            page_count=data.get("page_count", 0), cached=False)


def current_paths(version):
    source = path(version.file_path)
    extraction = path(version.extraction_path) if version.extraction_path else None
    if not source.is_file() or not extraction or not extraction.is_file() or _bytes_hash(source) != version.sha256:
        return None
    _, pdf_relative, map_relative = _paths(version, _bytes_hash(extraction), _converter_fingerprint(current_app.config))
    pdf_file, map_file = path(pdf_relative), path(map_relative)
    if not pdf_file.is_file() or not map_file.is_file():
        return None
    return pdf_relative, map_relative


def page_map(version, page_number):
    paths = current_paths(version)
    if not paths:
        raise FileNotFoundError("Preview map is not prepared")
    artifact = read_json(paths[1])
    if artifact.get("source_sha") != version.sha256 or artifact.get("extraction_sha") != _bytes_hash(path(version.extraction_path)):
        raise FileNotFoundError("Preview map is stale")
    if not isinstance(page_number, int) or page_number < 1 or page_number > artifact.get("page_count", 0):
        raise ValueError("Хуудасны дугаар буруу байна.")
    page = artifact["pages"][page_number - 1]
    proposal_relative = f"previews/{version.id}/proposals.json"
    try:
        proposal_data = read_json(proposal_relative)
        if proposal_data.get("source_sha") == version.sha256:
            page["proposals"] = [item for item in proposal_data.get("items", []) if item.get("page") == page_number]
    except (OSError, ValueError):
        page["proposals"] = []
    return page


def make_ocr_proposal(version, page_number, rect=None, region_id=None):
    paths = current_paths(version)
    if not paths:
        raise ValueError("Эхлээд preview үүсгэнэ үү.")
    mapping = read_json(paths[1])
    if mapping.get("source_sha") != version.sha256:
        raise ValueError("Эх баримт өөрчлөгдсөн байна. Preview-г дахин бэлтгэнэ үү.")
    if not isinstance(page_number, int) or page_number < 1 or page_number > mapping["page_count"]:
        raise ValueError("Хуудасны дугаар буруу байна.")
    page = mapping["pages"][page_number - 1]
    if region_id:
        image = next((item for item in page.get("regions", []) if item.get("id") == region_id), None)
        if not image:
            raise ValueError("OCR шалгах бүс олдсонгүй.")
        rect = image["rect"]
    if not isinstance(rect, list) or len(rect) != 4:
        raise ValueError("OCR бүсийн координат буруу байна.")
    try:
        bounds = [float(value) for value in rect]
    except (TypeError, ValueError) as error:
        raise ValueError("OCR бүсийн координат буруу байна.") from error
    left, bottom, right, top = bounds
    width, height = float(page["width"]), float(page["height"])
    view_box = [float(value) for value in page.get("view_box", [0, 0, width, height])]
    if len(view_box) != 4 or not all(math.isfinite(value) for value in view_box):
        raise ValueError("PDF хуудасны координат хүчингүй байна.")
    x0, y0, x1, y1 = view_box
    if not all(math.isfinite(value) for value in bounds):
        raise ValueError("OCR бүсийн координат хязгаараас гарсан байна.")
    if not (x0 <= left < right <= x1 and y0 <= bottom < top <= y1):
        raise ValueError("OCR бүс хуудасны хэмжээнээс гарсан байна.")
    if (right - left) * (top - bottom) > width * height * MAX_ROI_FRACTION:
        raise ValueError("OCR бүс хуудасны хэт их талбайг хамарч байна.")
    pdf_relative = paths[0]
    config = current_app.config
    with mutation_lock():
        relative = f"previews/{version.id}/proposals.json"
        try:
            data = read_json(relative)
            if data.get("source_sha") != version.sha256 or data.get("extraction_sha") != mapping["extraction_sha"]:
                data = {"source_sha": version.sha256, "extraction_sha": mapping["extraction_sha"], "items": []}
        except (OSError, ValueError):
            data = {"source_sha": version.sha256, "extraction_sha": mapping["extraction_sha"], "items": []}
        existing_region = next((item for item in data["items"] if item.get("page") == page_number
            and item.get("rect") == bounds and item.get("status") == "proposal"), None)
        if existing_region:
            return existing_region
        result = _run_worker({"action": "ocr", "pdf": str(path(pdf_relative)), "page": page_number,
            "rect": bounds, "language": config["OCR_LANGUAGES"], "tesseract": config["TESSERACT_CMD"],
            "tessdata": config.get("TESSDATA_PREFIX", ""), "timeout": config["OCR_TIMEOUT"]},
            config["OCR_TIMEOUT"] + 15)
        if not result.get("text"):
            raise ValueError("Сонгосон бүсээс текст таньсангүй.")
        item = {"id": hashlib.sha256(f"{version.id}:{version.sha256}:{page_number}:{bounds}:{result['text']}".encode()).hexdigest()[:24],
            "page": page_number, "rect": bounds, "text": result["text"], "words": result["words"],
            "mean_confidence": result["mean_confidence"], "source_sha": version.sha256,
            "extraction_sha": mapping["extraction_sha"], "status": "proposal"}
        data["items"] = [existing for existing in data["items"] if existing.get("id") != item["id"]]
        data["items"].append(item)
        write_json(relative, data)
    return item


def apply_proposals(version, proposal_ids):
    if not isinstance(proposal_ids, list) or not proposal_ids or len(proposal_ids) > 100:
        raise ValueError("Шинэ хувилбарт нэгээс 100 хүртэл OCR санал сонгоно уу.")
    if len(set(proposal_ids)) != len(proposal_ids) or any(not isinstance(value, str) or len(value) > 64 for value in proposal_ids):
        raise ValueError("OCR саналын жагсаалт буруу байна.")
    if version.state == "deleted":
        raise ValueError("Устгасан хувилбараас review хувилбар үүсгэх боломжгүй.")
    if not version.extraction_path or not path(version.extraction_path).is_file():
        raise ValueError("Эх хувилбарт таньсан текст алга.")
    prepared_paths = current_paths(version)
    if not prepared_paths:
        raise ValueError("Эхлээд preview үүсгэнэ үү.")
    map_relative = prepared_paths[1]
    mapping = read_json(map_relative)
    relative = f"previews/{version.id}/proposals.json"
    if any(not isinstance(value, str) or len(value) > 64 for value in proposal_ids):
        raise ValueError("OCR саналын жагсаалт буруу байна.")
    if len(set(proposal_ids)) != len(proposal_ids):
        raise ValueError("OCR саналын жагсаалтад давхардсан ID байна.")

    from .ingestion import get_document
    with mutation_lock():
        proposals = read_json(relative)
        if (proposals.get("source_sha") != version.sha256 or proposals.get("extraction_sha") != mapping.get("extraction_sha")
                or mapping.get("source_sha") != version.sha256):
            raise ValueError("OCR санал хуучирсан байна. Шинэ preview үүсгэнэ үү.")
        selected = [item for item in proposals.get("items", []) if item.get("id") in proposal_ids]
        if len(selected) != len(proposal_ids):
            raise ValueError("Сонгосон OCR санал олдсонгүй.")
        in_progress = [item for item in selected if item.get("status") in ("applying", "applied")]
        if in_progress:
            targets = {item.get("target_version") for item in in_progress}
            if len(in_progress) == len(selected) and len(targets) == 1 and None not in targets:
                existing_version = db.session.get(DocumentVersion, next(iter(targets)))
                if existing_version and existing_version.document_id == version.document_id:
                    for item in selected:
                        item["status"] = "applied"
                    write_json(relative, proposals)
                    return existing_version
            for item in in_progress:
                item["status"] = "proposal"
                item.pop("target_version", None)
            write_json(relative, proposals)
        if any(item.get("status") != "proposal" for item in selected):
            raise ValueError("Сонгосон OCR санал аль хэдийн ашиглагдсан байна.")
        document = get_document(version.document_id)
        artifact = read_json(version.extraction_path)
        existing = "".join(str(block.get("text") or "").casefold() for block in artifact.get("blocks", []))
        warnings = list(artifact.get("warnings", []))
        next_block = max((int(block.get("block", 0)) for block in artifact.get("blocks", [])), default=0) + 1
        for item in selected:
            duplicate = "".join(item["text"].casefold().split()) in "".join(existing.split())
            artifact["blocks"].append({"text": item["text"], "heading": "OCR санал · %s-р хуудас" % item["page"],
                "kind": "ocr_region", "method": "ocr_proposal", "page": item["page"], "block": next_block,
                "source_region": {"page": item["page"], "rect": item["rect"], "proposal_id": item["id"]}})
            next_block += 1
            warning = (f"{item['page']}-р хуудасны OCR саналыг эхтэй нь заавал тулгана уу. "
                f"Engine confidence: {item['mean_confidence'] if item['mean_confidence'] is not None else 'тодорхойгүй'}%. ")
            if duplicate:
                warning += "Танилтын тексттэй давхцаж болзошгүй."
            warnings.append(warning)
        artifact["warnings"] = list(dict.fromkeys(warnings))
        artifact["preview_ocr_provenance"] = {"source_sha": version.sha256, "base_version": version.id,
            "proposal_ids": list(proposal_ids), "layout_revision": LAYOUT_REVISION}
        number = db.session.scalar(select(func.max(DocumentVersion.version_number)).where(
            DocumentVersion.document_id == document.id)) or 0
        config = dict(version.config)
        config_hash = fingerprint(config)
        target = DocumentVersion(id=uid(), document_id=document.id, version_number=number + 1,
            original_filename=version.original_filename, media_type=version.media_type, sha256=version.sha256,
            file_path=version.file_path, config=config, config_hash=config_hash,
            collection_name="rag_" + config_hash[:24], state="review", warnings=list(artifact["warnings"]),
            review_required=True, approved_at=None)
        extraction_relative = f"extractions/preview-ocr-{target.id}.json"
        for item in selected:
            item["status"] = "applying"
            item["target_version"] = target.id
        write_json(relative, proposals)
        try:
            write_json(extraction_relative, artifact)
            target.extraction_path = extraction_relative
            db.session.add(target)
            db.session.commit()
        except Exception:
            db.session.rollback()
            for item in selected:
                item["status"] = "proposal"
                item.pop("target_version", None)
            write_json(relative, proposals)
            try:
                path(extraction_relative).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
            raise
        for item in selected:
            item["status"] = "applied"
        write_json(relative, proposals)
        return target


def delete_version_artifacts(version_id):
    target = (root() / "previews" / str(version_id)).resolve()
    if target.is_relative_to(root()) and target.exists() and target.is_dir():
        shutil.rmtree(target, ignore_errors=True)

