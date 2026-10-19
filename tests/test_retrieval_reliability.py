"""Retrieval regressions using temporary artifacts and in-memory provider doubles."""
from types import SimpleNamespace
from unittest.mock import Mock
import math

import pytest
from flask import Flask

from app.services import retrieval, sparse
from app.services.storage import read_json, write_json


def _application(tmp_path):
    app = Flask(__name__)
    app.config.update(DATA_DIR=tmp_path, AI_PROVIDER="openai", EMBEDDING_MODEL="test",
        EMBEDDING_DIMENSIONS=3, DENSE_TOP_K=15, SPARSE_TOP_K=15, RRF_K=60,
        RETRIEVAL_MODE="hybrid", RERANKING_ENABLED=False)
    return app


def _chunk(chunk_id="a", text="policy", document="d", version="v"):
    return dict(id=chunk_id, text=text, metadata=dict(version_id=version, document_id=document))


def test_rrf_duplicate_contributes_once_and_preserves_first_rank():
    hits = sparse.rrf({"dense_0": [dict(id="a", distance=.2), dict(id="a", distance=.9), dict(id="b", distance=.3)],
                       "sparse_0": [dict(id="b", sparse_score=2), dict(id="a", sparse_score=1)]})
    by_id = {hit["id"]: hit for hit in hits}
    assert by_id["a"]["rrf_score"] == pytest.approx(1 / 61 + 1 / 62)
    assert by_id["a"]["distance"] == .2
    assert by_id["a"]["branch_ranks"] == {"dense_0": 1, "sparse_0": 2}
    assert by_id["b"]["branch_ranks"]["dense_0"] == 3
    assert [h["id"] for h in sparse.rrf({"x": [dict(id="b")], "y": [dict(id="a")]})] == ["a", "b"]


@pytest.mark.parametrize("mutation", ["nonobject", "digest", "metadata", "terms", "manifest"])
def test_sparse_corruption_degrades_without_crashing(tmp_path, mutation):
    with _application(tmp_path).app_context():
        sparse.write_index("v", [_chunk()], "title", "policy.txt")
        assert sparse.load_index("v")  # Warm the cache before corrupting it.
        artifact = read_json(sparse.index_path("v"))
        if mutation == "nonobject":
            artifact["documents"] = [None]
        elif mutation == "digest":
            artifact["content_hash"] = "incorrect"
        elif mutation == "metadata":
            artifact["documents"][0]["metadata"]["version_id"] = "other"
        elif mutation == "terms":
            artifact["documents"][0]["terms"] = ["injected"]
        elif mutation == "manifest":
            write_json("manifests/v.json", [dict(id="other", metadata={})])
        if mutation != "manifest":
            write_json(sparse.index_path("v"), artifact)
        assert sparse.search("policy", [("v", "title", "policy.txt")]) == ([], ["v"])


def test_sparse_root_isolation_and_filtered_compiled_statistics(tmp_path):
    for suffix, text in (("one", "policy"), ("two", "other")):
        with _application(tmp_path / suffix).app_context():
            sparse.write_index("v", [_chunk(text=text)], "", "")
    with _application(tmp_path / "one").app_context():
        assert sparse.search("policy", [("v", "", "")])[0]
        assert not sparse.search("other", [("v", "", "")])[0]
        sparse.write_index("v2", [_chunk("b", "other " * 20, "d2", "v2")], "", "")
        versions = [("v", "", ""), ("v2", "", "")]
        all_hits, _ = sparse.search("policy", versions)
        selected, _ = sparse.search("policy", versions, eligible_ids={"d"})
        assert selected[0]["sparse_score"] != all_hits[0]["sparse_score"]
        assert selected[0]["sparse_score"] == pytest.approx(math.log(1 + .5 / 1.5))
    with _application(tmp_path / "two").app_context():
        assert not sparse.search("policy", [("v", "", "")])[0]


def _mock_retrieve(monkeypatch):
    version = SimpleNamespace(id="v", collection_name="collection", version_number=1,
        original_filename="file.txt", config=dict(provider="openai", embedding_model="test", dimensions=3))
    document = SimpleNamespace(id="d", title="title")
    monkeypatch.setattr(retrieval, "active_versions", lambda *_: [(version, document)])
    monkeypatch.setattr(retrieval, "db", SimpleNamespace(session=SimpleNamespace(commit=lambda: None)))
    a, b, c = _chunk("a"), _chunk("b"), _chunk("c")
    a["distance"], b["distance"] = .1, .2
    branches = {"dense_0": [a, b], "dense_1": [b, a]}
    monkeypatch.setattr(retrieval, "dense_search", lambda *args, **kwargs: (sparse.rrf(branches), 0, 0, branches))
    monkeypatch.setattr(sparse, "search", lambda *args, **kwargs: ([c, dict(a, sparse_score=2)], []))
    return branches


def test_retrieve_flat_rrf_full_pool_and_original_metadata(monkeypatch, tmp_path):
    branches = _mock_retrieve(monkeypatch)
    with _application(tmp_path).app_context():
        hits, timing = retrieval.retrieve("policy", k=1, client=Mock(), candidates=["policy", "other"], include_pool=True)
    assert hits[0]["id"] == "a"
    assert hits[0]["rrf_score"] == pytest.approx(1 / 61 + 1 / 62 + 2 / 62)
    assert hits[0]["branch_ranks"] == dict(dense_0=1, dense_1=2, sparse_0=2, sparse_1=2)
    assert hits[0]["distance"] == .1
    assert len(timing["candidate_pool"]) == 3


@pytest.mark.parametrize("scores", [[1], [1, float("nan"), 2], [1, "2", 3]])
def test_invalid_reranker_preserves_fused_order(monkeypatch, tmp_path, scores):
    _mock_retrieve(monkeypatch)
    monkeypatch.setattr(retrieval, "_reranker", lambda: SimpleNamespace(predict=lambda *a, **k: scores))
    app = _application(tmp_path)
    with app.app_context():
        expected, _ = retrieval.retrieve("policy", k=5, client=Mock())
        app.config["RERANKING_ENABLED"] = True
        actual, _ = retrieval.retrieve("policy", k=5, client=Mock())
    assert [h["id"] for h in actual] == [h["id"] for h in expected]
    assert all(h.get("reranker_error") and "rerank_score" not in h for h in actual)


def test_missing_sparse_uses_whole_dense_pool(monkeypatch, tmp_path):
    _mock_retrieve(monkeypatch)
    monkeypatch.setattr(sparse, "search", lambda *a, **k: ([_chunk("c")], ["v"]))
    with _application(tmp_path).app_context():
        hits, timing = retrieval.retrieve("policy", k=5, client=Mock())
    assert {h["id"] for h in hits} == {"a", "b"}
    assert timing["sparse_degraded"] and timing["missing_sparse_versions"] == ["v"]


def test_sparse_rejects_wrong_active_document_and_manifest_metadata(tmp_path):
    with _application(tmp_path).app_context():
        sparse.write_index("v", [_chunk()], "title", "policy.txt")
        assert sparse.search("policy", [("v", "title", "policy.txt", "other")]) == ([], ["v"])
        write_json("manifests/v.json", [dict(id="a", metadata=dict(version_id="v", document_id="other"))])
        assert sparse.search("policy", [("v", "title", "policy.txt", "d")]) == ([], ["v"])


def test_active_sparse_requires_manifest_even_after_cache_warmup(tmp_path):
    with _application(tmp_path).app_context():
        chunk = _chunk()
        sparse.write_index("v", [chunk], "title", "policy.txt")
        assert sparse.load_index("v")  # Legacy standalone loading warms cache.
        versions = [("v", "title", "policy.txt", "d")]
        assert sparse.search("policy", versions) == ([], ["v"])
        write_json("manifests/v.json", [dict(id=chunk["id"], metadata=chunk["metadata"])])
        hits, missing = sparse.search("policy", versions)
        assert not missing and [hit["id"] for hit in hits] == ["a"]
