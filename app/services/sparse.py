"""Version-scoped BM25 index sharing Chroma's canonical chunk identifiers."""
import json
import math
import re
import threading
import unicodedata
from collections import Counter
from flask import current_app
from .storage import path, read_json, write_json, fingerprint

_cache = {}
_cache_lock = threading.RLock()
TOKEN_RE = re.compile(r"https?://[^\s]+|[\w]+(?:[./_-][\w]+)*", re.UNICODE)


def tokenize(text):
    normalized = unicodedata.normalize("NFC", str(text or "")).casefold()
    return TOKEN_RE.findall(normalized)


def index_path(version_id):
    return f"sparse/{version_id}.json"


def write_index(version_id, chunks, title, filename):
    documents = []
    for chunk in chunks:
        meta = dict(chunk["metadata"])
        documents.append(dict(id=chunk["id"], text=chunk["text"], metadata=meta,
            title=title, filename=filename, terms=tokenize(" ".join((title, filename, meta.get("heading", ""), chunk["text"])))))
    artifact = dict(schema=1, version_id=version_id, content_hash=fingerprint(documents), documents=documents)
    write_json(index_path(version_id), artifact)
    with _cache_lock:
        _cache.pop(version_id, None)
    return {item["id"] for item in documents}


def delete_index(version_id):
    with _cache_lock:
        _cache.pop(version_id, None)
    path(index_path(version_id)).unlink(missing_ok=True)


def load_index(version_id):
    target = path(index_path(version_id))
    if not target.exists():
        return None
    stamp = target.stat().st_mtime_ns
    with _cache_lock:
        cached = _cache.get(version_id)
        if cached and cached[0] == stamp:
            return cached[1]
        value = read_json(index_path(version_id))
        if value.get("schema") != 1 or value.get("version_id") != version_id:
            raise ValueError("Sparse index identity or schema is invalid.")
        docs = value.get("documents")
        if not isinstance(docs, list) or len({d.get("id") for d in docs}) != len(docs):
            raise ValueError("Sparse index contains invalid or duplicate chunk IDs.")
        _cache[version_id] = (stamp, docs)
        return docs


def search(query, versions, k=15, eligible_ids=None):
    """Score all eligible documents together so BM25 corpus stats are shared."""
    documents, missing = [], []
    for version_id, title, filename in versions:
        try:
            records = load_index(version_id)
        except (OSError, ValueError, KeyError, TypeError):
            records = None
        if records is None:
            missing.append(version_id)
            continue
        for record in records:
            if eligible_ids is None or record["metadata"].get("document_id") in eligible_ids:
                documents.append(record)
    query_terms = tokenize(query)
    if not documents or not query_terms:
        return [], missing
    frequencies = [Counter(item["terms"]) for item in documents]
    lengths = [sum(freq.values()) for freq in frequencies]
    average = sum(lengths) / len(lengths) or 1
    df = Counter(term for freq in frequencies for term in freq)
    k1, b = 1.5, .75
    hits = []
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
    hits.sort(key=lambda hit: (-hit["sparse_score"], hit["id"]))
    return hits[:k], missing


def rrf(rankings, k=60):
    scores, records, ranks = {}, {}, {}
    for branch, hits in rankings.items():
        for rank, hit in enumerate(hits, 1):
            chunk_id = hit["id"]
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (k + rank)
            records.setdefault(chunk_id, {}).update(hit)
            ranks.setdefault(chunk_id, {})[branch] = rank
    result = []
    for chunk_id in sorted(scores, key=lambda item: (-scores[item], item)):
        hit = records[chunk_id]
        hit.update(rrf_score=scores[chunk_id], branch_ranks=ranks[chunk_id])
        result.append(hit)
    return result
