import hashlib
import io
import json
import math
import os
import re
import pytest
from docx import Document
from sqlalchemy import create_engine, text
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


@pytest.fixture
def app(tmp_path):
    url = os.getenv("TEST_DATABASE_URL", "postgresql+psycopg://rag:rag@127.0.0.1:55438/rag_test")
    if not url.rsplit("/", 1)[-1].startswith("rag_test"):
        raise RuntimeError("TEST_DATABASE_URL must name a disposable rag_test* database")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Disposable PostgreSQL unavailable; set TEST_DATABASE_URL")
    finally:
        engine.dispose()
    app = create_app(dict(TESTING=True, DATA_DIR=tmp_path, SECRET_KEY="test-secret", SQLALCHEMY_DATABASE_URI=url,
        AI_PROVIDER="gemini", EMBEDDING_MODEL="gemini-embedding-001", GENERATION_MODEL="gemini-3.5-flash-lite",
        EMBEDDING_DIMENSIONS=128, GEMINI_FACTORY=FakeGemini, TRUSTED_HOSTS=["localhost", "127.0.0.1"]))
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


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
