import io
import json
from pathlib import Path
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import Document, DocumentVersion, QueryLog, uid
from app.services import ingestion
from app.services.storage import path, read_json, mutation_lock
from app.services.vectorstore import store
from app.services.rag import ask
from conftest import docx_file, csrf, FakeGemini

pytestmark = pytest.mark.integration


def test_lifecycle_review_idempotence_and_persistent_chroma(app):
    version, duplicate = ingestion.upload(docx_file(), "Leave", "en")
    assert not duplicate and version.state == "pending"
    assert ingestion.process(version.id).state == "review"
    assert db.session.get(Document, version.document_id).active_version_id is None
    with pytest.raises(ValueError):
        ingestion.process(version.id)
    ingestion.approve(version.id)
    ingestion.process(version.id)
    expected = {c["id"] for c in read_json(version.manifest_path)}
    assert store().ids(version.collection_name, version.id) == expected
    from app.services.vectorstore import VectorStore
    assert VectorStore().ids(version.collection_name, version.id) == expected
    ingestion.process(version.id)
    from werkzeug.datastructures import FileStorage
    second, duplicate = ingestion.upload(FileStorage(io.BytesIO(path(version.file_path).read_bytes()), filename="policy.docx"), "Duplicate", "en")
    assert duplicate and second.id == version.id
    assert not ingestion.reconcile()


def test_failure_never_replaces_active_and_retry_recovers(app, ready, monkeypatch):
    active_id = ready.id
    updated, _ = ingestion.upload(docx_file("Updated leave policy: ten days."), "Leave", "en", ready.document_id)
    ingestion.process(updated.id)
    ingestion.approve(updated.id)
    real = store().upsert
    def fail(*args):
        real(*args)
        raise ValueError("simulated interruption after vector writes")
    monkeypatch.setattr(store(), "upsert", fail)
    with pytest.raises(ValueError, match="interruption"):
        ingestion.process(updated.id)
    assert updated.state == "failed"
    assert db.session.get(Document, ready.document_id).active_version_id == active_id
    log, hits = ask("leave", uid(), retrieval_only=True)
    assert {h["metadata"]["version_id"] for h in hits} == {active_id}
    monkeypatch.setattr(store(), "upsert", real)
    ingestion.process(updated.id)
    assert db.session.get(Document, ready.document_id).active_version_id == updated.id
    assert len(store().ids(updated.collection_name, updated.id)) == updated.expected_chunks


def test_delete_failure_excludes_document_and_retry_cleans(app, ready, monkeypatch):
    real = store().delete_version
    monkeypatch.setattr(store(), "delete_version", lambda *args: (_ for _ in ()).throw(ValueError("disk unavailable")))
    with pytest.raises(ValueError):
        ingestion.delete_document(ready.document_id)
    assert db.session.get(Document, ready.document_id).state == "deleting"
    log, hits = ask("leave", uid())
    assert log.status == "insufficient_evidence" and not hits
    monkeypatch.setattr(store(), "delete_version", real)
    ingestion.delete_document(ready.document_id)
    assert not store().ids(ready.collection_name, ready.id)
    assert not path(ready.file_path).exists()
    assert not path(ingestion.extraction_cache(ready)).exists()
    assert db.session.get(Document, ready.document_id).state == "deleted"


def test_reconcile_detects_missing_vectors_and_invalidates(app, ready):
    store().delete_version(ready.collection_name, ready.id)
    assert any(i["kind"] == "index_mismatch" for i in ingestion.reconcile())
    assert ready.state == "ready"
    ingestion.reconcile(repair=True)
    assert ready.state == "failed"
    assert db.session.get(Document, ready.document_id).active_version_id is None
    ingestion.process(ready.id)
    assert ready.state == "ready"


def test_database_rejects_another_documents_active_version(app, ready):
    document = Document(id=uid(), title="Other", language="en")
    db.session.add(document)
    db.session.commit()
    document.active_version_id = ready.id
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_grounded_chat_trace_and_no_duplicate_content(app, ready):
    class TransactionChecking(FakeGemini):
        def fit_chunks(self, *args):
            assert not db.session().in_transaction(), "DB transaction open during embedding preparation"
            return super().fit_chunks(*args)
        def embed(self, *args, **kwargs):
            assert not db.session().in_transaction(), "DB transaction open during embeddings"
            return super().embed(*args, **kwargs)
        def count(self, *args, **kwargs):
            assert not db.session().in_transaction(), "DB transaction open during token counting"
            return super().count(*args, **kwargs)
        def generate(self, *args):
            assert not db.session().in_transaction(), "DB transaction open during generation"
            return super().generate(*args)
    app.config["GEMINI_FACTORY"] = TransactionChecking
    rebuilt = ingestion.reindex(ready.document_id)
    ingestion.process(rebuilt.id)
    ingestion.approve(rebuilt.id)
    ingestion.process(rebuilt.id)
    log, hits = ask("leave", uid())
    assert log.status == "answered", log.error
    assert log.citations[0]["label"] == "S1"
    assert "text" not in log.trace[0]
    assert hits[0]["selected"]
    assert log.timings["total_ms"] > 0


def test_bad_citation_retries_once_and_records_error(app, ready):
    class Invalid(FakeGemini):
        calls = 0
        def generate(self, prompt, system):
            Invalid.calls += 1
            return json.dumps(dict(status="answered", answer="Unsupported [S99]", sources=["S99"]))
    app.config["GEMINI_FACTORY"] = Invalid
    log, _ = ask("leave", uid())
    assert log.status == "error" and Invalid.calls == 2
    assert "not supplied" in log.error


def test_changed_embedding_space_fails_explicitly(app, ready):
    app.config["EMBEDDING_MODEL"] = "different-model"
    log, _ = ask("leave", uid())
    assert log.status == "error" and "different embedding space" in log.error


def test_empty_corpus_no_api_key(app):
    app.config.pop("GEMINI_FACTORY")
    app.config["GEMINI_API_KEY"] = ""
    log, hits = ask("Чөлөөний журам?", uid())
    assert log.status == "insufficient_evidence" and not hits


def test_ui_csrf_history_escaping_and_session_isolation(app, client, ready):
    assert client.post("/chat/messages", json={"question":"leave"}).status_code == 400
    assert client.post("/chat/messages", headers={"X-CSRF-Token":csrf(client), "Origin":"https://other.test"}, json={"question":"leave"}).status_code == 403
    response = client.post("/chat/messages", headers={"X-CSRF-Token":csrf(client)}, json={"question":"<script>alert(1)</script> leave"})
    assert response.status_code == 200, response.json
    query_id = response.json["id"]
    assert client.get(f"/queries/{query_id}").status_code == 200
    page = client.get("/chat").text
    assert "&lt;script&gt;" in page and "<script>alert" not in page
    assert app.test_client().get(f"/queries/{query_id}").status_code == 404
    assert client.get(f"/versions/{ready.id}").status_code == 200
    assert client.get("/evaluation").status_code == 200
    assert client.post("/chat/new", data={"csrf":csrf(client)}).status_code == 302
    assert client.get(f"/queries/{query_id}").status_code == 404


def test_cross_process_style_mutation_lock(app):
    with mutation_lock():
        with pytest.raises(ValueError, match="Another document"):
            with mutation_lock():
                pass


def test_embedding_batches_respect_request_budget(app):
    items = [{"text":"Ү" * 900} for _ in range(20)]
    batches = list(ingestion.embedding_batches(items, 16))
    assert sum(map(len, batches)) == 20
    assert all(len(batch) <= 16 and sum(len(item["text"].encode()) for item in batch) <= 16000 for batch in batches)


def test_eval_runner_persists_and_requires_label_review(app, ready, tmp_path, client):
    from app.evaluation import run_evaluation
    file = tmp_path / "dataset.jsonl"
    question = dict(id="en-en-1", language="en", evidence_language="en", direction="en->en", split="dev", answerable=True,
        question="leave", expected_answer="five days", review_status="draft", evidence=[dict(sha256=ready.sha256, unit_id="leave-1", block=2, excerpt="five working days")])
    file.write_text(json.dumps(question), encoding="utf8")
    with pytest.raises(ValueError, match="human review"):
        run_evaluation(file)
    run = run_evaluation(file, allow_unreviewed=True)
    assert run.finished_at and not run.snapshot["reviewed"]
    assert run.metrics["overall"]["passage_recall"]["mean"] == 1
    from app.models import EvaluationResult
    from app.evaluation import export_run
    result = db.session.scalar(select(EvaluationResult).where(EvaluationResult.run_id == run.id))
    response = client.post(f"/evaluation/results/{result.id}/review", data=dict(csrf=csrf(client), reviewer="test-fixture-reviewer", correctness="1", diagnosis="Synthetic route test; not actual human acceptance"))
    assert response.status_code == 302
    report = export_run(run)
    assert report["human_summary"]["correctness"]["reviewed"] == 1
    assert report["human_summary"]["correctness"]["fully_correct_fraction"] == 0
    assert report["human_summary"]["faithfulness"]["mean"] is None
    assert client.get(f"/evaluation/{run.id}").status_code == 200
    target = tmp_path / "report.json"
    command = app.test_cli_runner().invoke(args=["export-run", run.id, str(target)])
    assert command.exit_code == 0, command.output
    assert json.loads(target.read_text(encoding="utf8"))["snapshot"]["reviewed"] is False


def test_full_bilingual_dataset_engineering_smoke(app, monkeypatch):
    """80 cases exercise the runner, not model quality or human acceptance."""
    from werkzeug.datastructures import FileStorage
    from app.services.parsing import parse_direct
    from app.evaluation import run_evaluation
    monkeypatch.setattr(ingestion, "parse_bounded", parse_direct)
    corpus = Path(__file__).resolve().parents[1] / "evaluation/corpus"
    for file in sorted(corpus.glob("*.docx")):
        with file.open("rb") as handle:
            version, _ = ingestion.upload(FileStorage(handle, filename=file.name), file.stem, "mn" if file.stem.endswith("-mn") else "en")
        ingestion.process(version.id)
        ingestion.approve(version.id)
        ingestion.process(version.id)
    records = []
    for split, generate in (("dev", False), ("test", True)):
        run = run_evaluation(corpus / "questions.jsonl", split=split, generate=generate, allow_unreviewed=True)
        assert run.metrics["overall"]["count"] == 40
        assert run.metrics["overall"]["errors"] == 0
        for direction in ("mn->mn", "en->en", "mn->en", "en->mn"):
            assert run.metrics[direction]["count"] == 10
        records.append(dict(id=run.id, split=split, metrics=run.metrics))
    output = Path(__file__).resolve().parents[1] / "test-results"
    output.mkdir(exist_ok=True)
    (output / "mock-evaluation.json").write_text(json.dumps(dict(provider="FakeGemini TEST DOUBLE", human_review=False,
        disclaimer="Engineering smoke only. Not evidence of bilingual Gemini quality.", runs=records), indent=2), encoding="utf8")
