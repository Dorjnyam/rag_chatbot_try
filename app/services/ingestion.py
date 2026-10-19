"""Document lifecycle: PostgreSQL activation follows verified Chroma writes."""
import hashlib
import time
from pathlib import Path
from flask import current_app
from sqlalchemy import select, func
from ..extensions import db
from ..models import Document, DocumentVersion, now, uid
from .storage import fingerprint, mutation_lock, path, read_json, write_json
from .parsing import PARSER_REVISION, parse_bounded, native_fingerprint
from .chunking import chunk
from .providers import provider
from .vectorstore import store
from . import sparse


def index_config(provider_name=None):
    c = current_app.config
    selected = (provider_name or c.get("AI_PROVIDER", "gemini")).casefold()
    if selected == "openai":
        model, dimensions = c["OPENAI_EMBEDDING_MODEL"], c["OPENAI_EMBEDDING_DIMENSIONS"]
        fit_revision = "openai-tokenfit-v1"
    elif selected == "gemini":
        model = c["GEMINI_EMBEDDING_MODEL"] if provider_name else c["EMBEDDING_MODEL"]
        dimensions = c["GEMINI_EMBEDDING_DIMENSIONS"] if provider_name else c["EMBEDDING_DIMENSIONS"]
        fit_revision = "gemini-bytefit-v1"
    else:
        raise ValueError("Target provider must be openai or gemini.")
    return dict(parser=PARSER_REVISION, strategy="characters-v1", chunk_size=c["CHUNK_SIZE"], chunk_overlap=c["CHUNK_OVERLAP"],
        provider=selected, embedding_model=model, dimensions=dimensions, normalization="l2-v1", chunk_fitting=fit_revision,
        ocr_languages=c["OCR_LANGUAGES"], ocr_min_characters=c["OCR_MIN_CHARACTERS"], forced_pages=[],
        embedding_input_guard="utf8-1900-v1", native_tools=native_fingerprint(c))


def get_document(id_):
    document = db.session.get(Document, id_)
    if not document or document.state != "active":
        raise ValueError("Document is unavailable or deleted.")
    return document


def extraction_cache(version):
    config = version.config
    identity = dict(sha=version.sha256, parser=config["parser"], ocr=config["ocr_languages"],
        minimum=config["ocr_min_characters"], forced=config["forced_pages"])
    if "native_tools" in config:
        identity["native"] = config["native_tools"]
    hash_ = fingerprint(identity)
    return f"extractions/{hash_}.json"


def new_version(document, filename, media_type, sha256, file_path, config):
    number = db.session.scalar(select(func.max(DocumentVersion.version_number)).where(DocumentVersion.document_id == document.id)) or 0
    hash_ = fingerprint(config)
    version = DocumentVersion(id=uid(), document_id=document.id, version_number=number + 1, original_filename=filename,
        media_type=media_type, sha256=sha256, file_path=file_path, config=config, config_hash=hash_, collection_name="rag_" + hash_[:24])
    db.session.add(version)
    db.session.commit()
    return version


def upload(file, title, language, document_id=None):
    if language not in ("mn", "en", "mixed"):
        raise ValueError("Choose Mongolian, English, or mixed.")
    filename = (file.filename or "").replace("\\", "/").split("/")[-1][:255]
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in ("pdf", "docx", "doc"):
        raise ValueError("Upload a PDF, DOCX, or DOC file.")
    content = file.read(current_app.config["MAX_CONTENT_LENGTH"] + 1)
    if not content or len(content) > current_app.config["MAX_CONTENT_LENGTH"]:
        raise ValueError("Upload must be nonempty and at most 20 MiB.")
    digest = hashlib.sha256(content).hexdigest()
    with mutation_lock():
        document = get_document(document_id) if document_id else None
        matches = db.session.scalars(select(DocumentVersion).join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.sha256 == digest, Document.state == "active").order_by(DocumentVersion.created_at.desc())).all()
        config = index_config()
        for match in matches:
            if (not document_id or match.document_id == document_id) and match.config_hash == fingerprint(config):
                return match, True
        relative = f"uploads/{digest}.{extension}"
        # Expensive parser validation is bounded in the extraction subprocess.
        signature = content[:8]
        if extension == "pdf" and not signature.startswith(b"%PDF-"):
            raise ValueError("The upload is not a PDF.")
        if extension == "docx" and not signature.startswith(b"PK"):
            raise ValueError("The upload is not a DOCX archive.")
        if extension == "doc" and signature != bytes.fromhex("d0cf11e0a1b11ae1"):
            raise ValueError("The upload is not a legacy binary DOC file.")
        target = path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        created_source = not target.exists()
        try:
            if created_source:
                created_source = False
                # Exclusive creation preserves an existing content-addressed source.
                with target.open("xb") as handle:
                    created_source = True
                    handle.write(content)
            if document is None:
                document = Document(id=uid(), title=(title or filename).strip()[:240], language=language)
                db.session.add(document)
                db.session.flush()
            version = new_version(document, filename, extension, digest, relative, config)
            return version, False
        except Exception:
            db.session.rollback()
            if created_source:
                try:
                    references = db.session.scalar(select(func.count()).select_from(DocumentVersion)
                        .where(DocumentVersion.file_path == relative))
                    if references == 0:
                        target.unlink(missing_ok=True)
                except Exception:
                    # An unavailable database cannot prove that the source is unreferenced.
                    db.session.rollback()
                    current_app.logger.warning("Upload source cleanup deferred: reference check unavailable")
            raise



def reindex(document_id, forced_pages=()):
    with mutation_lock():
        document = get_document(document_id)
        previous = db.session.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document.id, DocumentVersion.state != "deleted").order_by(DocumentVersion.version_number.desc()))
        if not previous:
            raise ValueError("Document has no source version.")
        config = index_config()
        config["forced_pages"] = sorted(set(forced_pages))
        if any(not isinstance(page, int) or page < 1 or page > current_app.config["PDF_MAX_PAGES"] for page in forced_pages):
            raise ValueError("OCR page overrides must be valid positive page numbers.")
        return new_version(document, previous.original_filename, previous.media_type, previous.sha256, previous.file_path, config)


def approve(version_id, page_choices=None):
    with mutation_lock():
        version = db.session.get(DocumentVersion, version_id)
        if not version or version.state != "review":
            raise ValueError("This version is not awaiting extraction review.")
        get_document(version.document_id)
        artifact = read_json(version.extraction_path)
        choices = page_choices or {}
        for candidate in artifact.get("candidates", []):
            selected = choices.get(str(candidate["page"]), candidate["selected"])
            if selected not in ("native", "ocr") or candidate.get(selected) is None:
                raise ValueError("Selected page representation is unavailable.")
            candidate["selected"] = selected
            block = next(b for b in artifact["blocks"] if b.get("page") == candidate["page"])
            block["text"], block["method"] = candidate[selected], selected
        if not any(b["text"].strip() for b in artifact["blocks"]):
            raise ValueError("Cannot approve an extraction with no usable text.")
        # Review selections belong to this build, preserving the cached original extraction.
        relative = f"extractions/reviewed-{version.id}.json"
        write_json(relative, artifact)
        version.extraction_path = relative
        version.approved_at = now()
        version.state = "pending"
        db.session.commit()
        return version


def process(version_id, require_review=False):
    with mutation_lock():
        version = db.session.get(DocumentVersion, version_id)
        if not version:
            raise ValueError("Unknown document version.")
        document = get_document(version.document_id)
        if version.state == "ready":
            return version
        if version.state == "review":
            raise ValueError("Review and approve the extraction before indexing.")
        # Review versions can outlive a provider switch. Bind only non-active,
        # unindexed builds to the selected embedding space; retain extraction choices.
        target_config = index_config()
        old_config = version.config or {}
        identity = (old_config.get("provider", "gemini"), old_config.get("embedding_model"), old_config.get("dimensions"))
        target_identity = (target_config["provider"], target_config["embedding_model"], target_config["dimensions"])
        if identity != target_identity:
            if document.active_version_id == version.id:
                raise ValueError("Cannot change embedding configuration of an active version; prepare a provider migration.")
            previous_collection = version.collection_name
            store().delete_version(previous_collection, version.id)
            sparse.delete_index(version.id)
            for key in ("parser", "ocr_languages", "ocr_min_characters", "forced_pages", "native_tools"):
                if key in old_config:
                    target_config[key] = old_config[key]
            version.config = target_config
            version.config_hash = fingerprint(target_config)
            version.collection_name = "rag_" + version.config_hash[:24]
            version.expected_chunks = 0
            version.manifest_path = None
            version.error = None
            db.session.commit()
        version.started_at, version.finished_at, version.error = now(), None, None
        deadline = time.monotonic() + current_app.config["PROCESS_TIMEOUT"]
        client = None
        try:
            if not version.extraction_path or not path(version.extraction_path).exists():
                version.state = "parsing"
                version.approved_at = None
                db.session.commit()
                relative = extraction_cache(version)
                if path(relative).exists():
                    artifact = read_json(relative)
                else:
                    parse_config = dict(current_app.config)
                    parse_config.update(OCR_LANGUAGES=version.config["ocr_languages"], OCR_MIN_CHARACTERS=version.config["ocr_min_characters"])
                    artifact = parse_bounded(path(version.file_path), version.media_type, parse_config, version.config["forced_pages"])
                    write_json(relative, artifact)
                version.extraction_path = relative
                version.warnings = artifact["warnings"]
                version.review_required = bool(artifact["warnings"]) or require_review
                db.session.commit()
            artifact = read_json(version.extraction_path)
            if version.review_required and not version.approved_at:
                version.state = "review"
                db.session.commit()
                return version
            version.state = "indexing"
            db.session.commit()
            chunks = chunk(artifact["blocks"], version.config, version.id)
            if not chunks:
                raise ValueError("No usable chunks; this version cannot activate.")
            client = provider(deadline=deadline)
            chunks = client.fit_chunks(chunks, version.config["embedding_model"], version.id, version.config)
            for item in chunks:
                item["metadata"].update(document_id=document.id, version_id=version.id, language=document.language, config_hash=version.config_hash)
            version.expected_chunks = len(chunks)
            version.manifest_path = f"manifests/{version.id}.json"
            write_json(version.manifest_path, [{"id": c["id"], "metadata": c["metadata"]} for c in chunks])
            db.session.commit()
            vectors = store()
            # Remove leftovers from a failed attempt; the staged version is never searchable.
            vectors.delete_version(version.collection_name, version.id)
            for batch in embedding_batches(chunks, current_app.config["EMBEDDING_BATCH_SIZE"]):
                embedded = client.embed([c["text"] for c in batch], "RETRIEVAL_DOCUMENT", version.config["embedding_model"], version.config["dimensions"])
                vectors.upsert(version.collection_name, batch, embedded)
            if vectors.ids(version.collection_name, version.id) != {c["id"] for c in chunks}:
                raise ValueError("Chroma verification failed: indexed IDs differ from the manifest.")
            sparse_ids = sparse.write_index(version.id, chunks, document.title, version.original_filename)
            if sparse_ids != {c["id"] for c in chunks}:
                raise ValueError("BM25 verification failed: indexed IDs differ from the manifest.")
            if time.monotonic() > deadline:
                raise ValueError("Indexing exceeded the processing deadline.")
            version.state, version.finished_at = "ready", now()
            document.active_version_id = version.id
            db.session.commit()
            return version
        except Exception as exc:
            db.session.rollback()
            version = db.session.get(DocumentVersion, version_id)
            version.state, version.error, version.finished_at = "failed", str(exc), now()
            db.session.commit()
            raise ValueError(f"Processing failed: {exc}") from exc
        finally:
            if client and hasattr(client, "close"):
                client.close()


def embedding_batches(chunks, maximum_count, byte_budget=16000):
    """Conservative request budget, explicitly bytes rather than exact tokens."""
    batch, size = [], 0
    for item in chunks:
        length = len(item["text"].encode("utf8"))
        if batch and (len(batch) >= maximum_count or size + length > byte_budget):
            yield batch
            batch, size = [], 0
        batch.append(item)
        size += length
    if batch:
        yield batch


def delete_document(document_id):
    with mutation_lock():
        document = db.session.get(Document, document_id)
        if not document:
            raise ValueError("Unknown document.")
        if document.state == "deleted":
            return
        document.state, document.active_version_id = "deleting", None
        db.session.commit()
        versions = db.session.scalars(select(DocumentVersion).where(DocumentVersion.document_id == document.id)).all()
        for version in versions:
            store().delete_version(version.collection_name, version.id)
            if store().ids(version.collection_name, version.id):
                raise ValueError("Vectors remain; document stays deleting. Retry cleanup.")
            sparse.delete_index(version.id)
        others = db.session.scalars(select(DocumentVersion).join(Document, Document.id == DocumentVersion.document_id)
            .where(Document.id != document.id, Document.state != "deleted")).all()
        referenced = set()
        for other in others:
            referenced.update(filter(None, (other.file_path, other.extraction_path, other.manifest_path, extraction_cache(other))))
        for version in versions:
            artifacts = set(filter(None, (version.file_path, version.extraction_path, version.manifest_path, extraction_cache(version))))
            for relative in artifacts - referenced:
                path(relative).unlink(missing_ok=True)
            converted = path("converted") / Path(version.file_path).stem
            # Only generated DOC conversion artifacts under the known data root.
            source_references = db.session.scalar(select(func.count()).select_from(DocumentVersion).join(Document, Document.id == DocumentVersion.document_id)
                .where(DocumentVersion.file_path == version.file_path, Document.id != document.id, Document.state != "deleted"))
            if not source_references and converted.exists() and converted.is_relative_to(path("converted")):
                for file in converted.glob("*.docx"):
                    file.unlink()
            version.state = "deleted"
        document.state = "deleted"
        db.session.commit()


def reconcile(repair=False):
    """Read-only by default. Repairs invalidate incomplete active builds, never activate them."""
    with mutation_lock():
        issues = []
        versions = db.session.scalars(select(DocumentVersion)).all()
        known = {v.id: v for v in versions}
        for version in versions:
            if version.state == "deleted":
                continue
            if version.state in ("parsing", "indexing"):
                issues.append(dict(kind="interrupted", version=version.id))
                if repair:
                    version.state, version.error = "failed", "Interrupted processing; retry this version."
            for field in ("file_path", "extraction_path", "manifest_path"):
                relative = getattr(version, field)
                if relative and not path(relative).exists():
                    issues.append(dict(kind="missing_artifact", version=version.id, field=field))
            if version.state == "ready":
                expected = {c["id"] for c in read_json(version.manifest_path)} if version.manifest_path and path(version.manifest_path).exists() else set()
                actual = store().ids(version.collection_name, version.id)
                if not expected or expected != actual:
                    issues.append(dict(kind="index_mismatch", version=version.id, expected=len(expected), actual=len(actual)))
                    if repair:
                        version.state, version.error = "failed", "Reconciliation found an incomplete index; retry processing."
                        document = db.session.get(Document, version.document_id)
                        if document.active_version_id == version.id:
                            document.active_version_id = None
                try:
                    sparse_records = sparse.load_index(version.id)
                except (ValueError, OSError):
                    sparse_records = None
                sparse_ids = {item["id"] for item in sparse_records or []}
                if sparse_records is None or sparse_ids != expected:
                    issues.append(dict(kind="sparse_index_missing_or_mismatched", version=version.id, expected=len(expected), actual=len(sparse_ids)))
                    if repair:
                        version.state, version.error = "failed", "Reconciliation found an incomplete sparse index; reindex this version."
                        document = db.session.get(Document, version.document_id)
                        if document.active_version_id == version.id:
                            document.active_version_id = None
        for collection, id_, metadata in store().all_records():
            version = known.get(metadata.get("version_id"))
            if not version or version.state in ("failed", "deleted") or collection != version.collection_name:
                issues.append(dict(kind="orphan_vector", id=id_, collection=collection))
                if repair:
                    store().collection(collection).delete(ids=[id_])
        for document in db.session.scalars(select(Document).where(Document.state == "deleting")):
            issues.append(dict(kind="pending_deletion", document=document.id))
        if repair:
            db.session.commit()
        return issues


def backfill_sparse():
    """Rebuild BM25 indexes for legacy ready versions from persisted Chroma chunks."""
    from . import sparse
    with mutation_lock():
        versions = db.session.execute(select(DocumentVersion, Document).join(Document, Document.id == DocumentVersion.document_id)
            .where(Document.state == "active", DocumentVersion.state == "ready")).all()
        db.session.commit()
        results = []
        for version, document in versions:
            try:
                current = sparse.load_index(version.id)
            except (ValueError, OSError):
                current = None
            expected = store().ids(version.collection_name, version.id)
            if current is not None and {item["id"] for item in current} == expected:
                results.append(dict(version=version.id, status="current", chunks=len(expected)))
                continue
            data = store().collection(version.collection_name).get(where={"version_id": version.id}, include=["documents", "metadatas"])
            chunks = [dict(id=chunk_id, text=text, metadata=metadata) for chunk_id, text, metadata in zip(data["ids"], data["documents"], data["metadatas"])]
            written = sparse.write_index(version.id, chunks, document.title, version.original_filename)
            if written != expected:
                raise ValueError(f"BM25 backfill verification failed for version {version.id}")
            results.append(dict(version=version.id, status="rebuilt", chunks=len(written)))
        return results
