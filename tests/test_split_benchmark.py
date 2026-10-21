import json
from uuid import uuid4

from app.services.chunking import chunk
from app.split_benchmark import _document_metrics, _source_intervals, _tokenizer


def test_source_interval_coverage_does_not_confuse_repeated_text_and_respects_fences():
    blocks = [
        {"text": "давталт" * 60, "heading": "Нэг", "block": 1, "kind": "paragraph"},
        {"text": "давталт" * 60, "heading": "Нэг", "block": 2, "kind": "table"},
        {"text": "давталт" * 60, "heading": "Хоёр", "block": 3, "kind": "paragraph"},
    ]
    chunks = chunk(blocks, {"chunk_size": 100, "chunk_overlap": 20}, str(uuid4()))
    intervals, separators = _source_intervals(blocks, chunks, 100, 20)
    assert separators == 2
    assert set(intervals) == {"1", "2", "3"}
    assert all(sum(hi - lo for lo, hi in spans) >= len(blocks[int(key)-1]["text"])
               for key, spans in intervals.items())
    result = _document_metrics("DOC0001", blocks, chunks, chunks, _tokenizer(), 100, 20)
    assert result["coverage_ratio"] == 1.0
    assert result["complete_clause_preservation"] is None
    assert result["citation_source_span_accuracy"] is None
    assert result["quality_status"].startswith("N/A:")


def test_offline_report_does_not_include_chunk_or_source_text(tmp_path, monkeypatch):
    import app.split_benchmark as benchmark

    monkeypatch.setattr(benchmark, "PUBLIC_ROOT", tmp_path)
    source_text = "PRIVATE-TEST-ҮҮРЭГ-7781"
    chunks = chunk([{"text": source_text, "heading": "PRIVATE-HEADING", "block": 1}],
                   {"chunk_size": 200, "chunk_overlap": 0}, str(uuid4()))
    from app.services.storage import fingerprint
    document = {"doc_id": "DOC9999", "metrics": {"fitted_chunk_count": 1, "quality_status": "N/A"},
                "chunks": [{"ordinal": 0, "text_sha256": fingerprint(chunks[0]["text"]), "tokens": 5}],
                "status": "split_success"}
    report = {"run_id": "test-run", "snapshot_id": "test-snapshot", "dataset_sha256": "0" * 64,
              "parser_revision": "ordered-blocks-v1", "summary": {"split_documents": 1, "split_failures": 0,
              "preserved_load_failures": 0, "split_successes": 1, "chunk_count": 1, "wall_seconds": 1,
              "throughput_docs_per_second": 1, "chunk_token_median": 5, "chunk_token_p95": 5},
              "documents": [document]}
    benchmark._write_public_reports("privacy-check", report)
    contents = "\n".join(p.read_text(encoding="utf8") for p in tmp_path.iterdir())
    assert source_text not in contents
    assert "PRIVATE-HEADING" not in contents


def test_baseline_reference_fields_remain_na_without_human_reference():
    from app.split_benchmark import _document_metrics

    blocks = [{"text": "1.2.3. Заалт. Монгол өгүүлбэр.", "heading": "Туршилт", "block": 1}]
    chunks = chunk(blocks, {"chunk_size": 512, "chunk_overlap": 0}, str(uuid4()))
    result = _document_metrics("DOC0001", blocks, chunks, chunks, _tokenizer())
    assert result["numbered_clause_markers_heuristic"] == 1
    assert result["complete_clause_preservation"] is None
    assert result["sentence_boundary_accuracy"] is None
    assert result["citation_source_span_accuracy"] is None
    assert result["sentence_boundary_heuristic_status"].startswith("punctuation heuristic")
