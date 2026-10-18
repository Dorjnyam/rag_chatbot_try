"""Retrieval is independently callable without answer generation."""
import time
from sqlalchemy import select
from flask import current_app
from ..extensions import db
from ..models import Document, DocumentVersion
from .gemini import provider
from .vectorstore import store


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


def retrieve(question, k=5, document_ids=None, language=None, client=None):
    if k not in (1, 3, 5, 10, 20):
        raise ValueError("top-k must be 1, 3, 5, 10, or 20")
    if language and language not in ("mn", "en", "mixed"):
        raise ValueError("Invalid document language filter")
    rows = active_versions(document_ids, language)
    if not rows:
        return [], dict(query_embedding_ms=0, vector_search_ms=0)
    config = current_app.config
    for version, document in rows:
        if (version.config["embedding_model"], version.config["dimensions"]) != (config["EMBEDDING_MODEL"], config["EMBEDDING_DIMENSIONS"]):
            raise ValueError("An active index uses a different embedding space. Reindex all active documents before querying.")
    # Close the read transaction before remote API work.
    groups, titles = {}, {}
    for version, document in rows:
        groups.setdefault(version.collection_name, []).append(version.id)
        titles[version.id] = dict(title=document.title, version_number=version.version_number)
    db.session.commit()
    client = client or provider()
    start = time.monotonic()
    vector = client.embed([question], "RETRIEVAL_QUERY")[0]
    embedded = time.monotonic()
    hits = []
    for collection, ids in groups.items():
        hits.extend(store().query(collection, vector, ids, k))
    hits = sorted(hits, key=lambda h: (h["distance"], h["id"]))[:k]
    for rank, hit in enumerate(hits, 1):
        hit.update(rank=rank, similarity=1 - hit["distance"], selected=False, **titles[hit["metadata"]["version_id"]])
    return hits, dict(query_embedding_ms=(embedded - start) * 1000, vector_search_ms=(time.monotonic() - embedded) * 1000)
