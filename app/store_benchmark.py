"""Offline, isolated Chroma index-fidelity and scale evaluation.

No application database, production Chroma path, document text, or embedding API
is used here. Synthetic vectors are generated and retained only in memory.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import statistics
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


DIMENSIONS = 1536
SIZES = (1000, 2468, 5000, 10000)
TOP_K = (1, 5, 10)
QUERY_COUNT = 24
MAX_N = 10000
MIN_FREE_RAM_BYTES = 1024 ** 3
MIN_FREE_DISK_BYTES = 2 * 1024 ** 3


def _percentile(values, percentile):
    values = sorted(values)
    if not values:
        return None
    return float(values[max(0, math.ceil(percentile * len(values)) - 1)])


def _summary(values):
    return {"count": len(values), "p50": _percentile(values, .50),
        "p95": _percentile(values, .95), "p99": _percentile(values, .99),
        "mean": statistics.fmean(values) if values else None,
        "min": min(values) if values else None, "max": max(values) if values else None}


def _sha_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf8")).hexdigest()


def _available_memory_bytes():
    try:
        import psutil
        return int(psutil.virtual_memory().available)
    except ImportError:
        if os.name == "nt":
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            state = MEMORYSTATUSEX()
            state.dwLength = ctypes.sizeof(state)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
                return int(state.ullAvailPhys)
        else:
            try:
                pages = os.sysconf("SC_AVPHYS_PAGES")
                page_size = os.sysconf("SC_PAGE_SIZE")
                return int(pages * page_size)
            except (ValueError, OSError, AttributeError):
                pass
    return None


class _PeakRss:
    """Sample current process RSS; Windows has no psutil dependency here."""
    def __init__(self):
        self.peak = None
        self._stop = threading.Event()
        self._thread = None

    def _read(self):
        try:
            import psutil
            return int(psutil.Process(os.getpid()).memory_info().rss)
        except ImportError:
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
                counters = PROCESS_MEMORY_COUNTERS()
                counters.cb = ctypes.sizeof(counters)
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                psapi = ctypes.WinDLL("psapi", use_last_error=True)
                kernel32.GetCurrentProcess.restype = wintypes.HANDLE
                psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE,
                    ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD)
                psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
                handle = kernel32.GetCurrentProcess()
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    return int(counters.WorkingSetSize)
            else:
                try:
                    import resource
                    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    return int(value * (1024 if platform.system() != "Darwin" else 1))
                except (ImportError, OSError, AttributeError):
                    pass
        return None

    def __enter__(self):
        self.peak = self._read()
        def sample():
            while not self._stop.wait(.02):
                current = self._read()
                if current is not None:
                    self.peak = max(self.peak or 0, current)
        if self.peak is not None:
            self._thread = threading.Thread(target=sample, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
            current = self._read()
            if current is not None:
                self.peak = max(self.peak or 0, current)


def _synthetic_vectors(count, dimensions, seed):
    rng = np.random.default_rng(seed)
    vectors = rng.standard_normal((count, dimensions), dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors /= norms
    return vectors


def _exact_topk(query, vectors, ids, k):
    """Cosine distance over the eligible, stored-vector candidate set.

    IDs provide deterministic ordering for exact distance ties. A tolerance is
    applied only for diagnostics; the selected top-k is stable by (distance, ID).
    """
    if len(ids) == 0:
        return [], []
    candidates = np.asarray(vectors, dtype=np.float32)
    query = np.asarray(query, dtype=np.float32)
    q_norm = float(np.linalg.norm(query))
    norms = np.linalg.norm(candidates, axis=1)
    if q_norm == 0 or np.any(norms == 0):
        raise ValueError("Cosine reference requires non-zero finite vectors.")
    similarities = (candidates @ query) / (norms * q_norm)
    distances = np.clip(1.0 - similarities, 0.0, 2.0)
    order = sorted(range(len(ids)), key=lambda i: (float(distances[i]), str(ids[i])))[:min(k, len(ids))]
    return [str(ids[i]) for i in order], [float(distances[i]) for i in order]


def _distance_tie_count(distances, tolerance=1e-7):
    return sum(1 for left, right in zip(distances, distances[1:]) if abs(left - right) <= tolerance)


def _index_configs():
    # batch_size is included as a requested legacy setting because production
    # asks for it; Chroma's effective public collection config may omit it.
    return [
        {"id": "current_sync1", "label": "Production requested settings",
            "requested": {"space": "cosine", "batch_size": 1, "sync_threshold": 1,
                "max_neighbors": 16, "ef_construction": 100, "ef_search": 100}},
        {"id": "current_sync1_ef50", "label": "Production construction with search ef=50",
            "requested": {"space": "cosine", "batch_size": 1, "sync_threshold": 1,
                "max_neighbors": 16, "ef_construction": 100, "ef_search": 50}},
        {"id": "sync_default", "label": "Default sync threshold",
            "requested": {"space": "cosine", "sync_threshold": 1000,
                "max_neighbors": 16, "ef_construction": 100, "ef_search": 100}},
        {"id": "smaller_graph", "label": "Smaller HNSW graph",
            "requested": {"space": "cosine", "sync_threshold": 1000,
                "max_neighbors": 8, "ef_construction": 100, "ef_search": 100}},
    ]


def _create_collection(client, name, requested):
    configuration = {"hnsw": {key: value for key, value in requested.items()
        if key in {"space", "sync_threshold", "max_neighbors", "ef_construction", "ef_search"}}}
    return client.get_or_create_collection(name=name, embedding_function=None,
        metadata={"benchmark_only": True, "vector_source": "deterministic_synthetic"},
        configuration=configuration)


def _effective_hnsw(collection):
    config = collection.configuration or {}
    hnsw = config.get("hnsw") or {}
    return {key: hnsw.get(key) for key in ("space", "sync_threshold", "max_neighbors",
        "ef_construction", "ef_search", "batch_size", "resize_factor")}


def _collect_ids_and_vectors(collection, where=None):
    data = collection.get(where=where, include=["embeddings", "metadatas"])
    return (list(data["ids"]), np.asarray(data["embeddings"], dtype=np.float32), list(data["metadatas"]))


def _align_stored_records(expected_ids, stored_ids, vectors, metadatas):
    """Return vectors and metadata in exactly the requested stable ID order."""
    expected_ids = [str(item) for item in expected_ids]
    stored_ids = [str(item) for item in stored_ids]
    if len(stored_ids) != len(set(stored_ids)) or set(stored_ids) != set(expected_ids):
        raise RuntimeError("Stored IDs do not exactly match the expected vector ID set.")
    if len(metadatas) != len(stored_ids) or len(vectors) != len(stored_ids):
        raise RuntimeError("Stored ID, vector, and metadata counts differ.")
    index_by_id = {item: index for index, item in enumerate(stored_ids)}
    order = [index_by_id[item] for item in expected_ids]
    return (expected_ids, np.asarray(vectors, dtype=np.float32)[order],
        [dict(metadatas[index]) for index in order])


def _query_group(collection, queries, stored_ids, stored_vectors, stored_metadata, where, k=10):
    eligible_indices = [i for i, metadata in enumerate(stored_metadata)
        if where is None or all(metadata.get(key) == value for key, value in where.items())]
    expected_ids = [stored_ids[i] for i in eligible_indices]
    exact_vectors = stored_vectors[eligible_indices]
    eligible_count = len(expected_ids)
    chroma_candidate_ids = collection.get(where=where, include=[])["ids"]
    candidate_set_match = set(chroma_candidate_ids) == set(expected_ids)
    if not candidate_set_match:
        raise RuntimeError("Chroma metadata filter candidate IDs differ from the exact-reference candidate set.")
    requested_k = min(k, eligible_count)
    chroma_latencies, exact_latencies = [], []
    recall_hits, overlap_hits, distance_errors, complete, tie_cases = {k: [] for k in TOP_K}, {k: [] for k in TOP_K}, [], [], 0
    result_counts = []
    if not eligible_count:
        return {"eligible_count": 0, "queries": 0, "candidate_set_match": candidate_set_match,
            "chroma_ms": _summary([]),
            "exact_ms": _summary([]), "result_count_complete": None,
            "requested_k_less_than_eligible": {str(k): 0 for k in TOP_K},
            "index_recall_at_k": {str(k): None for k in TOP_K},
            "top_k_overlap": {str(k): None for k in TOP_K}, "distance_abs_error": None,
            "tie_cases": 0}
    for query in queries:
        start = time.perf_counter()
        result = collection.query(query_embeddings=[query], n_results=k,
            where=where, include=["metadatas", "distances"])
        chroma_latencies.append((time.perf_counter() - start) * 1000)
        found_ids = [str(item) for item in result["ids"][0]]
        found_distances = [float(item) for item in result["distances"][0]]
        result_counts.append(len(found_ids))
        complete.append(len(found_ids) == min(k, eligible_count))
        start = time.perf_counter()
        exact_ids, exact_distances = _exact_topk(query, exact_vectors, expected_ids, k)
        exact_latencies.append((time.perf_counter() - start) * 1000)
        tie_cases += _distance_tie_count(exact_distances)
        for top_k in TOP_K:
            exact_set = set(exact_ids[:top_k])
            found_set = set(found_ids[:top_k])
            denominator = min(top_k, eligible_count)
            recall_hits[top_k].append(len(exact_set & found_set) / denominator if denominator else None)
            overlap_hits[top_k].append(len(exact_set & found_set))
        # Compare every ANN-returned distance against that ID's actual stored
        # vector, not only IDs that happen to be in the exact top-k set.
        exact_by_id = {item: float(np.clip(1.0 - float(np.dot(query, exact_vectors[index]) /
            (np.linalg.norm(query) * np.linalg.norm(exact_vectors[index]))), 0.0, 2.0))
            for index, item in enumerate(expected_ids)}
        distance_errors.extend(abs(found_distances[i] - exact_by_id[item])
            for i, item in enumerate(found_ids) if item in exact_by_id)
    return {"eligible_count": eligible_count, "queries": len(queries),
        "candidate_set_match": candidate_set_match,
        "chroma_ms": _summary(chroma_latencies), "exact_ms": _summary(exact_latencies),
        "result_count_complete": all(complete), "result_count_min": min(result_counts),
        "result_count_max": max(result_counts),
        "requested_k_less_than_eligible": {str(k): int(eligible_count < k) for k in TOP_K},
        "index_recall_at_k": {str(k): statistics.fmean(v) if v else None for k, v in recall_hits.items()},
        "top_k_overlap": {str(k): statistics.fmean(v) if v else None for k, v in overlap_hits.items()},
        "distance_abs_error": _summary(distance_errors), "tie_cases": tie_cases}


def _directory_size(root):
    return sum(item.stat().st_size for item in root.rglob("*") if item.is_file())


def _run_one(chromadb, scratch_root, size, dimensions, repeat, config, seed, query_count):
    from chromadb.config import Settings

    run_root = Path(tempfile.mkdtemp(prefix="store-bench-", dir=scratch_root))
    chroma_path = run_root / "chroma"
    chroma_path.mkdir()
    vectors = _synthetic_vectors(size, dimensions, seed)
    ids = [f"v-{i:06d}" for i in range(size)]
    versions = np.zeros(size, dtype=np.int8)
    versions[::2] = 1
    versions[:min(3, size)] = 2
    metadata = [{"version_id": "small" if versions[i] == 2 else "eligible" if versions[i] == 1 else "other",
        "segment": "A" if i % 4 < 2 else "B"} for i in range(size)]
    queries = _synthetic_vectors(query_count, dimensions, seed + 100000)
    process_started = time.perf_counter()
    build_started = time.perf_counter()
    with _PeakRss() as rss:
        client = chromadb.PersistentClient(path=str(chroma_path), settings=Settings(anonymized_telemetry=False))
        collection = _create_collection(client, f"bench_{uuid.uuid4().hex[:12]}", config["requested"])
        collection_init_ms = (time.perf_counter() - build_started) * 1000
        upsert_ms = []
        upserted = 0
        # Bound the temporary Python object graph for each Chroma API operation.
        for start in range(0, size, 128):
            stop = min(size, start + 128)
            batch_started = time.perf_counter()
            collection.upsert(ids=ids[start:stop], embeddings=vectors[start:stop],
                metadatas=metadata[start:stop])
            upsert_ms.append((time.perf_counter() - batch_started) * 1000)
            upserted += stop - start
        build_total_ms = (time.perf_counter() - build_started) * 1000
        effective = _effective_hnsw(collection)
        stored_ids, stored_vectors, stored_metadata = _collect_ids_and_vectors(collection)
        if set(stored_ids) != set(ids) or stored_vectors.shape != vectors.shape:
            raise RuntimeError("Chroma synthetic vector roundtrip failed ID/dimension integrity check.")
        # Exact comparison consumes the exact embeddings returned by Chroma.
        stored_ids, stored_vectors, stored_metadata = _align_stored_records(
            ids, stored_ids, stored_vectors, stored_metadata)
        # Reopen from the same private path and verify persistence in a fresh client.
        client.close()
        collection_name = collection.name
        reopen_started = time.perf_counter()
        reopened = chromadb.PersistentClient(path=str(chroma_path), settings=Settings(anonymized_telemetry=False))
        reopened_collection = reopened.get_collection(collection_name, embedding_function=None)
        reopen_ms = (time.perf_counter() - reopen_started) * 1000
        # First query immediately after a fresh client is recorded separately.
        cold_started = time.perf_counter()
        cold_result = reopened_collection.query(query_embeddings=[queries[0]], n_results=10,
            include=["metadatas", "distances"])
        cold_query_ms = (time.perf_counter() - cold_started) * 1000
        reopened_ids, reopened_vectors, reopened_metadata = _collect_ids_and_vectors(reopened_collection)
        if set(reopened_ids) != set(ids) or reopened_vectors.shape != vectors.shape:
            raise RuntimeError("Fresh-process-style Chroma reopen did not preserve the vector set.")
        # ef_search is controlled at collection creation. Runtime modify() is
        # excluded because an earlier probe changed reported config but left
        # measured recall unchanged.
        query_rows = {}
        for name, where in (("unfiltered", None), ("filtered_segment_a", {"segment": "A"}),
            ("small_candidate_set", {"version_id": "small"})):
            reopened_collection.query(query_embeddings=[queries[0]], n_results=10,
                where=where, include=["metadatas", "distances"])
            query_rows[name] = _query_group(reopened_collection, queries, reopened_ids,
                reopened_vectors, reopened_metadata, where, 10)
        search_settings = [{"ef_search_requested_at_collection_creation": config["requested"].get("ef_search"),
            "ef_search_effective": _effective_hnsw(reopened_collection).get("ef_search"),
            "warmup_queries_excluded_per_filter": 1, "groups": query_rows}]
        reopen_smoke = reopened_collection.query(query_embeddings=[queries[0]], n_results=5,
            where={"version_id": "eligible"}, include=["metadatas", "distances"])
        reopened.close()
        disk_bytes = _directory_size(chroma_path)
        total_process_ms = (time.perf_counter() - process_started) * 1000
        # Chroma Collection objects retain the system connection. Drop references
        # before removing the trial directory (required on Windows file locking).
        del reopened_collection, reopened, collection, client
        import gc
        gc.collect()
    result = {"n": size, "dimensions": dimensions, "repeat": repeat,
        "configuration_id": config["id"], "requested_configuration": config["requested"],
        "effective_configuration": effective, "collection_init_ms": collection_init_ms,
        "index_build_ms": build_total_ms, "upsert_ms": _summary(upsert_ms),
        "upsert_total_ms": sum(upsert_ms), "upsert_items": upserted,
        "upsert_items_per_second": upserted / (sum(upsert_ms) / 1000) if sum(upsert_ms) else None,
        "reopen_ms": reopen_ms, "reopen_query_result_count": len(reopen_smoke["ids"][0]),
        "fresh_client_first_query_ms": cold_query_ms,
        "fresh_client_first_query_result_count": len(cold_result["ids"][0]),
        "warm_search_note": "per-filter warm-up query excluded before P50/P95/P99; OS file cache may remain warm",
        "persistence_verified": True, "isolated_disk_bytes": disk_bytes,
        "raw_vector_payload_bytes": int(size * dimensions * 4),
        "synthetic_vector_sha256": hashlib.sha256(vectors.tobytes()).hexdigest(),
        "synthetic_query_sha256": hashlib.sha256(queries.tobytes()).hexdigest(),
        "peak_process_rss_bytes": rss.peak, "peak_rss_scope": "whole benchmark process high-water sampled; includes Python/Chroma runtime",
        "total_trial_wall_ms": total_process_ms, "search_settings": search_settings,
        "vector_source": "deterministic_synthetic", "vector_payload_written_to_report": False}
    shutil.rmtree(run_root)
    return result


def run_store_benchmark(data_dir: Path, sizes=SIZES, repeats=2, dimensions=DIMENSIONS,
        query_count=QUERY_COUNT, seed=6102026) -> dict:
    """Run bounded synthetic index benchmarks; all Chroma directories are temporary."""
    import chromadb
    from chromadb.config import Settings

    sizes = tuple(int(value) for value in sizes)
    if not sizes or any(value < 3 or value > MAX_N for value in sizes):
        raise ValueError(f"N must be between 3 and {MAX_N}.")
    if repeats not in (1, 2, 3) or not 1 <= query_count <= 64 or dimensions != DIMENSIONS:
        raise ValueError("Use repeats 1..3, 1..64 queries, and the required 1536 dimensions.")
    free_ram = _available_memory_bytes()
    free_disk = shutil.disk_usage(data_dir).free
    if free_ram is not None and free_ram < MIN_FREE_RAM_BYTES:
        raise RuntimeError("Store benchmark stopped safely: less than 1 GiB available RAM.")
    if free_disk < MIN_FREE_DISK_BYTES:
        raise RuntimeError("Store benchmark stopped safely: less than 2 GiB free disk.")
    scratch_root = Path(data_dir).resolve() / "store-evaluation" / "scratch"
    scratch_root.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    results = []
    configurations = _index_configs()
    # Smaller graph is a bounded secondary comparison at corpus-scale and max scale.
    for size in sizes:
        allowed = (("current_sync1", "current_sync1_ef50", "sync_default", "smaller_graph")
            if size in (2468, 10000) else ("current_sync1", "current_sync1_ef50", "sync_default"))
        selected = [config for config in configurations if config["id"] in allowed]
        for config in selected:
            for repeat in range(repeats):
                results.append(_run_one(chromadb, scratch_root, size, dimensions, repeat + 1,
                    config, seed + size, query_count))
    report = {"schema_version": 1, "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "experiment": "Chapter 6 synthetic exact-vs-ANN and Chroma scale benchmark",
        "data_classification": "synthetic vectors only; no document text or private vector payload",
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
            "chromadb": importlib.metadata.version("chromadb"), "numpy": np.__version__,
            "cpu_count": os.cpu_count(), "free_ram_before_bytes": free_ram,
            "free_disk_before_bytes": free_disk},
        "design": {"sizes": list(sizes), "dimensions": dimensions, "repeats": repeats,
            "queries_per_trial": query_count, "seed": seed, "top_k": list(TOP_K),
            "exact_reference": "NumPy exhaustive cosine over embeddings read back from that same Chroma collection",
            "normalization": "float32 L2 unit vectors; exact distances recomputed from Chroma-returned vectors",
            "filter_groups": ["unfiltered", "filtered_segment_a", "small_candidate_set"],
            "query_timing_scope": "Chroma search-only with precomputed vectors; excludes embedding, PostgreSQL, reranking, generation",
            "batch_size_note": "requested production batch_size=1 is not exposed in Chroma 1.5.9 effective collection configuration; not treated as a confirmed active setting",
            "resource_guard": {"max_n": MAX_N, "min_free_ram_bytes": MIN_FREE_RAM_BYTES,
                "min_free_disk_bytes": MIN_FREE_DISK_BYTES}},
        "runs": results, "real_vector_snapshot": {"available": False,
            "reason": "Existing Embed smoke artifacts are plan-only or explicitly local-mock; no verified real OpenAI vector snapshot was found. Production Chroma was not opened."},
        "api_calls": 0, "postgres_access": False, "production_chroma_access": False,
        "semantic_or_human_relevance_measured": False,
        "limitations": ["Synthetic nearest-neighbor fidelity is not human relevance or Mongolian retrieval accuracy.",
            "Peak RSS is process-wide high-water during the trial and includes interpreter/Chroma runtime.",
            "Per-query exact cosine time is measured in NumPy; it is a reference, not an optimized production exact index.",
            "Chroma configuration values are reported as effective only when returned by collection.configuration."]}
    report["report_sha256"] = _sha_json(report)
    output_dir = Path(data_dir).resolve() / "store-evaluation" / "runs" / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    output_path = output_dir / "result.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    return {"run_id": run_id, "path": str(output_path), "report_sha256": report["report_sha256"],
        "runs": len(results), "vector_count_total": sum(row["n"] for row in results),
        "chromadb": report["environment"]["chromadb"], "dimensions": dimensions,
        "api_calls": 0, "production_data_touched": False}


def _fidelity_trial_in_directory(chromadb, run_root, vectors, queries, repeat, ef_search, seed):
    """Build one isolated graph from frozen vectors and capture query-level misses."""
    from chromadb.config import Settings

    chroma_path = run_root / "chroma"
    chroma_path.mkdir()
    size, dimensions = vectors.shape
    ids = [f"v-{i:06d}" for i in range(size)]
    metadata = [{"segment": "A" if i % 4 < 2 else "B",
        "version_id": "eligible" if i % 2 == 0 else "other"} for i in range(size)]
    requested = {"space": "cosine", "sync_threshold": 1, "max_neighbors": 16,
        "ef_construction": 100, "ef_search": int(ef_search)}
    with _PeakRss() as rss:
        client = chromadb.PersistentClient(path=str(chroma_path),
            settings=Settings(anonymized_telemetry=False))
        collection = _create_collection(client, f"fidelity_{uuid.uuid4().hex[:12]}", requested)
        build_started = time.perf_counter()
        for start in range(0, size, 128):
            stop = min(size, start + 128)
            collection.upsert(ids=ids[start:stop], embeddings=vectors[start:stop],
                metadatas=metadata[start:stop])
        build_elapsed_ms = (time.perf_counter() - build_started) * 1000
        stored_ids, stored_vectors, stored_metadata = _collect_ids_and_vectors(collection)
        if set(stored_ids) != set(ids) or stored_vectors.shape != vectors.shape:
            raise RuntimeError("Fidelity trial failed stored-vector identity/count validation.")
        stored_ids, stored_vectors, stored_metadata = _align_stored_records(
            ids, stored_ids, stored_vectors, stored_metadata)
        stored_vector_sha256 = hashlib.sha256(stored_vectors.tobytes()).hexdigest()
        stored_vector_max_abs_roundtrip_error = float(np.max(np.abs(stored_vectors - vectors)))
        effective = _effective_hnsw(collection)
        if effective.get("ef_search") != int(ef_search):
            raise RuntimeError(f"Requested ef_search={ef_search} was not effective: {effective!r}")
        if any(effective.get(key) != value for key, value in
            (("space", "cosine"), ("sync_threshold", 1), ("max_neighbors", 16),
                ("ef_construction", 100))):
            raise RuntimeError(f"Construction configuration drifted during ef_search ablation: {effective!r}")

        groups = {}
        failure_examples = {}
        warm_probe_ids = {}
        for group_name, where in (("unfiltered", None),
                ("filtered_segment_a", {"segment": "A"})):
            collection.query(query_embeddings=[queries[0]], n_results=10,
                where=where, include=["metadatas", "distances"])
            metrics = _query_group(collection, queries, stored_ids, stored_vectors,
                stored_metadata, where, 10)
            groups[group_name] = metrics
            warm_probe = collection.query(query_embeddings=[queries[0]], n_results=10,
                where=where, include=["distances"])
            warm_probe_ids[group_name] = [str(item) for item in warm_probe["ids"][0]]
            eligible_indices = [i for i, row in enumerate(stored_metadata)
                if where is None or all(row.get(key) == value for key, value in where.items())]
            candidate_ids = [stored_ids[i] for i in eligible_indices]
            candidate_vectors = stored_vectors[eligible_indices]
            candidate_lookup = {item: i for i, item in enumerate(candidate_ids)}
            failures = []
            for query_index, query in enumerate(queries):
                exact_ids, exact_distances = _exact_topk(query, candidate_vectors,
                    candidate_ids, 11)
                result = collection.query(query_embeddings=[query], n_results=10,
                    where=where, include=["distances"])
                ann_ids = [str(item) for item in result["ids"][0]]
                ann_distances = [float(item) for item in result["distances"][0]]
                missing = [item for item in exact_ids[:10] if item not in set(ann_ids)]
                if missing:
                    first_missing = missing[0]
                    rank = exact_ids.index(first_missing)
                    candidate = candidate_vectors[candidate_lookup[first_missing]]
                    exact_distance = float(np.clip(1.0 - float(np.dot(query, candidate) /
                        (np.linalg.norm(query) * np.linalg.norm(candidate))), 0.0, 2.0))
                    # Margin is between exact ranks 10 and 11; it describes how
                    # close the top-10 boundary is, not why a particular miss occurred.
                    boundary_margin = (exact_distances[10] - exact_distances[9]
                        if len(exact_distances) > 10 else None)
                    failures.append({"query_index": query_index,
                        "exact_topk_ids": exact_ids[:10],
                        "exact_topk_distances": exact_distances[:10],
                        "chroma_topk_ids": ann_ids,
                        "chroma_topk_distances": ann_distances,
                        "first_missed_exact_neighbor_id": first_missing,
                        "first_missed_exact_neighbor_rank_1_based": rank + 1,
                        "first_missed_exact_neighbor_distance": exact_distance,
                        "missed_neighbor_vs_ann_top1_distance_gap": (
                            float(ann_distances[0] - exact_distance) if ann_distances else None),
                        "exact_k10_boundary_margin": boundary_margin,
                        "eligible_candidate_count": len(candidate_ids),
                        "filter": where})
            failure_examples[group_name] = failures[:10]
        collection_name = collection.name
        client.close()
        del collection, client
        import gc
        gc.collect()

        # Reopen this exact persisted collection and compare the same first
        # query before treating persistence as proof of fidelity.
        reopen_started = time.perf_counter()
        reopened = chromadb.PersistentClient(path=str(chroma_path),
            settings=Settings(anonymized_telemetry=False))
        reopened_collection = reopened.get_collection(collection_name, embedding_function=None)
        reopen_client_ms = (time.perf_counter() - reopen_started) * 1000
        reopened_ids, reopened_vectors, reopened_metadata = _collect_ids_and_vectors(reopened_collection)
        reopened_ids, reopened_vectors, reopened_metadata = _align_stored_records(
            ids, reopened_ids, reopened_vectors, reopened_metadata)
        if hashlib.sha256(reopened_vectors.tobytes()).hexdigest() != stored_vector_sha256:
            raise RuntimeError("Stored vectors changed after Chroma persistence/reopen.")
        reopened_checks = {}
        for group_name, where in (("unfiltered", None),
                ("filtered_segment_a", {"segment": "A"})):
            started = time.perf_counter()
            probe = reopened_collection.query(query_embeddings=[queries[0]], n_results=10,
                where=where, include=["distances"])
            first_query_ms = (time.perf_counter() - started) * 1000
            probe_ids = [str(item) for item in probe["ids"][0]]
            eligible_indices = [i for i, row in enumerate(reopened_metadata)
                if where is None or all(row.get(key) == value for key, value in where.items())]
            expected_ids = [reopened_ids[i] for i in eligible_indices]
            exact_ids, _ = _exact_topk(queries[0], reopened_vectors[eligible_indices],
                expected_ids, 10)
            reopened_checks[group_name] = {"first_query_after_reopen_ms": first_query_ms,
                "same_top10_order_as_warm_collection": probe_ids == warm_probe_ids[group_name],
                "same_top10_set_as_warm_collection": set(probe_ids) == set(warm_probe_ids[group_name]),
                "exact_index_recall_at_10": (len(set(probe_ids) & set(exact_ids)) /
                    min(10, len(expected_ids))),
                "candidate_count": len(expected_ids),
                "result_count_complete": len(probe_ids) == min(10, len(expected_ids))}
        reopened.close()
        del reopened_collection, reopened
        gc.collect()
        peak_rss = rss.peak
        disk_bytes = _directory_size(chroma_path)
    return {"n": size, "dimensions": dimensions, "repeat": repeat,
        "seed": seed, "ef_search_requested": int(ef_search),
        "effective_configuration": effective, "build_and_upsert_ms": build_elapsed_ms,
        "isolated_disk_bytes": disk_bytes, "peak_process_rss_bytes": peak_rss,
        "reopen_client_ms": reopen_client_ms, "reopened_first_query_checks": reopened_checks,
        "synthetic_vector_sha256": hashlib.sha256(vectors.tobytes()).hexdigest(),
        "stored_vector_sha256": stored_vector_sha256,
        "stored_vector_max_abs_roundtrip_error": stored_vector_max_abs_roundtrip_error,
        "synthetic_query_sha256": hashlib.sha256(queries.tobytes()).hexdigest(),
        "groups": groups, "failure_examples": failure_examples}


def _fidelity_trial(chromadb, scratch_root, vectors, queries, repeat, ef_search, seed):
    run_root = Path(tempfile.mkdtemp(prefix="ann-fidelity-", dir=scratch_root))
    try:
        return _fidelity_trial_in_directory(chromadb, run_root, vectors, queries,
            repeat, ef_search, seed)
    finally:
        # Leave no synthetic Chroma files behind, even when a parity/config
        # assertion fails partway through a trial.
        shutil.rmtree(run_root, ignore_errors=True)


def run_ann_fidelity_investigation(data_dir: Path, repeats=3, query_count=32,
        ef_search_values=(50, 100, 200, 400), seed=6102026, size=10000) -> dict:
    """Controlled N=10k ef_search ablation; same vectors, queries, IDs and order."""
    import chromadb

    ef_search_values = tuple(int(value) for value in ef_search_values)
    if size != 10000 or repeats not in (1, 2, 3) or not 8 <= query_count <= 64:
        raise ValueError("Use N=10000, repeats 1..3, and 8..64 queries for fidelity investigation.")
    if len(ef_search_values) < 2 or len(set(ef_search_values)) != len(ef_search_values) or any(
            value < 10 or value > 1000 for value in ef_search_values):
        raise ValueError("ef_search values must be unique integers between 10 and 1000 (at least two).")
    free_ram = _available_memory_bytes()
    free_disk = shutil.disk_usage(data_dir).free
    if free_ram is not None and free_ram < MIN_FREE_RAM_BYTES:
        raise RuntimeError("ANN fidelity investigation stopped safely: less than 1 GiB available RAM.")
    if free_disk < MIN_FREE_DISK_BYTES:
        raise RuntimeError("ANN fidelity investigation stopped safely: less than 2 GiB free disk.")
    scratch_root = Path(data_dir).resolve() / "store-evaluation" / "scratch"
    scratch_root.mkdir(parents=True, exist_ok=True)
    vectors = _synthetic_vectors(size, DIMENSIONS, seed + size)
    queries = _synthetic_vectors(query_count, DIMENSIONS, seed + size + 100000)
    results = []
    for ef_search in ef_search_values:
        for repeat in range(1, repeats + 1):
            results.append(_fidelity_trial(chromadb, scratch_root, vectors, queries,
                repeat, ef_search, seed))
    stored_hashes = {row["stored_vector_sha256"] for row in results}
    if len(stored_hashes) != 1:
        raise RuntimeError("Chroma stored-vector hashes differ across ef_search configurations/trials.")

    summaries = {}
    for ef_search in ef_search_values:
        trials = [row for row in results if row["ef_search_requested"] == ef_search]
        group_summaries = {}
        for group_name in ("unfiltered", "filtered_segment_a"):
            group_rows = [row["groups"][group_name] for row in trials]
            group_summaries[group_name] = {
                "eligible_count": group_rows[0]["eligible_count"],
                "index_recall_at_k_mean_across_trials": {
                    str(k): statistics.fmean(row["index_recall_at_k"][str(k)] for row in group_rows)
                    for k in TOP_K},
                "search_p50_ms_mean_across_trial_p50": statistics.fmean(
                    row["chroma_ms"]["p50"] for row in group_rows),
                "search_p95_ms_mean_across_trial_p95": statistics.fmean(
                    row["chroma_ms"]["p95"] for row in group_rows),
                "search_p99_ms_mean_across_trial_p99": statistics.fmean(
                    row["chroma_ms"]["p99"] for row in group_rows),
                "candidate_set_match_all": all(row["candidate_set_match"] for row in group_rows),
                "result_count_complete_all": all(row["result_count_complete"] for row in group_rows),
                "distance_abs_error_max": max(row["distance_abs_error"]["max"]
                    for row in group_rows if row["distance_abs_error"]["max"] is not None),
                "reopened_first_query_ms_mean": statistics.fmean(
                    row["reopened_first_query_checks"][group_name]["first_query_after_reopen_ms"]
                    for row in trials),
                "warm_vs_reopened_top10_set_identical_all": all(
                    row["reopened_first_query_checks"][group_name]["same_top10_set_as_warm_collection"]
                    for row in trials),
                "reopened_result_count_complete_all": all(
                    row["reopened_first_query_checks"][group_name]["result_count_complete"]
                    for row in trials),
                "reopened_first_query_index_recall_at_10_mean": statistics.fmean(
                    row["reopened_first_query_checks"][group_name]["exact_index_recall_at_10"]
                    for row in trials),
            }
        summaries[str(ef_search)] = {"groups": group_summaries,
            "build_and_upsert_ms_mean": statistics.fmean(row["build_and_upsert_ms"] for row in trials),
            "build_and_upsert_ms_range": [min(row["build_and_upsert_ms"] for row in trials),
                max(row["build_and_upsert_ms"] for row in trials)],
            "disk_bytes_mean": statistics.fmean(row["isolated_disk_bytes"] for row in trials),
            "peak_process_rss_bytes_max": max(row["peak_process_rss_bytes"] or 0 for row in trials)}

    report = {"schema_version": 1, "investigation": "Phase 4.1 controlled ANN index-fidelity ef_search ablation",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_classification": "synthetic vectors and queries only; no private corpus or API data",
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
            "chromadb": importlib.metadata.version("chromadb"), "numpy": np.__version__,
            "cpu_count": os.cpu_count(), "free_ram_before_bytes": free_ram,
            "free_disk_before_bytes": free_disk},
        "design": {"n": size, "dimensions": DIMENSIONS, "repeats": repeats,
            "queries_per_trial": query_count, "seed": seed,
            "vector_generation_seed": seed + size,
            "query_generation_seed": seed + size + 100000,
            "vector_and_query_hash_identical_across_configurations": True,
            "chroma_stored_vectors_identical_across_configurations_and_repeats": True,
            "stored_vector_max_abs_roundtrip_error": max(
                row["stored_vector_max_abs_roundtrip_error"] for row in results),
            "insertion_order": "stable v-000000 through v-009999",
            "fixed_construction": {"space": "cosine", "sync_threshold": 1,
                "max_neighbors": 16, "ef_construction": 100},
            "search_values": list(ef_search_values), "top_k": list(TOP_K),
            "groups": ["unfiltered", "filtered_segment_a"],
            "latency_scope": "Chroma query only, precomputed float32 vectors; per-trial warm-up query excluded",
            "reopen_check": "same persisted collection reopened in a fresh PersistentClient; first query per filter timed separately and its top-10 compared to the pre-close warm collection",
            "exact_reference": "same Chroma-returned stored vectors and exact metadata eligible candidate IDs; NumPy exhaustive cosine; stable (distance, ID) order"},
        "summaries": summaries, "trials": results,
        "real_vector_snapshot": {"available": False,
            "reason": "Prior Phase 4 audit found only plan-only/local-mock artifacts; production Chroma was not inspected."},
        "api_calls": 0, "postgres_access": False, "production_chroma_access": False,
        "human_relevance_measured": False,
        "limitations": ["Synthetic isotropic vectors do not represent OpenAI document/query embeddings or human relevance.",
            "P95/P99 summarize small query samples; repeat counts are bounded to three.",
            "Failure examples use generated vector IDs only and contain no document text.",
            "Peak RSS is whole-process and includes Python/Chroma runtime."]}
    report["report_sha256"] = _sha_json(report)
    run_id = uuid.uuid4().hex[:12]
    output_dir = Path(data_dir).resolve() / "store-evaluation" / "ann-fidelity" / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    output_path = output_dir / "result.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    return {"run_id": run_id, "path": str(output_path), "report_sha256": report["report_sha256"],
        "chromadb": report["environment"]["chromadb"], "n": size, "dimensions": DIMENSIONS,
        "configurations": len(ef_search_values), "trials": len(results), "queries_per_trial": query_count,
        "api_calls": 0, "production_data_touched": False}


def load_store_report(path: Path) -> dict:
    report = json.loads(Path(path).read_text(encoding="utf8"))
    expected_hash = report.get("report_sha256")
    actual_hash = _sha_json({key: value for key, value in report.items() if key != "report_sha256"})
    if expected_hash != actual_hash:
        raise ValueError("Store benchmark report SHA-256 validation failed.")
    return report
