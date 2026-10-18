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
    assert result == dict(recall=.5, precision=.2, reciprocal_rank=.5)
    assert ranking_metrics([], set(), 5)["recall"] is None
    assert ranking_metrics([], {"a"}, 5)["reciprocal_rank"] == 0


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
