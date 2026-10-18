"""Transparent retrieval metrics; human review is stored separately."""
import importlib.metadata
import json
from collections import defaultdict
from statistics import mean
from sqlalchemy import select
from .extensions import db
from .models import Document, DocumentVersion, EvaluationQuestion, EvaluationResult, EvaluationRun, now, uid
from .services.storage import fingerprint, read_json, mutation_lock
from .services.rag import ask, config_snapshot


def ranking_metrics(retrieved_units, relevant_units, k):
    """A gold unit counts once even if several overlapping chunks contain it."""
    relevant = set(relevant_units)
    if not relevant:
        return dict(recall=None, precision=None, reciprocal_rank=None)
    seen, first, relevant_results = set(), None, 0
    for rank, units in enumerate(retrieved_units[:k], 1):
        found = set(units) & relevant
        if found and first is None:
            first = rank
        if found - seen:
            relevant_results += 1
        seen.update(found)
    return dict(recall=len(seen) / len(relevant), precision=relevant_results / k, reciprocal_rank=1 / first if first else 0)


def load_questions(file):
    questions = [json.loads(line) for line in file.read_text(encoding="utf8").splitlines() if line.strip()]
    seen = set()
    for q in questions:
        if q["id"] in seen or q["language"] not in ("mn", "en") or q["split"] not in ("dev", "test") or not isinstance(q["answerable"], bool):
            raise ValueError("Invalid or duplicate evaluation question.")
        if q["answerable"] and not q.get("evidence"):
            raise ValueError("Answerable evaluation questions require gold evidence.")
        seen.add(q["id"])
    return questions


def resolve_gold(question, versions):
    resolved = []
    for evidence in question.get("evidence", []):
        candidates = [v for v in versions if (evidence.get("version_id") == v.id if evidence.get("version_id") else evidence.get("sha256") == v.sha256)]
        if len(candidates) != 1:
            raise ValueError(f"Gold evidence for {question['id']} is missing or ambiguous in the active corpus.")
        v = candidates[0]
        resolved.append(dict(evidence, version_id=v.id, document_id=v.document_id))
    return resolved


def units_for_hit(hit, gold):
    metadata = hit["metadata"]
    units = set()
    for evidence in gold:
        if metadata["version_id"] != evidence["version_id"]:
            continue
        if evidence.get("page") and not metadata.get("page_start", 0) <= evidence["page"] <= metadata.get("page_end", 0):
            continue
        if evidence.get("block") and not metadata["block_start"] <= evidence["block"] <= metadata["block_end"]:
            continue
        # A block overlap alone is insufficient for large blocks: require the labeled excerpt.
        if evidence.get("excerpt") and evidence["excerpt"].casefold() not in hit["text"].casefold():
            continue
        units.add(evidence["unit_id"])
    return units


def aggregate(records):
    groups = defaultdict(list)
    for direction, metrics, timings, status in records:
        groups[direction].append((metrics, timings, status))
        groups["overall"].append((metrics, timings, status))
    result = {}
    for direction, rows in groups.items():
        group = dict(count=len(rows), errors=sum(status == "error" for _, _, status in rows))
        keys = set().union(*(m.keys() for m, _, _ in rows))
        for key in sorted(keys):
            values = [m[key] for m, _, _ in rows if isinstance(m.get(key), (int, float))]
            group[key] = dict(mean=mean(values), count=len(values)) if values else dict(mean=None, count=0)
        for stage in ("query_embedding_ms", "vector_search_ms", "generation_ms", "total_ms"):
            values = sorted(t.get(stage, 0) for _, t, _ in rows)
            group[stage] = dict(p50=percentile(values, .5), p95=percentile(values, .95))
        result[direction] = group
    return result


def percentile(values, fraction):
    if not values:
        return None
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def run_evaluation(file, split="dev", generate=False, k=5, allow_unreviewed=False):
    # Freeze document mutations for the entire run, not merely its snapshot.
    with mutation_lock():
        return _run_evaluation(file, split, generate, k, allow_unreviewed)


def _run_evaluation(file, split, generate, k, allow_unreviewed):
    questions = load_questions(file)
    selected = [q for q in questions if q["split"] == split]
    if not selected:
        raise ValueError("No questions in the requested split.")
    if not allow_unreviewed and any(q.get("review_status") != "reviewed" for q in selected):
        raise ValueError("Dataset needs bilingual human review. Use --allow-unreviewed only for engineering smoke tests.")
    versions = db.session.scalars(select(DocumentVersion).join(Document, Document.active_version_id == DocumentVersion.id)
        .where(Document.state == "active", DocumentVersion.state == "ready")).all()
    golds = {q["id"]: resolve_gold(q, versions) for q in selected}
    corpus = sorted([dict(id=v.id, document_id=v.document_id, sha256=v.sha256, config=v.config_hash, index_config=v.config) for v in versions], key=lambda v: v["id"])
    revision = fingerprint(questions)
    snapshot = dict(corpus=corpus, corpus_hash=fingerprint(corpus), dataset_hash=revision, split=split,
        reviewed=all(q.get("review_status") == "reviewed" for q in selected),
        packages={name: importlib.metadata.version(name) for name in ("Flask", "SQLAlchemy", "chromadb", "google-genai", "pypdf", "python-docx", "pytesseract")})
    run = EvaluationRun(id=uid(), snapshot=snapshot, config=dict(config_snapshot(k), generate=generate))
    db.session.add(run)
    for q in selected:
        if not db.session.get(EvaluationQuestion, (q["id"], revision)):
            db.session.add(EvaluationQuestion(id=q["id"], revision=revision, data=q))
    db.session.commit()
    records = []
    for q in selected:
        gold = golds[q["id"]]
        # Independent conversations prevent previous gold questions influencing answers.
        log, hits = ask(q["question"], uid(), k=k, language=q["evidence_language"], retrieval_only=not generate)
        units = [units_for_hit(h, gold) for h in hits]
        metrics = {"passage_" + key: value for key, value in ranking_metrics(units, [g["unit_id"] for g in gold], k).items()}
        documents = list(dict.fromkeys(h["metadata"]["document_id"] for h in hits))
        metrics.update({"document_" + key: value for key, value in ranking_metrics([{d} for d in documents], {g["document_id"] for g in gold}, k).items()})
        metrics["returned_count"] = len(hits)
        metrics["answerable"] = int(q["answerable"])
        if generate:
            metrics["abstention_match"] = int((log.status == "insufficient_evidence") == (not q["answerable"])) if log.status != "error" else 0
            metrics["citation_id_validity"] = 0 if log.status == "error" else 1
        db.session.add(EvaluationResult(run_id=run.id, question_id=q["id"], question_revision=revision, query_log_id=log.id, metrics=metrics))
        db.session.commit()
        records.append((q["direction"], metrics, log.timings, log.status))
    run.metrics = aggregate(records)
    run.finished_at = now()
    db.session.commit()
    return run


def compare_runs(first, second):
    # Chunk/model experiments change index IDs; compare the immutable source set instead.
    def sources(run):
        return sorted((v["document_id"], v["sha256"]) for v in run.snapshot["corpus"])
    if sources(first) != sources(second) or any(first.snapshot.get(k) != second.snapshot.get(k) for k in ("dataset_hash", "split")):
        raise ValueError("Runs do not share the same source corpus, dataset and split.")
    changes = {k: [first.config.get(k), second.config.get(k)] for k in first.config.keys() | second.config.keys() if first.config.get(k) != second.config.get(k)}
    return dict(first=first.id, second=second.id, changed_settings=changes, first_metrics=first.metrics, second_metrics=second.metrics,
        caution="Inspect index configurations too. Adopt changes only after reviewing per-question regressions and human answer scores.")


def human_summary(results):
    summary = dict(total=len(results))
    for field in ("correctness", "faithfulness", "citation_accuracy", "citation_coverage"):
        scores = [r.review[field] for r in results if r.review.get(field) is not None]
        summary[field] = dict(reviewed=len(scores), fully_correct=sum(s == 2 for s in scores),
            fully_correct_fraction=sum(s == 2 for s in scores) / len(scores) if scores else None,
            mean=mean(scores) if scores else None)
    return summary


def export_run(run):
    from .models import QueryLog
    results = db.session.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run.id)).all()
    rows = []
    for result in results:
        question = db.session.get(EvaluationQuestion, (result.question_id, result.question_revision))
        log = db.session.get(QueryLog, result.query_log_id)
        rows.append(dict(question=question.data, metrics=result.metrics, human_review=result.review,
            query=dict(id=log.id, status=log.status, answer=log.answer, citations=log.citations, trace=log.trace, timings=log.timings, usage=log.usage, error=log.error)))
    return dict(id=run.id, snapshot=run.snapshot, config=run.config, metrics=run.metrics, human_summary=human_summary(results), results=rows)
