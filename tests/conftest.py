import hashlib
import io
import json
import math
import os
import re
import uuid
import warnings
from contextlib import contextmanager
import pytest
from docx import Document
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from werkzeug.datastructures import FileStorage
from app import create_app
from app.extensions import db
from app.services.chunking import assign_ids


class FakeGemini:
    """Deterministic test double; never evidence of Gemini multilingual quality."""
    def __init__(self, deadline=None):
        self.attempts, self.usage = 0, {}
    def count(self, text, model=None):
        return max(1, math.ceil(len(text.encode()) / 3))
    def fit_chunks(self, chunks, model, version_id, config):
        return assign_ids(chunks, version_id, config)
    def embed(self, texts, task, model=None, dimensions=None):
        from flask import current_app
        from app.services.gemini import normalize_vector
        dims = dimensions or current_app.config["EMBEDDING_DIMENSIONS"]
        vectors = []
        for item in texts:
            vector = [0.] * dims
            for word in re.findall(r"\w+", item.casefold()):
                vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % dims] += 1
            if not any(vector):
                vector[0] = 1
            vectors.append(normalize_vector(vector, dims))
        return vectors
    def generate(self, prompt, system):
        return json.dumps(dict(status="answered", answer="Синтетик журмын дагуу таван ажлын өдөр байна. [S1]", sources=["S1"]), ensure_ascii=False)


@pytest.fixture(autouse=True)
def isolate_secrets_and_dotenv(monkeypatch):
    """No developer credentials or .env values enter any test app."""
    import app as application
    monkeypatch.setattr(application, "load_dotenv", lambda *args, **kwargs: False)
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@contextmanager
def disposable_database(admin_url, engine_factory=create_engine):
    """Create a fresh UUID database; ownership, not a name prefix, permits cleanup."""
    parsed = make_url(admin_url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("TEST_DATABASE_ADMIN_URL must use PostgreSQL")
    token = uuid.uuid4().hex
    name, marker = "rag_test_" + token, "pytest-owned:" + token
    admin = engine_factory(parsed, isolation_level="AUTOCOMMIT")
    owned = False
    try:
        with admin.connect() as connection:
            # Both SQL literals below contain only a locally generated UUID.
            connection.execute(text(f'CREATE DATABASE "{name}"'))
            connection.execute(text(f"COMMENT ON DATABASE \"{name}\" IS '{marker}'"))
            owned = True
        yield parsed.set(database=name).render_as_string(hide_password=False)
    finally:
        if owned:
            try:
                with admin.connect() as connection:
                    actual = connection.scalar(text("SELECT shobj_description(oid, 'pg_database') "
                        "FROM pg_database WHERE datname = :name"), {"name": name})
                    if actual != marker:
                        raise RuntimeError("Database ownership changed; refusing cleanup")
                    connection.execute(text(f'DROP DATABASE "{name}"'))
            except Exception as exc:
                warnings.warn(f"Disposable database cleanup deferred ({type(exc).__name__}); no forced cleanup performed")
        admin.dispose()


@pytest.fixture
def app(tmp_path):
    admin_url = os.getenv("TEST_DATABASE_ADMIN_URL")
    if not admin_url:
        pytest.skip("Integration isolation unavailable: set TEST_DATABASE_ADMIN_URL to a PostgreSQL admin connection with CREATEDB")
    # Entering the context must succeed before any app/schema is initialized.
    database = disposable_database(admin_url)
    try:
        url = database.__enter__()
    except Exception:
        pytest.skip("Cannot provision an owned disposable PostgreSQL database; verify TEST_DATABASE_ADMIN_URL and CREATEDB permission")
    try:
        application = create_app(dict(TESTING=True, DATA_DIR=tmp_path, SECRET_KEY="test-secret", SQLALCHEMY_DATABASE_URI=url,
            AI_PROVIDER="gemini", EMBEDDING_MODEL="gemini-embedding-001", GENERATION_MODEL="gemini-3.5-flash-lite",
            GEMINI_API_KEY="", OPENAI_API_KEY="", GEMINI_FACTORY=FakeGemini, OPENAI_FACTORY=FakeGemini,
            EMBEDDING_DIMENSIONS=128, TRUSTED_HOSTS=["localhost", "127.0.0.1"]))
        with application.app_context():
            try:
                db.create_all()
                yield application
            finally:
                db.session.remove()
                db.engine.dispose()
    finally:
        database.__exit__(None, None, None)


@pytest.fixture
def client(app):
    client = app.test_client()
    client.get("/chat")
    return client


def csrf(client):
    with client.session_transaction() as session:
        return session["csrf"]


def docx_file(text="Request leave five working days in advance.", table=False):
    buffer = io.BytesIO()
    document = Document()
    document.add_heading("Leave", 1)
    document.add_paragraph(text)
    if table:
        row = document.add_table(rows=1, cols=2).rows[0]
        row.cells[0].text, row.cells[1].text = "Days", "5"
    document.save(buffer)
    buffer.seek(0)
    return FileStorage(buffer, filename="policy.docx")


@pytest.fixture
def ready(app):
    from app.services.ingestion import upload, process, approve
    version, duplicate = upload(docx_file(), "Leave policy", "en")
    process(version.id)
    approve(version.id)
    return process(version.id)
