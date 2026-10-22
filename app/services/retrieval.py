"""Dense and sparse retrieval with rank fusion and an optional local reranker."""
import time
import math
import numbers
from sqlalchemy import select
from flask import current_app
from ..extensions import db
from ..models import Document, DocumentVersion
from .openai_provider import openai_client
from .vectorstore import store
from .embedding_contract import compatible_embedding_spaces
from .storage import fingerprint, read_json
from . import sparse


def active_versions(document_ids=None, language=None):
    statement = select(DocumentVersion, Document).join(Document, Document.active_version_id == DocumentVersion.id).where(Document.state == "active", DocumentVersion.state == "ready")
    if document_ids:
        statement = statement.where(Document.id.in_(document_ids))
    if language:
        statement = statement.where(Document.language == language)
    return db.session.execute(statement).all()


def still_active(hits):
    db.session.expire_all()
    valid = {v.id for v, d in active_versions()}
    return all(hit["metadata"]["version_id"] in valid for hit in hits)


def embedding_mismatch(version, config=None):
    config = config or current_app.config
    stored = version.config
    return not compatible_embedding_spaces(stored, config)


def incompatible_active_count():
    return sum(1 for version, _document in active_versions() if embedding_mismatch(version))


def validate_candidate_provenance(rankings, versions):
    """Fail closed unless every candidate agrees with its active source manifest."""
    version_documents = {version.id: document.id for version, document in versions}
    manifests = {}
    for hits in rankings.values():
        for hit in hits:
            if not isinstance(hit, dict) or not isinstance(hit.get("id"), str) or not hit["id"]:
                raise ValueError("Retrieval candidate has no canonical chunk ID.")
            metadata = hit.get("metadata")
            text = hit.get("text")
            version_id = metadata.get("version_id") if isinstance(metadata, dict) else None
            if (version_id not in version_documents or
                    metadata.get("document_id") != version_documents[version_id] or
                    not isinstance(text, str) or not text.strip()):
                raise ValueError("Retrieval candidate is outside the active document/version scope.")
            if version_id not in manifests:
                version = next(row for row, _document in versions if row.id == version_id)
                if not version.manifest_path:
                    raise ValueError("Active retrieval source manifest is unavailable.")
                try:
                    manifest = read_json(version.manifest_path)
                except (OSError, ValueError, TypeError) as exc:
                    raise ValueError("Active retrieval source manifest is invalid or unavailable.") from exc
                if not isinstance(manifest, list):
                    raise ValueError("Active retrieval source manifest is invalid.")
                entries = {}
                for item in manifest:
                    if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or
                            not isinstance(item.get("metadata"), dict) or item["id"] in entries):
                        raise ValueError("Active retrieval source manifest is invalid.")
                    item_meta = item["metadata"]
                    if (item_meta.get("version_id") != version_id or
                            item_meta.get("document_id") != version_documents[version_id]):
                        raise ValueError("Active retrieval source manifest has conflicting ownership.")
                    entries[item["id"]] = item_meta
                if not entries or (version.expected_chunks and len(entries) != version.expected_chunks):
                    raise ValueError("Active retrieval source manifest is incomplete.")
                manifests[version_id] = entries
            expected = manifests[version_id].get(hit["id"])
            if expected is None or metadata != expected:
                raise ValueError("Retrieval candidate provenance differs from its canonical manifest.")
            if expected.get("content_hash") and fingerprint(text) != expected["content_hash"]:
                raise ValueError("Retrieval candidate content differs from its canonical source manifest.")
    return True


def dense_search(queries, groups, top_k, client, return_rankings=False, embedding_configs=None):
    rankings = {}
    start = time.monotonic()
    vectors = client.embed(queries, "RETRIEVAL_QUERY")
    embedded = time.monotonic()
    for index, vector in enumerate(vectors):
        hits = []
        for collection, ids in groups.items():
            hits.extend(store().query(collection, vector, ids, top_k,
                (embedding_configs or {}).get(collection)))
        hits.sort(key=lambda hit: (hit["distance"], hit["id"]))
        rankings[f"dense_{index}"] = hits[:top_k]
    search_ms = (time.monotonic() - embedded) * 1000
    fused = sparse.rrf(rankings, current_app.config["RRF_K"])
    for hit in fused:
        hit["similarity"] = 1 - hit["distance"] if hit.get("distance") is not None else None
    result = (fused, (embedded - start) * 1000, search_ms)
    return (*result, rankings) if return_rankings else result


def _reranker():
    config = current_app.config
    cached = current_app.extensions.get("rag_reranker")
    if cached:
        return cached
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise ValueError("Optional reranking needs sentence-transformers installed.") from exc
    # Preserve the checkpoint's native BF16 weights to keep this optional
    # 0.6B CPU reranker within local memory limits. Qwen publishes the model in
    # BF16 and Sentence Transformers' CrossEncoder API accepts model_kwargs.
    model = CrossEncoder(config["RERANKER_MODEL_PATH"], local_files_only=True,
        device="cpu", model_kwargs={"torch_dtype": "auto"}, max_length=512)
    current_app.extensions["rag_reranker"] = model
    return model


def retrieve(question, k=5, document_ids=None, language=None, client=None, candidates=None, include_pool=False):
    if k not in (1, 3, 5, 10, 20):
        raise ValueError("top-k must be 1, 3, 5, 10, or 20")
    if language and language not in ("mn", "en", "mixed"):
        raise ValueError("Invalid document language filter")
    rows = active_versions(document_ids, language)
    if not rows:
        timings = dict(query_embedding_ms=0, vector_search_ms=0, sparse_search_ms=0, reranking_ms=0)
        if include_pool:
            timings["candidate_pool"] = []
        return [], timings
    config = current_app.config
    for version, document in rows:
        if embedding_mismatch(version):
            raise ValueError("The active index uses a different embedding space. OpenAI embedding тохиргоотой таарахгүй байна; OpenAI индексийн migration хийнэ үү.")
    groups, versions, titles, embedding_configs = {}, [], {}, {}
    for version, document in rows:
        groups.setdefault(version.collection_name, []).append(version.id)
        existing_config = embedding_configs.setdefault(version.collection_name, version.config)
        if not compatible_embedding_spaces(existing_config, version.config):
            raise ValueError("A Chroma collection contains document versions from incompatible embedding spaces.")
        versions.append((version.id, document.title, version.original_filename, document.id))
        titles[version.id] = dict(title=document.title, version_number=version.version_number, filename=version.original_filename)
    db.session.commit()
    client = client or openai_client()
    queries = list(dict.fromkeys((candidates or [question])[:3])) or [question]
    dense, embedding_ms, dense_ms, dense_rankings = dense_search(queries, groups, config["DENSE_TOP_K"], client,
        return_rankings=True, embedding_configs=embedding_configs)
    validate_candidate_provenance(dense_rankings, rows)
    sparse_start = time.monotonic()
    sparse_rankings, missing = {}, set()
    if config["RETRIEVAL_MODE"] == "hybrid":
        for index, query in enumerate(queries):
            hits, absent = sparse.search(query, versions, config["SPARSE_TOP_K"], set(document_ids) if document_ids else None)
            sparse_rankings[f"sparse_{index}"] = hits
            missing.update(absent)
        validate_candidate_provenance(sparse_rankings, rows)
    sparse_ms = (time.monotonic() - sparse_start) * 1000
    degraded = bool(missing) and config["RETRIEVAL_MODE"] == "hybrid"
    if config["RETRIEVAL_MODE"] == "dense" or degraded:
        hits = dense
    else:
        hits = sparse.rrf({**dense_rankings, **sparse_rankings}, config["RRF_K"])
    for hit in hits:
        hit.update(**titles[hit["metadata"]["version_id"]], selected=False, degraded=degraded)
        hit.setdefault("distance", None)
        hit["similarity"] = 1 - hit["distance"] if hit["distance"] is not None else None
    rerank_ms = 0.0
    if config["RERANKING_ENABLED"] and hits:
        rerank_start = time.monotonic()
        try:
            model = _reranker()
            scores = model.predict([(question, hit["text"]) for hit in hits], batch_size=16, show_progress_bar=False)
            scores = list(scores)
            if len(scores) != len(hits) or any(isinstance(score, bool) or not isinstance(score, numbers.Real) or not math.isfinite(float(score)) for score in scores):
                raise ValueError("Reranker returned invalid scores")
            for hit, score in zip(hits, scores):
                hit["rerank_score"] = float(score)
            hits.sort(key=lambda hit: (-hit["rerank_score"], hit["id"]))
        except Exception:
            current_app.logger.warning("Optional reranker failed; preserving fused retrieval order")
            for hit in hits:
                hit["reranker_error"] = True
        rerank_ms = (time.monotonic() - rerank_start) * 1000
    for rank, hit in enumerate(hits, 1):
        hit["rank"] = rank
    timings = dict(query_embedding_ms=embedding_ms, vector_search_ms=dense_ms, sparse_search_ms=sparse_ms,
        reranking_ms=rerank_ms, sparse_degraded=degraded, missing_sparse_versions=sorted(missing))
    if include_pool:
        timings["candidate_pool"] = hits
    return hits[:k], timings
