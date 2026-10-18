"""Exercise the real SDK serializer and response parser, without network access."""
import json
from types import SimpleNamespace
import httpx
import pytest
from google import genai
from google.genai import types
from flask import Flask
from app.config import settings
from app.services.gemini import Gemini


def test_real_sdk_embedding_count_and_generation_transport():
    app = Flask(__name__)
    app.config.update(settings("instance"), GEMINI_API_KEY="synthetic-not-a-real-key", EMBEDDING_DIMENSIONS=128)
    requests = []
    def respond(request):
        body = json.loads(request.content)
        requests.append((str(request.url), body))
        if "batchEmbedContents" in str(request.url):
            return httpx.Response(200, json={"embeddings":[{"values":[1.] + [0.] * 127}]})
        if "countTokens" in str(request.url):
            return httpx.Response(200, json={"totalTokens":12})
        return httpx.Response(200, json={"candidates":[{"content":{"parts":[{"text":'{"status":"insufficient_evidence","answer":"No evidence","sources":[]}'}], "role":"model"}, "finishReason":"STOP"}]})
    with app.app_context():
        adapter = Gemini()
        adapter.client = genai.Client(api_key="synthetic-not-a-real-key", http_options=types.HttpOptions(client_args={"transport":httpx.MockTransport(respond)}, retry_options=types.HttpRetryOptions(attempts=1)))
        assert len(adapter.embed(["hello"], "RETRIEVAL_DOCUMENT")[0]) == 128
        assert adapter.count("hello") == 12
        assert json.loads(adapter.generate("no evidence", "grounded"))["status"] == "insufficient_evidence"
        assert requests[0][1]["requests"][0]["taskType"] == "RETRIEVAL_DOCUMENT"
        assert "autoTruncate" not in requests[0][1]["requests"][0]
        adapter.client.close()


def test_sdk_permanent_error_does_not_retry():
    app = Flask(__name__)
    app.config.update(settings("instance"), GEMINI_API_KEY="fake")
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(403, json={"error":{"code":403,"message":"denied","status":"PERMISSION_DENIED"}})
    with app.app_context():
        adapter = Gemini()
        adapter.client = genai.Client(api_key="fake", http_options=types.HttpOptions(client_args={"transport":httpx.MockTransport(respond)}, retry_options=types.HttpRetryOptions(attempts=1)))
        with pytest.raises(ValueError, match="403"):
            adapter.embed(["hello"], "RETRIEVAL_QUERY")
        assert len(calls) == 1
        adapter.client.close()


def test_sdk_transient_retry_and_expired_deadline(monkeypatch):
    import time
    from app.services import gemini
    app = Flask(__name__)
    app.config.update(settings("instance"), GEMINI_API_KEY="fake", EMBEDDING_DIMENSIONS=128)
    calls, sleeps = [], []
    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After":"2"}, json={"error":{"code":429,"message":"quota","status":"RESOURCE_EXHAUSTED"}})
        return httpx.Response(200, json={"embeddings":[{"values":[1.] + [0.] * 127}]})
    monkeypatch.setattr(gemini.time, "sleep", sleeps.append)
    with app.app_context():
        adapter = Gemini()
        adapter.client = genai.Client(api_key="fake", http_options=types.HttpOptions(client_args={"transport":httpx.MockTransport(respond)}, retry_options=types.HttpRetryOptions(attempts=1)))
        assert adapter.embed(["hello"], "RETRIEVAL_QUERY")
        assert len(calls) == 2 and sleeps[0] >= 2
        adapter.deadline = time.monotonic() - 1
        with pytest.raises(ValueError, match="deadline"):
            adapter.count("hello")
        assert len(calls) == 2
        adapter.close()


def test_structured_quota_retry_and_deadline(monkeypatch):
    import time
    from app.services import gemini
    app = Flask(__name__)
    app.config.update(settings("instance"), GEMINI_API_KEY="fake", EMBEDDING_DIMENSIONS=128)
    calls, sleeps = [], []
    def respond(request):
        calls.append(request)
        if len(calls) in (1, 3):
            return httpx.Response(429, json={"error":{"code":429,"message":"quota", "details":[{"@type":"type.googleapis.com/google.rpc.RetryInfo", "retryDelay":"45s"}]}})
        return httpx.Response(200, json={"embeddings":[{"values":[1.] + [0.] * 127}]})
    monkeypatch.setattr(gemini.time, "sleep", sleeps.append)
    with app.app_context():
        adapter = Gemini()
        adapter.client = genai.Client(api_key="fake", http_options=types.HttpOptions(client_args={"transport":httpx.MockTransport(respond)}, retry_options=types.HttpRetryOptions(attempts=1)))
        assert adapter.embed(["hello"], "RETRIEVAL_QUERY")
        assert sleeps == [30, 15]
        adapter.deadline = time.monotonic() + 10
        with pytest.raises(ValueError, match="retry would exceed"):
            adapter.embed(["hello"], "RETRIEVAL_QUERY")
        assert len(calls) == 3 and sum(sleeps) == 45
        adapter.close()
