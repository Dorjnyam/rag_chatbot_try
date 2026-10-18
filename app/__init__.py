import secrets
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
    app.register_blueprint(documents)
    app.register_blueprint(chat)
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

    @app.errorhandler(ValueError)
    def invalid(exc):
        if request.is_json:
            return jsonify(error=str(exc)), 400
        from flask import render_template
        return render_template("error.html", message=str(exc)), 400

    @app.errorhandler(413)
    def too_large(exc):
        return "Upload exceeds the 20 MiB limit.", 413

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    return app
