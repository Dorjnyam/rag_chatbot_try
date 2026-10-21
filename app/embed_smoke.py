"""Private Phase 3 embedding smoke-test planning and isolated execution."""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .services.chunking import chunk as production_chunk
from .services.ingestion import embedding_batches, index_config


def _canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf8")).hexdigest()


def _sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for data in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def _normalized(value):
    return " ".join(unicodedata.normalize("NFC", value).split())


def _encoding(model):
    import tiktoken
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("o200k_base")


def _version_id(dataset_hash, doc_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"rag-embed-smoke:{dataset_hash}:{doc_id}"))


def prepare_smoke(data_dir: Path, config: dict):
    """Validate private references and form an A0 plan without DB, Chroma, or API calls."""
    data_dir = Path(data_dir).resolve()
    base = data_dir / "split-evaluation"
    dataset_file = base / "datasets" / "ground-truth-gt-v1.json"
    dataset = json.loads(dataset_file.read_text(encoding="utf8"))
    expected_hash = dataset.get("dataset_sha256")
    unhashed = dict(dataset)
    unhashed.pop("dataset_sha256", None)
    if not expected_hash or _canonical_hash(unhashed) != expected_hash:
        raise ValueError("Ground-truth dataset hash validation failed.")
    if not dataset.get("reviewer_confirmed") or not dataset.get("records"):
        raise ValueError("No reviewer-confirmed ground-truth records are available.")

    snapshot_id = dataset["snapshot_id"]
    snapshot_dir = (base / snapshot_id).resolve()
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Frozen parser manifest is missing.")
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    if manifest.get("snapshot_id") != snapshot_id or manifest.get("dataset_sha256") != dataset.get("source_dataset_sha256"):
        raise ValueError("Ground-truth and frozen parser snapshot identity differ.")
    manifest_rows = {row["doc_id"]: row for row in manifest.get("records", [])}
    if config.get("AI_PROVIDER") != "openai":
        raise ValueError("This preparation currently targets the configured OpenAI embedding provider only.")
    chunk_config = index_config("openai")
    model, dimensions = chunk_config["embedding_model"], chunk_config["dimensions"]
    if not isinstance(dimensions, int) or dimensions <= 0:
        raise ValueError("Embedding dimensions are invalid.")
    encoding = _encoding(model)
    snapshot_hash = dataset["source_dataset_sha256"]
    records, document_inputs, query_inputs, evidence_by_candidate = [], {}, [], {}
    chunks_by_doc = {}
    total_doc_tokens = total_query_tokens = total_bytes = 0
    for record in dataset["records"]:
        if not all(record.get(field) for field in ("human_confirmed", "evidence_matches_original", "question_evidence_relation_confirmed")):
            raise ValueError("Ground-truth record is not fully human-confirmed.")
        doc_id = record["doc_id"]
        source = manifest_rows.get(doc_id)
        if not source or source.get("load_status") != "success" or source.get("source_sha256") != record.get("source_sha256"):
            raise ValueError("Ground-truth document identity or source hash differs from frozen Load corpus.")
        artifact_path = (snapshot_dir / source["artifact"]).resolve()
        if not artifact_path.is_relative_to(snapshot_dir) or not artifact_path.is_file() or _sha_file(artifact_path) != source.get("artifact_sha256"):
            raise ValueError("Frozen parser artifact path or hash is invalid.")
        artifact = json.loads(artifact_path.read_text(encoding="utf8"))
        if artifact.get("parser") != "ordered-blocks-v1":
            raise ValueError("Smoke plan requires the frozen ordered-blocks-v1 parser output.")
        blocks = artifact.get("blocks", [])
        evidence = record.get("evidence_text", "")
        if not evidence or _normalized(evidence) not in _normalized("\n".join(block.get("text", "") for block in blocks)):
            raise ValueError("Human evidence no longer matches its frozen parsed document.")
        matching_blocks = [int(block["block"]) for block in blocks
            if _normalized(evidence) in _normalized(block.get("text", "")) and block.get("block") is not None]
        benchmark_version = _version_id(expected_hash, doc_id)
        chunks = production_chunk(blocks, chunk_config, benchmark_version)
        if not chunks:
            raise ValueError("A0 produced no chunks for an annotated document.")
        target_chunk_ids = [item["id"] for item in chunks
            if any(item["metadata"]["block_start"] <= block_id <= item["metadata"]["block_end"] for block_id in matching_blocks)]
        for item in chunks:
            item["metadata"].update(document_id=f"benchmark:{doc_id}", version_id=benchmark_version,
                doc_id=doc_id, source_sha256=record["source_sha256"], snapshot_id=snapshot_id,
                source_dataset_sha256=snapshot_hash, embedding_provider="openai",
                embedding_model=model, embedding_dimensions=dimensions,
                benchmark_only=True, smoke_dataset_hash=expected_hash)
            if item["id"] in target_chunk_ids:
                item["metadata"]["verified_annotation_candidate_id"] = record["candidate_id"]
                item["metadata"]["verified_source_location_json"] = json.dumps(
                    record.get("source_location") or {}, ensure_ascii=False, sort_keys=True)
        doc_token_count = sum(len(encoding.encode(item["text"])) for item in chunks)
        doc_byte_count = sum(len(item["text"].encode("utf8")) for item in chunks)
        query_variants = record.get("query_variants", {})
        if not query_variants:
            raise ValueError("Verified query variations are missing.")
        query_token_count = 0
        for language, query in query_variants.items():
            if not isinstance(query, str) or not query.strip():
                raise ValueError("An empty verified query variation was exported.")
            if len(query.encode("utf8")) > 1900:
                raise ValueError("A query exceeds the production OpenAI UTF-8 input guard.")
            token_count = len(encoding.encode(query))
            query_token_count += token_count
            query_inputs.append({"candidate_id": record["candidate_id"], "doc_id": doc_id,
                "language": language, "text": query, "token_count": token_count,
                "query_sha256": hashlib.sha256(query.encode("utf8")).hexdigest()})
        optional_metadata = record.get("source_location") or {}
        missing = [key for key in ("page", "section", "clause_id", "document_version")
            if optional_metadata.get(key) in (None, "")]
        missing_parser_metadata = []
        if not any("page_start" in item["metadata"] for item in chunks):
            missing_parser_metadata.append("page_provenance")
        if not any(item["metadata"].get("heading") for item in chunks):
            missing_parser_metadata.append("heading_hierarchy")
        if not any("source_regions_json" in item["metadata"] for item in chunks):
            missing_parser_metadata.append("source_span_anchor")
        if not any("clause_id" in item["metadata"] for item in chunks):
            missing_parser_metadata.append("clause_id")
        records.append({"candidate_id": record["candidate_id"], "doc_id": doc_id,
            "source_sha256": record["source_sha256"], "human_confirmed": True,
            "matched_parser_block_ids": matching_blocks, "evidence_chunk_ids": target_chunk_ids,
            "evidence_present_in_frozen_parse": True, "source_reference": record.get("source_reference"),
            "missing_optional_source_metadata": missing, "missing_parser_metadata": missing_parser_metadata,
            "query_variations": len(query_variants),
            "query_variation_languages": sorted(query_variants), "document_chunk_count": len(chunks),
            "document_chunk_tokens": doc_token_count, "document_chunk_bytes": doc_byte_count,
            "query_tokens": query_token_count})
        evidence_by_candidate[record["candidate_id"]] = evidence
        chunks_by_doc[doc_id] = chunks
        document_inputs[doc_id] = chunks
        total_doc_tokens += doc_token_count
        total_query_tokens += query_token_count
        total_bytes += doc_byte_count

    batch_sizes = {doc_id: len(list(embedding_batches(chunks, config["EMBEDDING_BATCH_SIZE"])))
        for doc_id, chunks in document_inputs.items()}
    query_batch_count = len(list(embedding_batches([{"text": item["text"]} for item in query_inputs], config["EMBEDDING_BATCH_SIZE"])))
    plan_identity = {"dataset_sha256": expected_hash, "snapshot_id": snapshot_id,
        "source_dataset_sha256": snapshot_hash, "parser": "ordered-blocks-v1", "strategy": "characters-v1",
        "chunk_size": chunk_config["chunk_size"], "chunk_overlap": chunk_config["chunk_overlap"],
        "chunk_config_sha256": _canonical_hash(chunk_config),
        "provider": "openai", "model": model, "dimensions": dimensions,
        "embedding_batch_size": config["EMBEDDING_BATCH_SIZE"], "embedding_batch_byte_budget": 16000,
        "tokenizer": encoding.name, "tiktoken_version": __import__("importlib.metadata", fromlist=["version"]).version("tiktoken"),
        "records": records,
        "chunk_hashes": {doc_id: [hashlib.sha256(item["text"].encode("utf8")).hexdigest() for item in chunks]
            for doc_id, chunks in chunks_by_doc.items()},
        "chunk_ids": {doc_id: [item["id"] for item in chunks] for doc_id, chunks in chunks_by_doc.items()},
        "queries": [{key: item[key] for key in ("candidate_id", "doc_id", "language", "token_count", "query_sha256")}
            for item in query_inputs]}
    plan_hash = _canonical_hash(plan_identity)
    plan = {"schema_version": 1, "plan_id": f"embed-smoke-{plan_hash[:12]}", "plan_sha256": plan_hash,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **{key: plan_identity[key] for key in ("dataset_sha256", "snapshot_id", "source_dataset_sha256", "parser",
           "strategy", "chunk_size", "chunk_overlap", "chunk_config_sha256", "provider", "model", "dimensions", "embedding_batch_size",
           "embedding_batch_byte_budget", "tokenizer", "tiktoken_version")},
        "document_count": len(records), "approved_question_count": len(records),
        "verified_query_variation_count": len(query_inputs), "document_chunk_count": sum(len(v) for v in chunks_by_doc.values()),
        "document_embedding_input_tokens_estimate": total_doc_tokens,
        "query_embedding_input_tokens_estimate": total_query_tokens,
        "total_embedding_input_tokens_estimate": total_doc_tokens + total_query_tokens,
        "document_embedding_batches_estimate": batch_sizes, "query_embedding_batches_estimate": query_batch_count,
        "max_request_count_estimate": sum(batch_sizes.values()) + query_batch_count,
        "estimated_raw_vector_payload_bytes": sum(len(v) for v in chunks_by_doc.values()) * dimensions * 4,
        "embedding_cost_usd_per_1m_input_tokens": 0.02,
        "estimated_embedding_cost_usd": round((total_doc_tokens + total_query_tokens) * 0.02 / 1_000_000, 8),
        "cost_price_reference": "https://developers.openai.com/api/docs/models/text-embedding-3-small",
        "cost_price_checked_date": "2026-10-21", "actual_api_usage": 0,
        "production_database_or_chroma_access": False, "records": records,
        "reproducibility": {"input_dataset_hash_verified": True, "source_hashes_verified": True,
            "artifact_hashes_verified": True, "chunk_ids_deterministic": True, "chunk_text_hashes_recorded": True},
        "execution_status": "PLAN_ONLY_NO_API_OR_CHROMA"}
    return plan, chunks_by_doc, query_inputs, evidence_by_candidate


def write_plan(plan, data_dir: Path):
    target_dir = Path(data_dir) / "split-evaluation" / "embed-smoke"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{plan['plan_id']}.json"
    if target.exists():
        old = json.loads(target.read_text(encoding="utf8"))
        if old.get("plan_sha256") != plan["plan_sha256"]:
            raise ValueError("Existing private smoke plan identity differs; refusing to overwrite it.")
        return target
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    temporary.replace(target)
    return target


def run_isolated_smoke(plan, chunks_by_doc, query_inputs, data_dir: Path, embedder,
                       batch_size=16, run_id=None):
    """Write/query a private Chroma collection; embedder is injected to make no hidden calls."""
    import chromadb
    run_id = run_id or uuid.uuid4().hex[:12]
    run_dir = Path(data_dir) / "split-evaluation" / "embed-smoke" / "runs" / run_id
    chroma_dir = run_dir / "chroma"
    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir), settings=chromadb.config.Settings(anonymized_telemetry=False))
    collection = client.get_or_create_collection(name="isolated_smoke", embedding_function=None,
        metadata={"hnsw:space": "cosine", "benchmark_only": True,
            "embedding_model": plan["model"], "embedding_dimensions": plan["dimensions"]})
    dimensions = plan["dimensions"]
    stored_ids, evidence_roundtrips = set(), []
    all_chunks = [item for doc_chunks in chunks_by_doc.values() for item in doc_chunks]
    for item in all_chunks:
        stored_ids.add(item["id"])
    if embedder is None:
        raise ValueError("An explicit mock or real embedder must be passed; no implicit provider is created.")
    doc_vectors = []
    for batch in embedding_batches(all_chunks, batch_size):
        doc_vectors.extend(embedder([item["text"] for item in batch], "RETRIEVAL_DOCUMENT"))
    if len(doc_vectors) != len(all_chunks) or any(len(vector) != dimensions for vector in doc_vectors):
        raise ValueError("Document embedding response count/dimensions do not match the experiment manifest.")
    collection.upsert(ids=[item["id"] for item in all_chunks], documents=[item["text"] for item in all_chunks],
        metadatas=[item["metadata"] for item in all_chunks], embeddings=doc_vectors)
    saved = collection.get(ids=list(stored_ids), include=["metadatas"])
    if set(saved["ids"]) != stored_ids:
        raise ValueError("Isolated Chroma returned a different ID set from the chunk manifest.")
    metadata_by_id = dict(zip(saved["ids"], saved["metadatas"]))
    for record in plan["records"]:
        matching = [chunk_id for chunk_id in record["evidence_chunk_ids"] if chunk_id in metadata_by_id
            and metadata_by_id[chunk_id].get("doc_id") == record["doc_id"]]
        evidence_roundtrips.append({"candidate_id": record["candidate_id"], "doc_id": record["doc_id"],
            "source_evidence_parse_match": record["evidence_present_in_frozen_parse"],
            "evidence_chunk_reference_roundtrip": bool(matching), "evidence_chunk_ids": matching,
            "missing_source_metadata": record["missing_optional_source_metadata"]})
    # Exercise Chroma query with vectors of verified target chunks only; this proves
    # storage/dimension/reference plumbing, not question semantic retrieval quality.
    query_vectors = []
    query_chunk_batches = list(embedding_batches([{"text": item["text"]} for item in query_inputs], batch_size))
    for batch in query_chunk_batches:
        query_vectors.extend(embedder([item["text"] for item in batch], "RETRIEVAL_QUERY"))
    if len(query_vectors) != len(query_inputs) or any(len(vector) != dimensions for vector in query_vectors):
        raise ValueError("Query embedding response count/dimensions do not match verified query variations.")
    vector_by_chunk = dict(zip([item["id"] for item in all_chunks], doc_vectors))
    retrieval_references = []
    by_id = {item["id"]: item for item in all_chunks}
    answer_records = {record["candidate_id"]: record for record in plan["records"]}
    for input_item, query_vector in zip(query_inputs, query_vectors):
        record = answer_records[input_item["candidate_id"]]
        results = collection.query(query_embeddings=[query_vector], n_results=min(5, len(chunks_by_doc[record["doc_id"]])),
            where={"doc_id": record["doc_id"]}, include=["metadatas", "distances"])
        hit_ids = results["ids"][0] if results["ids"] else []
        hit_metadata = results["metadatas"][0] if results["metadatas"] else []
        target_ids = set(record["evidence_chunk_ids"])
        evidence_hit_ids = sorted(target_ids.intersection(hit_ids))
        retrieval_references.append({"candidate_id": input_item["candidate_id"], "doc_id": input_item["doc_id"],
            "language": input_item["language"], "top_k": len(hit_ids), "result_chunk_ids": hit_ids,
            "result_source_sha256": [meta.get("source_sha256") for meta in hit_metadata],
            "result_pages": [meta.get("page_start") for meta in hit_metadata],
            "evidence_source_chunk_ids_in_top_k": evidence_hit_ids,
            "evidence_source_chunk_recall_at_k": len(evidence_hit_ids) / len(target_ids) if target_ids else None,
            "semantic_quality_claim": False})
    oracle_roundtrips = []
    for record in plan["records"]:
        target_ids = [item for item in record["evidence_chunk_ids"] if item in by_id]
        if target_ids:
            target_id = target_ids[0]
            result = collection.query(query_embeddings=[vector_by_chunk[target_id]], n_results=1,
                where={"doc_id": record["doc_id"]}, include=["metadatas"])
            returned = result["ids"][0] if result["ids"] else []
            oracle_roundtrips.append({"candidate_id": record["candidate_id"],
                "oracle_reference_roundtrip": bool(set(returned) & set(target_ids))})
        else:
            oracle_roundtrips.append({"candidate_id": record["candidate_id"], "oracle_reference_roundtrip": False})
    return {"plan_id": plan["plan_id"], "run_id": run_id, "isolated_chroma_path": str(chroma_dir),
        "collection_name": collection.name, "indexed_chunk_count": len(stored_ids),
        "embedding_dimensions": dimensions, "stored_ids_match_manifest": True,
        "source_reference_roundtrips": evidence_roundtrips,
        "mock_or_oracle_vector_roundtrips": oracle_roundtrips,
        "query_retrieval_smoke_diagnostics": retrieval_references,
        "query_semantic_quality_measured": False, "production_data_touched": False}


def deterministic_mock_vectors(texts, task, dimensions):
    """Deterministic mechanics-only vectors; never use for quality conclusions."""
    output = []
    for text in texts:
        seed = hashlib.sha256(text.encode("utf8")).digest()
        vector = [((seed[index % len(seed)] / 127.5) - 1.0) for index in range(dimensions)]
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        output.append([value / norm for value in vector])
    return output
