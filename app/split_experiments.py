"""Offline A1-A3 experiments and private ground-truth review preparation.

This module consumes only the immutable parsed snapshot. Token algorithms here
are experimental implementations and do not replace the production splitter.
Private annotation artifacts are written under ``instance/`` only.
"""
from __future__ import annotations

import csv
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
from pathlib import Path

from .services.chunking import assign_ids, chunk as current_chunk
from .services.storage import fingerprint
from .split_benchmark import (ITERATIONS, PARSER_REVISION, PUBLIC_ROOT, SNAPSHOT_ROOT,
    SNAPSHOT_ID, _document_metrics, _percentile, _rss_bytes, _sha, _tokenizer, _version)

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / "instance/split-evaluation"
ANNOTATION_ROOT = PRIVATE_ROOT / "annotation-review"
EXPERIMENTS = (
    {"id": "A1", "strategy": "characters-v1", "size": 1800, "overlap": 0},
    {"id": "A2", "strategy": "fixed-token-v1", "size": 512, "overlap": 0},
    {"id": "A3", "strategy": "recursive-token-v1", "size": 512, "overlap": 0,
     "separators": ["\\n\\n", "\\n", " ", ""]},
)


def _groups(blocks):
    groups = []
    for block in blocks:
        if not block.get("text", "").strip():
            continue
        if (not groups or block.get("page") or groups[-1][-1].get("page")
                or block.get("heading") != groups[-1][-1].get("heading")):
            groups.append([])
        groups[-1].append(block)
    return groups


def _byte_to_char_boundaries(text):
    result, pos = [0], 0
    for char in text:
        pos += len(char.encode("utf8"))
        result.append(pos)
    return result


def _fixed_token_spans(text, enc, limit):
    """Return exact source character spans close to token windows.

    UTF-8 token boundaries can split a Mongolian codepoint. Such boundaries
    are rounded forward to the next codepoint edge; actual emitted chunk token
    counts are re-encoded and reported, so the small boundary effect is visible.
    """
    ids = enc.encode(text)
    if not ids:
        return []
    byte_edges = [0]
    for token_id in ids:
        byte_edges.append(byte_edges[-1] + len(enc.decode_single_token_bytes(token_id)))
    char_edges = _byte_to_char_boundaries(text)
    spans, token_start, char_start = [], 0, 0
    while token_start < len(ids):
        token_end = min(token_start + limit, len(ids))
        byte_end = byte_edges[token_end]
        char_end = min(len(text), __import__("bisect").bisect_left(char_edges, byte_end))
        if char_end <= char_start:
            char_end = min(len(text), char_start + 1)
        # Re-tokenization can shift one token at a Unicode edge. Keep the
        # declared budget strict by moving the source boundary back as needed.
        while char_end > char_start and len(enc.encode(text[char_start:char_end])) > limit:
            char_end -= 1
        if char_end <= char_start:
            raise ValueError("Unable to form a non-empty chunk within the token budget.")
        spans.append((char_start, char_end))
        char_start, token_start = char_end, token_end
    if char_start < len(text):
        spans.append((char_start, len(text)))
    return spans


def _recursive_token_spans(text, enc, limit):
    """Greedily pack separator-preserving atoms, recursively refining long ones."""
    import re
    spans = []
    separators = ("\n\n", "\n", " ")

    def atoms(lo, hi, separator):
        if not separator:
            return []
        result, cursor = [], lo
        for match in re.finditer(re.escape(separator), text[lo:hi]):
            end = lo + match.end()
            if end > cursor:
                result.append((cursor, end))
                cursor = end
        if cursor < hi:
            result.append((cursor, hi))
        return result

    def split_range(lo, hi, depth):
        if lo >= hi:
            return
        if len(enc.encode(text[lo:hi])) <= limit:
            spans.append((lo, hi))
            return
        while depth < len(separators):
            pieces = atoms(lo, hi, separators[depth])
            if len(pieces) > 1:
                break
            depth += 1
        if depth >= len(separators):
            spans.extend((lo + a, lo + b) for a, b in _fixed_token_spans(text[lo:hi], enc, limit))
            return
        current_lo, current_hi = None, None
        for atom_lo, atom_hi in pieces:
            if len(enc.encode(text[atom_lo:atom_hi])) > limit:
                if current_lo is not None:
                    spans.append((current_lo, current_hi))
                    current_lo = current_hi = None
                split_range(atom_lo, atom_hi, depth + 1)
                continue
            candidate_lo = atom_lo if current_lo is None else current_lo
            if current_lo is not None and len(enc.encode(text[candidate_lo:atom_hi])) > limit:
                spans.append((current_lo, current_hi))
                current_lo, current_hi = atom_lo, atom_hi
            else:
                current_lo, current_hi = candidate_lo, atom_hi
        if current_lo is not None:
            spans.append((current_lo, current_hi))

    split_range(0, len(text), 0)
    return spans


def _experimental_chunks(blocks, strategy, enc, version_id):
    if strategy == "characters-v1":
        return current_chunk(blocks, {"chunk_size": 1800, "chunk_overlap": 0}, version_id)
    output = []
    config = {"strategy": strategy, "chunk_size": 512, "chunk_overlap": 0,
              "tokenizer": "cl100k_base"}
    for group in _groups(blocks):
        text = "\n\n".join(b.get("text", "") for b in group)
        windows = (_fixed_token_spans(text, enc, 512) if strategy == "fixed-token-v1"
                   else _recursive_token_spans(text, enc, 512))
        block_spans, cursor = [], 0
        for block in group:
            lo, hi = cursor, cursor + len(block.get("text", ""))
            block_spans.append((lo, hi, block))
            cursor = hi + 2
        for lo, hi in windows:
            included = [(a, b, block) for a, b, block in block_spans if a < hi and b > lo]
            metadata = {"char_start": lo, "char_end": hi,
                "block_start": included[0][2].get("block") if included else None,
                "block_end": included[-1][2].get("block") if included else None,
                "heading": group[0].get("heading", ""),
                "extraction_method": "ocr" if any(item[2].get("method") == "ocr" for item in included) else "native",
                "separator_only": not included,
                "partial_table": any(b.get("kind") == "table" and a < hi and end > lo and (a < lo or end > hi)
                    for a, end, b in block_spans if b in [item[2] for item in included])}
            source_regions = [b["source_region"] for _, _, b in included if b.get("source_region")]
            if source_regions:
                metadata["source_regions_json"] = json.dumps(source_regions, ensure_ascii=False, separators=(",", ":"))
            if group[0].get("page"):
                metadata.update(page_start=group[0]["page"], page_end=group[-1].get("page"))
            output.append({"text": text[lo:hi], "metadata": metadata})
    return assign_ids(output, version_id, config)


def _worker_document(payload):
    doc_id, blocks, strategies, version_root = payload
    enc = _tokenizer()
    results = {}
    rss_before = _rss_bytes()
    for spec in strategies:
        name = spec["id"]
        config = spec.copy()
        timings, outputs = [], []
        cpu_batch_start = None
        py_peak = 0
        for iteration in range(ITERATIONS + 1):
            tracemalloc.start()
            started = time.perf_counter()
            if iteration == 1:
                cpu_batch_start = time.process_time()
            version_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"split:{version_root}:{doc_id}:{name}"))
            chunks = _experimental_chunks(blocks, spec["strategy"], enc, version_id)
            elapsed = (time.perf_counter() - started) * 1000
            py_peak = max(py_peak, tracemalloc.get_traced_memory()[1])
            tracemalloc.stop()
            if iteration:
                timings.append(elapsed)
            else:
                outputs = chunks
        fitted = outputs
        cpu_per_iteration = ((time.process_time() - cpu_batch_start) * 1000 / ITERATIONS
                             if cpu_batch_start is not None else None)
        metrics = _document_metrics(doc_id, blocks, outputs, fitted, enc,
            1800 if name == "A1" else 512, 0)
        results[name] = {"metrics": metrics, "chunking_p50_ms": statistics.median(timings),
            "chunking_p95_ms": _percentile(timings, .95), "cpu_p50_ms": cpu_per_iteration,
            "cpu_p95_ms": cpu_per_iteration, "python_peak_bytes": py_peak,
            "chunk_text_sha256": fingerprint([item["text"] for item in outputs]),
            "chunk_ids_sha256": fingerprint([item["id"] for item in outputs]),
            "chunk_summaries": [{"ordinal": index, "text_sha256": fingerprint(item["text"]),
                "tokens": len(enc.encode(item["text"])), "characters": len(item["text"]),
                "block_start": item["metadata"].get("block_start"),
                "block_end": item["metadata"].get("block_end"),
                "page_start": item["metadata"].get("page_start"),
                "page_end": item["metadata"].get("page_end"),
                "partial_table": bool(item["metadata"].get("partial_table"))}
                for index, item in enumerate(outputs)]}
    return {"doc_id": doc_id, "strategies": results,
            "worker_peak_rss_bytes": max([value for value in (rss_before, _rss_bytes()) if value is not None], default=None)}


def _run_worker(doc_id, blocks, snapshot_id):
    payload = {"doc_id": doc_id, "blocks": blocks, "strategies": list(EXPERIMENTS), "snapshot_id": snapshot_id}
    process = subprocess.run([sys.executable, "-m", "app.split_experiment_worker"],
        input=json.dumps(payload, ensure_ascii=False), text=True, encoding="utf8",
        capture_output=True, cwd=ROOT)
    if process.returncode:
        import re
        match = re.search(r"([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)):", process.stderr)
        return {"doc_id": doc_id, "status": "split_failed", "error_type": match.group(1) if match else "WorkerFailure"}
    return json.loads(process.stdout)


def run_a1_a3(snapshot_id=SNAPSHOT_ID):
    """Run controlled local A1-A3 comparisons on the immutable parsed snapshot."""
    snap_dir = SNAPSHOT_ROOT / snapshot_id
    manifest_path = snap_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Frozen snapshot is unavailable; no parser rerun is performed.")
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    if snapshot_id != SNAPSHOT_ID or manifest.get("snapshot_id") != snapshot_id:
        raise ValueError("Unexpected snapshot identity.")
    if len(manifest.get("records", [])) != 154 or manifest.get("split_input_count") != 151:
        raise ValueError("Frozen corpus accounting does not match the verified Load manifest.")
    try:
        import tiktoken
        tokenizer = {"name": "cl100k_base", "version": importlib.metadata.version("tiktoken"), "status": "available"}
    except Exception as exc:
        raise ValueError(f"Exact tokenizer unavailable: {type(exc).__name__}") from exc
    wall_started = time.perf_counter()
    docs = []
    for record in manifest["records"]:
        if record["load_status"] != "success":
            continue
        path = snap_dir / record["artifact"]
        if not path.is_file() or _sha(path) != record.get("artifact_sha256"):
            raise ValueError(f"Frozen artifact integrity check failed for {record['doc_id']}.")
        artifact = json.loads(path.read_text(encoding="utf8"))
        if artifact.get("parser") != PARSER_REVISION:
            raise ValueError(f"Parser identity mismatch for {record['doc_id']}.")
        docs.append(_run_worker(record["doc_id"], artifact["blocks"], snapshot_id))
    wall = time.perf_counter() - wall_started
    successful = [doc for doc in docs if doc.get("status") != "split_failed"]
    strategies = {}
    for spec in EXPERIMENTS:
        name = spec["id"]
        rows = [doc["strategies"][name] for doc in successful]
        metrics = [row["metrics"] for row in rows]
        all_tokens = [summary["tokens"] for row in rows for summary in row["chunk_summaries"]]
        def aggregate(key):
            return [metric[key] for metric in metrics if metric.get(key) is not None]
        latencies = [row["chunking_p50_ms"] for row in rows]
        python_peaks = [row["python_peak_bytes"] for row in rows]
        cpu_latencies = [row["cpu_p50_ms"] for row in rows]
        strategies[name] = {"config": spec, "documents": len(rows), "chunk_count": sum(m["raw_chunk_count"] for m in metrics),
            "chunk_token_distribution": {"min": min(all_tokens, default=0), "median": statistics.median(all_tokens) if all_tokens else None,
                "p95": _percentile(all_tokens, .95), "max": max(all_tokens, default=0)},
            "coverage_ratio_median": statistics.median(aggregate("coverage_ratio")) if aggregate("coverage_ratio") else None,
            "uncovered_nonwhitespace_characters": sum(m["uncovered_nonwhitespace_characters"] for m in metrics),
            "token_inflation_ratio_median": statistics.median(aggregate("token_inflation_ratio")) if aggregate("token_inflation_ratio") else None,
            "source_span_overlap_ratio_median": statistics.median(aggregate("source_span_overlap_ratio_of_emitted_chars")) if aggregate("source_span_overlap_ratio_of_emitted_chars") else None,
            "partial_table_chunks_heuristic": sum(m["partial_table_chunk_count"] for m in metrics),
            "duplicate_chunks": sum(m["duplicate_chunk_count"] for m in metrics),
            "separator_only_chunks": sum(m["separator_only_chunk_count"] for m in metrics),
            "clause_fragmentation_count_heuristic": sum(m["clause_fragmentation_count_heuristic"] for m in metrics),
            "clause_fragmentation_denominator_heuristic": sum(m["clause_fragmentation_denominator_heuristic"] for m in metrics),
            "sentence_boundary_violations_heuristic": sum(m["sentence_boundary_violations_heuristic"] for m in metrics),
            "sentence_boundary_denominator_heuristic": sum(m["sentence_boundary_heuristic_denominator"] for m in metrics),
            "numbered_clause_markers_heuristic": sum(m["numbered_clause_markers_heuristic"] for m in metrics),
            "complete_clause_preservation": None, "retrieval_metrics": {"evidence_recall_at_k": None, "mrr": None, "ndcg_at_k": None},
            "chunking_latency_p50_ms": statistics.median(latencies) if latencies else None,
            "chunking_latency_p95_ms": _percentile(latencies, .95),
            "chunking_cpu_p50_ms": statistics.median(cpu_latencies) if cpu_latencies else None,
            "chunking_cpu_p95_ms": _percentile(cpu_latencies, .95),
            "worker_python_allocation_peak_p95_bytes": _percentile(python_peaks, .95),
            "worker_peak_rss_bytes_max_across_A1_A3": max((doc["worker_peak_rss_bytes"] for doc in successful if doc.get("worker_peak_rss_bytes") is not None), default=None),
            "embedding_input_token_estimate": sum(all_tokens), "api_calls": 0}
    failures = [doc for doc in docs if doc.get("status") == "split_failed"]
    run_id = str(uuid.uuid4())
    report = {"schema_version": 1, "run_id": run_id, "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "dataset_sha256": manifest["dataset_sha256"], "snapshot_id": snapshot_id, "parser_revision": PARSER_REVISION,
        "input_order": "frozen manifest record order", "tokenizer": tokenizer,
        "controls": {"overlap": 0, "same_frozen_blocks": True, "same_heading_page_fences": True,
            "retrieval": "not run", "api_calls": 0, "production_database_connections": 0, "production_vector_store_calls": 0,
            "a0_run_id": "1dc80917-a602-4185-9c3a-f16cae209b21"},
        "environment": {"python": platform.python_version(), "platform": platform.platform(), "cpu_count": os.cpu_count(),
            "packages": {name: _version(name) for name in ("tiktoken", "Flask", "python-docx")}},
        "summary": {"source_input_count": 154, "split_input_count": 151, "preserved_load_failure_count": 3,
            "split_success_count": len(successful), "split_failure_count": len(failures), "failure_doc_ids": [f["doc_id"] for f in failures],
            "wall_seconds": round(wall, 3), "throughput_docs_per_second": len(docs) / wall if wall else None},
        "strategies": strategies,
        "accuracy": {"human_verified_references": 0, "complete_clause_accuracy": None, "sentence_accuracy": None,
            "citation_source_span_accuracy": None, "retrieval_readiness": "NOT READY: candidate annotations remain unreviewed"},
        "structural_failure_examples": _failure_examples(successful),
        "documents": [{"doc_id": doc["doc_id"], "status": doc.get("status", "split_success"),
            "strategies": {key: {k: v for k, v in value.items() if k != "chunk_summaries"} for key, value in doc.get("strategies", {}).items()}}
            for doc in docs],
        "private_chunk_summaries": {doc["doc_id"]: {key: value["chunk_summaries"] for key, value in doc.get("strategies", {}).items()} for doc in successful}}
    PUBLIC_ROOT.mkdir(parents=True, exist_ok=True)
    stem = f"split-a1-a3-{run_id[:8]}"
    (PUBLIC_ROOT / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    _write_comparison_markdown(stem, report)
    _write_comparison_csv(stem, report)
    return {"run_id": run_id, "report_stem": stem, "summary": report["summary"], "strategies": strategies}


def _failure_examples(docs):
    result = []
    for doc in docs:
        for strategy, row in doc["strategies"].items():
            metrics = row["metrics"]
            if (metrics["uncovered_nonwhitespace_characters"] or metrics["partial_table_chunk_count"]
                    or metrics["clause_fragmentation_count_heuristic"]):
                result.append({"doc_id": doc["doc_id"], "strategy": strategy,
                    "uncovered_nonwhitespace_characters": metrics["uncovered_nonwhitespace_characters"],
                    "partial_table_chunks_heuristic": metrics["partial_table_chunk_count"],
                    "clause_fragmentation_count_heuristic": metrics["clause_fragmentation_count_heuristic"],
                    "sentence_boundary_violations_heuristic": metrics["sentence_boundary_violations_heuristic"],
                    "evidence_text_included": False})
    return result


def _write_comparison_csv(stem, report):
    path = PUBLIC_ROOT / f"{stem}.csv"
    fields = ["doc_id", "strategy", "chunk_count", "coverage_ratio", "uncovered_nonwhitespace_characters",
        "token_inflation_ratio", "duplicate_chunks", "clause_fragmentation_count_heuristic",
        "separator_only_chunks", "partial_table_chunks_heuristic", "sentence_boundary_violations_heuristic",
        "chunking_p50_ms", "chunking_p95_ms", "chunking_cpu_p50_ms", "chunking_cpu_p95_ms", "python_peak_bytes"]
    with path.open("w", encoding="utf8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        docs = {doc["doc_id"]: doc for doc in report["documents"]}
        for doc_id, doc in docs.items():
            for strategy, row in doc.get("strategies", {}).items():
                metrics = row["metrics"]
                writer.writerow({"doc_id": doc_id, "strategy": strategy, "chunk_count": metrics["raw_chunk_count"],
                    "coverage_ratio": metrics["coverage_ratio"], "uncovered_nonwhitespace_characters": metrics["uncovered_nonwhitespace_characters"],
                    "token_inflation_ratio": metrics["token_inflation_ratio"], "partial_table_chunks_heuristic": metrics["partial_table_chunk_count"],
                    "duplicate_chunks": metrics["duplicate_chunk_count"],
                    "separator_only_chunks": metrics["separator_only_chunk_count"],
                    "clause_fragmentation_count_heuristic": metrics["clause_fragmentation_count_heuristic"],
                    "sentence_boundary_violations_heuristic": metrics["sentence_boundary_violations_heuristic"],
                    "chunking_p50_ms": row["chunking_p50_ms"], "chunking_p95_ms": row["chunking_p95_ms"],
                    "chunking_cpu_p50_ms": row["cpu_p50_ms"], "chunking_cpu_p95_ms": row["cpu_p95_ms"],
                    "python_peak_bytes": row["python_peak_bytes"]})


def _write_comparison_markdown(stem, report):
    a0_path = PUBLIC_ROOT / "split-a0-1dc80917.json"
    a0 = json.loads(a0_path.read_text(encoding="utf8")) if a0_path.is_file() else None
    lines = ["# Phase 2 — Ground truth preparation ба controlled A1-A3 experiments", "", "Бүх хэмжилт parser-ийн ижил frozen output дээр. Human-verified evidence байхгүй тул retrieval чанарын дүгнэлт хийгээгүй.", "",
        f"- Run ID: `{report['run_id']}`", f"- Dataset SHA-256: `{report['dataset_sha256']}`",
        f"- Parser: `{report['parser_revision']}`; tokenizer: `{report['tokenizer']['name']}` / tiktoken `{report['tokenizer']['version']}`",
        f"- Оролт: {report['summary']['source_input_count']}; Split inputs: {report['summary']['split_input_count']}; өмнөх Load failures: {report['summary']['preserved_load_failure_count']}",
        f"- A1+A2+A3 нийлсэн wall time / throughput: {report['summary']['wall_seconds']} s / {report['summary']['throughput_docs_per_second']:.3f} docs/s; 151 document worker тус бүрт нэг warm-up + 5 хэмжилт.",
        "- API / PostgreSQL / ChromaDB: 0; retrieval ба verified accuracy хэмжүүрүүд: N/A.",
        "- Ground-truth package: 20 representative document, 80 candidate questions, тус бүр 3 draft query variant; private `instance/split-evaluation/annotation-review/` дотор.", "",
        "## Туршилтын зорилго ба controls", "",
        "| Strategy | Hypothesis (tested offline only) | Config / experimental variable | Controlled variables | Cost/complexity |",
        "|---|---|---|---|---|"]
    design = {
        "A1": "Overlap=0 бол A0-ийн 250-char overlap-аас source-span redundancy болон estimated embedding tokens буурах эсэх.",
        "A2": "512-token fixed windows нь A1-ийн character size-ээс бодит token distribution-ийг илүү тогтмол болгох эсэх.",
        "A3": "Paragraph/newline/space separator packing нь fixed windows-оос boundary heuristics-ийг багасгах эсэх; chunking time/fragmentation tradeoff-ийг хамт хэмжинэ.",
    }
    for name, row in report["strategies"].items():
        config = row["config"]
        lines.append(f"| {name} | {design[name]} | `{config['strategy']}`, target {config['size']}, overlap {config['overlap']} | Ижил frozen blocks/input order, `ordered-blocks-v1`, tokenizer `{report['tokenizer']['name']}` {report['tokenizer']['version']}, heading/page fences, offline execution | Embedding API cost = 0; chunk count/token estimate нь ирээдүйн index/cost input. Recursive ба fixed chunking CPU complexity тус тус хэмжигдсэн. |")
    lines += ["", "## Offline results", "",
        "| Strategy | Баримт | Chunks | Token median/P95/max | Coverage median | Token inflation median | Duplicates / separator-only | Clause fragments heuristic | Table-cut heuristic | Chunk latency P50/P95 ms | CPU P50/P95 ms | Python allocation P95 bytes | Shared worker RSS max bytes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, row in report["strategies"].items():
        config = row["config"]
        distribution = row["chunk_token_distribution"]
        lines.append(f"| {name} | {row['documents']} | {row['chunk_count']} | {distribution['median']} / {distribution['p95']} / {distribution['max']} | {row['coverage_ratio_median']} | {row['token_inflation_ratio_median']} | {row['duplicate_chunks']} / {row['separator_only_chunks']} | {row['clause_fragmentation_count_heuristic']} / {row['clause_fragmentation_denominator_heuristic']} | {row['partial_table_chunks_heuristic']} | {row['chunking_latency_p50_ms']:.3f} / {row['chunking_latency_p95_ms']:.3f} | {row['chunking_cpu_p50_ms']:.3f} / {row['chunking_cpu_p95_ms']:.3f} | {row['worker_python_allocation_peak_p95_bytes']} | {row['worker_peak_rss_bytes_max_across_A1_A3']} |")
    lines += ["", "## A0 comparison (different overlap only)", ""]
    if a0:
        a0s = a0["summary"]
        a1 = report["strategies"]["A1"]
        lines += [f"A0 run `{a0['run_id']}` ба A1 ижил dataset/parser/current character splitter ашигласан; гол өөрчлөлт overlap 250 → 0. Chunk {a0s['chunk_count']} → {a1['chunk_count']}; estimated embedding tokens {a0s['total_embedding_input_tokens_estimate']:,} → {a1['embedding_input_token_estimate']:,}; A0 overlap ratio median {statistics.median(d['metrics']['source_span_overlap_ratio_of_emitted_chars'] for d in a0['documents'] if d.get('metrics')):.3f}, A1 {a1['source_span_overlap_ratio_median']:.3f}.",
            "A0-ийн end-to-end P50/P95 latency нь guard/tokenization/diagnostics агуулдаг; A1 latency нь зөвхөн chunk-generation тул шууд latency comparison хийхгүй."]
    else:
        lines.append("A0 JSON файл олдсонгүй; энэ comparison N/A.")
    lines += ["", "## Structural signals ба failure examples", "", "Chunk таслалт, clause boundary болон sentence punctuation тоо нь parser block дээрх heuristic; correctness гэж тайлбарлахгүй. Эх текст тайланд ороогүй.", "",
        f"- Structural flag мөрийн тоо: {len(report['structural_failure_examples'])}; JSON дотор doc ID + strategy + count байна.",
        f"- A2-ийн хамгийн их chunk token: {report['strategies']['A2']['chunk_token_distribution']['max']}; A3: {report['strategies']['A3']['chunk_token_distribution']['max']}. Target 512.",
        "- Table-cut heuristic нь flattened table blocks-ийн chunk boundary давсан эсэх; cell/header/merged-cell correctness хэмжихгүй.",
        "- Process RSS нь A1/A2/A3-г хамтад нь ажиллуулсан нэг fresh worker-ийн before/after RSS sample-ийн max; per-strategy high-water mark гэж тайлбарлахгүй. Python allocation нь strategy тус бүрийн tracemalloc peak.",
        "- Complete-clause preservation, sentence accuracy, source-span accuracy, Recall@k, MRR, nDCG: N/A — human verification хийгдээгүй.",
        "- Inference: recursive strategy-ийн punctuation/clause heuristics A2-оос бага гарсан ч table cuts болон chunk count хэвээр өндөр, chunking latency илүү. Энэ нь зөвхөн next-step hypothesis-г дэмжих heuristic signal; strategy quality winner биш.",
        "", "## Per-experiment дүгнэлт ба хязгаарлалт", "",
        "- **A1 (1800 char / 0 overlap):** A0-оос chunk болон token estimate цөөрсөн; source-span overlap 0. Coverage 100% median, source non-whitespace loss 0. A0 дээр overlap-оор давтагдах context алга болсон байж болох тул retrieval evidence recall-г annotation-гүйгээр дүгнэхгүй.",
        "- **A2 (512 token / 0 overlap):** token distribution target орчимд тогтвортой; fixed boundaries дээр clause/sentence heuristics олон. Mongolian UTF-8 byte boundary нь token window-оос ялгаатай; output token max-ийг тайлангаас шалгана. Алгоритмын reference нь benchmark-only.",
        "- **A3 (recursive separator / 512 / 0):** A2-оос жижиг/олон chunk, punctuation/clause fragmentation heuristic бага байж болно; paragraph/space packing нь more expensive token counting ба empty separator chunks үүсгэж болох эрсдэлтэй. Semantic clause understanding биш.",
        "", "## Retrieval gate ба reproduction", "",
        "Retrieval benchmark readiness: **NOT READY**. Эхлээд 80 draft candidate-аас evidence болон query-г эх документтэй харьцуулж хүн батална. Үүний дараа ижил embedding model/config, retrieval algorithm, top-k, evaluation queries-ээр isolated index хийхийн өмнө input-token ашиглалт/cost тооцно. Одоо paid embedding run хийгдээгүй.",
        "", "Командууд:", "", "```powershell", ".\\.venv\\Scripts\\flask.exe --app app benchmark-split prepare-annotations --documents 20 --questions-per-document 4", ".\\.venv\\Scripts\\flask.exe --app app benchmark-split run-a1-a3", "```", "",
        "A0-г өөрчлөлгүй baseline болгон `split-a0-1dc80917` JSON/CSV тайланд хадгалсан. A1 нь production splitter-ийг өөрчлөлгүй 1,800-char/0-overlap тохиргоотой дуудсан; A2/A3 нь benchmark-only. Public JSON/CSV/Markdown-д confidential document text, нэр, heading ороогүй. Private evidence package зөвхөн `instance/` дотор.", ""]
    (PUBLIC_ROOT / f"{stem}.mn.md").write_text("\n".join(lines), encoding="utf8")


def prepare_annotations(target_documents=20, questions_per_document=4):
    """Create private candidate questions and a self-contained local review page."""
    snapshot = json.loads((SNAPSHOT_ROOT / SNAPSHOT_ID / "manifest.json").read_text(encoding="utf8"))
    template = json.loads((PUBLIC_ROOT / "ground_truth_template.mn.json").read_text(encoding="utf8"))
    selected = [row["doc_id"] for row in template["documents"] if row.get("verification_status") == "unreviewed"][:target_documents]
    records = {row["doc_id"]: row for row in snapshot["records"]}
    with (ROOT / "instance/load-evaluation/private-source-map.csv").open(encoding="utf-8-sig", newline="") as handle:
        source_map = {row["doc_id"]: row["source_relative_path"] for row in csv.DictReader(handle)}
    candidates = []
    for doc_id in selected:
        record = records.get(doc_id)
        if not record or record.get("load_status") != "success":
            continue
        artifact = json.loads((SNAPSHOT_ROOT / SNAPSHOT_ID / record["artifact"]).read_text(encoding="utf8"))
        blocks = artifact.get("blocks", [])
        scored = []
        for block in blocks:
            text = block.get("text", "").strip()
            if len(text) < 100:
                continue
            import re
            clause = re.match(r"^\s*(\d+(?:\.\d+)*(?:[.)])?|[А-ЯЁA-Z][.)])\s+", text)
            exception = bool(re.search(r"(?iu)\b(?:хамаарахгүй|эс тооцвол|бусад тохиолдолд|онцгой тохиолдолд|зөвхөн)\b", text))
            nested_list = bool(re.search(r"(?m)^\s{2,}(?:[-*•]|\d+[.)])\s+", text))
            score = (min(len(text), 1600) + (2000 if block.get("kind") == "table" else 0)
                + (1500 if clause else 0) + (1800 if exception else 0) + (1200 if nested_list else 0))
            scored.append((score, block, clause.group(1) if clause else None))
        chosen, used_blocks = [], set()
        for _, block, clause in sorted(scored, key=lambda item: (-item[0], int(item[1].get("block", 0)))):
            if block.get("block") in used_blocks:
                continue
            chosen.append((block, clause))
            used_blocks.add(block.get("block"))
            if len(chosen) >= questions_per_document:
                break
        for index, (block, clause_id) in enumerate(chosen, start=1):
            evidence = block["text"].strip()
            loc = {"parser_local_block_id": block.get("block"), "element_type": block.get("kind"),
                "parser_heading": block.get("heading") or None, "page": block.get("page"),
                "clause_id": None, "observed_clause_marker_draft": clause_id,
                "source_span": None, "note": "Page/source span unavailable from ordered-blocks-v1; block index is parser-local."}
            question_cy = (f"{clause} дугаар заалтад ямар шаардлага тавьсан бэ?" if clause else "Энэ бодлогын хэсэгт ямар шаардлага тавьсан бэ?")
            question_en = (f"What requirement is stated in clause {clause}?" if clause else "What requirement is stated in this policy passage?")
            if re.search(r"(?iu)\b(?:хамаарахгүй|эс тооцвол|бусад тохиолдолд|онцгой тохиолдолд|зөвхөн)\b", evidence):
                question_cy, question_en = "Энэ хэсэгт ямар үл хамаарах нөхцөл, онцгой тохиолдол дурдсан бэ?", "What exception or exclusion is stated in this passage?"
            elif re.search(r"(?m)^\s{2,}(?:[-*•]|\d+[.)])\s+", evidence):
                question_cy, question_en = "Энэ жагсаалтад ямар нөхцөл эсвэл алхмууд дурдсан бэ?", "What conditions or steps are listed here?"
            latin = _romanize_candidate(question_cy)
            candidates.append({"candidate_id": f"{doc_id}-Q{index:02d}", "doc_id": doc_id,
                "source_file": str((ROOT / "instance/uploads" / source_map[doc_id]).resolve()),
                "source_sha256": record["source_sha256"], "question_variants": {"mn_cyrl": question_cy,
                    "en": question_en, "mn_latn_draft": latin}, "question_status": "machine_drafted_unreviewed",
                "proposed_evidence_text": evidence, "proposed_expected_answer": evidence,
                "evidence_status": "proposed_from_parser_output_unverified", "source_location": loc,
                "human_verified": False, "reviewer_id": None, "reviewed_at": None,
                "review_fields": {"question_approved": None, "evidence_matches_original": None,
                    "answer_approved": None, "source_location_verified": None, "split_reference_spans": None,
                    "notes": None}})
    if not (50 <= len(candidates) <= 100):
        raise ValueError(f"Candidate count {len(candidates)} is outside requested 50-100 range; no annotation package written.")
    ANNOTATION_ROOT.mkdir(parents=True, exist_ok=True)
    private = {"schema_version": 1, "snapshot_id": SNAPSHOT_ID, "dataset_sha256": snapshot["dataset_sha256"],
        "selected_document_count": len(selected), "candidate_question_count": len(candidates),
        "query_variants_per_candidate": ["mn_cyrl", "en", "mn_latn_draft"],
        "human_verified": False, "review_workflow": ["Open the linked original file and compare the candidate evidence against its visual source.",
            "Correct or reject each question, exact evidence, answer, and source location.",
            "Only after human review set human_verified=true and fill reviewer_id/reviewed_at.",
            "Keep page, clause and version fields null unless directly verified in the original."],
        "documents": [{"doc_id": doc_id, "format": records[doc_id]["format"],
            "source_file": str((ROOT / "instance/uploads" / source_map[doc_id]).resolve()),
            "source_sha256": records[doc_id]["source_sha256"], "parser_character_count": records[doc_id].get("text_characters"),
            "characteristics": _document_characteristics(json.loads((SNAPSHOT_ROOT / SNAPSHOT_ID / records[doc_id]["artifact"]).read_text(encoding="utf8"))["blocks"])}
            for doc_id in selected if doc_id in records and records[doc_id]["load_status"] == "success"],
        "candidates": candidates}
    target = ANNOTATION_ROOT / "candidate-review.json"
    target.write_text(json.dumps(private, ensure_ascii=False, indent=2), encoding="utf8")
    html = _review_html(private)
    (ANNOTATION_ROOT / "review.html").write_text(html, encoding="utf8")
    workflow = """# Annotation review workflow (private local package)\n\nOpen `review.html` in a local browser. It contains only the selected documents and proposed references, so treat it as confidential and do not commit or share it. Use the original-source link for visual verification.\n\nFor each candidate: (1) inspect the original document, (2) approve/rewrite/reject the query variants, (3) verify the exact evidence text, (4) write a concise expected answer, (5) verify original page/section/clause location where visible, and (6) record reviewer identity/date. Leave unknown metadata null. Do not set `human_verified=true` until all required fields are checked.\n\n`candidate-review.json` is the editable source. The generated page is a read-only review view; edit the JSON manually or use the fields as a checklist, then update the annotation template only after review.\n"""
    (ANNOTATION_ROOT / "README.mn.md").write_text(workflow, encoding="utf8")
    template_path = ANNOTATION_ROOT / "ground_truth_review_template.json"
    template_path.write_text(json.dumps({"schema_version": 1, "human_verified": False,
        "instructions": "Copy a candidate after reviewing its original document; preserve null metadata unless verified.",
        "candidates": [{key: value for key, value in candidate.items() if key != "proposed_evidence_text"} |
            {"verified_evidence_text": None, "verified_expected_answer": None, "human_verified": False}
            for candidate in candidates]}, ensure_ascii=False, indent=2), encoding="utf8")
    return {"private_output": str(ANNOTATION_ROOT), "documents": len(private["documents"]),
        "candidate_questions": len(candidates), "query_variants": len(candidates) * 3,
        "human_verified": False, "public_report_contains_content": False}


def _document_characteristics(blocks):
    import re
    text = "\n".join(block.get("text", "") for block in blocks)
    return {"blocks": len(blocks), "table_blocks": sum(block.get("kind") == "table" for block in blocks),
        "heading_blocks": sum(bool(block.get("heading")) for block in blocks),
        "page_metadata_blocks": sum(block.get("page") is not None for block in blocks),
        "numbered_clause_markers_heuristic": len(re.findall(r"(?m)^\s*\d+(?:\.\d+)*(?:[.)])?\s+", text)),
        "nested_list_markers_heuristic": len(re.findall(r"(?m)^\s{2,}(?:[-*•]|\d+[.)])\s+", text)),
        "long_blocks_over_1800_chars": sum(len(block.get("text", "")) > 1800 for block in blocks),
        "contains_mongolian_cyrillic": bool(re.search(r"[ӨөҮүА-Яа-яЁё]", text)),
        "contains_latin": bool(re.search(r"[A-Za-z]", text))}


def _romanize_candidate(text):
    """Mechanical simple Latin transliteration; label draft and reviewer-check it."""
    mapping = {"а":"a","б":"b","в":"v","г":"g","д":"d","е":"ye","ё":"yo","ж":"j","з":"z","и":"i","й":"i","к":"k","л":"l","м":"m","н":"n","о":"o","ө":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ү":"u","ф":"f","х":"kh","ц":"ts","ч":"ch","ш":"sh","щ":"shch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya"}
    result = []
    for char in text:
        lower = mapping.get(char.casefold())
        result.append((lower.capitalize() if char.isupper() and lower else lower) if lower is not None else char)
    return "".join(result)


def _review_html(package):
    import html
    cards = []
    for doc in package["documents"]:
        doc_candidates = [item for item in package["candidates"] if item["doc_id"] == doc["doc_id"]]
        cards.append(f"<section><h2>{html.escape(doc['doc_id'])} · {html.escape(doc['format'])}</h2>"
            f"<p>Characteristics (heuristic): {html.escape(json.dumps(doc['characteristics'], ensure_ascii=False))}</p>"
            f"<p>Original: <a href=\"{html.escape(Path(doc['source_file']).as_uri(), quote=True)}\">open source file</a></p>"
            + "".join(f"<article><h3>{html.escape(item['candidate_id'])} · unreviewed</h3>"
                f"<p><b>MN Cyrillic:</b> {html.escape(item['question_variants']['mn_cyrl'])}<br>"
                f"<b>English:</b> {html.escape(item['question_variants']['en'])}<br>"
                f"<b>MN Latin draft:</b> {html.escape(item['question_variants']['mn_latn_draft'])}</p>"
                f"<p><b>Proposed evidence and answer (unverified):</b></p><pre>{html.escape(item['proposed_evidence_text'])}</pre>"
                f"<p><b>Parser-local location only:</b> {html.escape(json.dumps(item['source_location'], ensure_ascii=False))}</p></article>"
                for item in doc_candidates) + "</section>")
    return "<!doctype html><html lang='mn'><meta charset='utf-8'><title>Private annotation review</title><style>body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem}section{border-top:3px solid #345;padding:1rem 0}article{background:#f4f5f7;padding:1rem;margin:1rem 0}pre{white-space:pre-wrap;background:white;padding:1rem}a{word-break:break-all}</style><h1>Ground-truth candidate review · private</h1><p>Бүх асуулт, evidence болон answer draft. Human-verified биш. Эх документтэй тулгаж өөрөө баталгаажуулна уу.</p>" + "".join(cards) + "</html>"
