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
