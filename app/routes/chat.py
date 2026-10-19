from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, session, url_for
from urllib.parse import urlsplit, urlunsplit
from sqlalchemy import select, func
from types import SimpleNamespace
from ..extensions import db
from ..models import Document, DocumentVersion, QueryLog, EvaluationRun, EvaluationResult, EvaluationQuestion, uid, now
from ..services.rag import ask
from ..services.vectorstore import store

bp = Blueprint("chat", __name__)


def safe_return_to(value, fallback):
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


def conversation():
    session.setdefault("conversation", uid())
    return session["conversation"]


def paginated(statement, count_statement):
    """Bound every collection query and preserve its GET navigation context."""
    try:
        per_page = int(request.args.get("per_page", 25))
    except (TypeError, ValueError):
        per_page = 25
    if per_page not in (25, 50, 100):
        per_page = 25
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    total = db.session.scalar(count_statement) or 0
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    def link(target):
        values = {}
        if request.args.get("return_to"):
            values["return_to"] = safe_return_to(request.args["return_to"], url_for("chat.evaluations"))
        values.update(page=target, per_page=per_page)
        return url_for(request.endpoint, **(request.view_args or {}), **values)
    return statement.limit(per_page).offset((page - 1) * per_page), dict(page=page, pages=pages, per_page=per_page, total=total,
        previous=link(page - 1) if page > 1 else None, next=link(page + 1) if page < pages else None,
        current=link(page))


def public_chat_error(log):
    if log.error and "different embedding space" in log.error:
        from ..services.retrieval import incompatible_active_count
        count = incompatible_active_count()
        target = current_app.config["AI_PROVIDER"]
        return (f"{count} идэвхтэй индекс одоогийн {target.upper()} embedding-тэй нийцэхгүй байна. "
            f"Шилжүүлэх жагсаалтыг шалгахын тулд: flask migrate-indexes prepare --target-provider {target} --dry-run")
    messages = dict(
        provider_key_missing='Сонгосон үйлчилгээний API түлхүүр тохируулаагүй байна. Локал .env тохиргоогоо шалгана уу.',
        provider_quota='AI үйлчилгээний квот эсвэл төлбөрийн хязгаарт хүрсэн байна. Хэрэглээ болон төлбөрийн тохиргоогоо шалгана уу.',
        provider_request_failed='AI үйлчилгээ хүсэлтийг гүйцэтгэсэнгүй. Model access, үйлчилгээний төлөв болон тохиргоогоо шалгана уу.',
        request_deadline='Хүсэлт боловсруулах хугацааны хязгаарт хүрлээ. Дахин оролдоно уу.',
        question_too_long='Асуулт embedding-ийн багтаамжаас хэтэрлээ. Асуултаа товчилж дахин илгээнэ үү.',
        documents_changed='Хайлт хийх үед баримтын хувилбар өөрчлөгдсөн байна. Асуултаа дахин илгээнэ үү.')
    return messages.get(log.config.get('error_code'), 'Хариулт боловсруулахад техникийн алдаа гарлаа. Тохиргоо болон дэлгэрэнгүйг шалгана уу.')


@bp.get("/chat")
def chat():
    where = QueryLog.conversation_id == conversation()
    statement, pagination = paginated(select(QueryLog).where(where).order_by(QueryLog.created_at.desc(), QueryLog.id.desc()), select(func.count()).select_from(QueryLog).where(where))
    history = list(reversed(db.session.scalars(statement).all()))
    documents = db.session.scalars(select(Document).where(Document.state == "active", Document.active_version_id.is_not(None)).order_by(Document.title, Document.id)).all()
    from ..services.retrieval import incompatible_active_count
    return render_template("chat.html", history=history, documents=documents, incompatible_count=incompatible_active_count(), pagination=pagination)


@bp.post("/chat/new")
def new():
    session["conversation"] = uid()
    return redirect(url_for("chat.chat"))


@bp.post("/chat/messages")
def message():
    data = request.get_json(silent=True) or request.form
    question = data.get("question", "")
    if not isinstance(question, str):
        raise ValueError("Question must be text.")
    try:
        k = int(data.get("top_k", 5))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid top-k.") from exc
    if k not in (1, 3, 5, 10):
        raise ValueError("Choose top-k 1, 3, 5, or 10.")
    document_id = data.get("document_id") or None
    language = data.get("language") or None
    if language and language not in ("mn", "en", "mixed"):
        raise ValueError("Invalid document language filter.")
    retrieval_only = data.get("retrieval_only") in (True, "on", "true")
    log, hits = ask(question, conversation(), k, [document_id] if document_id else None, language, retrieval_only)
    return_to = safe_return_to(data.get("return_to"), url_for("chat.chat"))
    return jsonify(id=log.id, status=log.status, answer=log.answer, error=public_chat_error(log) if log.status == "error" else None, citations=log.citations,
        timings=log.timings, trace=hits, context_trace=log.config.get("context_trace", []), details_url=url_for("chat.query", query_id=log.id, return_to=return_to)), (502 if log.status == "error" else 200)


@bp.get("/queries/<query_id>")
def query(query_id):
    log = db.get_or_404(QueryLog, query_id)
    if log.conversation_id != conversation():
        abort(404)
    traces, seen = [], set()
    for context_only, source in ((False, log.trace), (True, log.config.get("context_trace", []))):
        for original in source:
            if original["id"] in seen:
                continue
            seen.add(original["id"])
            traces.append(dict(original, context_only=context_only))
    version_ids = {hit["metadata"]["version_id"] for hit in traces}
    versions = {v.id: v for v in db.session.scalars(select(DocumentVersion).where(DocumentVersion.id.in_(version_ids))).all()} if version_ids else {}
    document_ids = {v.document_id for v in versions.values()}
    documents = {d.id: d for d in db.session.scalars(select(Document).where(Document.id.in_(document_ids))).all()} if document_ids else {}
    groups = {}
    for hit in traces:
        version = versions.get(hit["metadata"]["version_id"])
        document = documents.get(version.document_id) if version else None
        hit["text"] = "Эх сурвалж устсан эсвэл индекс олдсонгүй."
        if document and document.state == "active":
            groups.setdefault(version.collection_name, []).append(hit)
    from chromadb.errors import NotFoundError
    for collection_name, collection_hits in groups.items():
        try:
            result = store().collection(collection_name).get(ids=[hit["id"] for hit in collection_hits], include=["documents"])
            texts = dict(zip(result["ids"], result["documents"]))
            for hit in collection_hits:
                hit["text"] = texts.get(hit["id"]) or hit["text"]
        except NotFoundError:
            pass
    return_to = safe_return_to(request.args.get("return_to"), url_for("chat.chat"))
    return render_template("query.html", log=log, traces=traces, return_to=return_to,
        error_message=public_chat_error(log) if log.error else None)


@bp.get("/evaluation")
def evaluations():
    statement, pagination = paginated(select(EvaluationRun).order_by(EvaluationRun.started_at.desc(), EvaluationRun.id.desc()), select(func.count()).select_from(EvaluationRun))
    runs = db.session.scalars(statement).all()
    return render_template("evaluation.html", runs=runs, results=None, is_detail=False, pagination=pagination)


@bp.get("/evaluation/<run_id>")
def evaluation(run_id):
    run = db.get_or_404(EvaluationRun, run_id)
    statement = (select(EvaluationResult, EvaluationQuestion, QueryLog)
        .join(EvaluationQuestion, (EvaluationResult.question_id == EvaluationQuestion.id) & (EvaluationResult.question_revision == EvaluationQuestion.revision))
        .join(QueryLog, EvaluationResult.query_log_id == QueryLog.id).where(EvaluationResult.run_id == run.id).order_by(EvaluationResult.question_id, EvaluationResult.id))
    statement, pagination = paginated(statement, select(func.count()).select_from(EvaluationResult).where(EvaluationResult.run_id == run.id))
    results = db.session.execute(statement).all()
    from ..evaluation import human_summary
    return_to = safe_return_to(request.args.get("return_to"), url_for("chat.evaluations"))
    reviews = db.session.scalars(select(EvaluationResult.review).where(EvaluationResult.run_id == run.id)).all()
    return render_template("evaluation.html", runs=[run], results=results, human=human_summary([SimpleNamespace(review=r) for r in reviews]), return_to=return_to, is_detail=True, pagination=pagination)


@bp.post("/evaluation/results/<result_id>/review")
def review(result_id):
    result = db.get_or_404(EvaluationResult, result_id)
    scores = {}
    for field in ("correctness", "faithfulness", "citation_accuracy", "citation_coverage"):
        raw = request.form.get(field, "")
        if raw not in ("", "0", "1", "2"):
            raise ValueError("Review scores must be 0, 1, 2, or left unreviewed.")
        scores[field] = int(raw) if raw else None
    if not request.form.get("reviewer", "").strip():
        raise ValueError("Enter a reviewer name or local identifier.")
    result.review = dict(scores, reviewer=request.form["reviewer"][:120], diagnosis=request.form.get("diagnosis", "")[:4000], method="human", reviewed_at=now().isoformat())
    db.session.commit()
    target = safe_return_to(request.form.get("return_to"), url_for("chat.evaluation", run_id=result.run_id))
    return redirect(target)
