import json
import hashlib
from flask import Flask
from app.generation_benchmark import run_offline_generation_benchmark
from app.config import settings
from pathlib import Path


def test_offline_generation_benchmark_uses_only_local_fixtures_and_redacts_text(tmp_path):
    app = Flask(__name__)
    config = settings(tmp_path)
    config.update(HISTORY_TOKENS=2000, CONTEXT_TOKENS=8000, INPUT_TOKENS=12000,
        OUTPUT_TOKENS=1000, CONTEXT_CHUNKS=6, GENERATION_MODEL="gpt-6-luna", OPENAI_REASONING_EFFORT="none")
    with app.app_context():
        result = run_offline_generation_benchmark(tmp_path, config, iterations=1)
    assert result["network_api_calls"] == 0
    assert result["postgresql_access"] is False and result["chroma_access"] is False
    assert result["outcomes"]["invalid_samples"] == 0
    assert result["fixture_counts"]["timed_sample_count"] == 12
    artifact = open(result["private_result_path"], encoding="utf8").read()
    assert hashlib.sha256(artifact.encode("utf8")).hexdigest() == result["private_result_sha256"]
    assert "Ээлжийн амралтыг ажилтны хүсэлт" not in artifact
    assert '"question":' not in artifact and "evidence_text" not in artifact
    assert json.loads(artifact)["raw_question_evidence_answer_retained"] is False
    assert len(json.loads(artifact)["cold_sample_records"]) == 12
    assert len(json.loads(artifact)["timed_sample_records"]) == 12


def test_offline_generation_benchmark_redacts_private_annotation_content(tmp_path):
    dataset_dir = tmp_path / "split-evaluation" / "datasets"
    dataset_dir.mkdir(parents=True)
    source = {"reviewer_confirmed": True, "dataset_version": "gt-test", "snapshot_id": "snapshot-test",
        "dataset_sha256": "fixture-hash", "valid_query_variation_count": 3,
        "records": [{"human_confirmed": True, "candidate_id": "private-candidate", "evidence_text": "PRIVATE_EVIDENCE_SENTINEL",
            "expected_answer": "PRIVATE_ANSWER_SENTINEL", "query_variants": {
                "mn_cyrl": "PRIVATE_QUESTION_SENTINEL", "en": "PRIVATE_EN_SENTINEL", "mn_latn_draft": "PRIVATE_LATIN_SENTINEL"}}]}
    (dataset_dir / "ground-truth-gt-v1.json").write_text(json.dumps(source), encoding="utf8")
    app = Flask(__name__)
    config = settings(tmp_path)
    config.update(HISTORY_TOKENS=2000, CONTEXT_TOKENS=8000, INPUT_TOKENS=12000,
        OUTPUT_TOKENS=1000, CONTEXT_CHUNKS=6, GENERATION_MODEL="gpt-6-luna", OPENAI_REASONING_EFFORT="none")
    with app.app_context():
        result = run_offline_generation_benchmark(tmp_path, config, iterations=1)
    assert result["private_annotation_dataset"]["approved_questions"] == 1
    assert result["fixture_counts"]["private_reference_variations"] == 3
    artifact = Path(result["private_result_path"]).read_text(encoding="utf8")
    for sentinel in ("PRIVATE_EVIDENCE_SENTINEL", "PRIVATE_ANSWER_SENTINEL", "PRIVATE_QUESTION_SENTINEL", "PRIVATE_EN_SENTINEL", "PRIVATE_LATIN_SENTINEL"):
        assert sentinel not in artifact
