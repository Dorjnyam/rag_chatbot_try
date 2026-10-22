import json

import numpy as np
import pytest

from app import store_benchmark
from app.store_benchmark import (_align_stored_records, _collect_ids_and_vectors, _create_collection, _exact_topk,
    _index_configs, _query_group, load_store_report, run_store_benchmark)


def test_exact_cosine_reference_uses_stable_id_tie_breaking():
    vectors = np.asarray([[1, 0], [1, 0], [0, 1]], dtype=np.float32)
    ids, distances = _exact_topk(np.asarray([1, 0], dtype=np.float32), vectors,
        ["z-id", "a-id", "m-id"], 2)
    assert ids == ["a-id", "z-id"]
    assert distances == [0.0, 0.0]


def test_stored_vector_and_metadata_alignment_uses_stable_ids():
    ids, vectors, metadata = _align_stored_records(
        ["a", "b"], ["b", "a"], np.asarray([[0, 1], [1, 0]], dtype=np.float32),
        [{"label": "b"}, {"label": "a"}])
    assert ids == ["a", "b"]
    assert vectors.tolist() == [[1.0, 0.0], [0.0, 1.0]]
    assert metadata == [{"label": "a"}, {"label": "b"}]


def test_chroma_effective_config_reports_unsupported_legacy_batch_size(tmp_path):
    import chromadb
    from chromadb.config import Settings
    from app.store_benchmark import _effective_hnsw

    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"), settings=Settings(anonymized_telemetry=False))
    config = _index_configs()[0]["requested"]
    collection = _create_collection(client, "benchmark_config", config)
    effective = _effective_hnsw(collection)
    assert effective["space"] == "cosine"
    assert effective["sync_threshold"] == 1
    assert effective["max_neighbors"] == 16
    assert effective["ef_search"] == 100
    assert effective["batch_size"] is None
    client.close()


def test_chroma_exact_reference_known_order_filtered_and_unfiltered(tmp_path):
    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(path=str(tmp_path / "known-order"),
        settings=Settings(anonymized_telemetry=False))
    collection = client.create_collection(name="known_order", embedding_function=None,
        metadata={"benchmark_only": True}, configuration={"hnsw": {"space": "cosine",
            "sync_threshold": 1, "max_neighbors": 16, "ef_construction": 100, "ef_search": 100}})
    vectors = np.asarray([[1, 0, 0], [0.8, 0.6, 0], [0, 1, 0], [-1, 0, 0]], dtype=np.float32)
    ids = ["known-nearest", "known-second", "known-third", "known-opposite"]
    metadata = [{"group": "include"}, {"group": "exclude"}, {"group": "include"}, {"group": "include"}]
    collection.add(ids=ids, embeddings=vectors, metadatas=metadata)
    stored_ids, stored_vectors, stored_metadata = _collect_ids_and_vectors(collection)
    order = {item: index for index, item in enumerate(stored_ids)}
    stored_vectors = stored_vectors[[order[item] for item in ids]]
    stored_metadata = [dict(stored_metadata[order[item]]) for item in ids]
    query = np.asarray([[1, 0, 0]], dtype=np.float32)

    unfiltered = _query_group(collection, query, ids, stored_vectors, stored_metadata, None, 10)
    filtered = _query_group(collection, query, ids, stored_vectors, stored_metadata,
        {"group": "include"}, 10)
    raw = collection.query(query_embeddings=query, n_results=4, include=["distances"])
    filtered_raw = collection.query(query_embeddings=query, n_results=4,
        where={"group": "include"}, include=["distances"])
    assert raw["ids"][0] == ["known-nearest", "known-second", "known-third", "known-opposite"]
    assert filtered_raw["ids"][0] == ["known-nearest", "known-third", "known-opposite"]
    assert unfiltered["candidate_set_match"] is True
    assert filtered["candidate_set_match"] is True
    assert unfiltered["index_recall_at_k"]["1"] == 1.0
    assert filtered["index_recall_at_k"]["1"] == 1.0
    assert unfiltered["result_count_complete"] is True
    assert filtered["result_count_complete"] is True
    assert max(unfiltered["distance_abs_error"]["max"],
        filtered["distance_abs_error"]["max"]) < 1e-6
    client.close()


@pytest.mark.parametrize("ef_search", [50, 100, 200, 400])
def test_chroma_applies_controlled_ef_search_values(tmp_path, ef_search):
    import chromadb
    from chromadb.config import Settings
    from app.store_benchmark import _effective_hnsw

    client = chromadb.PersistentClient(path=str(tmp_path / f"ef-{ef_search}"),
        settings=Settings(anonymized_telemetry=False))
    collection = client.create_collection(name=f"ef_search_{ef_search}", embedding_function=None,
        metadata={"benchmark_only": True}, configuration={"hnsw": {"space": "cosine",
            "sync_threshold": 1, "max_neighbors": 16, "ef_construction": 100,
            "ef_search": ef_search}})
    assert _effective_hnsw(collection)["ef_search"] == ef_search
    client.close()


def test_small_isolated_store_benchmark_checks_exact_parity_filters_and_reopen(tmp_path, monkeypatch):
    # This tiny fixture validates mechanics, not whether the host can run the 10k workload.
    monkeypatch.setattr(store_benchmark, "MIN_FREE_RAM_BYTES", 0)
    monkeypatch.setattr(store_benchmark, "MIN_FREE_DISK_BYTES", 0)
    result = run_store_benchmark(tmp_path, sizes=(12,), repeats=1, query_count=2, seed=7)
    report = load_store_report(result["path"])
    assert result["api_calls"] == 0
    assert result["production_data_touched"] is False
    assert report["real_vector_snapshot"]["available"] is False
    assert report["semantic_or_human_relevance_measured"] is False
    assert len(report["runs"]) == 3
    for run in report["runs"]:
        assert run["persistence_verified"] is True
        assert run["vector_payload_written_to_report"] is False
        assert len(run["search_settings"]) == 1
        groups = run["search_settings"][0]["groups"]
        assert groups["small_candidate_set"]["eligible_count"] == 3
        assert groups["small_candidate_set"]["candidate_set_match"] is True
        assert groups["small_candidate_set"]["result_count_complete"] is True
        assert groups["small_candidate_set"]["requested_k_less_than_eligible"]["5"] == 1
        assert groups["unfiltered"]["index_recall_at_k"]["10"] is not None
        assert groups["filtered_segment_a"]["eligible_count"] == 6
    parsed = json.loads(open(result["path"], encoding="utf8").read())
    assert "synthetic vectors only" in parsed["data_classification"]


def test_resource_guard_stops_before_creating_benchmark_scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(store_benchmark, "_available_memory_bytes", lambda: 1)
    with pytest.raises(RuntimeError, match="less than 1 GiB"):
        run_store_benchmark(tmp_path, sizes=(10000,), repeats=1, query_count=1)
    assert not (tmp_path / "store-evaluation" / "scratch").exists()
