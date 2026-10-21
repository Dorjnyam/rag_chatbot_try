"""Private, offline operational Embed measurements over the frozen A0 corpus."""
from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import statistics
import threading
import time
import tracemalloc
import uuid
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .embed_smoke import _encoding, _sha_file, deterministic_mock_vectors, prepare_smoke
from .services.chunking import chunk as production_chunk
from .services.embedding_contract import collection_contract_metadata, contract_sha256, validate_collection_metadata, validate_vectors
from .services.ingestion import embedding_batches, index_config
from .services.parsing import PARSER_REVISION
from .split_benchmark import REAL_REPORT, SNAPSHOT_ID, SNAPSHOT_ROOT


MAX_INPUT_TOKENS = 7900
PUBLIC_ROOT = Path(__file__).resolve().parents[1] / "evaluation" / "embed"


def _percentile(values, percentile):
    values = sorted(values)
    if not values:
        return None
    return values[max(0, math.ceil((percentile / 100) * len(values)) - 1)]


def _sha_json(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf8")).hexdigest()


def _classify_chunk(text):
    import re
    cyrillic = any("\u0400" <= char <= "\u052f" for char in text)
    latin = any(("a" <= char.lower() <= "z") for char in text)
    groups = []
    if cyrillic:
        groups.append("contains_cyrillic_heuristic")
    if latin and not cyrillic:
        groups.append("latin_only_heuristic")
    if latin and cyrillic:
        groups.append("mixed_script_heuristic")
    if any(char in text for char in "ӨөҮү"):
        groups.append("contains_mongolian_special_letters")
    if re.search(r"(?<!\w)\d+(?:\.\d+){1,}(?!\w)|(?<!\w)[A-Za-zА-Яа-яӨөҮү]+/\d+", text):
        groups.append("numbered_identifier_heuristic")
    return groups or ["other_or_numeric"]


def _summarize_texts(items, encoding):
    token_lengths = [len(encoding.encode(text)) for text in items]
    char_lengths = [len(text) for text in items]
    byte_lengths = [len(text.encode("utf8")) for text in items]
    word_lengths = [len(text.split()) for text in items]
    token_total = sum(token_lengths)
    char_total, byte_total, word_total = sum(char_lengths), sum(byte_lengths), sum(word_lengths)
    return {
        "items": len(items),
        "total_tokens": token_total,
        "tokens_per_unicode_character": token_total / char_total if char_total else None,
        "tokens_per_utf8_byte": token_total / byte_total if byte_total else None,
        "tokens_per_whitespace_word": token_total / word_total if word_total else None,
        "token_length": {"p50": _percentile(token_lengths, 50), "p95": _percentile(token_lengths, 95),
            "p99": _percentile(token_lengths, 99), "max": max(token_lengths, default=0),
            "over_7900_count": sum(length > MAX_INPUT_TOKENS for length in token_lengths)},
        "unicode_characters": char_total,
        "utf8_bytes": byte_total,
        "whitespace_words": word_total,
    }


def _frozen_corpus(data_dir: Path, runtime_config: dict):
    snapshot_dir = Path(data_dir) / "split-evaluation" / SNAPSHOT_ID
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Frozen Split parser manifest is missing.")
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    if manifest.get("snapshot_id") != SNAPSHOT_ID:
        raise ValueError("Frozen Split snapshot identity differs from the expected A0 corpus.")
    load_report = json.loads(REAL_REPORT.read_text(encoding="utf8"))
    if manifest.get("dataset_sha256") != load_report.get("dataset_sha256"):
        raise ValueError("Frozen Split corpus hash no longer matches the Phase 1 manifest.")
    records = manifest.get("records", [])
    successful = [row for row in records if row.get("load_status") == "success"]
    failed = [row for row in records if row.get("load_status") != "success"]
    if len(records) != 154 or len(successful) != 151 or len(failed) != 3:
        raise ValueError("Frozen corpus no longer has the expected 151 parsed and 3 retained Load failures.")
    config = index_config()
    if (config["chunk_size"], config["chunk_overlap"]) != (1800, 250):
        raise ValueError("The approved A0 baseline must remain 1,800 characters with 250-character overlap.")
    docs = {}
    for row in sorted(successful, key=lambda item: item["doc_id"]):
        artifact_path = (snapshot_dir / row["artifact"]).resolve()
        if not artifact_path.is_relative_to(snapshot_dir.resolve()) or not artifact_path.is_file():
            raise ValueError("Frozen parser artifact is missing or outside its snapshot.")
        if _sha_file(artifact_path) != row.get("artifact_sha256"):
            raise ValueError("Frozen parser artifact hash validation failed.")
        artifact = json.loads(artifact_path.read_text(encoding="utf8"))
        if artifact.get("parser") != PARSER_REVISION:
            raise ValueError("Frozen parser revision differs from the Phase 1 baseline.")
        version_id = str(uuid5(NAMESPACE_URL, f"embed-operational:{SNAPSHOT_ID}:{row['doc_id']}"))
        chunks = production_chunk(artifact.get("blocks", []), config, version_id)
        for item in chunks:
            item["metadata"].update(doc_id=row["doc_id"], source_sha256=row["source_sha256"],
                snapshot_id=SNAPSHOT_ID, benchmark_only=True, **collection_contract_metadata(config))
        docs[row["doc_id"]] = chunks
    return manifest, config, docs, len(failed)


def run_tokenization_benchmark(data_dir: Path, runtime_config: dict) -> dict:
    """Measure tokenizer efficiency; script classifications remain heuristics."""
    manifest, config, docs, load_failures = _frozen_corpus(Path(data_dir), runtime_config)
    plan, _smoke_chunks, query_inputs, _evidence = prepare_smoke(Path(data_dir), runtime_config)
    encoding = _encoding(config["embedding_model"])
    all_chunks = [item for doc in docs.values() for item in doc]
    by_group = {}
    chunk_sizes = {}
    special_letter_chunks = unicode_variant_chunks = 0
    for doc_id, chunks in docs.items():
        chunk_sizes[doc_id] = len(chunks)
        for item in chunks:
            groups = _classify_chunk(item["text"])
            if any(char in item["text"] for char in "ӨөҮү"):
                special_letter_chunks += 1
            nfc = __import__("unicodedata").normalize("NFC", item["text"])
            if len(encoding.encode(nfc)) != len(encoding.encode(item["text"])):
                unicode_variant_chunks += 1
            for group in groups:
                by_group.setdefault(group, []).append(item["text"])
    query_groups = {}
    for item in query_inputs:
        query_groups.setdefault(item["language"], []).append(item["text"])
    token_items = [item["text"] for item in all_chunks]
    run_id = uuid.uuid4().hex[:12]
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": created,
        "dataset": {"snapshot_id": SNAPSHOT_ID, "dataset_sha256": manifest["dataset_sha256"],
            "parser_revision": PARSER_REVISION, "input_documents": 154, "parsed_documents": len(docs),
            "retained_load_failures": load_failures, "a0_chunk_size_characters": 1800,
            "a0_overlap_characters": 250, "a0_chunks": len(all_chunks),
            "a0_chunk_counts_by_anonymous_doc_id": chunk_sizes},
        "embedding_contract": {**config, "api_calls": 0, "database_access": False, "chroma_access": False},
        "tokenizer": {"encoding": encoding.name, "package_version": importlib.metadata.version("tiktoken")},
        "chunks": _summarize_texts(token_items, encoding),
        "heuristic_text_groups": {key: _summarize_texts(value, encoding) for key, value in sorted(by_group.items())},
        "verified_query_variations_by_label": {key: _summarize_texts(value, encoding) for key, value in sorted(query_groups.items())},
        "unicode_diagnostics": {"chunks_containing_Ө_or_Ү": special_letter_chunks,
            "chunks_changed_token_count_after_NFC": unicode_variant_chunks,
            "classification_note": "Script and identifier group labels are diagnostics, not human-verified language labels."},
        "provider_usage_comparison": {"historical_paid_smoke_run_id": "88e507f8b31f",
            "preflight_tokens": 74251, "recorded_provider_prompt_tokens": 74251,
            "token_difference": 0, "scope": "2 documents and 6 query variations only; not the 151-document corpus."},
        "status": "offline_tokenization_only_no_embeddings",
    }
    report["run_sha256"] = _sha_json({key: value for key, value in report.items() if key != "run_sha256"})
    output_dir = Path(data_dir) / "split-evaluation" / "embed-benchmark" / "tokenization"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{run_id}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    with (output_dir / f"{run_id}.csv").open("w", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("group", "items", "total_tokens", "p50_tokens", "p95_tokens", "p99_tokens", "max_tokens", "over_7900"))
        writer.writeheader()
        groups = {"all_a0_chunks": report["chunks"], **report["heuristic_text_groups"],
            **{f"query_{key}": value for key, value in report["verified_query_variations_by_label"].items()}}
        for name, data in groups.items():
            writer.writerow({"group": name, "items": data["items"], "total_tokens": data["total_tokens"],
                "p50_tokens": data["token_length"]["p50"], "p95_tokens": data["token_length"]["p95"],
                "p99_tokens": data["token_length"]["p99"], "max_tokens": data["token_length"]["max"],
                "over_7900": data["token_length"]["over_7900_count"]})
    return {"run_id": run_id, "private_json": str(output_dir / f"{run_id}.json"),
        "private_csv": str(output_dir / f"{run_id}.csv"), "a0_chunks": len(all_chunks),
        "total_tokens": report["chunks"]["total_tokens"], "tokenizer": encoding.name,
        "run_sha256": report["run_sha256"]}


class _PeakRssSampler:
    def __init__(self):
        self.peak = None
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        try:
            import psutil
            process = psutil.Process(os.getpid())
        except ImportError:
            return self
        self.peak = process.memory_info().rss
        def sample():
            while not self._stop.wait(0.01):
                try:
                    self.peak = max(self.peak or 0, process.memory_info().rss)
                except Exception:
                    break
        self._thread = threading.Thread(target=sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)


def run_offline_pipeline_benchmark(data_dir: Path, runtime_config: dict, batch_sizes=(8, 16, 32)) -> dict:
    """Benchmark local mock batching and isolated Chroma writes; never calls a provider."""
    import chromadb
    from chromadb.config import Settings

    manifest, config, docs, load_failures = _frozen_corpus(Path(data_dir), runtime_config)
    all_chunks = [item for doc in docs.values() for item in doc]
    dataset_sha = manifest["dataset_sha256"]
    contract_meta = collection_contract_metadata(config)
    run_id = uuid.uuid4().hex[:12]
    base = Path(data_dir) / "split-evaluation" / "embed-benchmark" / "runs" / run_id
    base.mkdir(parents=True, exist_ok=False)
    results = []
    for batch_size in batch_sizes:
        if not isinstance(batch_size, int) or not 1 <= batch_size <= 100:
            raise ValueError("Offline benchmark batch sizes must be 1..100.")
        chroma_dir = base / f"batch-{batch_size}" / "chroma"
        chroma_dir.mkdir(parents=True)
        started_wall, started_cpu = time.perf_counter(), time.process_time()
        tracemalloc.start()
        mock_latencies, upsert_latencies = [], []
        schedule_start = time.perf_counter()
        batches = list(embedding_batches(all_chunks, batch_size))
        schedule_ms = (time.perf_counter() - schedule_start) * 1000
        mock_started = time.perf_counter()
        mock_total_ms = 0.0
        persistence_ms = 0.0
        with _PeakRssSampler() as rss:
            client = chromadb.PersistentClient(path=str(chroma_dir), settings=Settings(anonymized_telemetry=False))
            collection_started = time.perf_counter()
            collection = client.get_or_create_collection(name="embed_offline", embedding_function=None,
                metadata={"hnsw:space": "cosine", "benchmark_only": True,
                    "snapshot_id": SNAPSHOT_ID, "dataset_sha256": dataset_sha,
                    "embedding_contract_sha256": contract_meta["embedding_contract_sha256"],
                    **{key: value for key, value in contract_meta.items() if key != "embedding_contract_sha256"}})
            collection_init_ms = (time.perf_counter() - collection_started) * 1000
            validate_collection_metadata(collection.metadata, config)
            for batch in batches:
                embed_started = time.perf_counter()
                vectors = deterministic_mock_vectors([item["text"] for item in batch], "RETRIEVAL_DOCUMENT", config["dimensions"])
                generation_ms = (time.perf_counter() - embed_started) * 1000
                mock_latencies.append(generation_ms)
                validate_vectors(vectors, config["dimensions"], config["normalization"])
                mock_total_ms += generation_ms
                upsert_started = time.perf_counter()
                collection.upsert(ids=[item["id"] for item in batch], documents=[item["text"] for item in batch],
                    metadatas=[item["metadata"] for item in batch], embeddings=vectors)
                upsert_ms = (time.perf_counter() - upsert_started) * 1000
                upsert_latencies.append(upsert_ms)
                persistence_ms += upsert_ms
            orchestration_ms = (time.perf_counter() - mock_started) * 1000
            verify_started = time.perf_counter()
            stored = collection.get(include=["metadatas", "embeddings"])
            stored_ids = set(stored["ids"])
            expected_ids = {item["id"] for item in all_chunks}
            if stored_ids != expected_ids:
                raise ValueError("Offline Chroma roundtrip returned an ID set different from A0 chunks.")
            if any(meta.get("embedding_contract_sha256") != contract_meta["embedding_contract_sha256"] for meta in stored["metadatas"]):
                raise ValueError("Offline Chroma roundtrip lost or changed embedding contract metadata.")
            if len(stored["embeddings"]) != len(all_chunks) or any(len(vector) != config["dimensions"] for vector in stored["embeddings"]):
                raise ValueError("Offline Chroma roundtrip returned invalid vector dimensions.")
            verification_ms = (time.perf_counter() - verify_started) * 1000
            client.close()
        _, python_peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        disk_bytes = sum(path.stat().st_size for path in chroma_dir.rglob("*") if path.is_file())
        total_wall_ms = (time.perf_counter() - started_wall) * 1000
        total_cpu_ms = (time.process_time() - started_cpu) * 1000
        results.append({
            "batch_size": batch_size,
            "chunk_count": len(all_chunks),
            "batch_count": len(batches),
            "mock_vector_generation_ms": mock_total_ms,
            "generation_and_persistence_interleaved": True,
            "batch_orchestration_total_ms": orchestration_ms,
            "mock_vector_batch_latency_ms": {"p50": _percentile(mock_latencies, 50), "p95": _percentile(mock_latencies, 95)},
            "batch_schedule_ms": schedule_ms,
            "chroma_collection_init_ms": collection_init_ms,
            "chroma_upsert_total_ms": persistence_ms,
            "chroma_upsert_batch_latency_ms": {"p50": _percentile(upsert_latencies, 50), "p95": _percentile(upsert_latencies, 95)},
            "chroma_roundtrip_verification_ms": verification_ms,
            "end_to_end_mock_pipeline_ms": total_wall_ms,
            "process_cpu_ms": total_cpu_ms,
            "mock_chunks_per_second": len(all_chunks) / (mock_total_ms / 1000) if mock_total_ms else None,
            "mock_tokens_per_second": None,
            "provider_latency_ms": None,
            "provider_requests": 0,
            "provider_api_calls": 0,
            "peak_python_allocated_bytes": python_peak_bytes,
            "sampled_peak_process_rss_bytes": rss.peak,
            "sampled_peak_process_rss_supported": rss.peak is not None,
            "raw_vector_payload_bytes_estimate": len(all_chunks) * config["dimensions"] * 4,
            "isolated_chroma_disk_bytes": disk_bytes,
            "stored_ids_match": True,
            "contract_metadata_match": True,
            "semantic_quality_measured": False,
        })
    report = {"schema_version": 1, "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": {"snapshot_id": SNAPSHOT_ID, "dataset_sha256": dataset_sha,
            "parser_revision": PARSER_REVISION, "input_documents": 154,
            "parsed_documents": len(docs), "retained_load_failures": load_failures,
            "chunker": "A0 characters-v1", "chunk_size": 1800, "chunk_overlap": 250,
            "chunks": len(all_chunks)},
        "embedding_contract": {**config, "tokenizer": _encoding(config["embedding_model"]).name,
            "tiktoken_version": importlib.metadata.version("tiktoken")},
        "execution_environment": {"python": platform.python_version(), "platform": platform.platform(),
            "chromadb": importlib.metadata.version("chromadb")},
        "batch_runs": results,
        "limitations": ["All vectors are deterministic mocks; no semantic quality or provider performance is measured.",
            "Provider latency is N/A because paid calls were not run in this milestone.",
            "Raw vector bytes are an estimate; isolated Chroma disk bytes include its measured local files but not external OS cache.",
            "Peak process RSS is sampled when psutil is installed; Python allocation peak is measured with tracemalloc."],
        "api_calls": 0, "production_postgres_or_chroma_touched": False}
    report["run_sha256"] = _sha_json(report)
    output_dir = Path(data_dir) / "split-evaluation" / "embed-benchmark" / "runs" / run_id
    (output_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    return {"run_id": run_id, "private_result": str(output_dir / "result.json"),
        "a0_chunks": len(all_chunks), "batch_sizes": list(batch_sizes), "api_calls": 0,
        "run_sha256": report["run_sha256"]}
