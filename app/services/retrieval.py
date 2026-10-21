"""Dense and sparse retrieval with rank fusion and an optional local reranker."""
import time
import math
import numbers
from sqlalchemy import select
from flask import current_app
from ..extensions import db
from ..models import Document, DocumentVersion
from .providers import provider
from .vectorstore import store
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
    stored_provider = stored.get("provider", "gemini")
    return (stored_provider, stored.get("embedding_model"), stored.get("dimensions")) != (
        config.get("AI_PROVIDER", "gemini"), config["EMBEDDING_MODEL"], config["EMBEDDING_DIMENSIONS"])


def incompatible_active_count():
    return sum(1 for version, _document in active_versions() if embedding_mismatch(version))


def dense_search(queries, groups, top_k, client, return_rankings=False):
    rankings = {}
    start = time.monotonic()
    vectors = client.embed(queries, "RETRIEVAL_QUERY")
    embedded = time.monotonic()
    for index, vector in enumerate(vectors):
        hits = []
        for collection, ids in groups.items():
            hits.extend(store().query(collection, vector, ids, top_k))
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
            raise ValueError("The active index uses a different embedding space. OpenAI/Gemini embedding тохиргоо зөрж байна; migrate-indexes командаар шилжүүлнэ үү.")
    groups, versions, titles = {}, [], {}
    for version, document in rows:
        groups.setdefault(version.collection_name, []).append(version.id)
        versions.append((version.id, document.title, version.original_filename, document.id))
        titles[version.id] = dict(title=document.title, version_number=version.version_number, filename=version.original_filename)
    db.session.commit()
    client = client or provider()
    queries = list(dict.fromkeys((candidates or [question])[:3])) or [question]
    dense, embedding_ms, dense_ms, dense_rankings = dense_search(queries, groups, config["DENSE_TOP_K"], client, return_rankings=True)
    sparse_start = time.monotonic()
    sparse_rankings, missing = {}, set()
    if config["RETRIEVAL_MODE"] == "hybrid":
        for index, query in enumerate(queries):
            hits, absent = sparse.search(query, versions, config["SPARSE_TOP_K"], set(document_ids) if document_ids else None)
            sparse_rankings[f"sparse_{index}"] = hits
            missing.update(absent)
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
