"""Isolated fixture navigation regressions; providers remain mocked."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from flask import template_rendered

from app.extensions import db
from app.models import EvaluationQuestion, EvaluationResult, EvaluationRun, QueryLog, now, uid
from conftest import csrf

pytestmark = pytest.mark.integration


def _context(app, client, target):
    captured = []
    def capture(sender, template, context, **extra):
        captured.append(context)
    with template_rendered.connected_to(capture, app):
        response = client.get(target)
    assert response.status_code == 200
    return captured[-1], response


def test_chat_pagination_newest_segment_chronological_and_return(app, client):
    with client.session_transaction() as session:
        conversation = session["conversation"]
    start = now()
    for index in range(30):
        db.session.add(QueryLog(id=f"page-{index:02}", conversation_id=conversation, question=f"Question {index}", language="en", created_at=start + timedelta(seconds=index)))
    db.session.commit()
    context, _ = _context(app, client, "/chat")
    assert [q.id for q in context["history"]] == [f"page-{i:02}" for i in range(5, 30)]
    context, response = _context(app, client, "/chat?page=2&per_page=25")
    assert [q.id for q in context["history"]] == [f"page-{i:02}" for i in range(5)]
    assert "page%3D2" in response.get_data(as_text=True)
    context, _ = _context(app, client, "/chat?page=999&per_page=7")
    assert context["pagination"]["page"] == 2
    assert context["pagination"]["per_page"] == 25


def test_evaluation_list_pagination_preserves_origin(app, client):
    start = now()
    for index in range(27):
        db.session.add(EvaluationRun(id=f"eval-{index:02}", snapshot=dict(split="dev", reviewed=False), config={}, metrics={}, started_at=start + timedelta(seconds=index)))
    db.session.commit()
    context, response = _context(app, client, "/evaluation?page=2&per_page=25")
    assert [r.id for r in context["runs"]] == ["eval-01", "eval-00"]
    assert "page%3D2" in response.get_data(as_text=True)


def test_query_batches_source_lookup_and_adds_context_only(app, client, ready, monkeypatch):
    from app.routes import chat
    with client.session_transaction() as session:
        conversation = session["conversation"]
    metadata = dict(version_id=ready.id, document_id=ready.document_id, block_start=1, block_end=1)
    top = dict(id="canonical-one", metadata=metadata, title="Synthetic", rank=1, selected=True, label="S1")
    extra = dict(id="canonical-two", metadata=metadata, title="Synthetic", rank=2, selected=True, label="S2")
    log = QueryLog(id=uid(), conversation_id=conversation, question="Synthetic", language="en", trace=[top], config=dict(context_trace=[top, extra]))
    db.session.add(log)
    db.session.commit()
    calls = []
    def get(**kwargs):
        calls.append(kwargs["ids"])
        return dict(ids=kwargs["ids"], documents=["Synthetic source" for _ in kwargs["ids"]])
    monkeypatch.setattr(chat, "store", lambda: SimpleNamespace(collection=lambda name: SimpleNamespace(get=get)))
    context, response = _context(app, client, f"/queries/{log.id}?return_to=/chat%3Fpage%3D2%26per_page%3D25")
    assert calls == [["canonical-one", "canonical-two"]]
    assert [hit["context_only"] for hit in context["traces"]] == [False, True]
    assert context["return_to"] == "/chat?page=2&per_page=25"
    assert "Нэмэлт context нотолгоо" in response.get_data(as_text=True)


def test_chat_json_includes_context_trace_and_validates_return(app, client, monkeypatch):
    from app.routes import chat
    log = SimpleNamespace(id="synthetic", status="retrieved", answer="", citations=[], timings={}, config=dict(context_trace=[dict(id="extra")]))
    monkeypatch.setattr(chat, "ask", lambda *args, **kwargs: (log, []))
    response = client.post("/chat/messages", json=dict(question="Synthetic", return_to="/chat?page=2&per_page=25"), headers={"X-CSRF-Token": csrf(client)})
    assert response.status_code == 200
    assert response.json["context_trace"] == [dict(id="extra")]
    assert "page%3D2" in response.json["details_url"]
    response = client.post("/chat/messages", json=dict(question="Synthetic", return_to="https://outside.invalid"), headers={"X-CSRF-Token": csrf(client)})
    assert response.json["details_url"].endswith("return_to=/chat")


def test_evaluation_results_page_review_returns_to_same_page(app, client):
    run = EvaluationRun(id="result-pages", snapshot=dict(split="dev", reviewed=False), config={}, metrics={})
    db.session.add(run)
    for index in range(27):
        question_id, log_id = f"question-{index:02}", f"log-{index:02}"
        db.session.add(EvaluationQuestion(id=question_id, revision="revision", data=dict(question="Synthetic", direction="mn->mn", expected_answer="", evidence=[])))
        db.session.add(QueryLog(id=log_id, conversation_id=uid(), question="Synthetic", language="mn"))
    db.session.flush()
    for index in range(27):
        db.session.add(EvaluationResult(id=f"result-{index:02}", run_id=run.id, question_id=f"question-{index:02}", question_revision="revision", query_log_id=f"log-{index:02}", metrics={}))
    db.session.commit()
    target = "/evaluation/result-pages?page=2&per_page=25&return_to=/evaluation%3Fpage%3D2"
    context, response = _context(app, client, target)
    assert [r.id for r, q, log in context["results"]] == ["result-25", "result-26"]
    assert context["human"]["total"] == 27
    current = context["pagination"]["current"]
    response = client.post("/evaluation/results/result-25/review", data=dict(csrf=csrf(client), reviewer="Synthetic reviewer", correctness="2", return_to=current))
    assert response.status_code == 302
    assert response.location == current
