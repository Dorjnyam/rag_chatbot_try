"""Retrieval regressions using temporary artifacts and in-memory provider doubles."""
from types import SimpleNamespace
from unittest.mock import Mock
import math

import pytest
from flask import Flask

from app.services import retrieval, sparse
from app.services.storage import path, read_json, write_json


def _application(tmp_path):
    app = Flask(__name__)
    app.config.update(DATA_DIR=tmp_path, EMBEDDING_MODEL="test",
        EMBEDDING_DIMENSIONS=3, DENSE_TOP_K=15, SPARSE_TOP_K=15, RRF_K=60,
        RETRIEVAL_MODE="hybrid", RERANKING_ENABLED=False)
    return app


def _chunk(chunk_id="a", text="policy", document="d", version="v"):
    return dict(id=chunk_id, text=text, metadata=dict(version_id=version, document_id=document))


def test_storage_path_resolves_root_once_and_still_rejects_traversal(tmp_path):
    app = _application(tmp_path)
    with app.app_context():
        assert path("sparse/v.json") == (tmp_path / "sparse/v.json").resolve()
        with pytest.raises(ValueError, match="outside application data directory"):
            path("../outside.json")


def test_rrf_duplicate_contributes_once_and_preserves_first_rank():
    hits = sparse.rrf({"dense_0": [dict(id="a", distance=.2), dict(id="a", distance=.9), dict(id="b", distance=.3)],
                       "sparse_0": [dict(id="b", sparse_score=2), dict(id="a", sparse_score=1)]})
    by_id = {hit["id"]: hit for hit in hits}
    assert by_id["a"]["rrf_score"] == pytest.approx(1 / 61 + 1 / 62)
    assert by_id["a"]["distance"] is None
    assert by_id["a"]["branch_distances"] == {"dense_0": .2}
    assert by_id["a"]["branch_scores"] == {"sparse_0": 1}
    assert by_id["a"]["branch_ranks"] == {"dense_0": 1, "sparse_0": 2}
    assert by_id["b"]["branch_ranks"]["dense_0"] == 3
    assert [h["id"] for h in sparse.rrf({"x": [dict(id="b")], "y": [dict(id="a")]})] == ["a", "b"]


@pytest.mark.parametrize("field,value", [("text", "different source text"),
    ("metadata", dict(version_id="other", document_id="other"))])
def test_rrf_rejects_cross_branch_chunk_identity_conflicts(field, value):
    first = dict(id="shared", text="canonical", metadata=dict(version_id="v", document_id="d"))
    conflicting = dict(first, **{field: value})
    with pytest.raises(ValueError, match="disagree on chunk source identity"):
        sparse.rrf({"dense": [first], "sparse": [conflicting]})


@pytest.mark.parametrize("constant", [10, 30, 60, 100])
def test_rrf_known_answer_parameter_grid(constant):
    ranked = sparse.rrf({"lexical": [dict(id="a"), dict(id="b")],
        "dense": [dict(id="b"), dict(id="a")]}, constant)
    scores = {hit["id"]: hit["rrf_score"] for hit in ranked}
    assert scores["a"] == pytest.approx(1 / (constant + 1) + 1 / (constant + 2))
    assert scores["a"] == scores["b"]
    assert [hit["id"] for hit in ranked] == ["a", "b"]


def test_rrf_rejects_nonpositive_or_noninteger_rank_constant():
    for constant in (0, -1, 1.5, True):
        with pytest.raises(ValueError, match="positive integer"):
            sparse.rrf({}, constant)


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


def test_sparse_cache_reuses_more_than_32_immutable_version_indexes(tmp_path, monkeypatch):
    app = _application(tmp_path)
    with app.app_context():
        versions = []
        for index in range(40):
            version_id, document_id = f"v{index}", f"d{index}"
            sparse.write_index(version_id, [_chunk(f"c{index}", "policy clause", document_id, version_id)], "", "")
            versions.append((version_id, "", ""))
        sparse._cache.clear()
        sparse._compiled_cache.clear()
        calls = 0
        original = sparse.tokenize

        def counted(text):
            nonlocal calls
            calls += 1
            return original(text)

        monkeypatch.setattr(sparse, "tokenize", counted)
        first_profile, second_profile = {}, {}
        first, _ = sparse.search("policy", versions, 15, profile=first_profile)
        first_calls = calls
        calls = 0
        second, _ = sparse.search("policy", versions, 15, profile=second_profile)

    assert [item["id"] for item in first] == [item["id"] for item in second]
    assert first_calls == 41  # 40 stored records plus one query on cold load.
    assert calls == 1  # Only the query is tokenized after all version caches are warm.
    assert first_profile["index_cache_misses"] == 40
    assert second_profile["index_cache_hits"] == 40
    assert second_profile["corpus_stats_cache_hits"] == 1


def test_bm25_known_answer_scores_use_tf_length_and_robertson_idf(tmp_path):
    app = _application(tmp_path)
    with app.app_context():
        sparse.write_index("v", [
            _chunk("three", "policy policy policy"),
            _chunk("one", "policy"),
        ], "", "")
        hits, missing = sparse.search("policy", [("v", "", "")], 5)
    assert not missing
    idf = math.log(1.2)
    score_three = idf * (3 * 2.5) / (3 + 1.5 * (1 - .75 + .75 * 3 / 2))
    score_one = idf * 2.5 / (1 + 1.5 * (1 - .75 + .75 * 1 / 2))
    assert [hit["id"] for hit in hits] == ["three", "one"]
    assert hits[0]["sparse_score"] == pytest.approx(score_three)
    assert hits[1]["sparse_score"] == pytest.approx(score_one)


def _mock_retrieve(monkeypatch):
    version = SimpleNamespace(id="v", collection_name="collection", version_number=1,
        original_filename="file.txt", manifest_path="manifests/v.json", expected_chunks=3,
        config=dict(provider="openai", embedding_model="test", dimensions=3))
    document = SimpleNamespace(id="d", title="title")
    monkeypatch.setattr(retrieval, "active_versions", lambda *_: [(version, document)])
    monkeypatch.setattr(retrieval, "db", SimpleNamespace(session=SimpleNamespace(commit=lambda: None)))
    a, b, c = _chunk("a"), _chunk("b"), _chunk("c")
    manifest = [dict(id=item["id"], metadata=item["metadata"]) for item in (a, b, c)]
    monkeypatch.setattr(retrieval, "read_json", lambda _path: manifest)
    a["distance"], b["distance"] = .1, .2
    branches = {"dense_0": [a, b], "dense_1": [b, dict(a, distance=.25)]}
    monkeypatch.setattr(retrieval, "dense_search", lambda *args, **kwargs: (sparse.rrf(branches), 0, 0, branches))
    monkeypatch.setattr(sparse, "search", lambda *args, **kwargs: ([c,
        dict(id=a["id"], text=a["text"], metadata=a["metadata"], sparse_score=2)], []))
    return branches


def test_retrieve_flat_rrf_full_pool_and_original_metadata(monkeypatch, tmp_path):
    branches = _mock_retrieve(monkeypatch)
    with _application(tmp_path).app_context():
        hits, timing = retrieval.retrieve("policy", k=1, client=Mock(), candidates=["policy", "other"], include_pool=True)
    assert hits[0]["id"] == "a"
    assert hits[0]["rrf_score"] == pytest.approx(1 / 61 + 1 / 62 + 2 / 62)
    assert hits[0]["branch_ranks"] == dict(dense_0=1, dense_1=2, sparse_0=2, sparse_1=2)
    assert hits[0]["distance"] is None
    assert hits[0]["similarity"] is None
    assert hits[0]["branch_distances"] == {"dense_0": .1, "dense_1": .25}
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


def test_retrieve_rejects_dense_candidate_with_wrong_source_document(monkeypatch, tmp_path):
    branches = _mock_retrieve(monkeypatch)
    branches["dense_0"][0]["metadata"]["document_id"] = "other-document"
    with _application(tmp_path).app_context():
        with pytest.raises(ValueError, match="outside the active document/version scope"):
            retrieval.retrieve("policy", k=1, client=Mock())


def test_retrieve_rejects_sparse_candidate_outside_canonical_manifest(monkeypatch, tmp_path):
    _mock_retrieve(monkeypatch)
    monkeypatch.setattr(sparse, "search", lambda *a, **k: ([_chunk("unknown")], []))
    with _application(tmp_path).app_context():
        with pytest.raises(ValueError, match="differs from its canonical manifest"):
            retrieval.retrieve("policy", k=5, client=Mock())


def test_retrieve_rejects_candidate_text_that_disagrees_with_manifest_hash(monkeypatch, tmp_path):
    branches = _mock_retrieve(monkeypatch)
    hit = branches["dense_0"][0]
    hit["metadata"]["content_hash"] = retrieval.fingerprint("canonical text")
    hit["text"] = "different text"
    branches["dense_1"][1]["metadata"]["content_hash"] = hit["metadata"]["content_hash"]
    branches["dense_1"][1]["text"] = hit["text"]
    with _application(tmp_path).app_context():
        with pytest.raises(ValueError, match="content differs from its canonical source manifest"):
            retrieval.retrieve("policy", k=1, client=Mock())


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
