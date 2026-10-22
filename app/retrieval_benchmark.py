"""Offline Phase 5 retrieval mechanics benchmark over frozen A0 text.

The BM25 corpus is real frozen parser output. Dense vectors and query vectors
are deterministic random controls; they measure mechanics only, never quality.
No application database, production Chroma path, embedding API, or query text is
written to the report artifact.
"""
from __future__ import annotations

import csv
from collections import Counter
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import statistics
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from flask import current_app

from .services.chunking import chunk
from .services import rag, sparse
from .services.language import query_candidates
from .services.retrieval import validate_candidate_provenance
from .services.storage import fingerprint, write_json


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ID = "real-corpus-15b24382-v1"
SNAPSHOT_ROOT = ROOT / "instance/split-evaluation" / SNAPSHOT_ID
ANNOTATION_PATH = ROOT / "instance/split-evaluation/datasets/ground-truth-gt-v1.json"
SOURCE_MAP_PATH = ROOT / "instance/load-evaluation/private-source-map.csv"
EXPECTED_CORPUS_SHA256 = "15b24382aeba13e943382370f5a4bfb4ede50b4033805006bad692d23bf3d56a"
DIMENSIONS = 1536
DENSE_K = SPARSE_K = 15
ITERATIONS = 5


def _bm25_reference(query: str, versions: list, k: int) -> list:
    """Uncached reference calculation preserving the existing BM25 formula."""
    documents = []
    for version_id, _title, _filename, document_id in versions:
        records = sparse.load_index(version_id, require_manifest=True)
        documents.extend(record for record in records
            if record["metadata"].get("document_id") == document_id)
    terms = sparse.tokenize(query)
    documents.sort(key=lambda item: item["id"])
    counters = [Counter(item["terms"]) for item in documents]
    lengths = [sum(count.values()) for count in counters]
    average = sum(lengths) / len(lengths) or 1
    document_frequency = Counter(term for count in counters for term in count)
    scored = []
    for document, counts, length in zip(documents, counters, lengths):
        score = 0.0
        for term in terms:
            tf = counts[term]
            if tf:
                idf = math.log(1 + (len(documents) - document_frequency[term] + .5) /
                    (document_frequency[term] + .5))
                score += idf * tf * 2.5 / (tf + 1.5 * (1 - .75 + .75 * length / average))
        if score:
            scored.append((document["id"], score))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[:k]


def run_bm25_investigation(output_root: Path | None = None, iterations: int = ITERATIONS) -> dict:
    """Profile BM25 initialization and warm searches on the frozen A0 corpus."""
    if not 1 <= iterations <= 20:
        raise ValueError("iterations must be between 1 and 20")
    output_root = Path(output_root or ROOT / "instance/retrieval-evaluation")
    output_root.mkdir(parents=True, exist_ok=True)
    snapshot, documents = _load_corpus()
    annotation_dataset, queries = _load_queries()
    run_id = uuid.uuid4().hex[:12]
    run_root = output_root / "bm25" / run_id
    data_root = run_root / "scratch"
    data_root.mkdir(parents=True, exist_ok=False)
    original_data_dir = current_app.config["DATA_DIR"]
    version_rows, versions = [], []
    build_start, build_cpu = time.perf_counter(), time.process_time()
    rss_before = _rss_bytes()
    sparse._cache.clear()
    sparse._compiled_cache.clear()
    try:
        current_app.config["DATA_DIR"] = data_root
        for document in documents:
            version = SimpleNamespace(id=document["version_id"], title=document["title_proxy"],
                filename=document["filename"])
            manifest = [{"id": item["id"], "metadata": item["metadata"]} for item in document["chunks"]]
            write_json(f"manifests/{version.id}.json", manifest)
            sparse.write_index(version.id, document["chunks"], version.title, version.filename)
            version_rows.append(version)
            versions.append((version.id, version.title, version.filename, document["doc_id"]))
        index_build = {"wall_ms": (time.perf_counter() - build_start) * 1000,
            "cpu_ms": (time.process_time() - build_cpu) * 1000}

        first_profile = {}
        cold_wall, cold_cpu = time.perf_counter(), time.process_time()
        cold_hits, missing = sparse.search(queries[0], versions, SPARSE_K, profile=first_profile)
        cold = {"wall_ms": (time.perf_counter() - cold_wall) * 1000,
            "cpu_ms": (time.process_time() - cold_cpu) * 1000, "stages": first_profile,
            "missing_versions": len(missing), "candidate_count": len(cold_hits)}

        # Warm every distinct query signature before collecting comparable runs.
        for query in queries:
            sparse.search(query, versions, SPARSE_K)
        samples = {key: [] for key in ("total_search_ms", "cpu_ms", "index_load_validation_ms",
            "eligibility_filter_ms", "query_tokenization_ms", "corpus_stats_key_ms",
            "corpus_stats_build_ms", "candidate_scoring_ms", "final_ranking_topk_ms")}
        cache_counts = {"index_hits": 0, "index_misses": 0,
            "corpus_stats_hits": 0, "corpus_stats_misses": 0}
        equivalence = []
        for _iteration in range(iterations):
            for query in queries:
                profile = {}
                start_wall, start_cpu = time.perf_counter(), time.process_time()
                hits, missing = sparse.search(query, versions, SPARSE_K, profile=profile)
                wall_ms, cpu_ms = (time.perf_counter() - start_wall) * 1000, (time.process_time() - start_cpu) * 1000
                if missing:
                    raise ValueError("Frozen BM25 baseline has missing version indexes.")
                reference = _bm25_reference(query, versions, SPARSE_K)
                optimized = [(hit["id"], hit["sparse_score"]) for hit in hits]
                equivalent = (len(optimized) == len(reference) and all(
                    left[0] == right[0] and math.isclose(left[1], right[1], rel_tol=0, abs_tol=1e-12)
                    for left, right in zip(optimized, reference)))
                equivalence.append(equivalent)
                samples["total_search_ms"].append(wall_ms)
                samples["cpu_ms"].append(cpu_ms)
                for key in samples.keys() - {"total_search_ms", "cpu_ms"}:
                    samples[key].append(float(profile.get(key, 0)))
                cache_counts["index_hits"] += profile.get("index_cache_hits", 0)
                cache_counts["index_misses"] += profile.get("index_cache_misses", 0)
                cache_counts["corpus_stats_hits"] += profile.get("corpus_stats_cache_hits", 0)
                cache_counts["corpus_stats_misses"] += profile.get("corpus_stats_cache_misses", 0)

        result = {"schema_version": 1, "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_id": SNAPSHOT_ID, "dataset_sha256": snapshot["dataset_sha256"],
            "annotation_dataset_version": annotation_dataset["dataset_version"],
            "annotation_dataset_sha256": annotation_dataset["dataset_sha256"],
            "document_count": len(documents), "chunk_count": sum(len(item["chunks"]) for item in documents),
            "query_count": len(queries), "query_hashes": [hashlib.sha256(q.encode("utf8")).hexdigest() for q in queries],
            "configuration": {"sparse_top_k": SPARSE_K, "iterations_per_query": iterations,
                "warmups_per_query": 1, "bm25_k1": 1.5, "bm25_b": .75,
                "cache_limit": sparse._CACHE_LIMIT, "eligibility_filter": "none; same all-version corpus as R6"},
            "index_construction": index_build,
            "cold_application_cache_initialization": cold,
            "warm_search": {key: _stats(value) for key, value in samples.items()},
            "warm_search_variation": {key: _variation(value) for key, value in samples.items()},
            "cache_counts_across_warm_queries": cache_counts,
            "ranking_equivalence": {"reference": "uncached implementation of existing BM25 formula",
                "comparisons": len(equivalence), "exact_order_and_score_match": all(equivalence),
                "score_absolute_tolerance": 1e-12},
            "memory": {"process_peak_working_set_before_bytes": rss_before,
                "process_peak_working_set_after_bytes": _rss_bytes()},
            "environment": {"os": platform.platform(), "python": platform.python_version(),
                "machine": platform.machine(), "processor": platform.processor() or None,
                "logical_cpu_count": os.cpu_count()},
            "database_access": False, "provider_api_calls": 0,
            "limitations": ["Cold means empty Python application caches; OS filesystem cache is not flushed.",
                "Database active-version query latency is not included; BM25 search itself performs no database calls.",
                "CPU time is process-wide and sampled per synchronous benchmark call."]}
        result_path = run_root / "result.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
        result["private_result_path"] = str(result_path)
        result["private_result_sha256"] = _sha(result_path)
        return result
    finally:
        current_app.config["DATA_DIR"] = original_data_dir


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _percentile(values: list[float], p: float):
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(p * len(ordered)) - 1)]) if ordered else None


def _stats(values: list[float]) -> dict:
    return {"count": len(values), "p50_ms": _percentile(values, .50),
        "p95_ms": _percentile(values, .95), "p99_ms": _percentile(values, .99),
        "mean_ms": statistics.fmean(values) if values else None}


def _variation(values: list[float]) -> dict:
    mean = statistics.fmean(values) if values else None
    deviation = statistics.pstdev(values) if len(values) > 1 else 0.0 if values else None
    return {"min_ms": min(values) if values else None, "max_ms": max(values) if values else None,
        "population_stdev_ms": deviation,
        "coefficient_of_variation_percent": deviation / mean * 100 if mean else None}


def _vector(seed_text: str, seed: int) -> np.ndarray:
    key = hashlib.sha256(f"{seed}:{seed_text}".encode("utf8")).digest()
    rng = np.random.default_rng(int.from_bytes(key[:8], "little"))
    value = rng.standard_normal(DIMENSIONS).astype(np.float32)
    value /= np.linalg.norm(value)
    return value


def _rss_bytes() -> int | None:
    if os.name == "nt":
        try:
            from .split_benchmark import _rss_bytes as windows_process_peak
            return windows_process_peak()
        except Exception:
            return None
    try:
        import psutil
        return int(psutil.Process(os.getpid()).memory_info().rss)
    except ImportError:
        return None


class _RssSampler:
    def __init__(self):
        self.peak = _rss_bytes()
        self.stop_event = threading.Event()
        self.thread = None

    def __enter__(self):
        def sample():
            while not self.stop_event.wait(.02):
                value = _rss_bytes()
                if value is not None:
                    self.peak = max(self.peak or value, value)
        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop_event.set()
        self.thread.join()
        value = _rss_bytes()
        if value is not None:
            self.peak = max(self.peak or value, value)


def _load_corpus():
    manifest_path = SNAPSHOT_ROOT / "manifest.json"
    snapshot = json.loads(manifest_path.read_text(encoding="utf8"))
    if (snapshot.get("snapshot_id") != SNAPSHOT_ID or
            snapshot.get("dataset_sha256") != EXPECTED_CORPUS_SHA256 or
            snapshot.get("split_input_count") != 151 or
            snapshot.get("load_failure_count") != 3):
        raise ValueError("Frozen Load/Split snapshot identity or accounting does not match Phase 2.")
    with SOURCE_MAP_PATH.open(encoding="utf-8-sig", newline="") as handle:
        source_names = {row["doc_id"]: Path(row["source_relative_path"]).name
                        for row in csv.DictReader(handle)}
    documents = []
    config = {"chunk_size": 1800, "chunk_overlap": 250}
    for row in snapshot["records"]:
        if row.get("load_status") != "success":
            continue
        artifact_path = SNAPSHOT_ROOT / row["artifact"]
        if not artifact_path.is_file() or _sha(artifact_path) != row.get("artifact_sha256"):
            raise ValueError(f"Frozen extraction artifact failed integrity validation: {row.get('doc_id')}.")
        artifact = json.loads(artifact_path.read_text(encoding="utf8"))
        version_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"retrieval-benchmark:{SNAPSHOT_ID}:{row['doc_id']}"))
        doc_id = row["doc_id"]
        source_name = source_names.get(doc_id)
        title_proxy = Path(source_name).stem if source_name else ""
        chunks = chunk(artifact["blocks"], config, version_id)
        for ordinal, item in enumerate(chunks):
            item["metadata"].update(document_id=doc_id, version_id=version_id,
                chunk_ordinal=ordinal, config_hash=fingerprint(config))
        documents.append(dict(doc_id=doc_id, version_id=version_id,
            filename=source_name or "", title_proxy=title_proxy,
            blocks=artifact["blocks"], chunks=chunks, format=row.get("format")))
    if len(documents) != 151:
        raise ValueError(f"Frozen A0 corpus yielded {len(documents)} documents; expected 151.")
    return snapshot, documents


def _load_queries():
    dataset = json.loads(ANNOTATION_PATH.read_text(encoding="utf8"))
    if (dataset.get("dataset_version") != "gt-v1" or
            dataset.get("reviewer_confirmed") is not True or
            dataset.get("approved_question_count") != 2 or
            dataset.get("valid_query_variation_count") != 6):
        raise ValueError("Ground-truth export changed; expected the frozen two-question/six-variation smoke set.")
    return dataset, _verified_query_variants(dataset)


def _verified_query_variants(dataset: dict) -> list[str]:
    variants = []
    for record in dataset.get("records", []):
        if record.get("human_confirmed") is not True or record.get("question_evidence_relation_confirmed") is not True:
            raise ValueError("A retrieval smoke query is not explicitly human-confirmed.")
        values = record.get("query_variants")
        if not isinstance(values, dict):
            raise ValueError("Verified query variations must be keyed by language.")
        variants.extend(value for value in values.values() if isinstance(value, str) and value.strip())
    if len(variants) != 6:
        raise ValueError("Verified query variation count differs from the exported dataset.")
    return variants


def run_offline_benchmark(output_root: Path | None = None, iterations: int = ITERATIONS, seed: int = 72026) -> dict:
    """Benchmark real-corpus BM25 and synthetic-vector dense/fusion mechanics."""
    if not 1 <= iterations <= 20:
        raise ValueError("iterations must be between 1 and 20")
    output_root = Path(output_root or ROOT / "instance/retrieval-evaluation")
    output_root.mkdir(parents=True, exist_ok=True)
    snapshot, documents = _load_corpus()
    annotation_dataset, queries = _load_queries()
    dataset_root = current_app.config["DATA_DIR"]
    run_id = uuid.uuid4().hex[:12]
    run_root = output_root / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    scratch_root = run_root / "scratch"
    scratch_root.mkdir()
    context_config = {key: current_app.config[key] for key in
        ("CONTEXT_CHUNKS", "CONTEXT_TOKENS", "HISTORY_TOKENS", "INPUT_TOKENS")}

    from chromadb import PersistentClient
    import tiktoken

    encoding = tiktoken.get_encoding("cl100k_base")
    version_rows = []
    sparse_versions = []
    all_chunks = []
    rss_sampler = _RssSampler()
    rss_sampler.__enter__()
    sparse_build_start = time.perf_counter()
    try:
        current_app.config["DATA_DIR"] = scratch_root / "lexical"
        for document in documents:
            version = SimpleNamespace(id=document["version_id"], collection_name="benchmark_rag",
                version_number=1, original_filename=document["filename"],
                manifest_path=f"manifests/{document['version_id']}.json",
                expected_chunks=len(document["chunks"]), config={"provider": "openai",
                    "embedding_model": "text-embedding-3-small", "dimensions": DIMENSIONS,
                    "normalization": "l2-v1"})
            manifest = [{"id": item["id"], "metadata": item["metadata"]}
                        for item in document["chunks"]]
            write_json(version.manifest_path, manifest)
            sparse.write_index(version.id, document["chunks"], document["title_proxy"], document["filename"])
            version_rows.append((version, SimpleNamespace(id=document["doc_id"],
                title=document["title_proxy"], language="mixed")))
            sparse_versions.append((version.id, document["title_proxy"],
                document["filename"], document["doc_id"]))
            all_chunks.extend(document["chunks"])
        sparse_build_ms = (time.perf_counter() - sparse_build_start) * 1000

        chroma_root = scratch_root / "chroma"
        chroma = PersistentClient(path=str(chroma_root))
        collection = chroma.get_or_create_collection(
            "phase5-dense-mock", embedding_function=None, metadata={"hnsw:space": "cosine"})
        vectors = np.stack([_vector(item["id"], seed) for item in all_chunks])
        dense_build_start = time.perf_counter()
        collection.upsert(ids=[item["id"] for item in all_chunks],
            documents=[item["text"] for item in all_chunks],
            metadatas=[item["metadata"] for item in all_chunks], embeddings=vectors.tolist())
        dense_build_ms = (time.perf_counter() - dense_build_start) * 1000
        version_ids = [version.id for version, _document in version_rows]

        def search_dense(query_vector):
            return collection.query(query_embeddings=[query_vector], n_results=DENSE_K,
                where={"version_id": {"$in": version_ids}},
                include=["documents", "metadatas", "distances"])

        def dense_hits(result):
            return [dict(id=chunk_id, text=text, metadata=metadata, distance=distance)
                for chunk_id, text, metadata, distance in zip(result["ids"][0], result["documents"][0],
                    result["metadatas"][0], result["distances"][0])]

        # Warm local caches and Chroma query path; warm-up values are excluded.
        for query in queries:
            variants = query_candidates(query)["candidates"] or [query]
            for variant in variants:
                sparse.search(variant, sparse_versions, SPARSE_K)
                search_dense(_vector(variant, seed + 1).tolist())

        samples = {key: [] for key in ("query_normalization_ms", "bm25_search_ms", "dense_search_ms", "fusion_ms",
            "source_validation_ms", "context_assembly_ms", "mock_embedding_ms", "total_retrieval_ms")}
        candidate_counts, duplicate_rates, context_token_counts = [], [], []
        query_rows = []
        fake_token_client = SimpleNamespace(count=lambda text: len(encoding.encode(text)))
        current_app.config.update(CONTEXT_CHUNKS=6, CONTEXT_TOKENS=8000,
            HISTORY_TOKENS=2000, INPUT_TOKENS=12000)
        for iteration in range(iterations):
            for query in queries:
                total_start = time.perf_counter()
                stage_start = time.perf_counter()
                variants = query_candidates(query)["candidates"] or [query]
                normalization_ms = (time.perf_counter() - stage_start) * 1000
                mock_start = time.perf_counter()
                query_vectors = [_vector(variant, seed + 1).tolist() for variant in variants]
                mock_ms = (time.perf_counter() - mock_start) * 1000

                stage_start = time.perf_counter()
                sparse_rankings, missing = {}, set()
                for index, variant in enumerate(variants):
                    lexical, absent = sparse.search(variant, sparse_versions, SPARSE_K)
                    sparse_rankings[f"sparse_{index}"] = lexical
                    missing.update(absent)
                bm25_ms = (time.perf_counter() - stage_start) * 1000
                if missing:
                    raise ValueError("Private BM25 benchmark is missing one or more frozen document indexes.")

                dense_rankings = {}
                dense_start = time.perf_counter()
                for index, query_vector in enumerate(query_vectors):
                    dense_result = collection.query(query_embeddings=[query_vector], n_results=DENSE_K,
                        where={"version_id": {"$in": version_ids}},
                        include=["documents", "metadatas", "distances"])
                    dense_rankings[f"dense_{index}"] = dense_hits(dense_result)
                dense_ms = (time.perf_counter() - dense_start) * 1000

                stage_start = time.perf_counter()
                rankings = {**dense_rankings, **sparse_rankings}
                fused = sparse.rrf(rankings, 60)
                fusion_ms = (time.perf_counter() - stage_start) * 1000
                stage_start = time.perf_counter()
                validate_candidate_provenance(rankings, version_rows)
                validation_ms = (time.perf_counter() - stage_start) * 1000

                for hit in fused:
                    metadata = hit["metadata"]
                    version_id = metadata["version_id"]
                    version = next(row for row, _document in version_rows if row.id == version_id)
                    hit.update(title=Path(version.original_filename).stem,
                        filename=version.original_filename, version_number=version.version_number)
                stage_start = time.perf_counter()
                _prompt, selected = rag.build_context(query, fused, [], fake_token_client)
                context_ms = (time.perf_counter() - stage_start) * 1000
                context_tokens = sum(len(encoding.encode(item["text"])) for item in selected)
                branch_hits = [hit for branch in rankings.values() for hit in branch]
                branch_total = len(branch_hits)
                unique = len({item["id"] for item in branch_hits})
                total_ms = (time.perf_counter() - total_start) * 1000
                for key, value in (("query_normalization_ms", normalization_ms),
                    ("bm25_search_ms", bm25_ms), ("dense_search_ms", dense_ms),
                    ("fusion_ms", fusion_ms), ("source_validation_ms", validation_ms),
                    ("context_assembly_ms", context_ms), ("mock_embedding_ms", mock_ms),
                    ("total_retrieval_ms", total_ms)):
                    samples[key].append(value)
                candidate_counts.append({"bm25": sum(len(hits) for hits in sparse_rankings.values()),
                    "dense": sum(len(hits) for hits in dense_rankings.values()), "fused": len(fused),
                    "context": len(selected)})
                duplicate_rates.append(1 - unique / branch_total if branch_total else 0.0)
                context_token_counts.append(context_tokens)
                query_rows.append({"query_sha256": hashlib.sha256(query.encode("utf8")).hexdigest(),
                    "iteration": iteration + 1, "query_variant_count": len(variants),
                    "bm25_candidates": sum(len(hits) for hits in sparse_rankings.values()),
                    "dense_candidates": sum(len(hits) for hits in dense_rankings.values()), "fused_candidates": len(fused),
                    "context_chunks": len(selected), "context_tokens": context_tokens})

        packages = {}
        for package in ("Flask", "chromadb", "numpy", "tiktoken", "psutil"):
            try:
                packages[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                packages[package] = None
        sizes = {"bm25_and_manifest_bytes": sum(p.stat().st_size for p in (scratch_root / "lexical").rglob("*") if p.is_file()),
            "chroma_bytes": sum(p.stat().st_size for p in chroma_root.rglob("*") if p.is_file())}
        result = {
            "schema_version": 1, "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_id": SNAPSHOT_ID, "dataset_sha256": snapshot["dataset_sha256"],
            "annotation_dataset_version": annotation_dataset["dataset_version"],
            "annotation_dataset_sha256": annotation_dataset["dataset_sha256"],
            "query_count": len(queries), "query_set_sha256": fingerprint(sorted(hashlib.sha256(q.encode("utf8")).hexdigest() for q in queries)),
            "query_text_retained": False, "input_order": "frozen Load manifest order",
            "document_count": len(documents), "chunk_count": len(all_chunks),
            "a0_chunking": {"size_characters": 1800, "overlap_characters": 250,
                "chunk_ids_sha256": fingerprint([item["id"] for item in all_chunks]),
                "chunk_text_hash_sha256": fingerprint([item["metadata"]["content_hash"] for item in all_chunks])},
            "modes": {"bm25": "real frozen A0 parser text; title is source-filename stem proxy",
                "dense": "random deterministic normalized vectors; mechanics only, not semantic relevance",
                "hybrid": "RRF c=60; separate BM25 and cosine scores are not combined directly"},
            "configuration": {"dense_top_k": DENSE_K, "sparse_top_k": SPARSE_K,
                "rrf_k": 60, "context_chunks": 6, "context_tokens": 8000,
                "vector_dimensions": DIMENSIONS, "vector_seed": seed,
                "iterations": iterations, "warmup_per_query": 1},
            "api_calls": 0, "postgresql_access": False, "production_chroma_access": False,
            "embedding_provider_latency": None,
            "index_build_ms": {"bm25_with_manifest": sparse_build_ms, "dense_chroma_upsert": dense_build_ms},
            "latency": {key: _stats(value) for key, value in samples.items()},
            "candidate_counts": {key: statistics.fmean([row[key] for row in candidate_counts]) for key in ("bm25", "dense", "fused", "context")},
            "duplicate_rate_mean": statistics.fmean(duplicate_rates),
            "context_tokens": {"p50": _percentile(context_token_counts, .5),
                "p95": _percentile(context_token_counts, .95), "mean": statistics.fmean(context_token_counts)},
            "storage_bytes": sizes,
            "memory": {"process_peak_working_set_bytes": rss_sampler.peak,
                "note": "Operating-system process peak; includes Python and native Chroma allocations and startup."},
            "environment": {"os": platform.platform(), "python": platform.python_version(),
                "machine": platform.machine(), "processor": platform.processor() or None,
                "logical_cpu_count": os.cpu_count()},
            "packages": packages,
            "query_diagnostics_private_hashes_only": query_rows,
            "limitations": ["Synthetic dense vectors have no text-semantic relationship.",
                "Two independent verified questions/six correlated language variations are smoke inputs only.",
                "Source filename stem substitutes for unavailable production document title.",
                "No relevance metrics are computed."]}
        private_result = run_root / "result.json"
        private_result.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
        result["private_result_path"] = str(private_result)
        result["private_result_sha256"] = _sha(private_result)
        current_app.config["DATA_DIR"] = dataset_root
        if hasattr(chroma, "close"):
            chroma.close()
        return result
    finally:
        rss_sampler.__exit__(None, None, None)
        current_app.config["DATA_DIR"] = dataset_root
        current_app.config.update(context_config)
