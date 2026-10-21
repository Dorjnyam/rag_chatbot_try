"""Prepare, atomically activate, or roll back OpenAI document indexes."""
import time
from datetime import datetime, timezone
from flask import current_app
from sqlalchemy import select, func
from ..extensions import db
from ..models import Document, DocumentVersion, uid
from .storage import fingerprint, mutation_lock, path, read_json, write_json
from .ingestion import index_config
from .embedding_contract import collection_contract_metadata, compatible_embedding_spaces
from .openai_provider import openai_client
from .vectorstore import store
from . import sparse


def _manifest_path(value):
    if not value or value.startswith(("/", "\\")) or ":" in value:
        raise ValueError("Manifest must be a relative path inside the application data directory.")
    return value if value.endswith(".json") else value + ".json"


def _active_pairs():
    return db.session.execute(select(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.id == Document.active_version_id)
        .where(Document.state == "active", DocumentVersion.state == "ready")
        .order_by(Document.id)).all()


def _uses_embedding_config(version, config):
    stored = version.config
    return compatible_embedding_spaces(stored, config)


def prepare(manifest_name, dry_run=False):
    manifest_name = _manifest_path(manifest_name) if manifest_name else None
    if not dry_run and not manifest_name:
        raise ValueError("Provide --manifest for resumable preparation.")
    config = index_config()
    rows = _active_pairs()
    store_ = store()
    eligible, excluded, chunks_total, estimated_tokens = [], [], 0, 0
    already_compatible = 0
    for doc, source in rows:
        if _uses_embedding_config(source, config):
            already_compatible += 1
            continue
        actual = store_.ids(source.collection_name, source.id)
        if not actual:
            excluded.append({"document_id": doc.id, "version_id": source.id, "reason": "active dense index is empty"})
            continue
        dense = store_.collection(source.collection_name).get(where={"version_id": source.id}, include=["documents", "metadatas"])
        if set(dense["ids"]) != actual or any(m.get("version_id") != source.id for m in dense["metadatas"]):
            excluded.append({"document_id": doc.id, "version_id": source.id, "reason": "stored chunk manifest is inconsistent"})
            continue
        texts = dense["documents"]
        # Local estimate only: no tokenizer endpoint or provider request is used.
        estimated_tokens += sum(max(1, len(t.encode("utf8")) // 3) for t in texts)
        chunks_total += len(texts)
        eligible.append({"document_id": doc.id, "title": doc.title, "source_version_id": source.id, "chunks": len(texts)})
    summary = {"target_provider": "openai", "target_model": config["embedding_model"], "dimensions": config["dimensions"],
        "eligible_documents": len(eligible), "eligible_chunks": chunks_total, "estimated_input_tokens": estimated_tokens,
        "already_compatible_documents": already_compatible,
        "estimate_method": "UTF-8 bytes / 3 (rough local estimate; no API call)", "excluded": excluded, "dry_run": dry_run}
    if dry_run:
        return summary
    with mutation_lock():
        if path(manifest_name).exists():
            manifest = read_json(manifest_name)
            if manifest.get("status") in ("activated", "rolled_back"):
                raise ValueError("This migration has already been activated or rolled back; use a new manifest for another cutover.")
            if manifest.get("target_provider") != "openai" or manifest.get("target_config") != config:
                raise ValueError("Existing manifest uses a different target embedding configuration.")
            entries = {entry["document_id"]: entry for entry in manifest["entries"]}
        else:
            manifest = {"schema": 1, "target_provider": "openai", "target_config": config,
                "created_at": time.time(), "status": "preparing", "entries": []}
            entries = {}
        client = None
        try:
            client = openai_client(time.monotonic() + 900)
            for doc, source in rows:
                if _uses_embedding_config(source, config):
                    if doc.id in entries:
                        raise ValueError(f"Active source for {doc.id} already uses the target embedding space; use a new manifest.")
                    continue
                if any(x["document_id"] == doc.id for x in excluded):
                    continue
                entry = entries.get(doc.id)
                if entry and entry.get("source_version_id") != source.id:
                    raise ValueError(f"Active source changed for {doc.id}; use a new manifest.")
                if not entry:
                    target_config = dict(config, migration_manifest=manifest_name, migration_source_version=source.id)
                    previous_migrations = db.session.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)).all()
                    target = next((candidate for candidate in previous_migrations if
                        candidate.config.get("migration_manifest") == manifest_name and
                        candidate.config.get("migration_source_version") == source.id), None)
                    if target is None:
                        number = db.session.scalar(select(func.max(DocumentVersion.version_number)).where(DocumentVersion.document_id == doc.id)) or 0
                        target = DocumentVersion(id=uid(), document_id=doc.id, version_number=number + 1,
                            original_filename=source.original_filename, media_type=source.media_type, sha256=source.sha256,
                            file_path=source.file_path, extraction_path=source.extraction_path, state="indexing",
                            warnings=list(source.warnings or []), config=target_config, config_hash=fingerprint(target_config),
                            collection_name="rag_" + fingerprint(target_config)[:24], expected_chunks=source.expected_chunks)
                        db.session.add(target)
                        db.session.commit()
                    entry = {"document_id": doc.id, "source_version_id": source.id, "target_version_id": target.id,
                        "source_config_hash": source.config_hash,
                        "source_provider": source.config.get("provider", "unknown"),
                        "status": "pending", "chunks": source.expected_chunks}
                    manifest["entries"].append(entry)
                    entries[doc.id] = entry
                    write_json(manifest_name, manifest)
                target = db.session.get(DocumentVersion, entry["target_version_id"])
                if target.state == "ready" and target.manifest_path and path(target.manifest_path).exists():
                    expected = {item["id"] for item in read_json(target.manifest_path)}
                    sparse_records = sparse.load_index(target.id)
                    if expected and expected == store_.ids(target.collection_name, target.id) and sparse_records is not None and {r["id"] for r in sparse_records} == expected:
                        entry.update(status="ready", chunks=len(expected), target_config_hash=target.config_hash)
                        write_json(manifest_name, manifest)
                        continue
                data = store_.collection(source.collection_name).get(where={"version_id": source.id}, include=["documents", "metadatas"])
                chunks = [dict(id=i, text=t, metadata=dict(m)) for i, t, m in zip(data["ids"], data["documents"], data["metadatas"])]
                if {item["id"] for item in chunks} != store_.ids(source.collection_name, source.id):
                    raise ValueError(f"Source chunks changed for {doc.id}.")
                chunks = client.fit_chunks(chunks, config["embedding_model"], target.id, target.config)
                for item in chunks:
                    item["metadata"].update(document_id=doc.id, version_id=target.id, language=doc.language,
                        config_hash=target.config_hash, **collection_contract_metadata(target.config))
                target.expected_chunks, target.manifest_path = len(chunks), f"manifests/{target.id}.json"
                write_json(target.manifest_path, [{"id": c["id"], "metadata": c["metadata"]} for c in chunks])
                db.session.commit()
                store_.delete_version(target.collection_name, target.id)
                sparse.delete_index(target.id)
                from .ingestion import embedding_batches
                for batch in embedding_batches(chunks, current_app.config["EMBEDDING_BATCH_SIZE"]):
                    vectors = client.embed([c["text"] for c in batch], "RETRIEVAL_DOCUMENT", config["embedding_model"], config["dimensions"])
                    store_.upsert(target.collection_name, batch, vectors, target.config)
                dense_ids = store_.ids(target.collection_name, target.id)
                sparse_ids = sparse.write_index(target.id, chunks, doc.title, target.original_filename)
                expected = {c["id"] for c in chunks}
                if dense_ids != expected or sparse_ids != expected:
                    raise ValueError(f"Target dense/sparse ID verification failed for {doc.id}.")
                target.state, target.finished_at = "ready", datetime.now(timezone.utc)
                db.session.commit()
                entry.update(status="ready", chunks=len(chunks), target_config_hash=target.config_hash)
                write_json(manifest_name, manifest)
            manifest["status"] = "ready"
            write_json(manifest_name, manifest)
        except Exception:
            db.session.rollback()
            raise
        finally:
            if client and hasattr(client, "close"):
                client.close()
    summary.update(status=manifest["status"], manifest=manifest_name)
    return summary


def _read_ready_manifest(manifest_name):
    name = _manifest_path(manifest_name)
    manifest = read_json(name)
    if manifest.get("schema") != 1 or not manifest.get("entries") or manifest.get("status") not in ("ready", "activating"):
        raise ValueError("Manifest is missing, incomplete, or not ready for activation.")
    return name, manifest


def activate(manifest_name):
    with mutation_lock():
        name, manifest = _read_ready_manifest(manifest_name)
        if manifest.get("target_provider") != "openai":
            raise ValueError("Only OpenAI embedding indexes can be activated by this application.")
        for item in manifest["entries"]:
            doc = db.session.get(Document, item["document_id"])
            source, target = db.session.get(DocumentVersion, item["source_version_id"]), db.session.get(DocumentVersion, item["target_version_id"])
            if not doc or doc.active_version_id not in (item["source_version_id"], item["target_version_id"]) or not source or not target or target.state != "ready":
                raise ValueError(f"Migration is stale or incomplete for {item['document_id']}.")
            if source.config_hash != item["source_config_hash"] or source.state != "ready":
                raise ValueError(f"Previous version changed for {item['document_id']}.")
            if target.config.get("migration_source_version") != source.id or target.config.get("provider") != manifest["target_provider"]:
                raise ValueError("Migration target identity does not match its source or provider.")
            if (target.config_hash != fingerprint(target.config) or
                    any(target.config.get(key) != manifest["target_config"].get(key)
                        for key in ("provider", "embedding_model", "dimensions", "normalization", "chunk_fitting"))):
                raise ValueError("Migration target embedding configuration is stale or inconsistent.")
            expected = {x["id"] for x in read_json(target.manifest_path)}
            if expected != store().ids(target.collection_name, target.id):
                raise ValueError(f"Dense index verification failed for {doc.id}.")
            sparse_records = sparse.load_index(target.id)
            if sparse_records is None or {x["id"] for x in sparse_records} != expected:
                raise ValueError(f"Sparse index verification failed for {doc.id}.")
        manifest["status"] = "activating"
        write_json(name, manifest)
        try:
            for item in manifest["entries"]:
                doc = db.session.get(Document, item["document_id"])
                if doc.active_version_id == item["source_version_id"]:
                    doc.active_version_id = item["target_version_id"]
            db.session.commit()
        except Exception:
            db.session.rollback()
            manifest["status"] = "ready"
            write_json(name, manifest)
            raise
        manifest["status"] = "activated"
        write_json(name, manifest)
        return {"status": "activated", "documents": len(manifest["entries"]), "manifest": name}


def rollback(manifest_name):
    with mutation_lock():
        name = _manifest_path(manifest_name)
        manifest = read_json(name)
        if manifest.get("status") not in ("activated", "rolling_back", "rolled_back"):
            raise ValueError("Manifest is not activated.")
        providers = {item.get("source_provider", "unknown") for item in manifest["entries"]}
        if providers != {"openai"}:
            raise ValueError("Rollback to a non-OpenAI embedding index is disabled.")
        for item in manifest["entries"]:
            doc = db.session.get(Document, item["document_id"])
            if not doc or doc.active_version_id not in (item["target_version_id"], item["source_version_id"]):
                raise ValueError(f"Migration no longer owns the active version for {item['document_id']}.")
            old = db.session.get(DocumentVersion, item["source_version_id"])
            if not old or old.state != "ready":
                raise ValueError(f"Previous version is unavailable for {item['document_id']}.")
        manifest["status"] = "rolling_back"
        write_json(name, manifest)
        try:
            for item in manifest["entries"]:
                doc = db.session.get(Document, item["document_id"])
                if doc.active_version_id == item["target_version_id"]:
                    doc.active_version_id = item["source_version_id"]
            db.session.commit()
        except Exception:
            db.session.rollback()
            manifest["status"] = "activated"
            write_json(name, manifest)
            raise
        manifest["status"] = "rolled_back"
        write_json(name, manifest)
        return {"status": "rolled_back", "documents": len(manifest["entries"]), "manifest": name}
