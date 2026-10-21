"""Isolated semantic chunking experiment primitives; never used in production."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path


ABBREVIATIONS = ("г", "он", "зүйл", "дугаар", "х.б", "тух", "ж", "д", "проф", "др")


def split_sentences(text: str) -> list[str]:
    """Conservative Mongolian/English sentence boundaries with common policy exceptions."""
    if not text.strip():
        return []
    protected = text
    # Protect clause numbering, decimals, dates, initials, and common abbreviations.
    protected = re.sub(r"(?m)(^\s*\d+(?:\.\d+)*)(\.)\s", r"\1<DOT> ", protected)
    protected = re.sub(r"(?<=\d)\.(?=\d)", "<DOT>", protected)
    protected = re.sub(r"\b([А-ЯӨҮЁA-Z])\.(?=\s*[А-ЯӨҮЁA-Z])", r"\1<DOT>", protected)
    for abbreviation in ABBREVIATIONS:
        protected = re.sub(rf"(?i)\b{re.escape(abbreviation)}\.", lambda m: m.group().replace(".", "<DOT>"), protected)
    pieces = re.split(r"(?<=[.!?。！？])(?:[”’\"»」』)]*)\s+", protected)
    return [piece.replace("<DOT>", ".").strip() for piece in pieces if piece.strip()]


class EmbeddingProvider:
    """Small injected protocol implemented by local or explicitly authorized providers."""
    model_identity = "abstract"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class DeterministicMockEmbeddings(EmbeddingProvider):
    """Stable lexical mock for unit tests only; not a semantic-quality model."""
    model_identity = "mock-sha256-lexical-v1"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            values = [0.0] * 64
            for token in re.findall(r"[\wА-Яа-яӨөҮүЁё]+", text.casefold()):
                digest = hashlib.sha256(token.encode("utf8")).digest()
                index = int.from_bytes(digest[:2], "big") % len(values)
                values[index] += 1.0 if digest[2] & 1 else -1.0
            vectors.append(values)
        return vectors


def _cache_key(text: str, provider: EmbeddingProvider, config: dict) -> str:
    body = json.dumps({"text_sha256": hashlib.sha256(text.encode("utf8")).hexdigest(),
        "model": provider.model_identity, "config": config}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf8")).hexdigest()


def cached_embeddings(texts: list[str], provider: EmbeddingProvider, cache_dir: Path,
                      config: dict | None = None) -> list[list[float]]:
    """Private content-addressed cache. Text itself is never written to the cache."""
    config = config or {}
    cache_dir.mkdir(parents=True, exist_ok=True)
    result = []
    missing_texts, missing_keys = [], []
    for text in texts:
        key = _cache_key(text, provider, config)
        path = cache_dir / f"{key}.json"
        if path.is_file():
            result.append(json.loads(path.read_text(encoding="utf8")))
        else:
            result.append(None)
            missing_texts.append(text)
            missing_keys.append(key)
    if missing_texts:
        vectors = provider.embed_texts(missing_texts)
        if len(vectors) != len(missing_texts):
            raise ValueError("Embedding provider returned an unexpected vector count.")
        for key, vector in zip(missing_keys, vectors):
            (cache_dir / f"{key}.json").write_text(json.dumps(vector), encoding="utf8")
        iterator = iter(vectors)
        result = [next(iterator) if vector is None else vector for vector in result]
    return result


def _cosine_distance(left: list[float], right: list[float]) -> float:
    dot = sum(a*b for a,b in zip(left,right))
    norm = math.sqrt(sum(a*a for a in left) * sum(b*b for b in right))
    return 1.0 - dot / norm if norm else 0.0


def semantic_chunk_spans(text: str, vectors: list[list[float]], enc, *, percentile: int = 90,
                         max_tokens: int = 512) -> list[tuple[int, int]]:
    """Pack sentences and break on adjacent-vector distance percentile."""
    if percentile not in {80, 90, 95}:
        raise ValueError("Breakpoint percentile must be 80, 90, or 95.")
    sentences = split_sentences(text)
    if len(vectors) != len(sentences):
        raise ValueError("One boundary-identification vector is required per sentence.")
    if not sentences:
        return []
    distances = [_cosine_distance(vectors[i], vectors[i+1]) for i in range(len(vectors)-1)]
    cutoff = sorted(distances)[min(len(distances)-1, math.ceil(percentile/100*len(distances))-1)] if distances else float("inf")
    boundaries = {i+1 for i,distance in enumerate(distances) if distance >= cutoff}
    offsets, cursor = [], 0
    for sentence in sentences:
        start = text.find(sentence, cursor)
        if start < 0:
            start = cursor
        end = start + len(sentence)
        offsets.append((0 if not offsets else offsets[-1][1], end))
        cursor = end
    spans, current_start, current_end = [], offsets[0][0], offsets[0][1]
    for index in range(1, len(sentences)):
        start, end = offsets[index]
        candidate = text[current_start:end]
        should_break = index in boundaries or len(enc.encode(candidate)) > max_tokens
        if should_break and current_end > current_start:
            spans.append((current_start, current_end))
            current_start = start
        current_end = end
        if len(enc.encode(text[current_start:current_end])) > max_tokens:
            # Deterministic fixed-token fallback for an oversized individual sentence.
            from .split_experiments import _fixed_token_spans
            spans.extend((current_start+a, current_start+b)
                         for a,b in _fixed_token_spans(text[current_start:current_end], enc, max_tokens))
            current_start = current_end
    if current_end > current_start:
        spans.append((current_start, current_end))
    if spans:
        spans[-1] = (spans[-1][0], len(text))
    fitted = []
    from .split_experiments import _fixed_token_spans
    for lo, hi in spans:
        if len(enc.encode(text[lo:hi])) <= max_tokens:
            fitted.append((lo, hi))
        else:
            fitted.extend((lo+a, lo+b) for a,b in _fixed_token_spans(text[lo:hi], enc, max_tokens))
    return fitted


def structure_constrained_spans(text: str, vectors: list[list[float]], enc, *,
                                verified_boundaries: list[int] | None, percentile: int = 90,
                                max_tokens: int = 512) -> list[tuple[int, int]]:
    """S2 requires explicitly human-verified boundary offsets; otherwise it is blocked."""
    if not verified_boundaries:
        raise ValueError("S2 blocked: no verified structural boundaries were supplied.")
    spans = semantic_chunk_spans(text, vectors, enc, percentile=percentile, max_tokens=max_tokens)
    # Retain sentence-semantic cuts and additionally honor only supplied verified offsets.
    cuts = {end for _, end in spans[:-1]} | set(verified_boundaries)
    cuts = sorted(c for c in cuts if 0 < c < len(text))
    result, start = [], 0
    for end in cuts + [len(text)]:
        if len(enc.encode(text[start:end])) <= max_tokens:
            result.append((start, end))
            start = end
        else:
            from .split_experiments import _fixed_token_spans
            result.extend((start+a, start+b) for a,b in _fixed_token_spans(text[start:end],enc,max_tokens))
            start = end
    return result
