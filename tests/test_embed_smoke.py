import hashlib
import json

from app.embed_smoke import (_canonical_hash, deterministic_mock_vectors, prepare_smoke,
    run_isolated_smoke)


def _smoke_fixture(tmp_path):
    data_dir = tmp_path / "instance"
    base = data_dir / "split-evaluation"
    snapshot_id = "frozen-smoke-v1"
    snapshot = base / snapshot_id
    raw = snapshot / "raw"
    raw.mkdir(parents=True)
    text = "Ажилтан хүсэлтээ ажлын таван өдрийн өмнө гаргана."
    artifact = {"parser": "ordered-blocks-v1", "blocks": [{"block": 1, "kind": "paragraph", "text": text}]}
    artifact_file = raw / "DOC0001.json"
    artifact_file.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf8")
    source_sha = "a" * 64
    source_dataset_sha = "b" * 64
    (snapshot / "manifest.json").write_text(json.dumps({"snapshot_id": snapshot_id,
        "dataset_sha256": source_dataset_sha, "records": [{"doc_id": "DOC0001", "load_status": "success",
            "source_sha256": source_sha, "artifact": "raw/DOC0001.json",
            "artifact_sha256": hashlib.sha256(artifact_file.read_bytes()).hexdigest()}]}), encoding="utf8")
    record = {"candidate_id": "DOC0001-Q01", "doc_id": "DOC0001", "source_sha256": source_sha,
        "snapshot_id": snapshot_id, "human_confirmed": True, "evidence_matches_original": True,
        "question_evidence_relation_confirmed": True, "evidence_text": text,
        "query_variants": {"mn_cyrl": "Хүсэлтийг хэд хоногийн өмнө гаргах вэ?"},
        "source_location": {"page": None, "section": None, "clause_id": None, "document_version": None}}
    dataset = {"schema_version": 1, "dataset_version": "gt-test", "snapshot_id": snapshot_id,
        "source_dataset_sha256": source_dataset_sha, "reviewer_confirmed": True,
        "records": [record]}
    dataset["dataset_sha256"] = _canonical_hash(dataset)
    dataset_file = base / "datasets" / "ground-truth-gt-v1.json"
    dataset_file.parent.mkdir(parents=True)
    dataset_file.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf8")
    return data_dir


def test_plan_validates_snapshot_and_does_not_need_provider_or_database(tmp_path):
    from app import create_app
    app = create_app({"TESTING": True, "SECRET_KEY": "embed-smoke-test",
        "DATA_DIR": tmp_path / "app-data", "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    app.config.update(AI_PROVIDER="openai", OPENAI_EMBEDDING_MODEL="text-embedding-3-small",
        OPENAI_EMBEDDING_DIMENSIONS=16, CHUNK_SIZE=1800, CHUNK_OVERLAP=250)
    data_dir = _smoke_fixture(tmp_path)
    with app.app_context():
        plan, chunks, queries, evidence = prepare_smoke(data_dir, app.config)
    assert plan["approved_question_count"] == 1
    assert plan["document_chunk_count"] == 1
    assert plan["total_embedding_input_tokens_estimate"] > 0
    assert plan["production_database_or_chroma_access"] is False
    assert plan["records"][0]["evidence_present_in_frozen_parse"] is True
    assert plan["records"][0]["missing_optional_source_metadata"] == ["page", "section", "clause_id", "document_version"]
    assert len(chunks["DOC0001"]) == 1
    assert len(queries) == 1
    assert evidence["DOC0001-Q01"].startswith("Ажилтан")


def test_mock_pipeline_roundtrips_vectors_and_source_refs_in_isolated_chroma(tmp_path):
    from app import create_app
    app = create_app({"TESTING": True, "SECRET_KEY": "embed-smoke-test",
        "DATA_DIR": tmp_path / "app-data", "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    app.config.update(AI_PROVIDER="openai", OPENAI_EMBEDDING_MODEL="text-embedding-3-small",
        OPENAI_EMBEDDING_DIMENSIONS=16, CHUNK_SIZE=1800, CHUNK_OVERLAP=250)
    data_dir = _smoke_fixture(tmp_path)
    with app.app_context():
        plan, chunks, queries, evidence = prepare_smoke(data_dir, app.config)
        embedder = lambda texts, task: deterministic_mock_vectors(texts, task, plan["dimensions"])
        result = run_isolated_smoke(plan, chunks, queries, data_dir, embedder, run_id="fixed-test-run")
    assert result["indexed_chunk_count"] == 1
    assert result["stored_ids_match_manifest"] is True
    assert result["production_data_touched"] is False
    assert result["source_reference_roundtrips"][0]["evidence_chunk_reference_roundtrip"] is True
    assert result["mock_or_oracle_vector_roundtrips"][0]["oracle_reference_roundtrip"] is True
    assert result["query_semantic_quality_measured"] is False
    assert "chroma" in result["isolated_chroma_path"]
