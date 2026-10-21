"""Readiness probes are local and never create index directories or call AI."""
import json
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
import app as application


class ProbeConnection:
    def __init__(self, rows=(), failure=False):
        self.rows, self.failure, self.commands = rows, failure, []
    def begin(self):
        return nullcontext(self)
    def execute(self, statement):
        self.commands.append(str(statement))
        if self.failure:
            raise RuntimeError("secret-source-contents-must-not-leak")
        return SimpleNamespace(all=lambda: self.rows)


def make_app(tmp_path, monkeypatch, rows=(), failure=False, **config):
    app = application.create_app(dict(TESTING=True, DATA_DIR=tmp_path, SECRET_KEY="synthetic",
        SQLALCHEMY_DATABASE_URI="sqlite://", OPENAI_API_KEY="",
        OPENAI_FACTORY=lambda **kwargs: pytest.fail("readiness must not initialize provider"), **config))
    connection = ProbeConnection(rows, failure)
    engine = SimpleNamespace(connect=lambda: nullcontext(connection))
    monkeypatch.setattr(application, "db", SimpleNamespace(engine=engine))
    return app, connection


def test_readiness_empty_corpus_and_liveness_unchanged(tmp_path, monkeypatch):
    app, connection = make_app(tmp_path, monkeypatch)
    client = app.test_client()
    assert client.get("/health").json == {"status": "ok", "service": "learning-rag"}
    response = client.get("/ready")
    assert response.status_code == 200
    assert all(response.json["checks"].values())
    assert "SET LOCAL statement_timeout = '2000ms'" in connection.commands
    assert "SET TRANSACTION READ ONLY" in connection.commands
    assert not (tmp_path / "chroma").exists()
    assert not (tmp_path / "sparse").exists()


def test_readiness_database_failure_is_safe(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch, failure=True)
    response = app.test_client().get("/ready")
    assert response.status_code == 503
    assert response.json["checks"]["database"] is False
    assert "secret-source" not in response.get_data(as_text=True)


def test_readiness_openai_configuration_requires_key_or_factory(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    app.config["OPENAI_FACTORY"] = None
    response = app.test_client().get("/ready")
    assert response.status_code == 503
    assert response.json["checks"]["openai_configuration"] is False


def test_readiness_missing_index_does_not_create_it(tmp_path, monkeypatch):
    config = {"provider": "openai", "embedding_model": "text-embedding-3-small", "dimensions": 768}
    row = SimpleNamespace(id="synthetic", config=config, collection_name="synthetic",
        expected_chunks=1, manifest_path="manifests/synthetic.json")
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    (manifests / "synthetic.json").write_text(json.dumps([{"id": "chunk"}]))
    app, _ = make_app(tmp_path, monkeypatch, rows=[row], OPENAI_EMBEDDING_MODEL="text-embedding-3-small", OPENAI_EMBEDDING_DIMENSIONS=768)
    response = app.test_client().get("/ready")
    assert response.status_code == 503
    assert response.json["checks"]["embedding_compatibility"] is True
    assert response.json["checks"]["dense_artifacts"] is False
    assert not (tmp_path / "chroma").exists()
    assert not (tmp_path / "sparse").exists()


def test_readiness_rejects_embedding_migration_required(tmp_path, monkeypatch):
    row = SimpleNamespace(id="synthetic", config={"provider": "openai", "embedding_model": "other", "dimensions": 1536},
        collection_name="synthetic", expected_chunks=0, manifest_path=None)
    app, _ = make_app(tmp_path, monkeypatch, rows=[row])
    response = app.test_client().get("/ready")
    assert response.status_code == 503
    assert response.json["checks"]["embedding_compatibility"] is False


def test_readiness_reads_existing_catalog_without_initializing_chroma(tmp_path, monkeypatch):
    import sqlite3
    from app.services import sparse
    config = {"provider": "openai", "embedding_model": "text-embedding-3-small", "dimensions": 768}
    row = SimpleNamespace(id="synthetic", config=config, collection_name="synthetic",
        expected_chunks=1, manifest_path="manifests/synthetic.json")
    (tmp_path / "manifests").mkdir()
    (tmp_path / "manifests/synthetic.json").write_text(json.dumps([{"id": "chunk"}]))
    (tmp_path / "chroma").mkdir()
    with sqlite3.connect(tmp_path / "chroma/chroma.sqlite3") as connection:
        connection.execute("CREATE TABLE collections (name TEXT, dimension INTEGER)")
        connection.execute("INSERT INTO collections VALUES ('synthetic', 768)")
    monkeypatch.setattr(sparse, "load_index", lambda version: [{"id": "chunk"}])
    app, _ = make_app(tmp_path, monkeypatch, rows=[row], OPENAI_EMBEDDING_MODEL="text-embedding-3-small", OPENAI_EMBEDDING_DIMENSIONS=768)
    assert app.test_client().get("/ready").status_code == 200
    assert "vector_store" not in app.extensions
def test_ready_with_real_disposable_postgres_and_synthetic_indexes(app, ready, client):
    response = client.get('/ready')
    assert response.status_code == 200, response.json
    assert all(response.json['checks'].values())
    app.config['OPENAI_EMBEDDING_MODEL'] = 'incompatible-model'
    app.config['EMBEDDING_MODEL'] = 'incompatible-model'
    response = client.get('/ready')
    assert response.status_code == 503
    assert response.json['checks']['embedding_compatibility'] is False
