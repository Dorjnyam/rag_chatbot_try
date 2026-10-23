"""Exercise the OpenAI SDK serializer and response parser without network access."""
import httpx
import pytest
from flask import Flask
from openai import OpenAI
from app.config import settings
from app.services import openai_provider
from app.services.openai_provider import OpenAIProvider


def _adapter(response_handler):
    app = Flask(__name__)
    app.config.update(settings("instance"), OPENAI_API_KEY="fake",
        OPENAI_EMBEDDING_MODEL="text-embedding-3-small", OPENAI_EMBEDDING_DIMENSIONS=2,
        OPENAI_GENERATION_MODEL="gpt-test", API_TIMEOUT=5)
    with app.app_context():
        adapter = OpenAIProvider()
        adapter.client.close()
        adapter.client = OpenAI(api_key="fake", http_client=httpx.Client(
            transport=httpx.MockTransport(response_handler)), max_retries=0)
    return app, adapter


def test_openai_embedding_indexes_model_identity_and_retry_are_observable(monkeypatch):
    calls, sleeps = [], []

    def response(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "busy", "type": "rate_limit_error",
                "code": "rate_limit_exceeded", "param": None}})
        return httpx.Response(200, json={"object": "list", "data": [
            {"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
            {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}],
            "model": "text-embedding-3-small", "usage": {"prompt_tokens": 3, "total_tokens": 3}})

    monkeypatch.setattr(openai_provider.time, "sleep", sleeps.append)
    app, adapter = _adapter(response)
    with app.app_context():
        vectors = adapter.embed(["document", "query"], "RETRIEVAL_QUERY")
        assert vectors == [[1.0, 0.0], [0.0, 1.0]]
        assert len(calls) == 2 and len(sleeps) == 1
        assert adapter.attempts == 2
        assert adapter.embedding_response_models == {"text-embedding-3-small"}
        assert adapter.embedding_request_latency_ms and adapter.usage["embedding"]["total_tokens"] == 3
        assert len(adapter.provider_attempt_latency_ms) == 2
        adapter.close()


def test_openai_embedding_rejects_unknown_task_and_bad_response_indexes():
    def duplicate_indexes(_request):
        return httpx.Response(200, json={"object": "list", "data": [
            {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]},
            {"object": "embedding", "index": 0, "embedding": [0.0, 1.0]}],
            "model": "text-embedding-3-small", "usage": {"prompt_tokens": 2, "total_tokens": 2}})
    app, adapter = _adapter(duplicate_indexes)
    with app.app_context():
        with pytest.raises(ValueError, match="task"):
            adapter.embed(["x"], "OTHER")
        with pytest.raises(ValueError, match="indexes"):
            adapter.embed(["x", "y"], "RETRIEVAL_DOCUMENT")
        adapter.close()


def test_openai_embedding_rejects_unavailable_or_different_model_identity():
    def wrong_model(_request):
        return httpx.Response(200, json={"object": "list", "data": [
            {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}],
            "model": "different-model", "usage": {"prompt_tokens": 1, "total_tokens": 1}})
    app, adapter = _adapter(wrong_model)
    with app.app_context():
        with pytest.raises(ValueError, match="response model"):
            adapter.embed(["x"], "RETRIEVAL_DOCUMENT")
        adapter.close()


def test_openai_generation_uses_strict_schema_and_rejects_noncompleted_status():
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "resp_test", "object": "response", "created_at": 1,
            "status": "queued", "model": "gpt-test", "output": [], "usage": None})
    app, adapter = _adapter(response)
    with app.app_context(), pytest.raises(ValueError, match="did not complete"):
        adapter.generate('{"question":"x"}', "system")
    request_body = __import__("json").loads(calls[0].content)
    schema = request_body["text"]["format"]["schema"]
    assert request_body["text"]["format"]["strict"] is True
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"status", "answer", "sources"}
    assert adapter.generation_usage_status == "unavailable_response"
    adapter.close()


def test_openai_generation_completed_response_records_usage_without_temperature():
    raw = '{"status":"answered","answer":"Журмын дагуу мэдээлэл байна [S1].","sources":["S1"]}'
    bodies = []
    def response(request):
        bodies.append(__import__("json").loads(request.content))
        return httpx.Response(200, json={"id": "resp_test", "object": "response", "created_at": 1,
            "status": "completed", "model": "gpt-test", "output": [{"id": "msg_test", "type": "message",
                "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": raw, "annotations": []}]}],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                "input_tokens_details": {"cached_tokens": 2}, "output_tokens_details": {"reasoning_tokens": 1}}})
    app, adapter = _adapter(response)
    with app.app_context():
        assert adapter.generate("{}", "system") == raw
        assert adapter.generation_usage_status == "available"
        assert adapter.usage["generation"]["input_tokens_details"]["cached_tokens"] == 2
    assert "temperature" not in bodies[0]
    adapter.close()


def test_generation_nested_usage_counters_accumulate_across_calls():
    adapter = OpenAIProvider.__new__(OpenAIProvider)
    adapter.usage = {"generation": {}}
    adapter._record_usage("generation", {"input_tokens": 10,
        "input_tokens_details": {"cached_tokens": 3}, "output_tokens_details": {"reasoning_tokens": 2}})
    adapter._record_usage("generation", {"input_tokens": 8,
        "input_tokens_details": {"cached_tokens": 4}, "output_tokens_details": {"reasoning_tokens": 1}})
    assert adapter.usage["generation"] == {"input_tokens": 18,
        "input_tokens_details": {"cached_tokens": 7}, "output_tokens_details": {"reasoning_tokens": 3}}
