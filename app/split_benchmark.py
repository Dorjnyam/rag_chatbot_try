"""Private snapshot and offline instrumentation for the existing Split stage.

This module deliberately does not import the Flask database, vector store, or
OpenAI client factory. The only production behavior it invokes is the current
character chunker and its local token-limit fitting method.
"""
from __future__ import annotations

import csv
import ctypes
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .services.chunking import chunk
from .services.parsing import PARSER_REVISION, native_fingerprint
from .services.storage import fingerprint


ROOT = Path(__file__).resolve().parents[1]
REAL_REPORT = ROOT / "evaluation/load/reports/real-load-20261021T042112Z.json"
SOURCE_MAP = ROOT / "instance/load-evaluation/private-source-map.csv"
SNAPSHOT_ROOT = ROOT / "instance/split-evaluation"
PUBLIC_ROOT = ROOT / "evaluation/split"
SNAPSHOT_ID = "real-corpus-15b24382-v1"
ITERATIONS = 5
MAX_EMBEDDING_TOKENS = 7900


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_digest(files: list[tuple[str, Path]]) -> str:
    digest = hashlib.sha256()
    for rel, path in sorted(files, key=lambda pair: pair[0].casefold()):
        digest.update(rel.replace("\\", "/").encode("utf8") + b"\0")
        digest.update(str(path.stat().st_size).encode("ascii") + b"\0")
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def _current_config():
    from dotenv import load_dotenv
    from .config import settings
    load_dotenv(ROOT / ".env")
    config = settings(ROOT / "instance")
    config.update(PDF_MAX_PAGES=100, DOCX_MAX_EXPANDED=100 * 1024 * 1024,
                  OCR_TIMEOUT=60, CONVERT_TIMEOUT=120, PROCESS_TIMEOUT=900)
    return config


def _cache_identity(source_sha: str, config: dict) -> dict:
    identity = {"sha": source_sha, "parser": PARSER_REVISION,
                "ocr": config["OCR_LANGUAGES"], "minimum": config["OCR_MIN_CHARACTERS"],
                "forced": []}
    identity["native"] = native_fingerprint(config)
    return identity


def freeze_snapshot() -> dict:
    """Copy exact current raw extraction caches after verifying corpus identity."""
    report = json.loads(REAL_REPORT.read_text(encoding="utf8"))
    expected_corpus = report["dataset_sha256"]
    with SOURCE_MAP.open(encoding="utf-8-sig", newline="") as handle:
        mapping = list(csv.DictReader(handle))
    if len(mapping) != 154 or len(report["records"]) != 154:
        raise ValueError("Phase 1 corpus accounting is not the expected 154 records.")
    paths = [(row["source_relative_path"], ROOT / "instance/uploads" / row["source_relative_path"])
             for row in mapping]
    if _canonical_digest(paths) != expected_corpus:
        raise ValueError("Source corpus hash differs from the Phase 1 manifest; snapshot was not created.")
    by_id = {record["case_id"]: record for record in report["records"]}
    config = _current_config()
    out = SNAPSHOT_ROOT / SNAPSHOT_ID
    raw_dir = out / "raw"
    existing_manifest = out / "manifest.json"
    if existing_manifest.is_file():
        existing = json.loads(existing_manifest.read_text(encoding="utf8"))
        if existing.get("dataset_sha256") != expected_corpus or existing.get("snapshot_id") != SNAPSHOT_ID:
            raise ValueError("An incompatible frozen snapshot already exists; it was left unchanged.")
        for record in existing.get("records", []):
            if record.get("artifact"):
                artifact = out / record["artifact"]
                if not artifact.is_file() or _sha(artifact) != record.get("artifact_sha256"):
                    raise ValueError("Frozen snapshot artifact is missing or changed; snapshot was left unchanged.")
        return {"snapshot_id": SNAPSHOT_ID, "dataset_sha256": expected_corpus,
                "split_inputs": existing["split_input_count"], "load_failures": existing["load_failure_doc_ids"],
                "snapshot_dir": str(out), "reused": True}
    raw_dir.mkdir(parents=True, exist_ok=True)
    records, failures = [], []
    for order, row in enumerate(mapping):
        doc_id = row["doc_id"]
        load_record = by_id.get(doc_id)
        source = ROOT / "instance/uploads" / row["source_relative_path"]
        if not load_record or not source.is_file():
            raise ValueError(f"Phase 1 source mapping incomplete for {doc_id}.")
        source_hash = _sha(source)
        record = {"order": order, "doc_id": doc_id, "format": load_record["format"], "source_sha256": source_hash,
                  "source_bytes": source.stat().st_size, "load_status": load_record["status"],
                  "load_error_category": load_record.get("error_category"), "parser_revision": PARSER_REVISION,
                  "production_document_id": None, "production_version_id": None}
        if load_record["status"] != "success":
            failures.append(doc_id)
            records.append(record)
            continue
        cache_name = fingerprint(_cache_identity(source_hash, config)) + ".json"
        cache = ROOT / "instance/extractions" / cache_name
        if not cache.is_file():
            raise ValueError(f"Validated raw extraction cache missing for {doc_id}; no parser rerun attempted.")
        artifact = json.loads(cache.read_text(encoding="utf8"))
        if artifact.get("parser") != PARSER_REVISION:
            raise ValueError(f"Parser revision mismatch in raw cache for {doc_id}.")
        blocks = artifact.get("blocks")
        if not isinstance(blocks, list):
            raise ValueError(f"Invalid raw extraction shape for {doc_id}.")
        chars = sum(len(block.get("text", "")) for block in blocks)
        if chars != load_record["text_characters"] or len(blocks) != load_record["block_count"]:
            raise ValueError(f"Phase 1 raw output counts mismatch for {doc_id}.")
        target = raw_dir / f"{doc_id}.json"
        target.write_bytes(cache.read_bytes())
        record.update(artifact=f"raw/{doc_id}.json", artifact_sha256=_sha(target),
                      block_count=len(blocks), text_characters=chars)
        records.append(record)
    if len(records) != 154 or len(failures) != 3:
        raise ValueError("Expected 151 cached successes and three preserved Load failures.")
    identity = {"source_sha256": expected_corpus, "parser_revision": PARSER_REVISION,
                "ocr_languages": config["OCR_LANGUAGES"], "ocr_min_characters": config["OCR_MIN_CHARACTERS"],
                "native_tool_fingerprint": native_fingerprint(config),
                "load_run_id": report["benchmark_run_id"], "load_manifest": "evaluation/load/manifest.json",
                "cache_policy": "exact raw extraction cache; reviewed artifacts excluded"}
    snapshot = {"schema_version": 1, "snapshot_id": SNAPSHOT_ID, "created_at": datetime.now(timezone.utc).isoformat(),
                "dataset_sha256": expected_corpus, "source_count": 154,
                "split_input_count": 151, "load_failure_count": 3,
                "load_failure_doc_ids": failures, "production_ids": "unavailable; not inferred",
                "identity": identity, "records": records}
    (out / "manifest.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf8")
    return {"snapshot_id": SNAPSHOT_ID, "dataset_sha256": expected_corpus,
            "split_inputs": 151, "load_failures": failures, "snapshot_dir": str(out)}


def _tokenizer():
    import tiktoken
    return tiktoken.get_encoding("cl100k_base")


def _rss_bytes():
    try:
        if os.name == "nt":
            from ctypes import wintypes
            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            psapi = ctypes.WinDLL("Psapi.dll")
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            kernel32 = ctypes.WinDLL("kernel32.dll")
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return int(counters.PeakWorkingSetSize)
        else:
            import resource
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return int(rss * (1024 if sys.platform != "darwin" else 1))
    except Exception:
        pass
    return None


def _group_blocks(blocks):
    groups = []
    for block in blocks:
        if not block.get("text", "").strip():
            continue
        if (not groups or block.get("page") or groups[-1][-1].get("page")
                or block.get("heading") != groups[-1][-1].get("heading")):
            groups.append([])
        groups[-1].append(block)
    return groups


def _source_intervals(blocks, chunks, size=1800, overlap=250):
    """Map ordered chunk character offsets to source-block intervals.

    ``size`` is retained for call compatibility; offsets, not a presumed fixed
    window size, define coverage so offline token chunkers can share this audit.
    """
    groups = _group_blocks(blocks)
    by_block = {}
    separators = 0
    idx = 0
    for group in groups:
        text_len = sum(len(b["text"]) for b in group) + 2 * (len(group) - 1)
        spans, cursor = [], 0
        for b in group:
            lo, hi = cursor, cursor + len(b["text"])
            spans.append((lo, hi, b))
            cursor = hi + 2
        if len(group) > 1:
            separators += 2 * (len(group) - 1)
        covered_end = 0
        while covered_end < text_len and idx < len(chunks):
            item = chunks[idx]
            lo, hi = item["metadata"]["char_start"], item["metadata"]["char_end"]
            if lo > covered_end or hi <= covered_end or hi > text_len:
                raise ValueError("Chunk offsets have a gap or do not advance the source cover.")
            for blo, bhi, block in spans:
                left, right = max(lo, blo), min(hi, bhi)
                if right > left:
                    key = str(block.get("block", "unknown"))
                    by_block.setdefault(key, []).append((left - blo, right - blo))
            idx += 1
            covered_end = hi
    if idx != len(chunks):
        raise ValueError("Some output chunks could not be mapped to an ordered source group.")
    return by_block, separators


def _merge_intervals(intervals):
    merged = []
    for lo, hi in sorted(intervals):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _sentence_boundary_diagnostics(blocks, chunks, size, overlap):
    """Punctuation heuristic only; not a sentence segmenter or accuracy metric."""
    groups = _group_blocks(blocks)
    index = 0
    violations = 0
    boundaries = 0
    punctuation = set(".!?。！？;；:：”’)]}»」』\"")
    for group in groups:
        text = "\n\n".join(block.get("text", "") for block in group)
        while index < len(chunks):
            start = chunks[index]["metadata"]["char_start"]
            end = chunks[index]["metadata"]["char_end"]
            if end > len(text):
                raise ValueError("Chunk boundary exceeds source group while checking punctuation heuristic.")
            if end < len(text):
                boundaries += 1
                left = text[start:end].rstrip()
                if left and left[-1] not in punctuation:
                    violations += 1
            index += 1
            if end == len(text):
                break
    return violations, boundaries


def _document_metrics(doc_id, blocks, raw_chunks, fitted_chunks, enc, size=1800, overlap=250):
    intervals, separator_chars = _source_intervals(blocks, raw_chunks, size, overlap)
    text_by_block = {str(b.get("block", "unknown")): b.get("text", "") for b in blocks}
    covered = sum(hi-lo for spans in intervals.values() for lo, hi in _merge_intervals(spans))
    source_chars = sum(len(text) for text in text_by_block.values())
    uncovered_whitespace = 0
    uncovered_nonwhitespace = 0
    for block in blocks:
        key = str(block.get("block", "unknown"))
        text = block.get("text", "")
        cursor = 0
        gaps = []
        for lo, hi in _merge_intervals(intervals.get(key, [])):
            if lo > cursor:
                gaps.append(text[cursor:lo])
            cursor = max(cursor, hi)
        if cursor < len(text):
            gaps.append(text[cursor:])
        for gap in gaps:
            uncovered_whitespace += sum(char.isspace() for char in gap)
            uncovered_nonwhitespace += sum(not char.isspace() for char in gap)
    raw_texts = [item["text"] for item in raw_chunks]
    token_counts = [len(enc.encode(text)) for text in raw_texts]
    char_counts = [len(text) for text in raw_texts]
    content_texts = [text for text in raw_texts if text.strip()]
    duplicate_count = len(content_texts) - len(set(content_texts))
    separator_only_count = len(raw_texts) - len(content_texts)
    joined_chars = sum(char_counts)
    overlap_chars = max(0, joined_chars - covered - separator_chars)
    actual_overlap_tokens = 0
    grouped_chunks = {}
    for item in raw_chunks:
        metadata = item.get("metadata", {})
        key = (metadata.get("heading", ""), metadata.get("page_start"), metadata.get("page_end"))
        grouped_chunks.setdefault(key, []).append(item)
    for items in grouped_chunks.values():
        for previous, current in zip(items, items[1:]):
            prev_end = previous["metadata"].get("char_end", 0)
            curr_start = current["metadata"].get("char_start", prev_end)
            prev_block = previous["metadata"].get("block_end")
            curr_block = current["metadata"].get("block_start")
            same_group = (prev_block is None or curr_block is None
                          or (isinstance(prev_block, int) and isinstance(curr_block, int)
                              and curr_block <= prev_block))
            if same_group and curr_start < prev_end:
                actual_overlap_tokens += len(enc.encode(previous["text"][-(prev_end-curr_start):]))
    headings = [item["metadata"].get("heading", "") for item in raw_chunks]
    pages = [(item["metadata"].get("page_start"), item["metadata"].get("page_end")) for item in raw_chunks]
    clause_count = sum(len(__import__("re").findall(r"(?m)^\s*\d+(?:\.\d+)*(?:[.)])?\s+", text)) for text in text_by_block.values())
    import re
    clause_pattern = re.compile(r"(?m)^\s*\d+(?:\.\d+)*(?:[.)])?\s+")
    clause_markers = 0
    clause_fragmented = 0
    for key, text in text_by_block.items():
        matches = list(clause_pattern.finditer(text))
        clause_markers += len(matches)
        for index, match in enumerate(matches):
            clause_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            if not any(lo <= match.start() and hi >= clause_end for lo, hi in intervals.get(key, [])):
                clause_fragmented += 1
    sentence_violations, sentence_boundaries = _sentence_boundary_diagnostics(blocks, raw_chunks, size, overlap)
    partial_tables = sum(bool(item["metadata"].get("partial_table")) for item in raw_chunks)
    total_tokens = sum(token_counts)
    source_token_count = sum(len(enc.encode("\n\n".join(block.get("text", "") for block in group)))
                             for group in _group_blocks(blocks))
    per_document = {"doc_id": doc_id, "block_count": len(blocks), "source_characters": source_chars,
        "raw_chunk_count": len(raw_chunks), "fitted_chunk_count": len(fitted_chunks),
        "covered_source_characters": covered, "coverage_ratio": covered/source_chars if source_chars else None,
        "uncovered_source_characters": max(0, source_chars-covered), "inserted_separator_characters": separator_chars,
        "uncovered_whitespace_characters": uncovered_whitespace,
        "uncovered_nonwhitespace_characters": uncovered_nonwhitespace,
        "chunk_char_min": min(char_counts, default=0), "chunk_char_median": statistics.median(char_counts) if char_counts else None,
        "chunk_char_p95": _percentile(char_counts, .95), "chunk_char_max": max(char_counts, default=0),
        "chunk_token_min": min(token_counts, default=0), "chunk_token_median": statistics.median(token_counts) if token_counts else None,
        "chunk_token_p95": _percentile(token_counts, .95), "chunk_token_max": max(token_counts, default=0),
        "embedding_input_tokens_estimate": total_tokens, "duplicate_chunk_count": duplicate_count,
        "separator_only_chunk_count": separator_only_count,
        "duplicate_chunk_ratio": duplicate_count/len(raw_chunks) if raw_chunks else None,
        "source_span_overlap_characters": overlap_chars,
          "actual_overlap_tokens_estimate": actual_overlap_tokens,
        "source_span_overlap_ratio_of_emitted_chars": overlap_chars/joined_chars if joined_chars else 0,
        "character_target_utilization_median": statistics.median(char_counts)/size if char_counts and size else None,
        "source_text_tokens_without_overlap": source_token_count,
        "token_inflation_ratio": total_tokens/max(1, source_token_count),
        "partial_table_chunk_count": partial_tables,
        "heading_crossing_chunk_count": 0,
        "page_crossing_chunk_count": (sum(1 for a,b in pages if a is not None and b is not None and a != b)
                                       if any(a is not None or b is not None for a,b in pages) else None),
        "numbered_clause_markers_heuristic": clause_count,
        "clause_fragmentation_count_heuristic": clause_fragmented,
        "clause_fragmentation_denominator_heuristic": clause_markers,
        "clause_fragmentation_status": "parser-block regex heuristic; not verified clause accuracy",
        "sentence_boundary_violations_heuristic": sentence_violations,
        "sentence_boundary_heuristic_denominator": sentence_boundaries,
        "sentence_boundary_heuristic_status": "punctuation heuristic only; not ground-truth accuracy",
        "complete_clause_preservation": None, "sentence_boundary_accuracy": None,
        "citation_source_span_accuracy": None, "quality_status": "N/A: no verified human references"}
    return per_document


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction*len(ordered))-1)]


def _timed_worker(payload):
    """Spawn target: import and run only local chunk/fitting/tokenization code."""
    from .services.chunking import chunk as make_chunks
    from .services.openai_provider import OpenAIProvider
    blocks, config, version_id, embedding_model, doc_id = payload
    adapter = object.__new__(OpenAIProvider)
    adapter._encodings = {}
    adapter._encoding = OpenAIProvider._encoding.__get__(adapter, OpenAIProvider)
    enc = adapter._encoding(embedding_model)
    if enc is None:
        raise RuntimeError("tiktoken is unavailable; exact token measurements are not reported.")
    tracemalloc.start()
    rss_before = _rss_bytes()
    cpu_start = time.process_time()
    total_start = time.perf_counter()
    stage_cpu = {}
    t0 = time.perf_counter()
    c0 = time.process_time()
    raw = make_chunks(blocks, config, version_id)
    stage_cpu["chunking_cpu_ms"] = (time.process_time()-c0)*1000
    chunk_ms = (time.perf_counter()-t0)*1000
    chunk_peak = tracemalloc.get_traced_memory()[1]
    t1 = time.perf_counter()
    c1 = time.process_time()
    fitted = OpenAIProvider.fit_chunks(adapter, raw, embedding_model, version_id, config)
    stage_cpu["guard_cpu_ms"] = (time.process_time()-c1)*1000
    guard_ms = (time.perf_counter()-t1)*1000
    t2 = time.perf_counter()
    c2 = time.process_time()
    metrics = _document_metrics(doc_id, blocks, raw, fitted, enc, config["chunk_size"], config["chunk_overlap"])
    stage_cpu["metrics_tokenization_cpu_ms"] = (time.process_time()-c2)*1000
    token_ms = (time.perf_counter()-t2)*1000
    total_ms = (time.perf_counter()-total_start)*1000
    rss_after = _rss_bytes()
    rss_peak = max((value for value in (rss_before,rss_after) if value is not None), default=None)
    cpu_ms = (time.process_time()-cpu_start)*1000
    chunk_summaries = [{"ordinal": i, "id": item["id"], "text_sha256": fingerprint(item["text"]),
                        "characters": len(item["text"]), "tokens": len(enc.encode(item["text"])),
                        "content_hash": item["metadata"]["content_hash"],
                        "block_start": item["metadata"].get("block_start"), "block_end": item["metadata"].get("block_end"),
                        "page_start": item["metadata"].get("page_start"), "page_end": item["metadata"].get("page_end"),
                        "partial_table": bool(item["metadata"].get("partial_table"))}
                       for i,item in enumerate(fitted)]
    raw_chunk_summaries = [{"ordinal": i, "id": item["id"], "text_sha256": fingerprint(item["text"]),
                            "characters": len(item["text"]), "tokens": len(enc.encode(item["text"]))}
                           for i,item in enumerate(raw)]
    hashes = {"raw_text_sha256": fingerprint([c["text"] for c in raw]),
              "fitted_text_sha256": fingerprint([c["text"] for c in fitted]),
              "chunk_ids_sha256": fingerprint([c["id"] for c in fitted])}
    tracemalloc.stop()
    return {"metrics": metrics, "chunks": chunk_summaries, "raw_chunks": raw_chunk_summaries, **hashes,
            "chunking_ms": chunk_ms, "guard_ms": guard_ms, "metrics_tokenization_ms": token_ms,
            "total_ms": total_ms, "cpu_ms": cpu_ms, **stage_cpu, "chunking_python_peak_bytes": chunk_peak,
            "worker_rss_sample_bytes": rss_peak}


def _run_one(doc_id: str, artifact_path: Path, config: dict):
    artifact = json.loads(artifact_path.read_text(encoding="utf8"))
    version_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"split:{SNAPSHOT_ID}:{doc_id}"))
    model = config["OPENAI_EMBEDDING_MODEL"]
    payload = (artifact["blocks"], {"chunk_size": 1800, "chunk_overlap": 250}, version_id, model, doc_id)
    # One fresh process per document, with a discarded warm-up and five runs.
    process = subprocess.run([sys.executable, "-m", "app.split_benchmark_worker"],
        input=json.dumps({"blocks": payload[0], "config": payload[1], "version_id": payload[2],
                          "model": model, "doc_id": doc_id, "iterations": ITERATIONS + 1}, ensure_ascii=False),
        text=True, encoding="utf8", capture_output=True, cwd=ROOT)
    if process.returncode:
        import re
        match = re.search(r"([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)):", process.stderr)
        return {"doc_id": doc_id, "status": "split_failed", "failure_stage": "chunk_or_token_guard",
                "benchmark_version_id": version_id, "production_document_id": None, "production_version_id": None,
                "error_type": match.group(1) if match else "WorkerFailure",
                "quality_status": "N/A: Split output unavailable"}
    result = json.loads(process.stdout)
    if result.get("status") != "split_success":
        return result
    result["benchmark_version_id"] = version_id
    result["production_document_id"] = None
    result["production_version_id"] = None
    # Chunk text never leaves the child process or enters the report.
    return result


def run_baseline(snapshot_id: str = SNAPSHOT_ID) -> dict:
    snap_dir = SNAPSHOT_ROOT / snapshot_id
    manifest_path = snap_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Frozen snapshot not found; run benchmark-split freeze first.")
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    if manifest["snapshot_id"] != snapshot_id or manifest["dataset_sha256"] != json.loads(REAL_REPORT.read_text(encoding="utf8"))["dataset_sha256"]:
        raise ValueError("Snapshot identity does not match the verified Phase 1 corpus.")
    config = _current_config()
    try:
        import tiktoken
        enc = _tokenizer()
        tokenizer_status = {"name": "cl100k_base", "tiktoken_version": importlib.metadata.version("tiktoken"), "status": "available"}
    except Exception as exc:
        raise ValueError(f"Cannot run token baseline without cl100k_base: {type(exc).__name__}") from exc
    successful = [r for r in manifest["records"] if r["load_status"] == "success"]
    results = []
    wall_start = time.perf_counter()
    for record in successful:
        artifact_path = snap_dir / record["artifact"]
        if not artifact_path.is_file() or _sha(artifact_path) != record.get("artifact_sha256"):
            raise ValueError(f"Frozen artifact integrity check failed for {record['doc_id']}.")
        item = _run_one(record["doc_id"], artifact_path, config)
        results.append(item)
    wall = time.perf_counter()-wall_start
    measured = [r for r in results if r.get("status") != "split_failed"]
    failures = [r for r in results if r.get("status") == "split_failed"]
    latencies = [r["total_ms"] for r in measured]
    cpu = [r["worker_cpu_per_iteration_ms"] for r in measured if r.get("worker_cpu_per_iteration_ms") is not None]
    rss = [r["worker_rss_sample_bytes"] for r in measured if r.get("worker_rss_sample_bytes") is not None]
    python_peaks = [r["chunking_python_peak_bytes"] for r in measured if r.get("chunking_python_peak_bytes") is not None]
    token_counts = [c["tokens"] for r in measured for c in r["chunks"]]
    summary = {"input_documents": len(manifest["records"]), "split_documents": len(results),
        "split_successes": len(measured), "split_failures": len(failures),
        "split_failure_doc_ids": [r["doc_id"] for r in failures],
        "preserved_load_failures": len(manifest["load_failure_doc_ids"]), "chunk_count": sum(r["metrics"]["fitted_chunk_count"] for r in measured),
        "dataset_sha256": manifest["dataset_sha256"], "wall_seconds": round(wall,3),
        "throughput_docs_per_second": len(results)/wall if wall else None,
        "median_document_total_ms": statistics.median(latencies) if latencies else None,
        "p95_document_total_ms": _percentile(latencies,.95), "total_embedding_input_tokens_estimate": sum(token_counts),
        "median_document_cpu_ms": statistics.median(cpu) if cpu else None,
        "p95_document_cpu_ms": _percentile(cpu,.95),
        "median_worker_peak_rss_bytes": statistics.median(rss) if rss else None,
        "p95_worker_peak_rss_bytes": _percentile(rss,.95), "peak_worker_rss_bytes": max(rss) if rss else None,
        "median_chunking_python_peak_bytes": statistics.median(python_peaks) if python_peaks else None,
        "p95_chunking_python_peak_bytes": _percentile(python_peaks,.95),
        "stage_latency_ms": {stage: {"p50": statistics.median([r[stage] for r in measured]),
            "p95": _percentile([r[stage] for r in measured], .95)}
            for stage in ("chunking_ms", "guard_ms", "metrics_tokenization_ms")},
        "chunk_token_median": statistics.median(token_counts) if token_counts else None,
        "chunk_token_p95": _percentile(token_counts,.95), "api_calls": 0,
        "quality_accuracy_metrics": {"complete_clause_preservation": None, "sentence_boundary_accuracy": None,
            "citation_source_span_accuracy": None, "retrieval_recall_at_k": None, "mrr": None, "ndcg_at_k": None},
        "quality_na_reason": "No human-verified Split or retrieval evidence references are available.",
        "retrieval_latency": None, "context_tokens_per_query": None, "actual_index_bytes": None,
        "embedding_token_note": "Prospective input-token estimate; no API usage or vector index was created.",
        "peak_memory_note": "Fresh-worker process peak working set via Windows GetProcessMemoryInfo; chunking Python allocation peak via tracemalloc."}
    run_id = str(uuid.uuid4())
    env = {"python": platform.python_version(), "platform": platform.platform(), "cpu_count": os.cpu_count(),
           "packages": {name: _version(name) for name in ("Flask", "python-docx", "tiktoken", "psutil")},
           "tokenizer": tokenizer_status}
    report = {"schema_version": 1, "run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": snapshot_id, "dataset_sha256": manifest["dataset_sha256"], "parser_revision": PARSER_REVISION,
        "parser_config": {"strategy": "characters-v1", "chunk_size": 1800, "chunk_overlap": 250,
                          "normalization": "NFC/NUL removal/whitespace rules from ordered-blocks-v1"},
        "token_limit_guard": {"implementation": "OpenAIProvider.fit_chunks", "max_tokens": MAX_EMBEDDING_TOKENS,
                              "embedding_model": config["OPENAI_EMBEDDING_MODEL"], "api_used": False},
        "environment": env, "load_run_id": json.loads(REAL_REPORT.read_text(encoding="utf8"))["benchmark_run_id"],
        "production_database_connections": 0, "production_vector_store_calls": 0,
        "summary": summary, "documents": results}
    PUBLIC_ROOT.mkdir(parents=True, exist_ok=True)
    stem = f"split-a0-{run_id[:8]}"
    _write_public_reports(stem, report)
    return {"run_id": run_id, "snapshot_id": snapshot_id, "summary": summary,
            "report_stem": stem, "output_dir": str(PUBLIC_ROOT)}


def _version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def run_complexity_probe() -> dict:
    """Measure current span scans at several block counts with identical joined text."""
    canonical = "\n\n".join(f"unit-{index:04d} synthetic-policy " + "x"*16 for index in range(1000))
    separators = [index for index in range(len(canonical)-1) if canonical[index:index+2] == "\n\n"]
    results = []
    expected_hash = None
    config = {"chunk_size": 1800, "chunk_overlap": 250}
    for block_count in (1, 10, 100, 500):
        chosen = []
        for index in range(1, block_count):
            chosen.append(separators[round(index*(len(separators)-1)/block_count)])
        blocks, start = [], 0
        for position in chosen:
            blocks.append({"text": canonical[start:position], "heading": "synthetic", "block": len(blocks)+1,
                           "kind": "table"})
            start = position + 2
        blocks.append({"text": canonical[start:], "heading": "synthetic", "block": len(blocks)+1,
                       "kind": "table"})
        version = str(uuid.uuid5(uuid.NAMESPACE_URL, f"split-complexity:{block_count}"))
        first = chunk(blocks, config, version)
        text_hash = fingerprint([item["text"] for item in first])
        if expected_hash is None:
            expected_hash = text_hash
        if text_hash != expected_hash:
            raise ValueError("Synthetic block-count variants did not preserve canonical chunk text.")
        timings = []
        for iteration in range(6):
            started = time.perf_counter()
            current = chunk(blocks, config, version)
            elapsed = (time.perf_counter()-started)*1000
            if fingerprint([item["text"] for item in current]) != text_hash:
                raise ValueError("Synthetic complexity probe output changed across iterations.")
            if iteration:
                timings.append(elapsed)
        results.append({"synthetic_block_count": block_count, "canonical_text_characters": len(canonical),
            "chunk_count": len(first), "chunk_text_sha256": text_hash,
            "median_chunking_ms": statistics.median(timings), "p95_chunking_ms": _percentile(timings,.95),
            "iterations": len(timings), "warmup_runs": 1})
    probe = {"schema_version":1,"status":"measured_synthetic_only","strategy":"characters-v1",
        "chunk_size_characters":1800,"chunk_overlap_characters":250,"canonical_text_sha256":expected_hash,
        "purpose":"Measure block-span scan overhead while joined input text and output chunks stay identical.",
        "not_real_corpus_evidence":True,"results":results}
    target = PUBLIC_ROOT / "complexity-probe.json"
    target.write_text(json.dumps(probe,ensure_ascii=False,indent=2),encoding="utf8")
    return {"output":str(target),"results":results,"canonical_text_sha256":expected_hash}


def run_token_count_probe(run_id: str = "1dc80917-a602-4185-9c3a-f16cae209b21") -> dict:
    """Time only local token encoding over frozen fitted chunks; no API/provider calls."""
    report_path = PUBLIC_ROOT / f"split-a0-{run_id[:8]}.json"
    report = json.loads(report_path.read_text(encoding="utf8"))
    snapshot = json.loads((SNAPSHOT_ROOT / report["snapshot_id"] / "manifest.json").read_text(encoding="utf8"))
    records = {row["doc_id"]: row for row in snapshot["records"]}
    from .services.openai_provider import OpenAIProvider
    encoder = _tokenizer()
    config = {"chunk_size": 1800, "chunk_overlap": 250}
    results = []
    for row in report["documents"]:
        doc_id = row["doc_id"]
        artifact_path = SNAPSHOT_ROOT / report["snapshot_id"] / records[doc_id]["artifact"]
        if _sha(artifact_path) != records[doc_id]["artifact_sha256"]:
            raise ValueError(f"Frozen artifact integrity check failed for {doc_id}.")
        blocks = json.loads(artifact_path.read_text(encoding="utf8"))["blocks"]
        version_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"split:{report['snapshot_id']}:{doc_id}"))
        raw = chunk(blocks, config, version_id)
        adapter = object.__new__(OpenAIProvider)
        adapter._encodings = {}
        fitted = OpenAIProvider.fit_chunks(adapter, raw, "text-embedding-3-small", version_id, config)
        texts = [item["text"] for item in fitted]
        measured = []
        counts = []
        for iteration in range(6):
            started = time.perf_counter()
            current = [len(encoder.encode(text)) for text in texts]
            elapsed = (time.perf_counter()-started)*1000
            if iteration == 0:
                counts = current
            else:
                measured.append(elapsed)
            if current != counts:
                raise ValueError(f"Token count changed for {doc_id}.")
        expected = [item["tokens"] for item in row["chunks"]]
        if counts != expected:
            raise ValueError(f"Token probe disagrees with the A0 chunk inventory for {doc_id}.")
        results.append({"doc_id":doc_id,"chunk_count":len(texts),"embedding_input_tokens_estimate":sum(counts),
            "median_tokenizer_only_ms":statistics.median(measured),"p95_tokenizer_only_ms":_percentile(measured,.95),
            "iterations":5,"warmup_runs":1})
    values=[row["median_tokenizer_only_ms"] for row in results]
    probe={"schema_version":1,"status":"measured_offline","run_id":report["run_id"],
        "snapshot_id":report["snapshot_id"],"dataset_sha256":report["dataset_sha256"],
        "tokenizer":"cl100k_base","tiktoken_version":importlib.metadata.version("tiktoken"),
        "api_calls":0,"production_database_connections":0,"production_vector_store_calls":0,
        "documents":len(results),"chunk_count":sum(row["chunk_count"] for row in results),
        "embedding_input_tokens_estimate":sum(row["embedding_input_tokens_estimate"] for row in results),
        "document_tokenizer_latency_p50_ms":statistics.median(values),
        "document_tokenizer_latency_p95_ms":_percentile(values,.95),
        "measure_note":"Only cl100k_base encode() over fitted chunk text is timed; parser/chunker/provider/API costs excluded.",
        "results":results}
    target=PUBLIC_ROOT / "token-counting-probe.json"
    target.write_text(json.dumps(probe,ensure_ascii=False,indent=2),encoding="utf8")
    return {"output":str(target),"documents":len(results),"chunks":probe["chunk_count"],
        "tokens":probe["embedding_input_tokens_estimate"],
        "tokenizer_only_document_p50_ms":probe["document_tokenizer_latency_p50_ms"],
        "tokenizer_only_document_p95_ms":probe["document_tokenizer_latency_p95_ms"]}


def _write_public_reports(stem, report):
    out = PUBLIC_ROOT
    (out / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    fields = ["doc_id", "raw_chunk_count", "fitted_chunk_count", "source_characters", "covered_source_characters",
              "coverage_ratio", "chunk_token_median", "chunk_token_p95", "chunk_token_max", "embedding_input_tokens_estimate",
              "duplicate_chunk_count", "source_span_overlap_characters", "partial_table_chunk_count", "heading_crossing_chunk_count",
              "page_crossing_chunk_count", "chunking_ms", "guard_ms", "metrics_tokenization_ms", "total_ms",
              "worker_cpu_per_iteration_ms", "worker_cpu_total_ms", "cpu_ms", "chunking_cpu_ms", "guard_cpu_ms", "metrics_tokenization_cpu_ms",
              "chunking_python_peak_bytes", "worker_rss_sample_bytes", "quality_status"]
    with (out / f"{stem}.csv").open("w", encoding="utf8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report["documents"]:
            flat = {**row.get("metrics", {}), **row}
            writer.writerow({field: flat.get(field) for field in fields})
    summary = report["summary"]
    md = ["# Split A0 офлайн baseline", "", f"- Run ID: `{report['run_id']}`",
          f"- Snapshot: `{report['snapshot_id']}`", f"- Dataset SHA-256: `{report['dataset_sha256']}`",
          f"- Parser: `{report['parser_revision']}`; strategy `characters-v1`, 1800 characters, overlap 250 characters.",
          f"- Өгөгдөл: {summary['split_documents']} Load-success Split input, {summary['preserved_load_failures']} өмнөх Load failure.",
          f"- Split outcomes: {summary['split_successes']} амжилттай, {summary['split_failures']} exception.",
          f"- Үр дүн: {summary['chunk_count']} fitted chunks; wall {summary['wall_seconds']} s; throughput {summary['throughput_docs_per_second']:.3f} documents/s.",
          f"- Chunk token median/P95: {summary['chunk_token_median']} / {summary['chunk_token_p95']} `cl100k_base` tokens.",
          "- API usage: 0; PostgreSQL connection: 0; ChromaDB call: 0.",
          "- Accuracy, retrieval metrics, actual index size: N/A; human-verified evidence reference байхгүй.",
          "- Token count нь embedding request-ийн estimate; billed usage эсвэл index size биш.",
          "- Raw болон fitted output, per-document metric-үүд JSON-д; chunk text болон эх файлын нэр тайланд ороогүй.", ""]
    (out / f"{stem}.mn.md").write_text("\n".join(md), encoding="utf8")
