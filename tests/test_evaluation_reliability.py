"""Pure synthetic policy/metric regressions; no model, DB or company data."""
import json
import math
from types import SimpleNamespace

import pytest

from app.evaluation import METRIC_REVISION, compare_runs, corpus_chunk_mapping, load_questions, ranking_metrics
from app.services.language import normalize_romanized, validate_output_language


def test_binary_chunk_gain_and_evidence_recall_are_distinct():
    result = ranking_metrics([{"a", "b"}, {"a"}, set()], {"a", "b"}, 3,
        relevant_chunk_count=2, retrieved_ids=["one", "two", "three"])
    assert result["recall"] == 1
    assert result["precision"] == 2 / 3
    assert result["ndcg"] == 1
    assert result["reciprocal_rank"] == 1


def test_repeated_canonical_chunk_has_no_extra_gain():
    result = ranking_metrics([{"a"}, {"a"}, {"a"}], {"a"}, 3,
        relevant_chunk_count=2, retrieved_ids=["one", "one", "two"])
    assert result["precision"] == 2 / 3
    assert result["recall"] == 1
    assert result["ndcg"] == pytest.approx((1 + 1 / math.log2(4)) / (1 + 1 / math.log2(3)))


def test_ndcg_unavailable_without_full_corpus_mapping():
    assert ranking_metrics([{"a", "b"}], {"a", "b"}, 1)["ndcg"] is None
    with pytest.raises(ValueError, match="inconsistent"):
        ranking_metrics([{"a"}, {"a"}], {"a"}, 2, relevant_chunk_count=1)


@pytest.mark.parametrize("protected", ["policy_ajiltan_2026", "AJILTAN-04", "https://example.org/ajiltan", "amralt.docx", "AJILTAN"])
def test_romanization_preserves_full_identifier(protected):
    result = normalize_romanized(f"ajiltan amralt {protected}")
    assert result["text"] == f"ажилтан амралт {protected}"


def test_output_language_retains_english_prose_and_rejects_russian():
    assert validate_output_language("Синтетик журмын дагуу таван ажлын өдөр байна. [S1]") == "pass"
    assert validate_output_language("Ажлын өдөр байна. You must submit your request before Friday.") == "fail"
    assert validate_output_language("Согласно политике сотрудник должен оформить отпуск.") == "fail"
    assert validate_output_language("Текст") == "uncertain"
    assert validate_output_language("Policy Handbook журмын дагуу таван ажлын өдөр байна.", ["Policy Handbook"]) == "pass"
    assert validate_output_language("Policy Handbook журмын дагуу таван ажлын өдөр байна.") == "fail"


def test_protected_substrings_do_not_hide_english_words():
    assert validate_output_language("Ажлын өдөр байна. Employees must return.", ["Employee"]) == "fail"


def test_legacy_dataset_and_split_leak_detection(tmp_path):
    file = tmp_path / "questions.jsonl"
    question = dict(id="one", language="mn", split="dev", answerable=False, question="Асуулт")
    file.write_text(json.dumps(question), encoding="utf8")
    assert load_questions(file) == [question]
    question["intent_id"] = "leave-policy"
    other = dict(question, id="two", split="test")
    file.write_text("\n".join(map(json.dumps, [question, other])), encoding="utf8")
    with pytest.raises(ValueError, match="share one split"):
        load_questions(file)


def _run(**changes):
    config = dict(top_k=5, provider="openai", embedding_model="synthetic", dimensions=3)
    config.update(changes)
    return SimpleNamespace(id="run", config=config, metrics={}, snapshot=dict(
        corpus=[dict(document_id="doc", sha256="hash", index_config={"normalization": "l2-v1"})],
        dataset_hash="dataset", split="dev", metric_revision=METRIC_REVISION))


@pytest.mark.parametrize("changes", [dict(top_k=6), dict(provider="gemini"), dict(dimensions=4), dict(embedding_model="other")])
def test_comparison_rejects_incomparable_runs(changes):
    with pytest.raises(ValueError):
        compare_runs(_run(), _run(**changes))


def test_comparison_rejects_missing_or_changed_metric_revision():
    first, second = _run(), _run()
    assert compare_runs(first, second)["changed_settings"] == {}
    second.snapshot.pop("metric_revision")
    with pytest.raises(ValueError, match="metric revision"):
        compare_runs(first, second)


def test_corpus_mapping_requires_manifest_and_owned_metadata(monkeypatch):
    from app import evaluation
    from app.services import vectorstore
    version = SimpleNamespace(id="version", document_id="doc", expected_chunks=1, collection_name="collection", manifest_path="manifest")
    data = dict(ids=["chunk"], documents=["Synthetic evidence"], metadatas=[dict(version_id="version", document_id="doc")])
    collection = SimpleNamespace(get=lambda **kwargs: data)
    monkeypatch.setattr(vectorstore, "store", lambda: SimpleNamespace(collection=lambda name: collection))
    monkeypatch.setattr(evaluation, "read_json", lambda path: [dict(id="chunk")])
    assert corpus_chunk_mapping([version])[0]["id"] == "chunk"
    data["metadatas"][0]["document_id"] = "other"
    with pytest.raises(ValueError, match="invalid"):
        corpus_chunk_mapping([version])
    data["metadatas"][0]["document_id"] = "doc"
    data["ids"] = ["unexpected"]
    with pytest.raises(ValueError, match="manifest"):
        corpus_chunk_mapping([version])
