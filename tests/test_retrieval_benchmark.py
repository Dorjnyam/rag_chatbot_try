import numpy as np
import pytest

from app.retrieval_benchmark import _percentile, _stats, _vector, _verified_query_variants


def test_synthetic_query_and_document_vectors_are_deterministic_unit_vectors():
    first = _vector("private question", 7)
    second = _vector("private question", 7)
    assert np.array_equal(first, second)
    assert first.shape == (1536,)
    assert np.linalg.norm(first) == pytest.approx(1.0, abs=1e-6)
    assert not np.array_equal(first, _vector("another question", 7))


def test_benchmark_percentiles_report_sample_count_and_nearest_rank():
    assert _percentile([1, 2, 3, 4], .5) == 2
    assert _percentile([1, 2, 3, 4], .95) == 4
    assert _stats([1, 2, 3, 4]) == {
        "count": 4, "p50_ms": 2, "p95_ms": 4, "p99_ms": 4, "mean_ms": 2.5}


def test_verified_variation_loader_reads_text_values_not_language_keys():
    dataset = {"records": [
        {"human_confirmed": True, "question_evidence_relation_confirmed": True,
         "query_variants": {"mn_cyrl": "асуулт", "en": "question", "mn_latn_draft": "asuult"}},
        {"human_confirmed": True, "question_evidence_relation_confirmed": True,
         "query_variants": {"mn_cyrl": "асуулт 2", "en": "question 2", "mn_latn_draft": "asuult 2"}},
    ]}
    assert _verified_query_variants(dataset) == ["асуулт", "question", "asuult", "асуулт 2", "question 2", "asuult 2"]
