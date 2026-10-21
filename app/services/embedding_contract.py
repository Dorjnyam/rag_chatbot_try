"""Embedding-space identity and vector validation shared by indexing and retrieval."""
from __future__ import annotations

import hashlib
import json
import math
import numbers


CONTRACT_FIELDS = (
    "provider",
    "model",
    "model_revision",
    "dimensions",
    "preprocessing_version",
    "normalization",
    "query_document_compatibility",
)


def embedding_contract(config: dict) -> dict:
    """Return the stable, JSON-safe identity of vectors produced by a config.

    Missing fields are interpreted using the behavior of the legacy pipeline so
    existing indexes remain readable. A configured revision is never inferred.
    """
    provider = str(config.get("provider", config.get("EMBEDDING_PROVIDER", "openai"))).casefold()
    model = config.get("embedding_model", config.get("model", config.get("OPENAI_EMBEDDING_MODEL", config.get("EMBEDDING_MODEL"))))
    dimensions = config.get("dimensions", config.get("OPENAI_EMBEDDING_DIMENSIONS", config.get("EMBEDDING_DIMENSIONS")))
    revision = config.get("embedding_model_revision", config.get("model_revision"))
    if revision in (None, ""):
        revision = config.get("OPENAI_EMBEDDING_MODEL_REVISION")
    convention = config.get("query_document_compatibility")
    if not convention:
        convention = "symmetric-input-v1"
    identity = {
        "provider": provider,
        "model": str(model) if model is not None else None,
        "model_revision": str(revision) if revision not in (None, "") else None,
        "dimensions": int(dimensions) if dimensions is not None else None,
        "preprocessing_version": config.get("embedding_preprocessing_version", config.get("preprocessing_version", "raw-input-v1")),
        "normalization": config.get("normalization", "l2-v1"),
        "query_document_compatibility": convention,
    }
    return identity


def contract_sha256(config_or_contract: dict) -> str:
    identity = embedding_contract(config_or_contract)
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf8")).hexdigest()


def compatible_embedding_spaces(first: dict, second: dict) -> bool:
    left, right = embedding_contract(first), embedding_contract(second)
    for source, identity in ((first, left), (second, right)):
        stored_hash = source.get("embedding_contract_sha256")
        if stored_hash and stored_hash != contract_sha256(identity):
            return False
    return all(left[field] == right[field] for field in CONTRACT_FIELDS)


def collection_contract_metadata(config: dict) -> dict:
    identity = embedding_contract(config)
    metadata = {
        "embedding_contract_sha256": contract_sha256(identity),
        "embedding_provider": identity["provider"],
        "embedding_model": identity["model"],
        "embedding_dimensions": identity["dimensions"],
        "embedding_preprocessing_version": identity["preprocessing_version"],
        "embedding_normalization": identity["normalization"],
        "embedding_query_document_compatibility": identity["query_document_compatibility"],
    }
    if identity["model_revision"] is not None:
        metadata["embedding_model_revision"] = identity["model_revision"]
    return metadata


def validate_vectors(vectors, dimensions: int, normalization: str = "l2-v1") -> None:
    """Validate one batch before persistence; fail closed on invalid vectors."""
    if not isinstance(dimensions, int) or dimensions < 1:
        raise ValueError("Embedding contract has invalid dimensions.")
    if normalization != "l2-v1":
        raise ValueError("Unsupported embedding normalization contract.")
    for row, vector in enumerate(vectors):
        if not isinstance(vector, (list, tuple)) or len(vector) != dimensions:
            raise ValueError(f"Embedding vector {row} has an incompatible dimension.")
        if any(isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(float(value))
               for value in vector):
            raise ValueError(f"Embedding vector {row} contains a non-finite or non-numeric value.")
        norm = math.sqrt(sum(float(value) * float(value) for value in vector))
        if norm == 0 or not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
            raise ValueError(f"Embedding vector {row} does not satisfy the L2 normalization contract.")


def validate_collection_metadata(metadata: dict | None, config: dict) -> None:
    """Reject a collection carrying a different vector-space fingerprint."""
    metadata = metadata or {}
    expected = collection_contract_metadata(config)
    observed_hash = metadata.get("embedding_contract_sha256")
    if observed_hash != expected["embedding_contract_sha256"]:
        raise ValueError("Chroma collection embedding contract is missing or incompatible; refusing mixed-space writes.")
    for key, value in expected.items():
        if key in metadata and metadata[key] != value:
            raise ValueError(f"Chroma collection metadata mismatch for {key}.")
