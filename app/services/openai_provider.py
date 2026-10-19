"""OpenAI API adapter with bounded retries and local token budgeting."""
import random
import time
from flask import current_app
from .gemini import normalize_vector


class OpenAIProvider:
    def __init__(self, deadline=None):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ValueError("Install the OpenAI SDK from requirements.txt to use AI_PROVIDER=openai.") from exc
        self.config = current_app.config
        if not self.config.get("OPENAI_API_KEY"):
            raise ValueError("Set OPENAI_API_KEY in .env to use OpenAI embeddings and answers.")
        self.deadline = deadline
        self.embedding_model = self.config["OPENAI_EMBEDDING_MODEL"]
        self.generation_model = self.config["OPENAI_GENERATION_MODEL"]
        self.embedding_dimensions = self.config["OPENAI_EMBEDDING_DIMENSIONS"]
        self.attempts = 0
        self.usage = {"provider": "openai", "embedding_model": self.embedding_model,
            "generation_model": self.generation_model, "embedding": {}, "generation": {}}
        self._encodings = {}
        self.client = OpenAI(api_key=self.config["OPENAI_API_KEY"], timeout=self.config["API_TIMEOUT"], max_retries=0)

    def _encoding(self, model):
        if model not in self._encodings:
            try:
                import tiktoken
                try:
                    encoding = tiktoken.encoding_for_model(model)
                except KeyError:
                    encoding = tiktoken.get_encoding("o200k_base")
                self._encodings[model] = encoding
            except ImportError:
                self._encodings[model] = None
        return self._encodings[model]

    def count(self, text, model=None):
        encoding = self._encoding(model or self.generation_model)
        if encoding:
            return len(encoding.encode(text))
        # UTF-8 byte count is a conservative upper bound for byte-pair tokenizers.
        return len(text.encode("utf8")) + 16

    def _check_deadline(self):
        if self.deadline and time.monotonic() >= self.deadline:
            raise ValueError("Request deadline exceeded.")

    def _record_usage(self, section, usage):
        values = usage.model_dump(mode="json", exclude_none=True) if hasattr(usage, "model_dump") else dict(usage)
        current = self.usage.setdefault(section, {})
        for key, value in values.items():
            if isinstance(value, (int, float)):
                current[key] = current.get(key, 0) + value
            else:
                current[key] = value

    def _call(self, operation):
        for attempt in range(3):
            self._check_deadline()
            self.attempts += 1
            try:
                timeout = self.config["API_TIMEOUT"]
                if self.deadline:
                    timeout = min(timeout, self.deadline - time.monotonic())
                client = self.client.with_options(timeout=max(.1, timeout), max_retries=0)
                return operation(client)
            except Exception as exc:
                from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
                status = getattr(exc, "status_code", None)
                code = getattr(exc, "code", None)
                if isinstance(exc, APIStatusError):
                    body = getattr(exc, "body", None)
                    if isinstance(body, dict):
                        error = body.get("error", body)
                        if isinstance(error, dict):
                            code = error.get("code", code) or error.get("type", "")
                quota = code in ("insufficient_quota", "billing_hard_limit_reached")
                transient = isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)) or status in (429, 500, 502, 503, 504)
                if quota:
                    raise ValueError("OpenAI API quota or billing limit reached. Check API billing and usage limits.") from exc
                if not transient or attempt == 2:
                    safe_code = "".join(ch for ch in str(code or "") if ch.isalnum() or ch in "_-.")[:80]
                    detail = f", {safe_code}" if safe_code else ""
                    raise ValueError(f"OpenAI request failed ({status or type(exc).__name__}{detail}). Check API access, model, billing, or rate limits.") from exc
                delay = min(2 ** attempt + random.uniform(0, 0.25), 3)
                if self.deadline and time.monotonic() + delay >= self.deadline:
                    raise ValueError("OpenAI retry would exceed the request deadline.") from exc
                current_app.logger.warning("OpenAI transient error (%s); retry %s/2 in %.1f sec", status or "transport", attempt + 1, delay)
                time.sleep(delay)

    def fit_chunks(self, chunks, embedding_model, version_id, config):
        from .chunking import assign_ids
        encoding = self._encoding(embedding_model)
        max_tokens = 7900
        fitted = []

        def split(item):
            text = item["text"]
            size = len(encoding.encode(text)) if encoding else len(text.encode("utf8"))
            if size <= max_tokens:
                fitted.append(item)
                return
            if len(text) < 2:
                raise ValueError("Embedding input cannot fit the model token limit.")
            middle = len(text) // 2
            for start, end in ((0, middle), (middle, len(text))):
                meta = dict(item["metadata"])
                meta["char_start"] += start
                meta["char_end"] = item["metadata"]["char_start"] + end
                split(dict(text=text[start:end], metadata=meta))

        for item in chunks:
            split(item)
        return assign_ids(fitted, version_id, config)

    def embed(self, texts, task, model=None, dimensions=None):
        if not texts:
            return []
        model = model or self.embedding_model
        dimensions = dimensions or self.embedding_dimensions
        encoding = self._encoding(model)
        for text in texts:
            size = len(encoding.encode(text)) if encoding else len(text.encode("utf8"))
            if size > 7900:
                raise ValueError("Embedding input exceeds the 7,900-token safety limit; split the chunk first.")
        response = self._call(lambda client: client.embeddings.create(model=model, input=texts, dimensions=dimensions))
        usage = getattr(response, "usage", None)
        if usage:
            self._record_usage("embedding", usage)
        ordered = sorted(response.data, key=lambda item: item.index)
        if len(ordered) != len(texts):
            raise ValueError("Embedding response count does not match inputs.")
        return [normalize_vector(item.embedding, dimensions) for item in ordered]

    def generate(self, prompt, system):
        schema = {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["answered", "insufficient_evidence"]},
            "answer": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}},
        }, "required": ["status", "answer", "sources"], "additionalProperties": False}
        response = self._call(lambda client: client.responses.create(model=self.generation_model, instructions=system, input=prompt,
            max_output_tokens=self.config["OUTPUT_TOKENS"], reasoning={"effort": self.config["OPENAI_REASONING_EFFORT"]},
            text={"format": {"type": "json_schema", "name": "rag_answer", "strict": True, "schema": schema}}))
        if response.usage:
            self._record_usage("generation", response.usage)
        if response.status == "incomplete":
            raise ValueError("OpenAI response was incomplete or reached its output limit.")
        if any(getattr(part, "type", None) == "refusal" for output in response.output for part in getattr(output, "content", [])):
            raise ValueError("OpenAI refused the response.")
        if not response.output_text:
            raise ValueError("OpenAI returned no text.")
        return response.output_text

    def rewrite_query(self, question):
        schema = {"type": "object", "properties": {"query": {"type": "string"}},
            "required": ["query"], "additionalProperties": False}
        prompt = "Асуултыг эх санаа, нэр, дугаар, шүүлтүүрийг хадгалан Монгол кирилл хайлтын хэлбэрт оруул. Шинэ баримт бүү нэм. Асуулт: " + question
        response = self._call(lambda client: client.responses.create(model=self.generation_model,
            instructions="Return only a concise, faithful search query. Preserve every identifier and number.", input=prompt,
            max_output_tokens=256, reasoning={"effort": self.config["OPENAI_REASONING_EFFORT"]},
            text={"format": {"type": "json_schema", "name": "query_rewrite", "strict": True, "schema": schema}}))
        if response.status != "completed" or not response.output_text:
            return None
        try:
            import json
            value = json.loads(response.output_text)
            return value.get("query") if isinstance(value, dict) and isinstance(value.get("query"), str) else None
        except (ValueError, TypeError):
            return None

    def close(self):
        self.client.close()
