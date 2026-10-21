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


def test_overlap_token_spans_keep_coverage_budget_and_real_overlap():
    from app.split_experiments import _token_overlap_spans, _validate_overlap_config
    enc = _tokenizer()
    assert _token_overlap_spans("", enc, 128, 16) == []
    assert _token_overlap_spans("Монгол", enc, 128, 16) == [(0, len("Монгол"))]
    text = ("Монгол компанийн бодлого. Үүрэг, хориг, үл хамаарах нөхцөл. " * 300)
    for recursive in (False, True):
        spans = _token_overlap_spans(text, enc, 128, 16, recursive)
        assert spans[0][0] == 0 and spans[-1][1] == len(text)
        assert all(lo <= previous_hi and hi > previous_hi for (lo, hi), (_, previous_hi)
                   in zip(spans[1:], spans[:-1]))
        assert all(len(enc.encode(text[lo:hi])) <= 128 for lo, hi in spans)
        assert any(lo < prior_hi for (lo, _), (_, prior_hi) in zip(spans[1:], spans[:-1]))
    from app.split_experiments import _experimental_chunks
    blocks = [{"text": text, "heading": "Нэг", "block": 1, "kind": "paragraph"}]
    version_id = str(uuid4())
    first = _experimental_chunks(blocks, "fixed-token-v1", enc, version_id, size=128,
                                 overlap=16, config_id="test-overlap")
    second = _experimental_chunks(blocks, "fixed-token-v1", enc, version_id, size=128,
                                  overlap=16, config_id="test-overlap")
    assert [item["id"] for item in first] == [item["id"] for item in second]
    assert [item["text"] for item in first] == [item["text"] for item in second]
    for size, overlap in ((0, 0), (10, -1), (10, 10)):
        try:
            _validate_overlap_config(size, overlap)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid overlap configuration accepted")


def test_source_interval_audit_accepts_token_overlap_without_false_loss():
    text = "Монгол журам. Үүрэг, хариуцлага, үл хамаарах нөхцөл. " * 20
    blocks = [{"text": text, "heading": "Нэг", "block": 1, "kind": "paragraph"}]
    chunks = []
    for lo, hi in ((0, 180), (140, 320), (280, len(text))):
        chunks.append({"text": text[lo:hi], "metadata": {"char_start": lo, "char_end": hi,
            "block_start": 1, "block_end": 1, "heading": "Нэг", "partial_table": False}})
    result = _document_metrics("DOC0001", blocks, chunks, chunks, _tokenizer(), 512, 51)
    assert result["coverage_ratio"] == 1.0
    assert result["uncovered_nonwhitespace_characters"] == 0
    assert result["source_span_overlap_characters"] == 80
    assert result["actual_overlap_tokens_estimate"] > 0


def test_semantic_chunking_primitives_mock_cache_and_structural_gate(tmp_path):
    from app.split_semantic import (DeterministicMockEmbeddings, cached_embeddings,
        semantic_chunk_spans, split_sentences, structure_constrained_spans)
    enc = _tokenizer()
    text = "1.2.3. Ажилтан үүргээ биелүүлнэ. Тух. дараагийн заалт. Дүн 1.25 байна."
    sentences = split_sentences(text)
    assert len(sentences) == 3
    provider = DeterministicMockEmbeddings()
    vectors = cached_embeddings(sentences, provider, tmp_path, {"purpose": "unit"})
    assert cached_embeddings(sentences, provider, tmp_path, {"purpose": "unit"}) == vectors
    assert semantic_chunk_spans(text, vectors, enc, percentile=90, max_tokens=64)
    long_text = "үүрэг хариуцлага " * 300
    long_sentences = split_sentences(long_text)
    long_vectors = provider.embed_texts(long_sentences)
    long_spans = semantic_chunk_spans(long_text, long_vectors, enc, max_tokens=32)
    assert long_spans[0][0] == 0 and long_spans[-1][1] == len(long_text)
    assert all(len(enc.encode(long_text[lo:hi])) <= 32 for lo, hi in long_spans)
    assert all(left[1] == right[0] for left, right in zip(long_spans, long_spans[1:]))
    try:
        structure_constrained_spans(text, vectors, enc, verified_boundaries=None)
    except ValueError as exc:
        assert "blocked" in str(exc)
    else:
        raise AssertionError("S2 must require verified structure")


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


def test_a1_a3_chunks_preserve_mongolian_source_offsets_and_are_deterministic():
    from app.split_experiments import _experimental_chunks
    from app.split_benchmark import _merge_intervals

    blocks = [
        {"text": "1.2.3. Өдөр тутмын бодлого. " + ("Монгол өгүүлбэр. " * 180),
         "heading": "Хэсэг A", "block": 1, "kind": "paragraph"},
        {"text": "Хүснэгт | багана\nмөр | утга\n" + ("давтагдсан агуулга " * 40),
         "heading": "Хэсэг A", "block": 2, "kind": "table"},
        {"text": "Ижил гарчигтай хоёр дахь хэсэг. " * 30,
         "heading": "Хэсэг B", "block": 3, "kind": "paragraph"},
    ]
    enc = _tokenizer()
    for strategy in ("fixed-token-v1", "recursive-token-v1"):
        stable_version = str(uuid4())
        first = _experimental_chunks(blocks, strategy, enc, stable_version)
        second = _experimental_chunks(blocks, strategy, enc, stable_version)
        assert [item["text"] for item in first] == [item["text"] for item in second]
        assert [item["id"] for item in first] == [item["id"] for item in second]
        if strategy == "fixed-token-v1":
            assert max(len(enc.encode(item["text"])) for item in first) <= 512
        intervals, _ = _source_intervals(blocks, first, 512, 0)
        assert all(sum(hi-lo for lo, hi in _merge_intervals(spans)) == len(blocks[int(key)-1]["text"])
                   for key, spans in intervals.items())
        assert all("\ufffd" not in item["text"] for item in first)


def test_recursive_chunks_prefer_sentence_paragraph_separator_and_keep_metrics_unverified():
    from app.split_experiments import _experimental_chunks

    blocks = [{"text": ("Нэг заалт өгүүлбэрээр төгсөнө.\n\n" * 150), "heading": "", "block": 1}]
    version_id = str(uuid4())
    chunks = _experimental_chunks(blocks, "recursive-token-v1", _tokenizer(), version_id)
    assert len(chunks) > 1
    result = _document_metrics("DOC0001", blocks, chunks, chunks, _tokenizer(), 512, 0)
    assert result["coverage_ratio"] == 1.0
    assert result["complete_clause_preservation"] is None
    assert result["sentence_boundary_accuracy"] is None
