"""Version-scoped BM25 index sharing Chroma's canonical chunk identifiers."""
import json
import hashlib
import math
import re
import threading
import time
import unicodedata
from collections import Counter, OrderedDict
from uuid import UUID, uuid5
from flask import current_app
from .storage import path, write_json, fingerprint

_cache = OrderedDict()
_compiled_cache = OrderedDict()
# Covers the frozen 151-version corpus while remaining bounded for larger stores.
_CACHE_LIMIT = 256
_cache_lock = threading.RLock()
TOKEN_RE = re.compile(r"https?://[^\s]+|[\w]+(?:[./_-][\w]+)*", re.UNICODE)


def tokenize(text):
    normalized = unicodedata.normalize("NFC", str(text or "")).casefold()
    return TOKEN_RE.findall(normalized)


def index_path(version_id):
    return f"sparse/{version_id}.json"


def _remember(cache, key, value):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _CACHE_LIMIT:
        cache.popitem(last=False)


def _invalidate(version_id):
    target = str(path(index_path(version_id)))
    with _cache_lock:
        for key in list(_cache):
            if key[0] == target:
                del _cache[key]
        # Corpus statistics depend on the entire eligible version set.
        _compiled_cache.clear()


def write_index(version_id, chunks, title, filename):
    documents = []
    for chunk in chunks:
        meta = dict(chunk["metadata"])
        documents.append(dict(id=chunk["id"], text=chunk["text"], metadata=meta,
            title=title, filename=filename, terms=tokenize(" ".join((title, filename, meta.get("heading", ""), chunk["text"])))))
    artifact = dict(schema=1, version_id=version_id, content_hash=fingerprint(documents), documents=documents)
    write_json(index_path(version_id), artifact)
    _invalidate(version_id)
    return {item["id"] for item in documents}


def delete_index(version_id):
    _invalidate(version_id)
    path(index_path(version_id)).unlink(missing_ok=True)


def load_index(version_id, require_manifest=False, with_signature=False):
    target = path(index_path(version_id))
    if not target.exists():
        return (None, None, False) if with_signature else None
    # Hash the bytes, rather than trusting mtime/size: same-size corruption and
    # copies retaining file timestamps must never reuse a validated artifact.
    raw = target.read_text(encoding="utf8")
    manifest_target = path(f"manifests/{version_id}.json")
    if require_manifest and not manifest_target.exists():
        raise ValueError("Active sparse index requires its canonical manifest.")
    manifest_raw = manifest_target.read_text(encoding="utf8") if manifest_target.exists() else None
    key = (str(target), hashlib.sha256(raw.encode("utf8")).hexdigest(),
        hashlib.sha256(manifest_raw.encode("utf8")).hexdigest() if manifest_raw is not None else None)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return (cached, key, True) if with_signature else cached
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("schema") != 1 or value.get("version_id") != version_id:
            raise ValueError("Sparse index identity or schema is invalid.")
        docs = value.get("documents")
        if not isinstance(docs, list) or not all(isinstance(d, dict) for d in docs):
            raise ValueError("Sparse index records must be objects.")
        ids = []
        for record in docs:
            meta = record.get("metadata")
            if (not isinstance(record.get("id"), str) or not record["id"] or
                    not all(isinstance(record.get(field), str) for field in ("text", "title", "filename")) or
                    not isinstance(meta, dict) or meta.get("version_id") != version_id or
                    not isinstance(meta.get("document_id"), str) or not meta["document_id"] or
                    not isinstance(record.get("terms"), list) or not all(isinstance(t, str) for t in record["terms"])):
                raise ValueError("Sparse record identity or types are invalid.")
            expected_terms = tokenize(" ".join((record["title"], record["filename"], str(meta.get("heading", "")), record["text"])))
            if record["terms"] != expected_terms:
                raise ValueError("Sparse record terms do not match its source text.")
            if meta.get("content_hash") and meta["content_hash"] != fingerprint(record["text"]):
                raise ValueError("Sparse record content hash is invalid.")
            if "chunk_ordinal" in meta and "config_hash" in meta:
                ordinal = meta["chunk_ordinal"]
                if type(ordinal) is not int or ordinal < 0 or record["id"] != str(uuid5(UUID(version_id), f"{meta['config_hash']}:{ordinal}")):
                    raise ValueError("Sparse chunk ID differs from its canonical identity.")
            ids.append(record["id"])
        if len(set(ids)) != len(ids) or value.get("content_hash") != fingerprint(docs):
            raise ValueError("Sparse index contains invalid or duplicate chunk IDs.")
        if manifest_raw is not None:
            manifest = json.loads(manifest_raw)
            if (not isinstance(manifest, list) or not all(isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(row.get("metadata"), dict) for row in manifest)):
                raise ValueError("Sparse manifest is invalid.")
            expected = {row["id"]: row["metadata"] for row in manifest}
            if len(expected) != len(manifest) or set(expected) != set(ids) or any(expected[row["id"]] != row["metadata"] for row in docs):
                raise ValueError("Sparse IDs or metadata differ from canonical manifest.")
        _remember(_cache, key, docs)
        return (docs, key, False) if with_signature else docs


def search(query, versions, k=15, eligible_ids=None, profile=None):
    """Score all eligible documents together so BM25 corpus stats are shared."""
    documents, missing, signatures = [], [], []
    if profile is not None:
        for key in ("index_load_validation_ms", "eligibility_filter_ms", "query_tokenization_ms",
            "corpus_stats_key_ms", "corpus_stats_build_ms", "candidate_scoring_ms",
            "final_ranking_topk_ms", "index_cache_hits", "index_cache_misses",
            "corpus_stats_cache_hits", "corpus_stats_cache_misses", "eligible_documents",
            "matched_candidates"):
            profile.setdefault(key, 0)
    for version in versions:
        version_id, title, filename = version[:3]
        expected_document = version[3] if len(version) > 3 else None
        started = time.perf_counter() if profile is not None else None
        try:
            records, signature, cache_hit = load_index(version_id, require_manifest=expected_document is not None,
                with_signature=True)
            if records is not None and expected_document is not None and any(record["metadata"]["document_id"] != expected_document for record in records):
                raise ValueError("Sparse document identity differs from active version.")
        except (OSError, ValueError, KeyError, TypeError):
            records, signature, cache_hit = None, None, False
        if profile is not None:
            profile["index_load_validation_ms"] += (time.perf_counter() - started) * 1000
            profile["index_cache_hits"] += int(cache_hit)
            profile["index_cache_misses"] += int(not cache_hit)
        if records is None:
            missing.append(version_id)
            continue
        signatures.append(signature)
        filter_started = time.perf_counter() if profile is not None else None
        for record in records:
            if eligible_ids is None or record["metadata"].get("document_id") in eligible_ids:
                documents.append(record)
        if profile is not None:
            profile["eligibility_filter_ms"] += (time.perf_counter() - filter_started) * 1000
    token_started = time.perf_counter() if profile is not None else None
    query_terms = tokenize(query)
    if profile is not None:
        profile["query_tokenization_ms"] += (time.perf_counter() - token_started) * 1000
    if not documents or not query_terms:
        return [], missing
    key_started = time.perf_counter() if profile is not None else None
    documents.sort(key=lambda item: item["id"])
    # Index signatures already cover terms, metadata, IDs, and source text. Reuse
    # them instead of JSON-serializing and hashing every chunk on each query.
    compiled_key = (str(path("sparse")), tuple(sorted(signatures)),
        tuple(sorted(eligible_ids)) if eligible_ids is not None else None)
    if profile is not None:
        profile["corpus_stats_key_ms"] += (time.perf_counter() - key_started) * 1000
    with _cache_lock:
        compiled = _compiled_cache.get(compiled_key)
        if compiled is None:
            stats_started = time.perf_counter() if profile is not None else None
            frequencies = [Counter(item["terms"]) for item in documents]
            lengths = [sum(freq.values()) for freq in frequencies]
            average = sum(lengths) / len(lengths) or 1
            df = Counter(term for freq in frequencies for term in freq)
            compiled = (frequencies, lengths, average, df)
            _remember(_compiled_cache, compiled_key, compiled)
            if profile is not None:
                profile["corpus_stats_build_ms"] += (time.perf_counter() - stats_started) * 1000
                profile["corpus_stats_cache_misses"] += 1
        else:
            _compiled_cache.move_to_end(compiled_key)
            if profile is not None:
                profile["corpus_stats_cache_hits"] += 1
    frequencies, lengths, average, df = compiled
    k1, b = 1.5, .75
    hits = []
    scoring_started = time.perf_counter() if profile is not None else None
    for document, counts, length in zip(documents, frequencies, lengths):
        score = 0.0
        for term in query_terms:
            tf = counts[term]
            if tf:
                idf = math.log(1 + (len(documents) - df[term] + .5) / (df[term] + .5))
                score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / average))
        if score:
            hits.append(dict(id=document["id"], text=document["text"], metadata=document["metadata"],
                title=document["title"], filename=document["filename"], sparse_score=score))
    if profile is not None:
        profile["candidate_scoring_ms"] += (time.perf_counter() - scoring_started) * 1000
    ranking_started = time.perf_counter() if profile is not None else None
    hits.sort(key=lambda hit: (-hit["sparse_score"], hit["id"]))
    result = hits[:k]
    if profile is not None:
        profile["final_ranking_topk_ms"] += (time.perf_counter() - ranking_started) * 1000
        profile["eligible_documents"] += len(documents)
        profile["matched_candidates"] += len(hits)
    return result, missing


def rrf(rankings, k=60):
    if type(k) is not int or k <= 0:
        raise ValueError("RRF rank constant must be a positive integer.")
    scores, records, ranks, branch_scores, branch_distances = {}, {}, {}, {}, {}
    for branch, hits in rankings.items():
        seen = set()
        for rank, hit in enumerate(hits, 1):
            chunk_id = hit["id"]
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            existing = records.get(chunk_id)
            if existing is not None:
                # A shared ID is the join key between lexical and dense indexes.
                # Never let the later branch silently replace its source identity.
                for field in ("text", "metadata"):
                    left, right = existing.get(field), hit.get(field)
                    if left is not None and right is not None and left != right:
                        raise ValueError("Retrieval branches disagree on chunk source identity or content.")
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (k + rank)
            record = records.setdefault(chunk_id, {})
            # Preserve source identity, but keep ranking-specific numbers scoped
            # to their branch. A cosine distance and a BM25 score are not
            # interchangeable, and later query variants must not overwrite them.
            for field, target in (("sparse_score", branch_scores), ("distance", branch_distances)):
                if hit.get(field) is not None:
                    target.setdefault(chunk_id, {})[branch] = hit[field]
            for field, value in hit.items():
                if field not in ("sparse_score", "distance", "similarity", "rrf_score", "branch_ranks",
                                 "branch_scores", "branch_distances"):
                    record.setdefault(field, value)
            ranks.setdefault(chunk_id, {})[branch] = rank
    result = []
    for chunk_id in sorted(scores, key=lambda item: (-scores[item], item)):
        hit = records[chunk_id]
        hit.update(rrf_score=scores[chunk_id], branch_ranks=ranks[chunk_id],
                   branch_scores=branch_scores.get(chunk_id, {}),
                   branch_distances=branch_distances.get(chunk_id, {}),
                   distance=None, similarity=None, sparse_score=None)
        result.append(hit)
    return result
