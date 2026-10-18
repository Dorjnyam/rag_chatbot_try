from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import select
from ..extensions import db
from ..models import Document, DocumentVersion
from ..services import ingestion
from ..services.storage import path, read_json

bp = Blueprint("documents", __name__)


@bp.get("/")
def home():
    return redirect(url_for("chat.chat"))


@bp.get("/documents")
def documents():
    docs = db.session.scalars(select(Document).order_by(Document.created_at.desc())).all()
    versions = db.session.scalars(select(DocumentVersion).order_by(DocumentVersion.version_number.desc())).all()
    return render_template("documents.html", documents=docs, versions=versions)


@bp.post("/documents")
@bp.post("/documents/<document_id>/versions")
def upload(document_id=None):
    file = request.files.get("file")
    if not file:
        raise ValueError("Choose a document file.")
    version, duplicate = ingestion.upload(file, request.form.get("title", ""), request.form.get("language", "mixed"), document_id)
    flash("Identical source/build already exists." if duplicate else "Upload saved. Process it to inspect extraction and build the index.")
    return redirect(url_for("documents.version", version_id=version.id))


@bp.get("/versions/<version_id>")
def version(version_id):
    version = db.get_or_404(DocumentVersion, version_id)
    document = db.get_or_404(Document, version.document_id)
    artifact = read_json(version.extraction_path) if version.extraction_path and path(version.extraction_path).exists() else None
    manifest = read_json(version.manifest_path) if version.manifest_path and path(version.manifest_path).exists() else []
    return render_template("version.html", version=version, document=document, artifact=artifact, manifest=manifest)


@bp.get("/versions/<version_id>/source")
def source(version_id):
    version = db.get_or_404(DocumentVersion, version_id)
    document = db.get_or_404(Document, version.document_id)
    if document.state != "active" or not path(version.file_path).exists():
        abort(410)
    return send_file(path(version.file_path), as_attachment=version.media_type != "pdf", download_name=version.original_filename)


@bp.post("/versions/<version_id>/process")
def process(version_id):
    version = ingestion.process(version_id)
    flash("Extraction needs review." if version.state == "review" else "Index verified and version activated.")
    return redirect(url_for("documents.version", version_id=version.id))


@bp.post("/versions/<version_id>/approve")
def approve(version_id):
    if request.form.get("reviewed") != "yes":
        raise ValueError("Compare extraction against the original and confirm review first.")
    choices = {key[5:]: value for key, value in request.form.items() if key.startswith("page_")}
    ingestion.approve(version_id, choices)
    flash("Review recorded. Process this version to generate embeddings.")
    return redirect(url_for("documents.version", version_id=version_id))


@bp.post("/documents/<document_id>/reindex")
def reindex(document_id):
    try:
        pages = [int(p.strip()) for p in request.form.get("ocr_pages", "").split(",") if p.strip()]
    except ValueError as exc:
        raise ValueError("OCR overrides must be comma-separated page numbers.") from exc
    version = ingestion.reindex(document_id, pages)
    return redirect(url_for("documents.version", version_id=version.id))


@bp.post("/documents/<document_id>/delete")
def delete(document_id):
    ingestion.delete_document(document_id)
    flash("Document vectors and unreferenced files deleted. Historical records retain a tombstone.")
    return redirect(url_for("documents.documents"))
