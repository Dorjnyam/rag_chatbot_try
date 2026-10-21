"""Private human annotation review state, validation, readiness, and export."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LANGUAGES = ("mn_cyrl", "en", "mn_latn_draft")
QUERY_TYPES = ("requirement", "exception", "procedure", "table_fact", "definition", "other")
REQUIRED_QUERY_TYPES = ("requirement", "exception", "procedure", "table_fact", "definition")
ACCEPTED_STATES = {"approved", "corrected"}
REVIEW_STATES = {"draft", "approved", "corrected", "rejected", "skipped"}
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")


def _canonical_hash(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8")
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    return " ".join(value.split())


def _bundle_path(review_root: Path) -> Path:
    return review_root / "candidate-review.json"


def _initial_state(review_root: Path) -> dict:
    bundle = json.loads(_bundle_path(review_root).read_text(encoding="utf8"))
    candidates = {}
    for source in bundle.get("candidates", []):
        candidate_id = source.get("candidate_id")
        if not candidate_id or candidate_id in candidates:
            raise ValueError("Candidate package contains a missing or duplicate candidate ID.")
        candidates[candidate_id] = {
            "candidate_id": candidate_id,
            "doc_id": source.get("doc_id"),
            "source_sha256": source.get("source_sha256"),
            "source_file": source.get("source_file"),
            "snapshot_id": bundle.get("snapshot_id"),
            "proposed_question_variants": source.get("question_variants", {}),
            "question_variants": source.get("question_variants", {}).copy(),
            "verified_variations": {language: False for language in LANGUAGES},
            "proposed_evidence_text": source.get("proposed_evidence_text", ""),
            "evidence_text": source.get("proposed_evidence_text", ""),
            "proposed_expected_answer": source.get("proposed_expected_answer", ""),
            "expected_answer": source.get("proposed_expected_answer", ""),
            "source_reference": "",
            "source_location": {"page": None, "section": None, "clause_id": None, "document_version": None},
            "source_location_verified": False,
            "evidence_matches_original": False,
            "question_evidence_relation_confirmed": False,
            "query_type": "",
            "status": "draft",
            "human_confirmed": False,
            "reviewer_id": None,
            "reviewed_at": None,
            "updated_at": None,
            "rejection_reason": None,
        }
    return {"schema_version": 1, "snapshot_id": bundle.get("snapshot_id"),
        "dataset_sha256": bundle.get("dataset_sha256"), "created_at": _now(), "candidates": candidates}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state(review_root: Path) -> dict:
    """Load resumable reviewer state, initializing it once from the immutable draft bundle."""
    review_root.mkdir(parents=True, exist_ok=True)
    state_path = review_root / "review-state.json"
    if not state_path.is_file():
        state = _initial_state(review_root)
        save_state(review_root, state)
        return state
    state = json.loads(state_path.read_text(encoding="utf8"))
    bundle = json.loads(_bundle_path(review_root).read_text(encoding="utf8"))
    if state.get("snapshot_id") != bundle.get("snapshot_id") or state.get("dataset_sha256") != bundle.get("dataset_sha256"):
        raise ValueError("Review state belongs to a different frozen corpus; it was left unchanged.")
    expected = {candidate.get("candidate_id") for candidate in bundle.get("candidates", [])}
    if set(state.get("candidates", {})) != expected:
        raise ValueError("Review state candidate IDs do not match the immutable draft package.")
    return state


def save_state(review_root: Path, state: dict) -> None:
    review_root.mkdir(parents=True, exist_ok=True)
    path = review_root / "review-state.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    temporary.replace(path)


def _source_path(record: dict, uploads_root: Path) -> Path | None:
    raw_path = record.get("source_file")
    if not raw_path:
        return None
    candidate = Path(raw_path).resolve()
    root = uploads_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def validate_source_identity(record: dict, uploads_root: Path) -> tuple[bool, str | None]:
    path = _source_path(record, uploads_root)
    if path is None:
        return False, "source_path_outside_uploads_or_missing"
    if not path.is_file():
        return False, "source_file_missing"
    if not record.get("doc_id") or not re.fullmatch(r"DOC\d{4}", record["doc_id"]):
        return False, "invalid_document_identity"
    expected_hash = record.get("source_sha256")
    if not expected_hash or _file_sha256(path) != expected_hash:
        return False, "source_hash_mismatch"
    return True, None


def _snapshot_contains_evidence(record: dict, evidence_text: str, snapshot_root: Path) -> bool:
    snapshot_id = record.get("snapshot_id")
    if not snapshot_id or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", snapshot_id):
        return False
    snapshot_dir = (snapshot_root / snapshot_id).resolve()
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf8"))
        doc = next(row for row in manifest["records"] if row.get("doc_id") == record.get("doc_id"))
        artifact_path = (snapshot_dir / doc["artifact"]).resolve()
        artifact_path.relative_to(snapshot_dir)
        if not artifact_path.is_file() or _file_sha256(artifact_path) != doc.get("artifact_sha256"):
            return False
        artifact = json.loads(artifact_path.read_text(encoding="utf8"))
        if artifact.get("parser") != "ordered-blocks-v1":
            return False
        corpus = "\n".join(block.get("text", "") for block in artifact.get("blocks", []))
        return _normalized_text(evidence_text) in _normalized_text(corpus)
    except (OSError, ValueError, KeyError, StopIteration, json.JSONDecodeError):
        return False


def validate_approved_record(record: dict, uploads_root: Path, snapshot_root: Path) -> list[str]:
    errors = []
    source_ok, source_error = validate_source_identity(record, uploads_root)
    if not source_ok:
        errors.append(source_error or "invalid_source")
    questions = record.get("question_variants", {})
    verified = record.get("verified_variations", {})
    if not (questions.get("mn_cyrl") or "").strip() or not verified.get("mn_cyrl"):
        errors.append("mn_cyrl_question_not_verified")
    if not any((questions.get(language) or "").strip() and verified.get(language) for language in LANGUAGES):
        errors.append("no_verified_query_variation")
    evidence = (record.get("evidence_text") or "").strip()
    if not evidence:
        errors.append("missing_evidence")
    if not (record.get("expected_answer") or "").strip():
        errors.append("missing_expected_answer")
    if not record.get("question_evidence_relation_confirmed"):
        errors.append("question_evidence_relationship_not_confirmed")
    if not record.get("evidence_matches_original"):
        errors.append("evidence_not_visually_verified")
    if not _snapshot_contains_evidence(record, evidence, snapshot_root):
        anchor_present = bool(record.get("source_reference", "").strip()) and bool(record.get("source_location_verified"))
        if not anchor_present:
            errors.append("evidence_not_in_frozen_extraction_or_confirmed_source_anchor")
    if record.get("query_type") not in REQUIRED_QUERY_TYPES:
        errors.append("query_type_not_verified_or_not_representative")
    if not (record.get("reviewer_id") or "").strip():
        errors.append("reviewer_id_missing")
    if not record.get("human_confirmed"):
        errors.append("explicit_human_confirmation_missing")
    location = record.get("source_location", {})
    supplied_location = any(location.get(field) not in (None, "") for field in ("page", "section", "clause_id", "document_version"))
    if supplied_location and not record.get("source_location_verified"):
        errors.append("source_location_not_explicitly_verified")
    if record.get("document_identity_locked") is False:
        errors.append("document_identity_changed")
    return errors


def readiness_report(review_root: Path, uploads_root: Path, snapshot_root: Path) -> dict:
    state = load_state(review_root)
    records = list(state["candidates"].values())
    counts = {status: sum(record.get("status") == status for record in records) for status in REVIEW_STATES}
    approved = [record for record in records if record.get("status") in ACCEPTED_STATES]
    valid_variations = {language: 0 for language in LANGUAGES}
    for record in approved:
        for language in LANGUAGES:
            if record.get("verified_variations", {}).get(language) and (record.get("question_variants", {}).get(language) or "").strip():
                valid_variations[language] += 1
    source_invalid = sum(not validate_source_identity(record, uploads_root)[0] for record in records)
    missing_evidence = sum(not record.get("evidence_text", "").strip() for record in records)
    invalid_approved = {record["candidate_id"]: validate_approved_record(record, uploads_root, snapshot_root)
        for record in approved}
    invalid_approved = {key: value for key, value in invalid_approved.items() if value}
    type_coverage = {query_type: sum(record.get("query_type") == query_type for record in approved)
        for query_type in REQUIRED_QUERY_TYPES}
    reasons = []
    if not approved:
        reasons.append("Баталгаажсан асуулт алга.")
    if invalid_approved:
        reasons.append("Approved/corrected төлөвтэй зарим бичлэгийн source/evidence/confirmation validation алдаатай.")
    if any(count == 0 for count in valid_variations.values()):
        reasons.append("Cyrillic, English, Latin query variation бүрт баталгаажсан жишээ хүрээгүй.")
    uncovered_types = [key for key, count in type_coverage.items() if count == 0]
    if uncovered_types:
        reasons.append("Төлөөлөх query type дутуу: " + ", ".join(uncovered_types) + ".")
    if source_invalid:
        reasons.append("Эх document-ийн identity/hash шалгалт амжилтгүй бичлэг байна.")
    return {"dataset_sha256": state["dataset_sha256"], "snapshot_id": state["snapshot_id"],
        "total_questions": len(records), "approved_questions": len(approved),
        "rejected_questions": counts["rejected"], "awaiting_review_questions": counts["draft"] + counts["corrected"] + counts["skipped"],
        "status_counts": counts, "verified_query_variation_coverage": valid_variations,
        "missing_evidence_questions": missing_evidence, "invalid_source_references": source_invalid,
        "approved_validation_failures": invalid_approved, "approved_query_type_coverage": type_coverage,
        "representativeness_rule": {"required_query_types": list(REQUIRED_QUERY_TYPES),
            "required_language_variations": list(LANGUAGES), "rule": "type ба language бүрд human-verified жишээ байх; зөвхөн тоон босго хангалтгүй"},
        "retrieval_evaluation_ready": not reasons, "readiness_reasons": reasons}


def export_dataset(review_root: Path, uploads_root: Path, snapshot_root: Path,
                   datasets_root: Path, version: str) -> dict:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError("Dataset version нь үсэг/тоо/цэг/зураас/underscore агуулсан 1-40 тэмдэгт байна.")
    state = load_state(review_root)
    accepted = [record for record in state["candidates"].values() if record.get("status") in ACCEPTED_STATES]
    failures = {record["candidate_id"]: validate_approved_record(record, uploads_root, snapshot_root)
        for record in accepted}
    failures = {key: value for key, value in failures.items() if value}
    if failures:
        raise ValueError(f"Approved dataset export зогсов: {len(failures)} record validation failure(s). Readiness report-ийг шалгана уу.")
    if not accepted:
        raise ValueError("Export хийх human-verified annotation алга.")
    records = []
    for record in accepted:
        records.append({"candidate_id": record["candidate_id"], "doc_id": record["doc_id"],
            "source_sha256": record["source_sha256"], "snapshot_id": record["snapshot_id"],
            "query_variants": {language: record["question_variants"][language]
                for language in LANGUAGES if record.get("verified_variations", {}).get(language)
                and record.get("question_variants", {}).get(language, "").strip()},
            "query_type": record["query_type"], "evidence_text": record["evidence_text"],
            "expected_answer": record["expected_answer"], "source_reference": record.get("source_reference") or None,
            "source_location": record.get("source_location"), "evidence_matches_original": True,
            "question_evidence_relation_confirmed": True, "human_confirmed": True,
            "reviewer_id": record["reviewer_id"], "reviewed_at": record["reviewed_at"]})
    payload = {"schema_version": 1, "dataset_version": version, "snapshot_id": state["snapshot_id"],
        "source_dataset_sha256": state["dataset_sha256"], "reviewer_confirmed": True,
        "approved_question_count": len(records),
        "valid_query_variation_count": sum(len(record["query_variants"]) for record in records),
        "query_variation_coverage": {language: sum(language in record["query_variants"] for record in records)
            for language in LANGUAGES}, "records": records, "validation_failures": [],
        "representativeness_and_retrieval_readiness": readiness_report(review_root, uploads_root, snapshot_root)}
    payload["dataset_sha256"] = _canonical_hash(payload)
    datasets_root.mkdir(parents=True, exist_ok=True)
    target = datasets_root / f"ground-truth-{version}.json"
    if target.exists():
        raise ValueError("Энэ dataset version аль хэдийн байна; export overwrite хийхгүй.")
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    temporary.replace(target)
    return {"dataset_version": version, "path": str(target), "dataset_sha256": payload["dataset_sha256"],
        "approved_questions": len(records), "valid_query_variations": payload["valid_query_variation_count"],
        "retrieval_evaluation_ready": payload["representativeness_and_retrieval_readiness"]["retrieval_evaluation_ready"],
        "public_report_contains_evidence": False}
