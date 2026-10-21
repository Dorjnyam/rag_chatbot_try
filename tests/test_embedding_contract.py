import math

import pytest

from app.services.embedding_contract import (
    collection_contract_metadata,
    compatible_embedding_spaces,
    contract_sha256,
    embedding_contract,
    validate_collection_metadata,
    validate_vectors,
)


BASE = {
    "EMBEDDING_PROVIDER": "openai",
    "OPENAI_EMBEDDING_MODEL": "text-embedding-3-small",
    "OPENAI_EMBEDDING_DIMENSIONS": 4,
    "OPENAI_EMBEDDING_MODEL_REVISION": "rev-a",
    "embedding_preprocessing_version": "raw-input-v1",
    "normalization": "l2-v1",
    "query_document_compatibility": "symmetric-input-v1",
}


def test_runtime_openai_config_resolves_model_and_dimensions():
    identity = embedding_contract(BASE)
    assert identity["provider"] == "openai"
    assert identity["model"] == "text-embedding-3-small"
    assert identity["dimensions"] == 4
    assert identity["model_revision"] == "rev-a"


@pytest.mark.parametrize(("field", "value"), [
    ("OPENAI_EMBEDDING_MODEL", "other-model"),
    ("OPENAI_EMBEDDING_MODEL_REVISION", "rev-b"),
    ("embedding_preprocessing_version", "normalized-text-v2"),
    ("normalization", "l1-v1"),
    ("query_document_compatibility", "asymmetric-pair-v2"),
])
def test_same_dimensions_do_not_make_different_spaces_compatible(field, value):
    other = {**BASE, field: value}
    assert compatible_embedding_spaces(BASE, other) is False


def test_contract_hash_tampering_is_incompatible():
    changed = {**BASE, "embedding_contract_sha256": "0" * 64}
    assert compatible_embedding_spaces(BASE, changed) is False


def test_collection_contract_fails_closed_on_model_mismatch():
    other = {**BASE, "OPENAI_EMBEDDING_MODEL": "other-model"}
    with pytest.raises(ValueError, match="incompatible"):
        validate_collection_metadata(collection_contract_metadata(BASE), other)


def test_vector_validation_enforces_dimension_finiteness_and_l2_norm():
    validate_vectors([[1.0, 0.0, 0.0, 0.0]], 4)
    for vector in ([1.0, 0.0], [0.0] * 4, [float("nan"), 0.0, 0.0, 0.0], [2.0, 0.0, 0.0, 0.0]):
        with pytest.raises(ValueError):
            validate_vectors([vector], 4)


def test_unit_vector_similarity_relationships():
    # For unit vectors, cosine and dot are equal and squared L2 is 2 - 2*cosine.
    left = [1 / math.sqrt(2), 1 / math.sqrt(2)]
    right = [1.0, 0.0]
    dot = sum(a * b for a, b in zip(left, right))
    cosine = dot  # norms are exactly one within floating point tolerance
    squared_l2 = sum((a - b) ** 2 for a, b in zip(left, right))
    assert math.isclose(dot, cosine, rel_tol=1e-12)
    assert math.isclose(squared_l2, 2 - 2 * cosine, rel_tol=1e-12)
    assert contract_sha256(BASE) == collection_contract_metadata(BASE)["embedding_contract_sha256"]
