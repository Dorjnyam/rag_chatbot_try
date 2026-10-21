import hashlib
import json

import pytest

from app.annotation_review import (export_dataset, load_state, readiness_report,
    validate_approved_record)


def _fixture_package(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    source_path = uploads / "policy.docx"
    source_path.write_bytes(b"synthetic immutable original source")
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    snapshot_id = "frozen-test-v1"
    snapshot_root = tmp_path / "snapshots"
    snapshot_dir = snapshot_root / snapshot_id
    raw = snapshot_dir / "raw"
    raw.mkdir(parents=True)
    artifact = {"parser": "ordered-blocks-v1", "blocks": [{"block": 1,
        "text": "Ажилтан энэ журмын шаардлагыг мөрдөнө.", "kind": "paragraph"}]}
    artifact_path = raw / "DOC0001.json"
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf8")
    artifact_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    dataset_sha = "a" * 64
    manifest = {"snapshot_id": snapshot_id, "dataset_sha256": dataset_sha, "records": [{
        "doc_id": "DOC0001", "load_status": "success", "artifact": "raw/DOC0001.json",
        "artifact_sha256": artifact_hash}]}
    (snapshot_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf8")
    review_root = tmp_path / "annotation-review"
    review_root.mkdir()
    candidate = {"candidate_id": "DOC0001-Q01", "doc_id": "DOC0001",
        "source_file": str(source_path), "source_sha256": source_hash,
        "question_variants": {"mn_cyrl": "Ямар шаардлага байна?", "en": "What requirement is stated?",
            "mn_latn_draft": "Yamar shaardlaga baina?"},
        "proposed_evidence_text": "Ажилтан энэ журмын шаардлагыг мөрдөнө.",
        "proposed_expected_answer": "Ажилтан журмын шаардлагыг мөрдөнө."}
    bundle = {"snapshot_id": snapshot_id, "dataset_sha256": dataset_sha, "candidates": [candidate]}
    bundle_path = review_root / "candidate-review.json"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf8")
    original_bundle = bundle_path.read_bytes()
    return review_root, uploads, snapshot_root, candidate, original_bundle


def _approve_record(record):
    record["status"] = "approved"
    record["question_variants"] = {"mn_cyrl": "Ямар шаардлага байна?", "en": "What requirement is stated?",
        "mn_latn_draft": "Yamar shaardlaga baina?"}
    record["verified_variations"] = {"mn_cyrl": True, "en": True, "mn_latn_draft": True}
    record["evidence_text"] = "Ажилтан энэ журмын шаардлагыг мөрдөнө."
    record["expected_answer"] = "Ажилтан журмын шаардлагыг мөрдөнө."
    record["evidence_matches_original"] = True
    record["question_evidence_relation_confirmed"] = True
    record["query_type"] = "requirement"
    record["reviewer_id"] = "reviewer-test"
    record["reviewed_at"] = "2026-10-21T00:00:00+00:00"
    record["human_confirmed"] = True


def test_review_state_resumes_without_modifying_original_package(tmp_path):
    review_root, _, _, _, original_bundle = _fixture_package(tmp_path)
    state = load_state(review_root)
    assert state["candidates"]["DOC0001-Q01"]["status"] == "draft"
    assert state["candidates"]["DOC0001-Q01"]["human_confirmed"] is False
    assert load_state(review_root)["candidates"]["DOC0001-Q01"]["status"] == "draft"
    assert (review_root / "candidate-review.json").read_bytes() == original_bundle


def test_approval_requires_explicit_human_checks_and_frozen_source_evidence(tmp_path):
    review_root, uploads, snapshot_root, _, _ = _fixture_package(tmp_path)
    state = load_state(review_root)
    record = state["candidates"]["DOC0001-Q01"]
    _approve_record(record)
    assert validate_approved_record(record, uploads, snapshot_root) == []
    record["human_confirmed"] = False
    assert "explicit_human_confirmation_missing" in validate_approved_record(record, uploads, snapshot_root)
    record["human_confirmed"] = True
    record["evidence_text"] = "not present in frozen extraction"
    assert "evidence_not_in_frozen_extraction_or_confirmed_source_anchor" in validate_approved_record(record, uploads, snapshot_root)


def test_export_is_versioned_hashed_private_and_readiness_uses_type_language_coverage(tmp_path):
    review_root, uploads, snapshot_root, _, _ = _fixture_package(tmp_path)
    state = load_state(review_root)
    _approve_record(state["candidates"]["DOC0001-Q01"])
    from app.annotation_review import save_state
    save_state(review_root, state)
    report = readiness_report(review_root, uploads, snapshot_root)
    assert report["approved_questions"] == 1
    assert report["retrieval_evaluation_ready"] is False
    assert "table_fact" in report["readiness_reasons"][0] or any("query type" in reason for reason in report["readiness_reasons"])
    output = tmp_path / "datasets"
    result = export_dataset(review_root, uploads, snapshot_root, output, "v1")
    exported = json.loads((output / "ground-truth-v1.json").read_text(encoding="utf8"))
    assert result["dataset_sha256"] == exported["dataset_sha256"]
    assert exported["reviewer_confirmed"] is True
    assert exported["valid_query_variation_count"] == 3
    assert exported["records"][0]["doc_id"] == "DOC0001"
    with pytest.raises(ValueError, match="overwrite"):
        export_dataset(review_root, uploads, snapshot_root, output, "v1")


def test_flask_review_route_saves_and_explicitly_approves(tmp_path, monkeypatch):
    from app import create_app
    import app.routes.annotation_review as route_module

    review_root, uploads, snapshot_root, _, _ = _fixture_package(tmp_path)
    monkeypatch.setattr(route_module, "_paths", lambda: (review_root, uploads, snapshot_root))
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path / "app-data", "SECRET_KEY": "review-test",
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    client = app.test_client()
    page = client.get("/annotation-review/")
    assert page.status_code == 200
    assert b"NOT READY" in page.data
    assert "талбаруудыг хэрхэн бөглөх" in page.get_data(as_text=True).lower()
    assert b"synthetic immutable original source" in client.get("/annotation-review/source/DOC0001-Q01").data
    with client.session_transaction() as session:
        csrf = session["csrf"]
    data = {"csrf": csrf, "action": "approve", "human_confirmation": "yes", "reviewer_id": "human-reviewer",
        "question_mn_cyrl": "Ямар шаардлага байна?", "verified_mn_cyrl": "yes",
        "question_en": "What requirement is stated?", "verified_en": "yes",
        "question_mn_latn_draft": "Yamar shaardlaga baina?", "verified_mn_latn_draft": "yes",
        "query_type": "requirement", "evidence_text": "Ажилтан энэ журмын шаардлагыг мөрдөнө.",
        "expected_answer": "Ажилтан журмын шаардлагыг мөрдөнө.", "evidence_matches_original": "yes",
        "question_evidence_relation_confirmed": "yes"}
    response = client.post("/annotation-review/DOC0001-Q01?next=DOC0001-Q01", data=data)
    assert response.status_code == 302
    record = load_state(review_root)["candidates"]["DOC0001-Q01"]
    assert record["status"] == "corrected"
    assert record["human_confirmed"] is True
    assert record["reviewer_id"] == "human-reviewer"
    forbidden = client.get("/annotation-review/", environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert forbidden.status_code == 403


def test_annotation_source_preview_converts_docx_and_serves_inline_cached_pdf(tmp_path, monkeypatch):
    from app import create_app
    import app.routes.annotation_review as route_module
    import app.services.preview_worker as worker

    review_root, uploads, snapshot_root, _, _ = _fixture_package(tmp_path)
    monkeypatch.setattr(route_module, "_paths", lambda: (review_root, uploads, snapshot_root))
    calls = []

    def fake_convert(source, output_file, executable, timeout):
        calls.append((source, executable, timeout))
        output_file.write_bytes(b"%PDF-1.7 preview bytes")
        return output_file

    monkeypatch.setattr(worker, "_convert", fake_convert)
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path / "app-data", "SECRET_KEY": "preview-test",
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    app.instance_path = str(tmp_path / "review-instance")
    client = app.test_client()
    url = "/annotation-review/preview/DOC0001-Q01"
    first = client.get(url)
    second = client.get(url)
    assert first.status_code == 200
    assert first.mimetype == "application/pdf"
    assert first.headers["Content-Disposition"].startswith("inline")
    assert first.headers["Content-Security-Policy"].find("frame-ancestors 'self'") >= 0
    assert first.data.startswith(b"%PDF")
    assert second.data == first.data
    assert len(calls) == 1
