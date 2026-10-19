from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, session, url_for
from urllib.parse import urlsplit, urlunsplit
from sqlalchemy import select
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


def public_chat_error(log):
    if log.error and "different embedding space" in log.error:
        from ..services.retrieval import incompatible_active_count
        count = incompatible_active_count()
        target = current_app.config["AI_PROVIDER"]
        return (f"{count} идэвхтэй индекс одоогийн {target.upper()} embedding-тэй нийцэхгүй байна. "
            f"Шилжүүлэх жагсаалтыг шалгахын тулд: flask migrate-indexes prepare --target-provider {target} --dry-run")
    return "Хариулт боловсруулахад техникийн алдаа гарлаа. Тохиргоо болон дэлгэрэнгүйг шалгана уу."


@bp.get("/chat")
def chat():
    history = db.session.scalars(select(QueryLog).where(QueryLog.conversation_id == conversation()).order_by(QueryLog.created_at)).all()
    documents = db.session.scalars(select(Document).where(Document.state == "active", Document.active_version_id.is_not(None))).all()
    from ..services.retrieval import incompatible_active_count
    return render_template("chat.html", history=history, documents=documents, incompatible_count=incompatible_active_count())


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
    return jsonify(id=log.id, status=log.status, answer=log.answer, error=public_chat_error(log) if log.status == "error" else None, citations=log.citations,
        timings=log.timings, trace=hits, details_url=url_for("chat.query", query_id=log.id, return_to=url_for("chat.chat"))), (502 if log.status == "error" else 200)


@bp.get("/queries/<query_id>")
def query(query_id):
    log = db.get_or_404(QueryLog, query_id)
    if log.conversation_id != conversation():
        abort(404)
    traces = []
    for hit in log.trace:
        hit = dict(hit)
        version = db.session.get(DocumentVersion, hit["metadata"]["version_id"])
        document = db.session.get(Document, version.document_id) if version else None
        hit["text"] = "Эх сурвалж устсан эсвэл индекс олдсонгүй."
        if document and document.state == "active":
            from chromadb.errors import NotFoundError
            try:
                result = store().collection(version.collection_name).get(ids=[hit["id"]], include=["documents"])
                if result["documents"]:
                    hit["text"] = result["documents"][0]
            except NotFoundError:
                pass
        traces.append(hit)
    return_to = safe_return_to(request.args.get("return_to"), url_for("chat.chat"))
    return render_template("query.html", log=log, traces=traces, return_to=return_to)


@bp.get("/evaluation")
def evaluations():
    runs = db.session.scalars(select(EvaluationRun).order_by(EvaluationRun.started_at.desc())).all()
    return render_template("evaluation.html", runs=runs, results=None, is_detail=False)


@bp.get("/evaluation/<run_id>")
def evaluation(run_id):
    run = db.get_or_404(EvaluationRun, run_id)
    results = db.session.execute(select(EvaluationResult, EvaluationQuestion, QueryLog)
        .join(EvaluationQuestion, (EvaluationResult.question_id == EvaluationQuestion.id) & (EvaluationResult.question_revision == EvaluationQuestion.revision))
        .join(QueryLog, EvaluationResult.query_log_id == QueryLog.id).where(EvaluationResult.run_id == run.id)).all()
    from ..evaluation import human_summary
    return_to = safe_return_to(request.args.get("return_to"), url_for("chat.evaluations"))
    return render_template("evaluation.html", runs=[run], results=results, human=human_summary([r for r, q, log in results]), return_to=return_to, is_detail=True)


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
