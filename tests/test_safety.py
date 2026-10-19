"""Safety and provider bounds, entirely offline and without a real database."""
from contextlib import nullcontext
from types import SimpleNamespace
import re
import os
import httpx
import pytest
from flask import Flask
from conftest import disposable_database
from app.services.gemini import Gemini
from app.services.openai_provider import OpenAIProvider


class AdminDouble:
    def __init__(self, changed=False):
        self.statements, self.marker, self.changed = [], None, changed
        self.disposed = False
    def connect(self):
        return nullcontext(self)
    def execute(self, statement):
        value = str(statement)
        self.statements.append(value)
        if value.startswith("COMMENT"):
            self.marker = value.split(" IS '")[1][:-1]
    def scalar(self, statement, parameters):
        assert "shobj_description(oid, 'pg_database')" in str(statement)
        assert re.fullmatch(r"rag_test_[a-f0-9]{32}", parameters["name"])
        return "someone-else" if self.changed else self.marker
    def dispose(self):
        self.disposed = True


def test_database_never_reuses_supplied_database():
    admin = AdminDouble()
    urls = []
    def factory(url, **options):
        urls.append(url)
        assert options == {"isolation_level": "AUTOCOMMIT"}
        return admin
    with disposable_database("postgresql+psycopg://test:secret@localhost/existing", factory) as url:
        assert re.search(r"/rag_test_[a-f0-9]{32}$", url)
        assert not any("DROP" in sql for sql in admin.statements)
    assert admin.disposed
    assert sum(sql.startswith("CREATE DATABASE") for sql in admin.statements) == 1
    assert sum(sql.startswith("DROP DATABASE") for sql in admin.statements) == 1
    assert all('"existing"' not in sql for sql in admin.statements)


def test_database_cleanup_refuses_changed_owner():
    admin = AdminDouble(changed=True)
    with pytest.warns(UserWarning, match="cleanup deferred"):
        with disposable_database("postgresql+psycopg://test:secret@localhost/existing", lambda *a, **k: admin):
            pass
    assert not any(sql.startswith("DROP") for sql in admin.statements)


def test_database_rejects_non_postgres_before_connecting():
    with pytest.raises(ValueError, match="PostgreSQL"):
        with disposable_database("sqlite://", lambda *a, **k: pytest.fail("must not connect")):
            pass


@pytest.mark.parametrize("kind", ["gemini", "openai"])
def test_rewrite_transient_failure_has_one_http_attempt(kind):
    app = Flask(__name__)
    attempts = []
    def failure(*args, **kwargs):
        attempts.append(1)
        if kind == "gemini":
            raise httpx.ConnectError("offline")
        from openai import APIConnectionError
        raise APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
    if kind == "gemini":
        adapter = Gemini.__new__(Gemini)
        adapter.config = {"GENERATION_MODEL": "fake", "API_TIMEOUT": 1}
        adapter.client = SimpleNamespace(models=SimpleNamespace(generate_content=failure))
    else:
        adapter = OpenAIProvider.__new__(OpenAIProvider)
        adapter.config = {"API_TIMEOUT": 1, "OPENAI_REASONING_EFFORT": "none"}
        adapter.generation_model = "fake"
        adapter.client = SimpleNamespace(responses=SimpleNamespace(create=failure))
        adapter.client.with_options = lambda **kwargs: adapter.client
    adapter.deadline, adapter.attempts = None, 0
    with app.app_context(), pytest.raises(ValueError):
        adapter.rewrite_query("ajiltan 2026")
    assert attempts == [1]
    assert adapter.attempts == 1


@pytest.mark.parametrize("existing,references,deleted", [(False, 0, True), (False, 1, False), (True, 0, False)])
def test_upload_failure_removes_only_new_unreferenced_source(tmp_path, monkeypatch, existing, references, deleted):
    import hashlib
    from io import BytesIO
    from werkzeug.datastructures import FileStorage
    from app.services import ingestion
    content = b"%PDF-synthetic-source"
    relative = "uploads/" + hashlib.sha256(content).hexdigest() + ".pdf"
    target = tmp_path / relative
    if existing:
        target.parent.mkdir(parents=True)
        target.write_bytes(content)
    session = SimpleNamespace(scalars=lambda *a: SimpleNamespace(all=lambda: []),
        add=lambda *a: None, flush=lambda: None, rollback=lambda: None,
        scalar=lambda *a: references)
    monkeypatch.setattr(ingestion, "db", SimpleNamespace(session=session))
    monkeypatch.setattr(ingestion, "index_config", lambda: {})
    monkeypatch.setattr(ingestion, "mutation_lock", lambda: nullcontext())
    def failed(*args):
        raise RuntimeError("synthetic commit failure")
    monkeypatch.setattr(ingestion, "new_version", failed)
    app = Flask(__name__)
    app.config.update(DATA_DIR=tmp_path, MAX_CONTENT_LENGTH=100)
    with app.app_context(), pytest.raises(RuntimeError, match="commit failure"):
        ingestion.upload(FileStorage(BytesIO(content), filename="synthetic.pdf"), "Synthetic", "en")
    assert target.exists() is not deleted
