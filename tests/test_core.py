import json
import math
from uuid import uuid4
import pytest
from app.services.chunking import chunk
from app.services.gemini import normalize_vector
from app.services.rag import validate_answer
from app.services.parsing import normalize, parse_direct, validate_file
from app.evaluation import ranking_metrics, units_for_hit, load_questions


def test_chunk_coverage_offsets_and_determinism():
    blocks = [dict(text="abcdefghijklmno", heading="", block=1, method="native", kind="page", page=1),
              dict(text="second page", heading="", block=2, method="native", kind="page", page=2)]
    config = dict(chunk_size=8, chunk_overlap=3)
    version = str(uuid4())
    result = chunk(blocks, config, version)
    assert result == chunk(blocks, config, version)
    assert [c["text"] for c in result[:3]] == ["abcdefgh", "fghijklm", "klmno"]
    assert all(c["metadata"]["page_start"] == c["metadata"]["page_end"] for c in result)
    assert len({c["id"] for c in result}) == len(result)


def test_chunk_groups_headings_and_empty():
    config = dict(chunk_size=1800, chunk_overlap=250)
    blocks = [dict(text="A", heading="H1", block=1, method="native"), dict(text="B", heading="H1", block=2, method="native"), dict(text="C", heading="H2", block=3, method="native")]
    result = chunk(blocks, config, str(uuid4()))
    assert [c["text"] for c in result] == ["A\n\nB", "C"]
    assert chunk([], config, str(uuid4())) == []
    with pytest.raises(ValueError):
        chunk(blocks, dict(chunk_size=2, chunk_overlap=2), str(uuid4()))


@pytest.mark.parametrize("vector", [[0,0], [1], [math.inf,0], [math.nan,1]])
def test_invalid_vectors(vector):
    with pytest.raises(ValueError):
        normalize_vector(vector, 2)


def test_vector_normalization():
    assert normalize_vector([3,4], 2) == [.6,.8]


def test_openai_embedding_response_is_ordered_and_normalized():
    from types import SimpleNamespace
    from app.services.openai_provider import OpenAIProvider

    adapter = object.__new__(OpenAIProvider)
    adapter.embedding_model, adapter.embedding_dimensions = "text-embedding-3-small", 2
    adapter._encoding = lambda _model: None
    adapter._call = lambda operation: SimpleNamespace(data=[
        SimpleNamespace(index=1, embedding=[0, 3]),
        SimpleNamespace(index=0, embedding=[4, 0]),
    ])
    assert adapter.embed(["first", "second"], "RETRIEVAL_QUERY") == [[1, 0], [0, 1]]


def test_openai_embedding_rejects_over_limit_input():
    from app.services.openai_provider import OpenAIProvider

    adapter = object.__new__(OpenAIProvider)
    adapter.embedding_model, adapter.embedding_dimensions = "text-embedding-3-small", 2
    adapter._encoding = lambda _model: None
    with pytest.raises(ValueError, match="7,900-token"):
        adapter.embed(["x" * 8000], "RETRIEVAL_QUERY")


def test_provider_configuration_keeps_legacy_gemini_and_uses_openai_defaults(monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.setenv("EMBEDDING_MODEL", "legacy-gemini-model")
    legacy = settings(tmp_path)
    assert legacy["AI_PROVIDER"] == "gemini"
    assert legacy["EMBEDDING_MODEL"] == "legacy-gemini-model"
    monkeypatch.setenv("AI_PROVIDER", "openai")
    selected = settings(tmp_path)
    assert selected["EMBEDDING_MODEL"] == "text-embedding-3-small"
    assert selected["GENERATION_MODEL"] == "gpt-6-luna"
    assert selected["EMBEDDING_DIMENSIONS"] == 1536


def test_embedding_byte_guard_splits_without_token_count_endpoint():
    from app.services.gemini import Gemini
    adapter = object.__new__(Gemini)
    text = "Үүрэг " * 500
    blocks = [dict(text=text, heading="H", block=1, method="native")]
    config = dict(chunk_size=1800, chunk_overlap=250)
    version = str(uuid4())
    original = chunk(blocks, config, version)
    result = adapter.fit_chunks(original, "gemini-embedding-001", version, config)
    assert len(result) > len(original)
    assert all(len(item["text"].encode()) <= 1900 for item in result)
    assert "".join(item["text"] for item in result) == "".join(item["text"] for item in original)


@pytest.mark.parametrize("raw", ['broken', '{}', '{"status":"answered","answer":"hi","sources":[]}', '{"status":"answered","answer":"hi [S2]","sources":["S2"]}'])
def test_invalid_answers(raw):
    with pytest.raises(ValueError):
        validate_answer(raw, [{"label":"S1"}])


def test_citation_and_abstention_contract():
    assert validate_answer(json.dumps(dict(status="answered", answer="Five days [S1]", sources=["S1"])), [{"label":"S1"}])["status"] == "answered"
    assert validate_answer(json.dumps(dict(status="insufficient_evidence", answer="No evidence", sources=[])), [])["sources"] == []


def test_metrics_hand_calculated_and_overlap_deduplicated():
    result = ranking_metrics([set(), {"a"}, {"a"}], {"a", "b"}, 5)
    assert result == dict(recall=.5, precision=.2, reciprocal_rank=.5, hit_rate=1.0,
        ndcg=(1 / math.log2(3)) / (1 + 1 / math.log2(3)))
    assert ranking_metrics([], set(), 5)["recall"] is None
    assert ranking_metrics([], {"a"}, 5)["reciprocal_rank"] == 0


def test_mongolian_query_detection_and_conservative_normalization():
    from app.services.language import detect_language, normalize_romanized, query_candidates
    assert detect_language("Ажилтны амралт хэдэн хоног вэ?")["language"] == "mn_cyrl"
    assert detect_language("How many days of annual leave?")["language"] == "en"
    assert detect_language("Ajiltnii eeljiin amralt heden honog ve?")["language"] == "mn_latn"
    assert detect_language("Ajiltnii annual leave heden honog ve?")["language"] == "mixed"
    assert detect_language("xyzzy")["language"] == "unknown"
    normalized = normalize_romanized("Ajiltnii eeljiin amralt heden honog ve? Ref POL-04")
    assert normalized["text"] == "Ажилтны ээлжийн амралт хэдэн хоног вэ? Ref POL-04"
    assert query_candidates("Ajiltnii eeljiin amralt heden honog ve?")["original"].startswith("Ajiltnii")


def test_bm25_unicode_metadata_and_rrf_deduplication(tmp_path):
    from flask import Flask
    from app.services.sparse import write_index, search
    from app.services.sparse import rrf
    app = Flask(__name__)
    app.config["DATA_DIR"] = tmp_path
    with app.app_context():
        chunks = [dict(id="leave", text="Ажилтны ээлжийн амралт таван өдөр", metadata=dict(version_id="v1", document_id="d1", heading="Хүний нөөц")),
            dict(id="code", text="POL-04 журам", metadata=dict(version_id="v1", document_id="d1", heading=""))]
        write_index("v1", chunks, "Ажилтны журам", "hr-policy.docx")
        hits, missing = search("амралт POL-04", [("v1", "Ажилтны журам", "hr-policy.docx")], 5)
        assert not missing and {hit["id"] for hit in hits} == {"leave", "code"}
    merged = rrf({"dense": [dict(id="x", text="same"), dict(id="y")], "sparse": [dict(id="x", sparse_score=2)]})
    assert [item["id"] for item in merged] == ["x", "y"]
    assert merged[0]["branch_ranks"] == {"dense": 1, "sparse": 1}


def test_gold_requires_excerpt_and_location():
    gold = [dict(unit_id="x", version_id="v", block=2, excerpt="correct qualification")]
    hit = dict(text="wrong passage", metadata=dict(version_id="v", block_start=1, block_end=4))
    assert not units_for_hit(hit, gold)
    hit["text"] = "A correct qualification is here"
    assert units_for_hit(hit, gold) == {"x"}


def test_normalization_preserves_mongolian():
    assert normalize("Өдөр\x00\t Үүрэг\n  дүрэм  ") == "Өдөр Үүрэг\nдүрэм"


def test_docx_order_and_tables(tmp_path):
    from conftest import docx_file
    file = tmp_path / "sample.docx"
    file.write_bytes(docx_file("Өдөр Үүрэг", table=True).read())
    result = parse_direct(file, "docx", {"DOCX_MAX_EXPANDED":1000000})
    assert result["blocks"][1]["text"] == "Өдөр Үүрэг"
    assert result["blocks"][2]["text"] == "Days | 5"
    assert all("page" not in b for b in result["blocks"])


def test_corrupt_docx_and_legacy_rename_rejected(tmp_path):
    file = tmp_path / "fake.docx"
    file.write_text("not a document")
    with pytest.raises(ValueError):
        validate_file(file, "docx", {"DOCX_MAX_EXPANDED":10000})
    with pytest.raises(ValueError):
        validate_file(file, "doc", {})


def test_synthetic_dataset_balance_and_family_splits(tmp_path):
    from app.samples import generate
    dataset = load_questions(generate(tmp_path))
    assert len(dataset) == 80
    assert len(list(tmp_path.glob("*.docx"))) == 16
    assert sum(q["answerable"] for q in dataset) == 60
    assert {q["review_status"] for q in dataset} == {"draft"}
    assert any(len(q["evidence"]) == 2 for q in dataset)
    assert any(any(e.get("block") == 6 for e in q["evidence"]) for q in dataset)
    for direction in {q["direction"] for q in dataset}:
        group = [q for q in dataset if q["direction"] == direction]
        assert len(group) == 20
        assert sum(q["split"] == "dev" for q in group) == 10
    families = {}
    for q in dataset:
        assert families.setdefault(q["family"], q["split"]) == q["split"]
