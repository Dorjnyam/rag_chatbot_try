import secrets
import json
from pathlib import Path
from urllib.parse import urlsplit
from flask import Flask, abort, jsonify, request, session
from dotenv import load_dotenv
from .config import settings, validate
from .extensions import db, migrate


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(settings(app.instance_path))
    if test_config:
        app.config.update(test_config)
    validate(app.config)
    data = Path(app.config["DATA_DIR"])
    data.mkdir(parents=True, exist_ok=True)
    if not app.config["SECRET_KEY"]:
        key = data / "session.key"
        try:
            with key.open("x", encoding="utf8") as handle:
                handle.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        app.config["SECRET_KEY"] = key.read_text(encoding="utf8")
    db.init_app(app)
    migrate.init_app(app, db, compare_type=True)
    from . import models
    from .routes.documents import bp as documents
    from .routes.chat import bp as chat
    from .routes.annotation_review import bp as annotation_review
    app.register_blueprint(documents)
    app.register_blueprint(chat)
    app.register_blueprint(annotation_review)
    from .cli import register
    register(app)

    @app.before_request
    def local_csrf():
        if request.remote_addr not in (None, "127.0.0.1", "::1"):
            abort(403, "This learning application is local-only.")
        session.setdefault("csrf", secrets.token_urlsafe(32))
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                abort(403, "Cross-origin request rejected.")
            supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
            if not secrets.compare_digest(supplied, session["csrf"]):
                abort(400, "Invalid CSRF token. Refresh the page and retry.")

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "learning-rag"}

    @app.get("/ready")
    def ready():
        checks = readiness_checks(app)
        healthy = all(checks.values())
        return jsonify(status="ready" if healthy else "not_ready", checks=checks), 200 if healthy else 503

    @app.errorhandler(ValueError)
    def invalid(exc):
        if request.is_json:
            return jsonify(error="Оруулсан мэдээллээ шалгаад дахин оролдоно уу."), 400
        from flask import render_template
        return render_template("error.html", message="Оруулсан мэдээллээ шалгаад дахин оролдоно уу."), 400

    @app.errorhandler(413)
    def too_large(exc):
        if request.path.startswith("/documents"):
            app.logger.warning("rag_upload %s", json.dumps({"event": "rejected_or_failed", "outcome": "error",
                "stage": "http_body_limit", "content_length_bytes": request.content_length,
                "limit_bytes": app.config["MAX_CONTENT_LENGTH"], "error_type": "RequestEntityTooLarge",
                "error_category": "request_body_size_limit_exceeded"}, separators=(",", ":")))
        return "Файлын хэмжээ 20 MiB хязгаараас хэтэрсэн байна.", 413

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def unavailable(exc):
        message = "Энэ хүсэлтийг гүйцэтгэх эрхгүй эсвэл хүссэн мэдээлэл олдсонгүй."
        if request.is_json:
            return jsonify(error=message), exc.code
        from flask import render_template
        return render_template("error.html", message=message), exc.code

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        frame_ancestors = "'self'" if request.endpoint == "annotation_review.source_preview" else "'none'"
        response.headers["Content-Security-Policy"] = f"default-src 'self'; script-src 'self'; worker-src 'self'; style-src 'self'; font-src 'self' data:; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors {frame_ancestors}; base-uri 'self'; form-action 'self'"
        return response

    return app


def readiness_checks(app):
    """Read-only readiness; never initializes Chroma or calls an AI provider."""
    import os
    import json
    import sqlite3
    from types import SimpleNamespace
    from sqlalchemy import select, text
    from .models import Document, DocumentVersion
    from .services.retrieval import embedding_mismatch
    from .services import sparse
    from .services.storage import path

    checks = dict(database=False, storage=False, openai_configuration=False,
        embedding_compatibility=False, dense_artifacts=False, sparse_artifacts=False)
    checks["openai_configuration"] = bool(app.config.get("OPENAI_API_KEY") or callable(app.config.get("OPENAI_FACTORY")))
    try:
        root = Path(app.config["DATA_DIR"]).resolve()
        checks["storage"] = root.is_dir() and os.access(root, os.R_OK | os.W_OK | os.X_OK)
    except (OSError, ValueError):
        return checks
    rows = []
    try:
        # Use an independent connection: probing must never commit a request session.
        with db.engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SET LOCAL statement_timeout = '2000ms'"))
                connection.execute(text("SET TRANSACTION READ ONLY"))
                connection.execute(text("SELECT 1"))
                statement = select(DocumentVersion.id, DocumentVersion.config,
                    DocumentVersion.collection_name, DocumentVersion.expected_chunks,
                    DocumentVersion.manifest_path).join(Document, Document.active_version_id == DocumentVersion.id).where(
                    Document.state == "active", DocumentVersion.state == "ready")
                rows = connection.execute(statement).all()
        checks["database"] = True
    except Exception:
        return checks
    try:
        checks["embedding_compatibility"] = all(not embedding_mismatch(SimpleNamespace(config=row.config), app.config) for row in rows)
        if not rows:
            checks["dense_artifacts"] = checks["sparse_artifacts"] = True
            return checks
        manifests = {}
        for row in rows:
            if not row.manifest_path:
                return checks
            manifest = json.loads(path(row.manifest_path).read_text(encoding="utf8"))
            ids = {item["id"] for item in manifest}
            if len(ids) != row.expected_chunks or not ids:
                return checks
            manifests[row.id] = ids
        vector_store = app.config.get("VECTOR_STORE") or app.extensions.get("vector_store")
        if vector_store is not None:
            checks["dense_artifacts"] = all(vector_store.ids(row.collection_name, row.id) == manifests[row.id] for row in rows)
        else:
            # PersistentClient may create directories or mutate its catalog. Probe
            # only the existing SQLite catalog through a read-only connection.
            catalog = root / "chroma" / "chroma.sqlite3"
            if catalog.is_file() and os.access(catalog, os.R_OK):
                with sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True, timeout=2) as connection:
                    collections = dict(connection.execute("SELECT name, dimension FROM collections"))
                checks["dense_artifacts"] = all(collections.get(row.collection_name) == row.config.get("dimensions") for row in rows)
        if app.config.get("RETRIEVAL_MODE", "hybrid") == "dense":
            checks["sparse_artifacts"] = True
        else:
            checks["sparse_artifacts"] = all(
                {item["id"] for item in sparse.load_index(row.id) or []} == manifests[row.id] for row in rows)
    except Exception:
        # No exception body, paths, credentials or source content in HTTP output.
        pass
    return checks
