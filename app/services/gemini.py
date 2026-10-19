"""Official SDK integration. Retries, limits and usage remain visible here."""
import math
import time
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from flask import current_app
from google import genai
from google.genai import types, errors
import httpx


class Gemini:
    def __init__(self, deadline=None):
        self.config = current_app.config
        if not self.config["GEMINI_API_KEY"]:
            raise ValueError("Set GEMINI_API_KEY in .env to use embeddings and answers.")
        self.deadline = deadline
        self.attempts = 0
        self.usage = {}
        self.client = genai.Client(api_key=self.config["GEMINI_API_KEY"], http_options=types.HttpOptions(
            timeout=self.config["API_TIMEOUT"] * 1000, retry_options=types.HttpRetryOptions(attempts=1)))

    def options(self):
        seconds = self.config["API_TIMEOUT"]
        if self.deadline:
            seconds = min(seconds, self.deadline - time.monotonic())
        if seconds <= 0:
            raise ValueError("Request deadline exceeded.")
        return types.HttpOptions(timeout=max(1, int(seconds * 1000)))

    def call(self, operation, *, max_attempts=3):
        for attempt in range(max_attempts):
            try:
                self.attempts += 1
                return operation(self.options())
            except (errors.APIError, httpx.TransportError) as exc:
                status = getattr(exc, "code", None)
                transient = isinstance(exc, httpx.TransportError) or status == 429 or status in (500, 502, 503, 504)
                if not transient or attempt + 1 == max_attempts:
                    raise ValueError(f"Gemini request failed ({status or 'transport'}). Check API access, model and quota.") from exc
                hint = retry_delay(exc)
                delay = max(2 ** attempt + random.uniform(0, 0.3), hint if hint is not None else (30 if status == 429 else 0))
                if self.deadline and time.monotonic() + delay >= self.deadline:
                    raise ValueError("Gemini retry would exceed the request deadline.") from exc
                current_app.logger.warning("Gemini %s; retrying in %.1f seconds", status or "transport error", delay)
                while delay > 0:
                    interval = min(delay, 30)
                    time.sleep(interval)
                    delay -= interval

    def count(self, text, model=None):
        response = self.call(lambda options: self.client.models.count_tokens(model=model or self.config["GENERATION_MODEL"], contents=text,
            config=types.CountTokensConfig(http_options=options)))
        if response.total_tokens is None:
            raise ValueError("Gemini returned no token count; cannot enforce the token budget.")
        return response.total_tokens

    def fit_chunks(self, chunks, embedding_model, version_id, config):
        from .chunking import assign_ids
        fitted = []
        def fit(item):
            # embedding-001 does not expose count_tokens. This conservative guard
            # is explicitly bytes, not a claimed exact tokenizer. Never truncate.
            if len(item["text"].encode("utf8")) <= 1900:
                fitted.append(item)
                return
            if len(item["text"]) < 2:
                raise ValueError("Embedding input cannot fit the model token limit.")
            mid = len(item["text"]) // 2
            for lo, hi in ((0, mid), (mid, len(item["text"]))):
                meta = dict(item["metadata"])
                meta["char_start"] += lo
                meta["char_end"] = item["metadata"]["char_start"] + hi
                fit(dict(text=item["text"][lo:hi], metadata=meta))
        for item in chunks:
            fit(item)
        return assign_ids(fitted, version_id, config)

    def embed(self, texts, task, model=None, dimensions=None):
        if not texts:
            return []
        model = model or self.config["EMBEDDING_MODEL"]
        if any(len(text.encode("utf8")) > 1900 for text in texts):
            raise ValueError("Embedding input exceeds the conservative 1,900 UTF-8 byte guard. Shorten the question or split the chunk.")
        dimensions = dimensions or self.config["EMBEDDING_DIMENSIONS"]
        response = self.call(lambda options: self.client.models.embed_content(model=model, contents=texts,
            config=types.EmbedContentConfig(task_type=task, output_dimensionality=dimensions, http_options=options)))
        vectors = [item.values for item in response.embeddings or []]
        if len(vectors) != len(texts):
            raise ValueError("Embedding response count does not match inputs.")
        return [normalize_vector(v, dimensions) for v in vectors]

    def generate(self, prompt, system):
        schema = {"type": "object", "properties": {"status": {"type": "string", "enum": ["answered", "insufficient_evidence"]},
            "answer": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}}},
            "required": ["status", "answer", "sources"]}
        response = self.call(lambda options: self.client.models.generate_content(model=self.config["GENERATION_MODEL"], contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0, max_output_tokens=self.config["OUTPUT_TOKENS"],
                response_mime_type="application/json", response_json_schema=schema, http_options=options)))
        if response.usage_metadata:
            self.usage = response.usage_metadata.model_dump(mode="json", exclude_none=True)
        if not response.text:
            raise ValueError("Gemini returned no text (possibly safety-blocked or output-limited).")
        return response.text

    def rewrite_query(self, question):
        """One bounded structured rewrite request; caller validates protected terms."""
        schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
        response = self.call(lambda options: self.client.models.generate_content(model=self.config["GENERATION_MODEL"],
            contents="Асуултыг эх санаа, нэр, дугаар, шүүлтүүрийг хадгалан Монгол кирилл хайлтын хэлбэрт оруул. Шинэ баримт бүү нэм. Асуулт: " + question,
            config=types.GenerateContentConfig(system_instruction="Only return a concise, faithful search query. Preserve all identifiers and numbers.",
                temperature=0, max_output_tokens=256, response_mime_type="application/json", response_json_schema=schema,
                http_options=options)), max_attempts=1)
        try:
            value = __import__("json").loads(response.text or "")
            return value.get("query") if isinstance(value, dict) else None
        except (ValueError, TypeError):
            return None

    def close(self):
        self.client.close()


def normalize_vector(values, dimensions):
    if values is None or len(values) != dimensions or any(not math.isfinite(v) for v in values):
        raise ValueError("Embedding has invalid dimensions or nonfinite values.")
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0:
        raise ValueError("Embedding has zero norm.")
    return [v / norm for v in values]


def retry_delay(exc):
    """Honor both HTTP hints and Google RPC RetryInfo, without logging payloads."""
    delays = []
    response = getattr(exc, "response", None)
    header = getattr(response, "headers", {}).get("Retry-After") if response is not None else None
    if header:
        try:
            delays.append(max(0, float(header)))
        except ValueError:
            try:
                delays.append(max(0, (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                pass
    payload = getattr(exc, "response_json", None) or getattr(exc, "details", None) or {}
    details = payload.get("error", payload).get("details", []) if isinstance(payload, dict) else []
    for detail in details:
        if isinstance(detail, dict) and detail.get("@type", "").endswith("google.rpc.RetryInfo"):
            hint = detail.get("retryDelay", "")
            try:
                if isinstance(hint, str) and hint.endswith("s"):
                    delays.append(max(0, float(hint[:-1])))
                elif isinstance(hint, dict):
                    delays.append(max(0, float(hint.get("seconds", 0)) + float(hint.get("nanos", 0)) / 1e9))
            except (TypeError, ValueError):
                pass
    return max(delays) if delays else None


def provider(deadline=None):
    from .providers import provider as select_provider
    return select_provider("gemini", deadline=deadline)
