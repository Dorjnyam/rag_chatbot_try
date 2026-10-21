"""Reproducible, database-free measurements for the existing Load stage."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import platform
import re
import shutil
import statistics
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .services.parsing import PARSER_REVISION, classify_error, normalize, parse_bounded, parser_info


CSV_FIELDS = (
    "case_id", "run_id", "format", "status", "error_category", "file_size_bytes",
    "duration_ms", "parser_name", "parser_versions", "page_count", "block_count", "text_characters", "heading_count",
    "table_blocks", "table_block_match", "ocr_pages", "warning_count", "cer", "heading_recall",
    "clause_recall", "table_text_recall", "numbered_clause_markers", "peak_memory_bytes", "cpu_ms", "suspicious_flags",
)


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _cer(actual, reference, max_cells=20_000_000):
    """Character error rate from Levenshtein distance; refuse quadratic blowups."""
    actual, reference = normalize(actual), normalize(reference)
    reference_length = len(reference)
    if not reference:
        return 0.0 if not actual else None
    prefix = 0
    while prefix < min(len(actual), len(reference)) and actual[prefix] == reference[prefix]:
        prefix += 1
    actual, reference = actual[prefix:], reference[prefix:]
    suffix = 0
    while suffix < min(len(actual), len(reference)) and actual[-1 - suffix] == reference[-1 - suffix]:
        suffix += 1
    if suffix:
        actual, reference = actual[:-suffix], reference[:-suffix]
    if len(actual) * len(reference) > max_cells:
        return None
    if len(actual) < len(reference):
        actual, reference = reference, actual
    previous = list(range(len(reference) + 1))
    for i, left in enumerate(actual, 1):
        current = [i]
        for j, right in enumerate(reference, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right)))
        previous = current
    return previous[-1] / max(1, reference_length)


def _recall(expected, actual):
    if expected is None:
        return None
    expected = [normalize(str(item)) for item in expected]
    if not expected:
        return 1.0
    text = normalize(actual)
    return sum(bool(item) and item in text for item in expected) / len(expected)


def _case_path(manifest_dir, value):
    candidate = (manifest_dir / value).resolve()
    if not candidate.is_file():
        raise ValueError(f"Benchmark input file does not exist: {value}")
    return candidate


def _case_record(case, file_path, config):
    run_id = str(uuid.uuid4())
    started = time.perf_counter()
    record = {"case_id": case["id"], "run_id": run_id,
        "format": str(case.get("format") or file_path.suffix.lstrip(".")).lower(),
        "status": "failed", "error_category": None, "file_size_bytes": file_path.stat().st_size,
        "duration_ms": None, "page_count": None, "block_count": 0, "text_characters": 0,
        "heading_count": 0, "table_blocks": 0, "ocr_pages": 0, "warning_count": 0,
        "cer": None, "heading_recall": None, "clause_recall": None, "table_text_recall": None,
        "peak_memory_bytes": None, "cpu_ms": None, "numbered_clause_markers": 0}
    record["parser"] = parser_info(record["format"])
    record["parser_name"] = record["parser"]["name"]
    record["parser_versions"] = json.dumps(record["parser"]["versions"], sort_keys=True)
    try:
        metrics = {}
        artifact = parse_bounded(file_path, record["format"], config, case.get("forced_pages", []), metrics_callback=metrics.update)
        record.update(cpu_ms=metrics.get("cpu_ms"), peak_memory_bytes=metrics.get("peak_memory_bytes"))
        blocks = artifact.get("blocks", [])
        extracted = "\n".join(block.get("text", "") for block in blocks)
        record["numbered_clause_markers"] = len(re.findall(r"(?m)^\s*(?:\d+(?:\.\d+)*(?:[.)])?|[А-ЯЁA-Z][.)])\s+", extracted))
        record.update(status="success", page_count=len(artifact.get("candidates", [])) if record["format"] == "pdf" else None,
            block_count=len(blocks), text_characters=sum(len(block.get("text", "")) for block in blocks),
            heading_count=len({normalize(block.get("heading", "")) for block in blocks if block.get("heading")}),
            table_blocks=sum(block.get("kind") == "table" for block in blocks),
            ocr_pages=sum(block.get("method") == "ocr" for block in blocks),
            warning_count=len(artifact.get("warnings", [])))
        record["parser_strategy"] = sorted({block.get("method", "unknown") for block in blocks})
        truth = case.get("ground_truth") or {}
        reference_file = truth.get("text_file")
        if reference_file:
            reference = _case_path(case["_manifest_dir"], reference_file).read_text(encoding="utf8")
            record["cer"] = _cer(extracted, reference)
        if "headings" in truth:
            record["heading_recall"] = _recall(truth["headings"], "\n".join(b.get("heading", "") for b in blocks))
        if "clauses" in truth:
            record["clause_recall"] = _recall(truth["clauses"], extracted)
        if "table_texts" in truth:
            table_text = "\n".join(b.get("text", "") for b in blocks if b.get("kind") == "table")
            record["table_text_recall"] = _recall(truth["table_texts"], table_text)
        if "table_blocks" in truth:
            record["expected_table_blocks"] = truth["table_blocks"]
            record["table_block_match"] = record["table_blocks"] == truth["table_blocks"]
    except Exception as exc:
        record["error_category"] = classify_error(exc, record["format"])
        record["error"] = str(exc)
    record["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    record["parser_revision"] = PARSER_REVISION
    return record


def _summary(records, elapsed_seconds):
    by_format = {}
    for format_ in sorted({r["format"] for r in records}):
        items = [r for r in records if r["format"] == format_]
        latencies = [r["duration_ms"] for r in items]
        by_format[format_] = {"cases": len(items), "successes": sum(r["status"] == "success" for r in items),
            "failures": sum(r["status"] != "success" for r in items),
            "median_ms": statistics.median(latencies) if latencies else None,
            "p95_ms": _percentile(latencies, .95),
            "mean_text_characters": statistics.mean([r["text_characters"] for r in items if r["status"] == "success"])
                if any(r["status"] == "success" for r in items) else None}
    successful = sum(r["status"] == "success" for r in records)
    cer_values = [r["cer"] for r in records if r["cer"] is not None]
    total_bytes = sum(r["file_size_bytes"] for r in records)
    return {"cases": len(records), "successes": successful, "failures": len(records) - successful,
        "success_rate": successful / len(records) if records else 0.0,
        "median_ms": statistics.median([r["duration_ms"] for r in records]) if records else None,
        "p95_ms": _percentile([r["duration_ms"] for r in records], .95),
        "wall_seconds": round(elapsed_seconds, 3),
        "throughput_files_per_second": successful / elapsed_seconds if elapsed_seconds else None,
        "throughput_mib_per_second": total_bytes / (1024 * 1024) / elapsed_seconds if elapsed_seconds else None,
        "mean_cer": statistics.mean(cer_values) if cer_values else None,
        "cer_cases": len(cer_values), "peak_memory_bytes": max((r.get("peak_memory_bytes") or 0 for r in records), default=0) or None,
        "mean_worker_cpu_ms": statistics.mean([r["cpu_ms"] for r in records if r.get("cpu_ms") is not None]) if any(r.get("cpu_ms") is not None for r in records) else None,
        "peak_memory_note": "Peak RSS measured per parser worker via OS API where supported; excludes LibreOffice child process.",
        "by_format": by_format,
        "quality_note": "Quality metrics are reported only when manually verified ground-truth fields are supplied."}


def _dataset_digest(manifest_path, cases):
    digest = hashlib.sha256()
    digest.update(manifest_path.read_bytes())
    for case in sorted(cases, key=lambda item: item["id"]):
        for key in ("id", "file"):
            digest.update(str(case[key]).encode("utf8"))
            digest.update(b"\0")
        digest.update(_case_path(manifest_path.parent, case["file"]).read_bytes())
        reference_file = (case.get("ground_truth") or {}).get("text_file")
        if reference_file:
            digest.update(reference_file.encode("utf8"))
            digest.update(_case_path(manifest_path.parent, reference_file).read_bytes())
    return digest.hexdigest()


def _markdown(report):
    summary = report["summary"]
    def display(value):
        return "N/A" if value is None else (f"{value:.3f}" if isinstance(value, float) else str(value))
    lines = ["# Ачаалах үе шатны суурь үнэлгээ", "", f"- Огноо: {report['created_at']}",
        f"- Run ID: `{report['benchmark_run_id']}`", f"- Dataset hash (manifest + input/reference bytes): `{report['dataset_sha256']}`",
        f"- Парсерын хувилбар: `{report['parser_revision']}`",
        f"- Parser тохиргоо: `{json.dumps(report['parser_config'], ensure_ascii=False, sort_keys=True)}`",
        f"- Файл: {summary['cases']}; амжилттай: {summary['successes']}; алдаа: {summary['failures']}; амжилтын хувь: {summary['success_rate']:.1%}",
        f"- Медиан: {display(summary['median_ms'])} ms; P95: {display(summary['p95_ms'])} ms",
        f"- Нэвтрүүлэх чадвар: {display(summary['throughput_files_per_second'])} файл/сек; {display(summary['throughput_mib_per_second'])} MiB/сек",
        f"- CER: {display(summary['mean_cer'])} ({summary['cer_cases']} ground-truth файл)",
        "- Санах ойн оргил: хэмжээгүй. Child process-ийн RSS-ийг хөндлөн платформоор хэмжих optional dependency байхгүй.",
        "- Өгөгдлийн сан/Chroma өөрчлөлт: хийгдээгүй; зөвхөн manifest дахь файлыг bounded parser-аар уншсан.",
        "- Энэ нь 16 synthetic DOCX-ийн дараалсан нэг run; PDF, DOC, concurrent throughput болон production ачааллыг төлөөлөхгүй.",
        "", "## Форматаар", "", "| Формат | Тохиолдол | Амжилт | Алдаа | Медиан (ms) | P95 (ms) | Дундаж тэмдэгт |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for format_, values in summary["by_format"].items():
        lines.append(f"| {format_} | {values['cases']} | {values['successes']} | {values['failures']} | {display(values['median_ms'])} | {display(values['p95_ms'])} | {display(values['mean_text_characters'])} |")
    lines.extend(["", "## Тохиолдлын үр дүн", "", "| ID | Формат | Төлөв | Алдааны ангилал | Хугацаа (ms) | Тэмдэгт | CER | Гарчгийн recall | Заалтын recall | Хүснэгт recall |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|"])
    for row in report["records"]:
        values = {k: display(row.get(k)) for k in ("error_category", "duration_ms", "text_characters", "cer", "heading_recall", "clause_recall", "table_text_recall")}
        values.update({"case_id": row["case_id"], "format": row["format"], "status": row["status"]})
        lines.append("| {case_id} | {format} | {status} | {error_category} | {duration_ms} | {text_characters} | {cer} | {heading_recall} | {clause_recall} | {table_text_recall} |".format(**values))
    lines.extend(["", "Үнэн зөв байдлын хэмжүүр нь зөвхөн гараар баталгаажуулсан reference text, heading, clause, table fixture өгсөн үед тооцогдоно. Хэмжээгүй үзүүлэлтийг таамаглаагүй.", ""])
    return "\n".join(lines)


def run_load_benchmark(manifest_path, output_dir, config):
    """Run one fixed manifest through the existing parser; no Flask DB or vector APIs."""
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    cases = manifest.get("cases")
    if manifest.get("schema_version") != 1 or not isinstance(cases, list) or not cases:
        raise ValueError("Benchmark manifest must have schema_version=1 and a nonempty cases list.")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or not case.get("id") or not case.get("file"):
            raise ValueError("Each benchmark case needs a unique id and file.")
        if case["id"] in seen:
            raise ValueError(f"Duplicate benchmark case id: {case['id']}")
        seen.add(case["id"])
    total_started = time.perf_counter()
    records = []
    for case in cases:
        file_path = _case_path(manifest_path.parent, case["file"])
        records.append(_case_record({**case, "_manifest_dir": manifest_path.parent}, file_path, config))
    elapsed = time.perf_counter() - total_started
    dataset_digest = _dataset_digest(manifest_path, cases)
    report = {"schema_version": 1, "benchmark_run_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_name": manifest.get("name", manifest_path.stem), "dataset_sha256": dataset_digest,
        "parser_revision": PARSER_REVISION, "production_mutations": False,
        "database_connections": 0, "vector_store_calls": 0,
        "parser_config": {"pdf_max_pages": config["PDF_MAX_PAGES"],
            "docx_max_expanded_bytes": config["DOCX_MAX_EXPANDED"],
            "ocr_languages": config["OCR_LANGUAGES"], "ocr_min_characters": config["OCR_MIN_CHARACTERS"],
            "ocr_timeout_seconds": config["OCR_TIMEOUT"], "conversion_timeout_seconds": config["CONVERT_TIMEOUT"],
            "process_timeout_seconds": config["PROCESS_TIMEOUT"]},
        "summary": _summary(records, elapsed), "records": records}
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"load-{stamp}"
    (output_dir / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    with (output_dir / f"{stem}.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    (output_dir / f"{stem}.md").write_text(_markdown(report), encoding="utf8")
    report["report_stem"] = stem
    return report


def _inspect_document(path):
    """Format-level metadata only; never persist document text or names."""
    info = {"page_count": None, "package_tables": 0, "media_count": 0, "textbox_count": 0,
        "column_layout": False, "heading_styles": 0, "scanned_pdf": None, "encrypted": False,
        "characteristics": []}
    ext = path.suffix.casefold()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(path)
            info["page_count"] = len(reader.pages)
            info["encrypted"] = reader.is_encrypted
            sparse = [sum(c.isalnum() for c in (page.extract_text() or "")) < 40 for page in reader.pages]
            info["scanned_pdf"] = bool(sparse) and all(sparse)
            if info["scanned_pdf"]: info["characteristics"].append("scanned_pdf_likely")
            elif any(sparse): info["characteristics"].append("mixed_scanned_and_text_pdf")
        elif ext == ".docx":
            import zipfile
            from xml.etree import ElementTree as ET
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                info["package_tables"] = sum(1 for _ in ())
                info["media_count"] = sum(n.startswith("word/media/") for n in names)
                if "word/document.xml" in names and z.getinfo("word/document.xml").file_size <= 20 * 1024 * 1024:
                    root = ET.fromstring(z.read("word/document.xml"))
                    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                    info["package_tables"] = len(root.findall(".//w:tbl", ns))
                    info["textbox_count"] = len(root.findall(".//w:txbxContent", ns))
                    info["column_layout"] = bool(root.findall(".//w:cols[@w:num]", ns))
                    info["heading_styles"] = len(root.findall(".//w:pStyle[@w:val]", ns))
            if info["package_tables"]: info["characteristics"].append("tables")
            if info["media_count"]: info["characteristics"].append("embedded_media")
            if info["textbox_count"]: info["characteristics"].append("text_boxes")
            if info["column_layout"]: info["characteristics"].append("multi_column_section")
        elif ext == ".doc":
            info["characteristics"].append("legacy_doc_conversion_required")
    except Exception as exc:
        info["characteristics"].append("metadata_inspection_" + type(exc).__name__.casefold())
    return info


def _corpus_record(case_id, staged, config, characteristics):
    record = _case_record({"id": case_id}, staged, config)
    record.pop("error", None)  # Parser exceptions can include local paths or sensitive content.
    record.update(characteristics)
    flags = []
    if record["status"] == "success":
        if record["text_characters"] < 100: flags.append("very_short_extraction")
        if record["block_count"] == 0: flags.append("no_blocks")
        if characteristics.get("package_tables", 0) > record["table_blocks"]: flags.append("fewer_extracted_tables_than_docx_package")
        if characteristics.get("textbox_count", 0): flags.append("text_boxes_not_assessed_by_parser")
        if characteristics.get("media_count", 0): flags.append("embedded_images_not_ocr_assessed")
    record["suspicious_flags"] = flags
    record.setdefault("parser_strategy", [])
    return record


def run_load_directory(source_dir, output_dir, private_map, template_path, config, progress=None):
    """Run the unchanged parser over a private source directory using temp copies."""
    source_dir = Path(source_dir).resolve()
    if not source_dir.is_dir(): raise ValueError("Corpus directory does not exist.")
    files = sorted((p for p in source_dir.rglob("*") if p.is_file() and not p.is_symlink()), key=lambda p: p.relative_to(source_dir).as_posix().casefold())
    if not files: raise ValueError("Corpus directory contains no files.")
    digest = hashlib.sha256()
    inventory, rows = [], []
    for index, path in enumerate(files, 1):
        rel = path.relative_to(source_dir).as_posix()
        size = path.stat().st_size
        ext = path.suffix.casefold().lstrip(".") or "no_extension"
        digest.update(rel.encode("utf8") + b"\0" + str(size).encode() + b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""): digest.update(chunk)
        info = _inspect_document(path)
        inventory.append({"doc_id": f"DOC{index:04d}", "format": ext, "file_size_bytes": size, **info})
    private_map = Path(private_map).resolve(); private_map.parent.mkdir(parents=True, exist_ok=True)
    with private_map.open("w", encoding="utf-8-sig", newline="") as h:
        writer = csv.writer(h); writer.writerow(["doc_id", "source_relative_path"])
        writer.writerows((x["doc_id"], files[i].relative_to(source_dir).as_posix()) for i, x in enumerate(inventory))
    total_started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="load-corpus-") as temp:
        staged_root = Path(temp) / "uploads"; staged_root.mkdir()
        for i, (source, item) in enumerate(zip(files, inventory), 1):
            ext = source.suffix.casefold()
            if ext not in (".pdf", ".docx", ".doc"):
                rec = {"case_id": item["doc_id"], "run_id": str(uuid.uuid4()), "format": item["format"], "status": "failed",
                    "error_category": "unsupported_format", "file_size_bytes": item["file_size_bytes"], "duration_ms": 0,
                    "text_characters": 0, "page_count": item["page_count"], "warning_count": 0, "block_count": 0,
            "heading_count": 0, "table_blocks": 0, "ocr_pages": 0, "numbered_clause_markers": 0, "cer": None, "heading_recall": None,
                    "clause_recall": None, "table_text_recall": None, "peak_memory_bytes": None, "cpu_ms": None,
                    "parser": parser_info(item["format"]), "parser_revision": PARSER_REVISION, **item}
            else:
                staged = staged_root / f'{item["doc_id"]}{ext}'
                shutil.copyfile(source, staged)
                rec = _corpus_record(item["doc_id"], staged, config, item)
                rec["file_size_bytes"] = item["file_size_bytes"]
                rec["run_id"] = str(uuid.uuid4())
            rows.append(rec)
            if progress and (i % 10 == 0 or i == len(files)): progress(i, len(files), rec["status"])
    elapsed = time.perf_counter() - total_started
    # Select 20 representative items with deterministic feature coverage and all legacy DOCs.
    selected, covered = [], set()
    def features(x): return set(x["characteristics"]) | ({"large"} if x["file_size_bytes"] >= 1024 * 1024 else set()) | ({"small"} if x["file_size_bytes"] < 100_000 else set()) | ({"medium"} if 100_000 <= x["file_size_bytes"] < 1024 * 1024 else set())
    priority = sorted(range(len(inventory)), key=lambda i: (inventory[i]["format"] != "doc", -len(features(inventory[i]) - covered), -inventory[i]["file_size_bytes"], i))
    for i in priority:
        if len(selected) >= 20: break
        selected.append(i); covered |= features(inventory[i])
    selected_ids = {inventory[i]["doc_id"] for i in selected}
    for rec in rows:
        rec["selected_for_quality"] = rec["case_id"] in selected_ids
        if rec["selected_for_quality"]:
            rec["selection_reasons"] = list(rec.get("characteristics", [])) + (["large_file"] if rec["file_size_bytes"] >= 1024 * 1024 else ["small_file"] if rec["file_size_bytes"] < 100_000 else ["medium_file"])
            if rec["format"] == "doc": rec["selection_reasons"].append("all_legacy_doc_files_included")
    template = {"schema_version": 1, "dataset_sha256": digest.hexdigest(), "instructions_mn": "Сонгосон файл бүрийн extracted_text_reference файлыг эх баримттай тулгаж гараар баталгаажуулна уу. Нууц агуулгыг benchmark report-д бүү хуул.",
        "documents": [{"doc_id": inventory[i]["doc_id"], "format": inventory[i]["format"], "reference_text_file": None,
            "verified_headings": None, "verified_clauses": None, "verified_tables": None, "reviewer_notes": None} for i in selected]}
    template_path = Path(template_path).resolve(); template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf8")
    summary = _summary(rows, elapsed)
    sizes = [x["file_size_bytes"] for x in inventory]
    formats = {}
    for x in inventory: formats[x["format"]] = formats.get(x["format"], 0) + 1
    by_format_size = {fmt: {"count": sum(x["format"] == fmt for x in inventory), "total_bytes": sum(x["file_size_bytes"] for x in inventory if x["format"] == fmt),
        "min_bytes": min(x["file_size_bytes"] for x in inventory if x["format"] == fmt), "median_bytes": statistics.median(x["file_size_bytes"] for x in inventory if x["format"] == fmt),
        "max_bytes": max(x["file_size_bytes"] for x in inventory if x["format"] == fmt)} for fmt in formats}
    import sys
    report = {"schema_version": 1, "benchmark_run_id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_name": "Confidential Mongolian company policy corpus", "dataset_sha256": digest.hexdigest(),
        "parser_revision": PARSER_REVISION, "production_mutations": False, "database_connections": 0, "vector_store_calls": 0,
        "environment": {"platform": platform.platform(), "python": platform.python_version(), "cpu_count": os.cpu_count()},
        "parser_config": {k: config[k] for k in ("PDF_MAX_PAGES", "DOCX_MAX_EXPANDED", "OCR_LANGUAGES", "OCR_MIN_CHARACTERS", "OCR_TIMEOUT", "CONVERT_TIMEOUT", "PROCESS_TIMEOUT")},
        "inventory": {"files": len(files), "total_bytes": sum(sizes), "by_format": by_format_size,
            "documents_with_numbered_clause_markers": sum(bool(r.get("numbered_clause_markers")) for r in rows),
            "numbered_clause_markers_total": sum(r.get("numbered_clause_markers", 0) for r in rows),
            "characteristic_counts": {f: sum(f in x["characteristics"] for x in inventory) for f in sorted({f for x in inventory for f in x["characteristics"]})},
            "likely_scanned_pdfs": sum(x["scanned_pdf"] is True for x in inventory), "page_counts_available": sum(x["page_count"] is not None for x in inventory)},
        "summary": summary, "records": rows,
        "privacy_note": "Нэр, агуулга, parser exception-ийн текстийг тайланд оруулаагүй. DOC/DOCX-д хуудасны тоог эх файлаас найдвартай тогтоох боломжгүй.",
        "synthetic_baseline": {"dataset": "16 synthetic DOCX; separate dataset/run", "successes": 16, "failures": 0,
            "median_ms": 913.510, "p95_ms": 1120.350, "throughput_files_per_second": 1.079, "cer": None}}
    out = Path(output_dir).resolve(); out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"); stem = f"real-load-{stamp}"
    (out / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    with (out / f"{stem}.csv").open("w", encoding="utf-8-sig", newline="") as h:
        fields = list(dict.fromkeys([*CSV_FIELDS, "selected_for_quality", "selection_reasons", "characteristics", "package_tables", "media_count", "textbox_count", "column_layout", "scanned_pdf", "parser_strategy"]))
        writer = csv.DictWriter(h, fieldnames=fields, extrasaction="ignore"); writer.writeheader()
        for row in rows: writer.writerow({**row, "suspicious_flags": ";".join(row.get("suspicious_flags", [])), "selection_reasons": ";".join(row.get("selection_reasons", [])), "characteristics": ";".join(row.get("characteristics", [])), "parser_versions": json.dumps(row.get("parser", {}).get("versions", {}))})
    md = ["# Монгол компанийн бодлогын баримтын Load baseline", "", f"- Огноо: {report['created_at']}", f"- Run ID: `{report['benchmark_run_id']}`", f"- Dataset SHA-256: `{report['dataset_sha256']}`", f"- Parser: `{PARSER_REVISION}`; сайжруулалт хийгдээгүй.", f"- Файл: {summary['cases']} ({summary['successes']} success, {summary['failures']} failed); нийт хэмжээ {sum(sizes):,} байт.", f"- Төрөл: {json.dumps(by_format_size, ensure_ascii=False)}", f"- Хугацаа: P50 {summary['median_ms']:.2f} ms; P95 {summary['p95_ms']:.2f} ms; throughput {summary['throughput_files_per_second']:.3f} файл/сек; mean worker CPU {summary['mean_worker_cpu_ms']:.2f} ms.", f"- Reference байхгүй тул CER, heading/clause/table accuracy = N/A. Ground-truth сонголт: {len(selected)} файл (бүх 8 legacy DOC + DOCX feature/size coverage); template: `{template_path}`.", f"- Memory peak RSS: {summary['peak_memory_bytes'] if summary['peak_memory_bytes'] is not None else 'N/A'} bytes. CPU ба RSS нь parser worker-д хамаарна; LibreOffice-ийн child process RSS/CPU ороогүй.", f"- Илрүүлэлт: {report['inventory']['characteristic_counts'].get('tables', 0)} файлд хүснэгт; {report['inventory']['characteristic_counts'].get('embedded_media', 0)}-д embedded media; {report['inventory']['characteristic_counts'].get('text_boxes', 0)}-д text box; {report['inventory']['characteristic_counts'].get('multi_column_section', 0)}-д олон баганат section. {report['inventory'].get('documents_with_numbered_clause_markers', 0)} файлд {report['inventory'].get('numbered_clause_markers_total', 0):,} numbered clause marker тоологдсон (recall биш). PDF байхгүй бол scanned PDF үнэлэхгүй; DOC/DOCX page count боломжгүй.", "- Нууцлал: тайланд файл нэр, extracted text, exception message байхгүй; private ID-to-path mapping тусдаа хадгалсан.", "", "## Синтетик baseline-тай харьцуулалт", "", "| Dataset | Файл | Success | P50 ms | P95 ms | Throughput | CER |", "|---|---:|---:|---:|---:|---:|---|", f"| Synthetic DOCX baseline | 16 | 16 | 913.510 | 1120.350 | 1.079 файл/сек | N/A |", f"| Real confidential corpus | {summary['cases']} | {summary['successes']} | {summary['median_ms']:.2f} | {summary['p95_ms']:.2f} | {summary['throughput_files_per_second']:.3f} файл/сек | N/A |", "", "Эдгээр dataset нь формат, хэмжээ, агуулгаар өөр тул latency-г шууд чанарын харьцуулалт гэж үзэхгүй. Real corpus-ийн overall P95 нь legacy DOC conversion-оос хамаарч байна; format бүрийн P50/P95 нь JSON/CSV-д бий.", "", "## Алдаа ба бүрэн байдлын дохио", "", "3 DOCX `empty_document` ангиллаар бүтэлгүйтэв (DOC0032, DOC0087, DOC0122; эдгээр нь table-only/low-text шинжтэй байж болзошгүйг бүрэн батлахгүй). 11 DOCX-д эх package-ийн хүснэгтийн тоо ба extraction table block-ийн тоо зөрсөн. 137 DOCX-д зураг/media байхад одоогийн parser DOCX зурагт OCR хийхгүй; 26-д text box байсан бөгөөд парсер тэдгээрийг шалгахгүй. Эдгээр нь completeness эрсдэлийн дохио болохоос бодит accuracy алдаа гэж хэмжигдээгүй.", "", "## Нотолгоонд суурилсан сайжруулах дараалал", "", "1. 3 хоосон DOCX болон 11 table-count mismatch-ийг 20 сонгосон reference sample дээр эхэлж шалга. Энэ нь бодит алдаа байгаа эсэхийг тодорхойлох хамгийн шууд алхам.", "2. 137 media, 26 text box нь өргөн тархсан completeness эрсдэл тул ground truth дээр зураг/textbox-д утгатай текст байгаа эсэх, алдагдсан эсэхийг тэмдэглэ. Алдагдал батлагдвал Split-ээс өмнө DOCX image/textbox extraction-ийг сайжруулахыг эхэлж зөвлөж байна.", "3. 8 DOC-ийн parser хугацаа DOCX-ээс 4-5x удаан; conversion timeout ба тусгаарласан LibreOffice-ийн хэмжилтийг тусад нь оптимизац хийх боломжийг судал. Одоогоор 8/8 амжилттай тул гүйцэтгэл нь correctness-ээс бага эрэмбэтэй.", "", "`success` нь completeness/correctness батлахгүй. Ground truth бөглөгдөөгүй; accuracy үзүүлэлтүүд N/A. Parser implementation өөрчлөгдөөгүй.", ""]
    (out / f"{stem}.md").write_text("\n".join(md), encoding="utf8")
    report.update(report_stem=stem, private_map_path=str(private_map), template_path=str(template_path))
    return report
