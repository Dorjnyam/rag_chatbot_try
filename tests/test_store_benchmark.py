import json

import numpy as np
import pytest

from app import store_benchmark
from app.store_benchmark import _create_collection, _exact_topk, _index_configs, load_store_report, run_store_benchmark


def test_exact_cosine_reference_uses_stable_id_tie_breaking():
    vectors = np.asarray([[1, 0], [1, 0], [0, 1]], dtype=np.float32)
    ids, distances = _exact_topk(np.asarray([1, 0], dtype=np.float32), vectors,
        ["z-id", "a-id", "m-id"], 2)
    assert ids == ["a-id", "z-id"]
    assert distances == [0.0, 0.0]


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
