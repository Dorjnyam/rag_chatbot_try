"""Version-scoped BM25 index sharing Chroma's canonical chunk identifiers."""
import json
import math
import re
import threading
import unicodedata
from collections import Counter, OrderedDict
from uuid import UUID, uuid5
from flask import current_app
from .storage import path, write_json, fingerprint

_cache = OrderedDict()
_compiled_cache = OrderedDict()
_CACHE_LIMIT = 32
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


def load_index(version_id, require_manifest=False):
    target = path(index_path(version_id))
    if not target.exists():
        return None
    # Hash the bytes, rather than trusting mtime/size: same-size corruption and
    # copies retaining file timestamps must never reuse a validated artifact.
    raw = target.read_text(encoding="utf8")
    manifest_target = path(f"manifests/{version_id}.json")
    if require_manifest and not manifest_target.exists():
        raise ValueError("Active sparse index requires its canonical manifest.")
    manifest_raw = manifest_target.read_text(encoding="utf8") if manifest_target.exists() else None
    key = (str(target), fingerprint(raw), fingerprint(manifest_raw))
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached
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
        return docs


def search(query, versions, k=15, eligible_ids=None):
    """Score all eligible documents together so BM25 corpus stats are shared."""
    documents, missing = [], []
    for version in versions:
        version_id, title, filename = version[:3]
        expected_document = version[3] if len(version) > 3 else None
        try:
            records = load_index(version_id, require_manifest=expected_document is not None)
            if records is not None and expected_document is not None and any(record["metadata"]["document_id"] != expected_document for record in records):
                raise ValueError("Sparse document identity differs from active version.")
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
    documents.sort(key=lambda item: item["id"])
    # Include the filtered records, data root and immutable content fingerprints.
    # The cache is deliberately bounded; each filtered corpus owns its statistics.
    compiled_key = (str(path("sparse")), tuple(sorted((item["id"], fingerprint(item)) for item in documents)))
    with _cache_lock:
        compiled = _compiled_cache.get(compiled_key)
        if compiled is None:
            frequencies = [Counter(item["terms"]) for item in documents]
            lengths = [sum(freq.values()) for freq in frequencies]
            average = sum(lengths) / len(lengths) or 1
            df = Counter(term for freq in frequencies for term in freq)
            compiled = (frequencies, lengths, average, df)
            _remember(_compiled_cache, compiled_key, compiled)
        else:
            _compiled_cache.move_to_end(compiled_key)
    frequencies, lengths, average, df = compiled
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
        seen = set()
        for rank, hit in enumerate(hits, 1):
            chunk_id = hit["id"]
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (k + rank)
            records.setdefault(chunk_id, {}).update(hit)
            ranks.setdefault(chunk_id, {})[branch] = rank
    result = []
    for chunk_id in sorted(scores, key=lambda item: (-scores[item], item)):
        hit = records[chunk_id]
        hit.update(rrf_score=scores[chunk_id], branch_ranks=ranks[chunk_id])
        result.append(hit)
    return result
