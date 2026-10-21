"""Loopback-only human review UI for private Split evaluation annotations."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for

from .. import annotation_review as review

bp = Blueprint("annotation_review", __name__, url_prefix="/annotation-review")


def _paths():
    instance = Path(current_app.instance_path)
    root = Path(__file__).resolve().parents[2]
    return (instance / "split-evaluation" / "annotation-review", root / "instance" / "uploads",
            instance / "split-evaluation")


@bp.get("/")
def index():
    review_root, uploads_root, snapshot_root = _paths()
    try:
        state = review.load_state(review_root)
        readiness = review.readiness_report(review_root, uploads_root, snapshot_root)
    except (OSError, ValueError, KeyError) as exc:
        current_app.logger.warning("annotation_review_load_failed type=%s", type(exc).__name__)
        abort(503, "Private review package олдсонгүй эсвэл snapshot identity тохирохгүй байна.")
    items = list(state["candidates"].values())
    candidate_id = request.args.get("candidate", "")
    selected = next((item for item in items if item["candidate_id"] == candidate_id), None)
    if selected is None and items:
        selected = items[0]
    position = next((index for index, item in enumerate(items) if selected and item["candidate_id"] == selected["candidate_id"]), 0)
    return render_template("annotation_review.html", candidate=selected, readiness=readiness,
        total=len(items), position=position, query_types=review.QUERY_TYPES,
        languages=review.LANGUAGES, previous_id=items[position-1]["candidate_id"] if position else None,
        next_id=items[position+1]["candidate_id"] if position+1 < len(items) else None)


@bp.get("/source/<candidate_id>")
def source(candidate_id):
    review_root, uploads_root, _ = _paths()
    try:
        state = review.load_state(review_root)
    except (OSError, ValueError, KeyError):
        abort(404)
    record = state["candidates"].get(candidate_id)
    if not record:
        abort(404)
    valid, _ = review.validate_source_identity(record, uploads_root)
    path = review._source_path(record, uploads_root)
    if not valid or path is None:
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name, conditional=True)


@bp.post("/<candidate_id>")
def update(candidate_id):
    review_root, uploads_root, snapshot_root = _paths()
    try:
        state = review.load_state(review_root)
    except (OSError, ValueError, KeyError):
        abort(503)
    record = state["candidates"].get(candidate_id)
    if not record:
        abort(404)
    old_values = {key: record.get(key) for key in (
        "question_variants", "verified_variations", "evidence_text", "expected_answer", "source_reference",
        "source_location", "source_location_verified", "evidence_matches_original",
        "question_evidence_relation_confirmed", "query_type")}
    old_status = record.get("status", "draft")
    old_human_confirmed = bool(record.get("human_confirmed"))
    variants = {language: request.form.get(f"question_{language}", "").strip() for language in review.LANGUAGES}
    verified_variations = {language: request.form.get(f"verified_{language}") == "yes" for language in review.LANGUAGES}
    location = {field: request.form.get(f"location_{field}", "").strip() or None
        for field in ("page", "section", "clause_id", "document_version")}
    record.update({"question_variants": variants, "verified_variations": verified_variations,
        "evidence_text": request.form.get("evidence_text", "").strip(),
        "expected_answer": request.form.get("expected_answer", "").strip(),
        "source_reference": request.form.get("source_reference", "").strip(),
        "source_location": location,
        "source_location_verified": request.form.get("source_location_verified") == "yes",
        "evidence_matches_original": request.form.get("evidence_matches_original") == "yes",
        "question_evidence_relation_confirmed": request.form.get("question_evidence_relation_confirmed") == "yes",
        "query_type": request.form.get("query_type", "") if request.form.get("query_type") in review.QUERY_TYPES else "",
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    reviewer_id = request.form.get("reviewer_id", "").strip()[:120]
    action = request.form.get("action", "save")
    changed = old_values != {key: record.get(key) for key in old_values}
    record["human_confirmed"] = False
    error_messages = []

    if action == "approve":
        record["reviewer_id"] = reviewer_id or None
        record["human_confirmed"] = request.form.get("human_confirmation") == "yes"
        record["status"] = "corrected" if changed or record["question_variants"] != record["proposed_question_variants"] else "approved"
        record["reviewed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        error_messages = review.validate_approved_record(record, uploads_root, snapshot_root)
        if error_messages:
            record["status"] = "corrected" if changed else "draft"
            record["human_confirmed"] = False
    elif action == "reject":
        record["reviewer_id"] = reviewer_id or None
        record["rejection_reason"] = request.form.get("rejection_reason", "").strip()[:1000]
        if not reviewer_id or not record["rejection_reason"] or request.form.get("human_confirmation") != "yes":
            error_messages = ["Татгалзахад reviewer ID, шалтгаан, ил тод баталгаажуулалт шаардлагатай."]
            record["status"] = "corrected" if changed else "draft"
        else:
            record["status"] = "rejected"
            record["reviewed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    elif action == "skip":
        record["status"] = "skipped"
        record["reviewer_id"] = reviewer_id or record.get("reviewer_id")
    elif action == "save":
        record["reviewer_id"] = reviewer_id or record.get("reviewer_id")
        record["status"] = "corrected" if changed else old_status
        record["human_confirmed"] = False if changed else old_human_confirmed
        if changed:
            record["reviewed_at"] = None
    else:
        error_messages = ["Үйлдэл танигдсангүй."]

    if error_messages:
        flash("Шалгах шаардлагатай: " + "; ".join(error_messages), "error")
    else:
        flash({"approve": "Reviewer confirmation хадгалагдлаа.", "reject": "Candidate татгалзсан төлөвт хадгалагдлаа.",
               "skip": "Дараа шалгахаар хадгаллаа.", "save": "Өөрчлөлт хадгалагдлаа."}.get(action, "Хадгаллаа."), "success")
    try:
        review.save_state(review_root, state)
    except OSError as exc:
        current_app.logger.error("annotation_review_save_failed type=%s", type(exc).__name__)
        abort(500, "Review state хадгалахад алдаа гарлаа.")
    target = request.args.get("next") if action in {"approve", "reject", "skip"} else None
    if target not in state["candidates"]:
        target = candidate_id
    return redirect(url_for("annotation_review.index", candidate=target))
