from urllib.parse import urlsplit, urlunsplit

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import aliased
from ..extensions import db
from ..models import Document, DocumentVersion
from ..services import ingestion
from ..services.storage import path, read_json
from ..services import preview as document_preview

bp = Blueprint("documents", __name__)


STATUS_ORDER = ("review", "failed", "indexing", "parsing", "pending", "ready", "deleting", "deleted", "no_version")
STATUS_LABELS = {
    "review": "Шалгах шаардлагатай", "failed": "Алдаатай", "indexing": "Индексжүүлж байна",
    "parsing": "Боловсруулж байна", "pending": "Хүлээгдэж байна", "ready": "Бэлэн",
    "deleting": "Устгаж байна", "deleted": "Устгасан", "no_version": "Хувилбаргүй",
}


def safe_return_to(value, fallback):
    """Accept only a local GET route; prevent open redirects and malformed paths."""
    if not value or not isinstance(value, str) or any(ord(ch) < 32 for ch in value) or "\\" in value:
        return fallback
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return fallback
    try:
        endpoint, _ = request.url_rule.map.bind("localhost").match(parsed.path, method="GET")
    except Exception:
        return fallback
    if endpoint not in {"documents.documents", "chat.chat", "chat.query", "chat.evaluations", "chat.evaluation"}:
        return fallback
    return urlunsplit(("", "", parsed.path, parsed.query, parsed.fragment))


def _like(value):
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@bp.get("/")
def home():
    return redirect(url_for("chat.chat"))


@bp.get("/documents")
def documents():
    q = request.args.get("q", "").strip()[:120]
    status = request.args.get("status", "").strip()
    language = request.args.get("language", "").strip()
    file_type = request.args.get("type", "").strip().lower()
    sort = request.args.get("sort", "newest")
    group = request.args.get("group", "status")
    try:
        page = max(1, int(request.args.get("page", "1")))
        per_page = int(request.args.get("per_page", "25"))
    except ValueError:
        page, per_page = 1, 25
    if per_page not in (25, 50, 100):
        per_page = 25
    if status not in (*STATUS_ORDER, ""):
        status = ""
    if language not in ("mn", "en", "mixed", ""):
        language = ""
    if file_type not in ("pdf", "docx", "doc", ""):
        file_type = ""
    if sort not in ("newest", "oldest", "title_asc", "title_desc"):
        sort = "newest"
    if group not in ("status", "language", "type", "none"):
        group = "status"

    latest_id = (select(DocumentVersion.id).where(DocumentVersion.document_id == Document.id)
                 .order_by(DocumentVersion.version_number.desc(), DocumentVersion.created_at.desc()).limit(1)
                 .correlate(Document).scalar_subquery())
    latest = aliased(DocumentVersion)
    effective_status = case((Document.state == "deleted", "deleted"), (Document.state == "deleting", "deleting"),
                            (latest.id.is_(None), "no_version"), else_=latest.state)
    effective_type = func.lower(latest.media_type)
    base = select(Document, latest).outerjoin(latest, latest.id == latest_id)
    conditions = []
    if status == "deleted":
        conditions.append(or_(Document.state == "deleted", latest.state == "deleted"))
    else:
        conditions.append(Document.state != "deleted")
        conditions.append(or_(latest.state != "deleted", latest.id.is_(None)))
    if q:
        like = _like(q)
        conditions.append(or_(Document.title.ilike(like, escape="\\"), select(DocumentVersion.id).where(
            DocumentVersion.document_id == Document.id, DocumentVersion.original_filename.ilike(like, escape="\\")).exists()))
    if language:
        conditions.append(Document.language == language)
    if file_type:
        conditions.append(effective_type.like(f"%.{file_type}") | (effective_type == file_type))
    if status:
        conditions.append(effective_status == status)
    filtered = base.where(*conditions)

    # Common status chips respect search/language/type, while remaining usable when one status is selected.
    # Rebuild without the status predicate (SQLAlchemy expressions do not have identity semantics).
    count_conditions = [Document.state != "deleted", or_(latest.state != "deleted", latest.id.is_(None))] if status != "deleted" else [or_(Document.state == "deleted", latest.state == "deleted")]
    if q:
        like = _like(q)
        count_conditions.append(or_(Document.title.ilike(like, escape="\\"), select(DocumentVersion.id).where(
            DocumentVersion.document_id == Document.id, DocumentVersion.original_filename.ilike(like, escape="\\")).exists()))
    if language:
        count_conditions.append(Document.language == language)
    if file_type:
        count_conditions.append(effective_type.like(f"%.{file_type}") | (effective_type == file_type))
    status_counts = dict(db.session.execute(select(effective_status, func.count()).select_from(Document)
        .outerjoin(latest, latest.id == latest_id).where(*count_conditions).group_by(effective_status)).all())

    group_expr = {"status": effective_status, "language": Document.language, "type": effective_type}.get(group)
    sort_expr = {
        "newest": latest.created_at.desc().nullslast(), "oldest": latest.created_at.asc().nullsfirst(),
        "title_asc": Document.title.asc(), "title_desc": Document.title.desc(),
    }[sort]
    ordering = []
    if group_expr is not None:
        if group == "status":
            ordering.append(case(*[(effective_status == value, index) for index, value in enumerate(STATUS_ORDER)], else_=99))
        else:
            ordering.append(group_expr.asc().nullslast())
    ordering.extend((sort_expr, Document.id.asc()))
    total = db.session.scalar(select(func.count()).select_from(filtered.subquery())) or 0
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    group_counts = {}
    if group_expr is not None:
        group_counts = dict(db.session.execute(select(group_expr, func.count()).select_from(Document)
            .outerjoin(latest, latest.id == latest_id).where(*conditions).group_by(group_expr)).all())
    rows = db.session.execute(filtered.order_by(*ordering).offset((page - 1) * per_page).limit(per_page)).all()
    page_ids = [doc.id for doc, _ in rows]
    histories = {}
    if page_ids:
        for version in db.session.scalars(select(DocumentVersion).where(DocumentVersion.document_id.in_(page_ids))
                .order_by(DocumentVersion.document_id, DocumentVersion.version_number.desc())):
            histories.setdefault(version.document_id, []).append(version)
    items = []
    for doc, newest in rows:
        if group == "status":
            key = "deleted" if doc.state == "deleted" else "deleting" if doc.state == "deleting" else (newest.state if newest else "no_version")
            label = STATUS_LABELS.get(key, key)
        elif group == "language":
            key, label = doc.language or "unknown", {"mn": "Монгол", "en": "Англи", "mixed": "Холимог"}.get(doc.language, "Тодорхойгүй")
        elif group == "type":
            key, label = (newest.media_type if newest else "unknown"), (newest.media_type.upper() if newest else "Төрөлгүй")
        else:
            key, label = "all", "Баримтууд"
        items.append({"document": doc, "latest": newest, "versions": histories.get(doc.id, []), "group_key": key, "group_label": label,
                      "active_version": doc.active_version_id})
    groups = []
    for item in items:
        if not groups or groups[-1]["key"] != item["group_key"]:
            groups.append({"key": item["group_key"], "label": item["group_label"],
                           "count": group_counts.get(item["group_key"], 0), "items": []})
        groups[-1]["items"].append(item)
    query_params = request.args.to_dict(flat=True)
    return_to = request.full_path[:-1] if request.full_path.endswith("?") else request.full_path
    return render_template("documents.html", groups=groups, total=total, page=page, per_page=per_page,
        pages=pages, q=q, status=status, language=language, file_type=file_type,
        sort=sort, group=group, query_params=query_params, status_counts=status_counts,
        status_labels=STATUS_LABELS, return_to=return_to)


@bp.post("/documents")
@bp.post("/documents/<document_id>/versions")
def upload(document_id=None):
    file = request.files.get("file")
    if not file:
        raise ValueError("Choose a document file.")
    version, duplicate = ingestion.upload(file, request.form.get("title", ""), request.form.get("language", "mixed"), document_id)
    flash("Ижил эх баримт, тохиргоотой хувилбар өмнө нь бүртгэгдсэн байна." if duplicate else "Файл хадгалагдлаа. Танилтыг шалгаж индекс үүсгэнэ үү.")
    target = safe_return_to(request.form.get("return_to"), url_for("documents.documents"))
    return redirect(url_for("documents.version", version_id=version.id, return_to=target))


@bp.get("/versions/<version_id>")
def version(version_id):
    version = db.get_or_404(DocumentVersion, version_id)
    document = db.get_or_404(Document, version.document_id)
    artifact = read_json(version.extraction_path) if version.extraction_path and path(version.extraction_path).exists() else None
    manifest = read_json(version.manifest_path) if version.manifest_path and path(version.manifest_path).exists() else []
    return_to = safe_return_to(request.args.get("return_to"), url_for("documents.documents"))
    return render_template("version.html", version=version, document=document, artifact=artifact, manifest=manifest,
        return_to=return_to, preview_available=bool(document.state == "active" and artifact and version.state != "deleted"))


@bp.post("/versions/<version_id>/preview/prepare")
def prepare_preview(version_id):
    version = db.get_or_404(DocumentVersion, version_id)
    document = db.get_or_404(Document, version.document_id)
    if document.state != "active":
        abort(410)
    try:
        return jsonify(document_preview.prepare(version))
    except ValueError as error:
        # These messages are deliberately safe, actionable preview diagnostics;
        # the global JSON handler otherwise masks every preview failure as a
        # form-validation error, making a blank viewer impossible to diagnose.
        current_app.logger.info("Preview preparation rejected for version %s: %s", version.id, error)
        return jsonify(error=str(error)), 422
    except Exception:
        current_app.logger.exception("Unexpected preview preparation failure for version %s", version.id)
        return jsonify(error="Preview бэлтгэх үед серверийн алдаа гарлаа. Серверийн log-ийг шалгана уу."), 500


@bp.get("/versions/<version_id>/preview")
def preview_pdf(version_id):
    version = db.get_or_404(DocumentVersion, version_id)
    document = db.get_or_404(Document, version.document_id)
    if document.state != "active":
        abort(410)
    try:
        prepared = document_preview.current_paths(version)
    except (OSError, ValueError):
        prepared = None
    if not prepared:
        abort(404)
    response = send_file(path(prepared[0]), mimetype="application/pdf", as_attachment=False,
        download_name=f"preview-{version.id}.pdf", conditional=True, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'self'; sandbox"
    return response


@bp.get("/versions/<version_id>/preview/map")
def preview_map(version_id):
    version = db.get_or_404(DocumentVersion, version_id)
    document = db.get_or_404(Document, version.document_id)
    if document.state != "active":
        abort(410)
    try:
        page_number = int(request.args.get("page", "1"))
        return jsonify(document_preview.page_map(version, page_number))
    except (FileNotFoundError, OSError, ValueError, KeyError, TypeError):
        abort(404)


@bp.post("/versions/<version_id>/preview/ocr")
def preview_ocr(version_id):
    version = db.get_or_404(DocumentVersion, version_id)
    document = db.get_or_404(Document, version.document_id)
    if document.state != "active":
        abort(410)
    body = request.get_json(silent=True) or {}
    try:
        page_number = int(body.get("page", 0))
    except (TypeError, ValueError) as error:
        raise ValueError("Хуудасны дугаар буруу байна.") from error
    proposal = document_preview.make_ocr_proposal(version, page_number, body.get("rect"), body.get("region_id"))
    return jsonify(proposal), 201


@bp.post("/versions/<version_id>/preview/apply")
def apply_preview_ocr(version_id):
    version = db.get_or_404(DocumentVersion, version_id)
    document = db.get_or_404(Document, version.document_id)
    if document.state != "active":
        abort(410)
    body = request.get_json(silent=True) or {}
    new_version = document_preview.apply_proposals(version, body.get("proposal_ids"))
    target = url_for("documents.version", version_id=new_version.id,
        return_to=safe_return_to(body.get("return_to"), url_for("documents.documents")))
    return jsonify(version_id=new_version.id, state=new_version.state, url=target), 201


@bp.get("/versions/<version_id>/source")
def source(version_id):
    version = db.get_or_404(DocumentVersion, version_id)
    document = db.get_or_404(Document, version.document_id)
    if document.state != "active" or not path(version.file_path).exists():
        abort(410)
    return send_file(path(version.file_path), as_attachment=version.media_type != "pdf", download_name=version.original_filename)


@bp.post("/versions/<version_id>/process")
def process(version_id):
    version = ingestion.process(version_id)
    flash("Танилтыг шалгах шаардлагатай." if version.state == "review" else "Индексийг шалгаж, хувилбарыг идэвхжүүллээ.")
    target = safe_return_to(request.form.get("return_to"), url_for("documents.documents"))
    return redirect(url_for("documents.version", version_id=version.id, return_to=target))


@bp.post("/versions/<version_id>/approve")
def approve(version_id):
    if request.form.get("reviewed") != "yes":
        raise ValueError("Compare extraction against the original and confirm review first.")
    choices = {key[5:]: value for key, value in request.form.items() if key.startswith("page_")}
    ingestion.approve(version_id, choices)
    flash("Шалгалт хадгалагдлаа. Embedding үүсгэхийн тулд энэ хувилбарыг боловсруулна уу.")
    target = safe_return_to(request.form.get("return_to"), url_for("documents.documents"))
    return redirect(url_for("documents.version", version_id=version_id, return_to=target))


@bp.post("/documents/<document_id>/reindex")
def reindex(document_id):
    try:
        pages = [int(p.strip()) for p in request.form.get("ocr_pages", "").split(",") if p.strip()]
    except ValueError as exc:
        raise ValueError("OCR overrides must be comma-separated page numbers.") from exc
    version = ingestion.reindex(document_id, pages)
    target = safe_return_to(request.form.get("return_to"), url_for("documents.documents"))
    return redirect(url_for("documents.version", version_id=version.id, return_to=target))


@bp.post("/documents/<document_id>/delete")
def delete(document_id):
    ingestion.delete_document(document_id)
    flash("Баримтын индекс болон ашиглагдаагүй файлыг устгалаа. Түүхэн бүртгэл хадгалагдлаа.")
    return redirect(safe_return_to(request.form.get("return_to"), url_for("documents.documents")))
