"""Transparent retrieval metrics; human review is stored separately."""
import importlib.metadata
import json
import math
from collections import defaultdict
from statistics import mean
from sqlalchemy import select
from .extensions import db
from .models import Document, DocumentVersion, EvaluationQuestion, EvaluationResult, EvaluationRun, now, uid
from .services.storage import fingerprint, read_json, mutation_lock
from .services.rag import ask, config_snapshot

METRIC_REVISION = "evidence-recall-binary-chunk-v2"


def ranking_metrics(retrieved_units, relevant_units, k, relevant_chunk_count=None, retrieved_ids=None):
    """Evidence recall is deduplicated; precision/DCG use binary chunk relevance.

    nDCG requires the full eligible corpus relevance count, never the gold-unit
    count or the observed result count. Repeated canonical IDs have no gain.
    """
    if not isinstance(k, int) or k <= 0:
        raise ValueError("Metric K must be a positive integer.")
    relevant = set(relevant_units)
    if not relevant:
        return dict(recall=None, precision=None, reciprocal_rank=None, hit_rate=None, ndcg=None)
    seen, first, relevant_results, dcg, seen_ids = set(), None, 0, 0.0, set()
    for rank, units in enumerate(retrieved_units[:k], 1):
        canonical_id = retrieved_ids[rank - 1] if retrieved_ids is not None else rank
        if canonical_id in seen_ids:
            continue
        seen_ids.add(canonical_id)
        found = set(units) & relevant
        if found and first is None:
            first = rank
        if found:
            relevant_results += 1
            dcg += 1 / math.log2(rank + 1)
        seen.update(found)
    if relevant_chunk_count is not None and (not isinstance(relevant_chunk_count, int) or relevant_chunk_count < relevant_results):
        raise ValueError("Full corpus relevant chunk count is inconsistent with retrieved relevance.")
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(relevant_chunk_count or 0, k) + 1))
    return dict(recall=len(seen) / len(relevant), precision=relevant_results / k,
        reciprocal_rank=1 / first if first else 0, hit_rate=float(bool(seen)),
        ndcg=(dcg / ideal if ideal else 0.0) if relevant_chunk_count is not None else None)


def load_questions(file):
    questions = [json.loads(line) for line in file.read_text(encoding="utf8").splitlines() if line.strip()]
    seen, group_splits = set(), {}
    for q in questions:
        if q["id"] in seen or q["language"] not in ("mn", "en") or q["split"] not in ("dev", "test") or not isinstance(q["answerable"], bool):
            raise ValueError("Invalid or duplicate evaluation question.")
        if q.get("input_language", q["language"]) not in ("mn", "en", "mn_cyrl", "mn_latn", "mixed", "unknown"):
            raise ValueError("Invalid evaluation input language category.")
        if q["answerable"] and not q.get("evidence"):
            raise ValueError("Answerable evaluation questions require gold evidence.")
        seen.add(q["id"])
        for field in ("intent_id", "family_id", "intent", "family", "intent_group"):
            group = q.get(field)
            if group is not None:
                identity = (field, str(group))
                if identity in group_splits and group_splits[identity] != q["split"]:
                    raise ValueError("Evaluation intent/family variants must share one split.")
                group_splits[identity] = q["split"]
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
        if not direction.startswith("input:"):
            groups["overall"].append((metrics, timings, status))
    result = {}
    for direction, rows in groups.items():
        group = dict(count=len(rows), errors=sum(status == "error" for _, _, status in rows))
        keys = set().union(*(m.keys() for m, _, _ in rows))
        for key in sorted(keys):
            values = [m[key] for m, _, _ in rows if isinstance(m.get(key), (int, float))]
            group[key] = dict(mean=mean(values), count=len(values)) if values else dict(mean=None, count=0)
        for stage in ("query_embedding_ms", "vector_search_ms", "sparse_search_ms", "query_rewriting_ms", "reranking_ms", "context_ms", "generation_ms", "total_ms"):
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


def corpus_chunk_mapping(versions):
    """Read stored chunks without embeddings or API calls for an exact IDCG."""
    from .services.vectorstore import store
    records = []
    for version in versions:
        data = store().collection(version.collection_name).get(where={"version_id": version.id}, include=["documents", "metadatas"])
        if len(data["ids"]) != version.expected_chunks or len(data["documents"]) != len(data["ids"]) or len(data["metadatas"]) != len(data["ids"]):
            raise ValueError("Eligible corpus chunk mapping is incomplete.")
        if not version.manifest_path or {row["id"] for row in read_json(version.manifest_path)} != set(data["ids"]):
            raise ValueError("Eligible corpus mapping does not match its canonical manifest.")
        for id_, text, metadata in zip(data["ids"], data["documents"], data["metadatas"]):
            if not isinstance(text, str) or not metadata or metadata.get("version_id") != version.id or metadata.get("document_id") != version.document_id:
                raise ValueError("Eligible corpus chunk mapping is invalid.")
            records.append(dict(id=id_, text=text, metadata=metadata))
    if len({r["id"] for r in records}) != len(records):
        raise ValueError("Eligible corpus has duplicate canonical IDs.")
    return records


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
    try:
        corpus_chunks = corpus_chunk_mapping(versions)
        mapping_status = "complete"
    except Exception as exc:
        # Retrieval can still be measured; never invent the unavailable IDCG.
        corpus_chunks, mapping_status = None, "unavailable:" + type(exc).__name__
    document_languages = dict(db.session.execute(select(Document.id, Document.language).where(Document.id.in_([v.document_id for v in versions]))).all())
    languages = {v.id: document_languages[v.document_id] for v in versions}
    corpus = sorted([dict(id=v.id, document_id=v.document_id, sha256=v.sha256, config=v.config_hash, index_config=v.config) for v in versions], key=lambda v: v["id"])
    revision = fingerprint(questions)
    snapshot = dict(corpus=corpus, corpus_hash=fingerprint(corpus), dataset_hash=revision, split=split,
        metric_revision=METRIC_REVISION, relevance_mapping=mapping_status,
        reviewed=all(q.get("review_status") == "reviewed" for q in selected),
        packages={name: importlib.metadata.version(name) for name in ("Flask", "SQLAlchemy", "chromadb", "google-genai", "openai", "tiktoken", "pypdf", "python-docx", "pytesseract")})
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
        log, hits = ask(q["question"], uid(), k=k, language=q.get("evidence_language"), retrieval_only=not generate)
        units = [units_for_hit(h, gold) for h in hits]
        eligible = [h for h in corpus_chunks if not q.get("evidence_language") or languages[h["metadata"]["version_id"]] == q["evidence_language"]] if corpus_chunks is not None else None
        relevant_count = sum(bool(units_for_hit(h, gold)) for h in eligible) if eligible is not None else None
        metrics = {"passage_" + key: value for key, value in ranking_metrics(units, [g["unit_id"] for g in gold], k,
            relevant_chunk_count=relevant_count, retrieved_ids=[h["id"] for h in hits]).items()}
        documents = list(dict.fromkeys(h["metadata"]["document_id"] for h in hits))
        relevant_documents = {g["document_id"] for g in gold if not q.get("evidence_language") or document_languages[g["document_id"]] == q["evidence_language"]}
        metrics.update({"document_" + key: value for key, value in ranking_metrics([{d} for d in documents], relevant_documents, k,
            relevant_chunk_count=len(relevant_documents), retrieved_ids=documents).items()})
        metrics["returned_count"] = len(hits)
        metrics["answerable"] = int(q["answerable"])
        category = q.get("input_language", "mn_cyrl" if q["language"] == "mn" else "en")
        metrics["language_detection_accuracy"] = float(log.config.get("input_language") == category)
        expected_normalized = q.get("expected_normalized_query")
        if expected_normalized:
            metrics["normalization_exact_match"] = float(expected_normalized in log.config.get("query_candidates", []))
        if generate:
            metrics["abstention_match"] = int((log.status == "insufficient_evidence") == (not q["answerable"])) if log.status != "error" else 0
            validation = log.config.get("generation_validation", {})
            metrics["first_response_citation_id_validity"] = validation.get("first_citation_valid")
            metrics["first_response_language_compliance"] = language_metric(validation.get("first_language"))
            metrics["repair_attempted"] = validation.get("repair_attempted")
            metrics["repair_language_compliance"] = language_metric(validation.get("repair_language"))
            metrics["fallback_used"] = bool(validation.get("fallback_reason")) if validation else None
            metrics["answer_language_compliance"] = language_metric(validation.get("delivered_language"))
            metrics["citation_id_validity"] = validation.get("delivered_citation_valid")
        db.session.add(EvaluationResult(run_id=run.id, question_id=q["id"], question_revision=revision, query_log_id=log.id, metrics=metrics))
        db.session.commit()
        category = q.get("input_language", "mn_cyrl" if q["language"] == "mn" else "en")
        records.append((q["direction"], metrics, log.timings, log.status))
        records.append(("input:" + category, metrics, log.timings, log.status))
    run.metrics = aggregate(records)
    run.finished_at = now()
    db.session.commit()
    return run


def compare_runs(first, second):
    # Chunk/model experiments change index IDs; compare the immutable source set instead.
    def sources(run):
        return sorted((v["document_id"], v["sha256"]) for v in run.snapshot["corpus"])
    revision_first, revision_second = first.snapshot.get("metric_revision"), second.snapshot.get("metric_revision")
    if sources(first) != sources(second) or any(first.snapshot.get(k) != second.snapshot.get(k) for k in ("dataset_hash", "split")):
        raise ValueError("Runs do not share the same source corpus, dataset and split.")
    if not revision_first or revision_first != revision_second:
        raise ValueError("Runs require the same explicit metric revision.")
    if first.config.get("top_k") != second.config.get("top_k"):
        raise ValueError("Runs require the same final K.")
    def embedding_space(run):
        return (run.config.get("provider", "gemini"), run.config.get("embedding_model"), run.config.get("dimensions"),
            tuple(sorted({v.get("index_config", {}).get("normalization", "l2-v1") for v in run.snapshot["corpus"]})))
    if embedding_space(first) != embedding_space(second):
        raise ValueError("Runs require the same embedding space.")
    changes = {k: [first.config.get(k), second.config.get(k)] for k in first.config.keys() | second.config.keys() if first.config.get(k) != second.config.get(k)}
    return dict(first=first.id, second=second.id, changed_settings=changes, first_metrics=first.metrics, second_metrics=second.metrics,
        caution="Inspect index configurations too. Adopt changes only after reviewing per-question regressions and human answer scores.")


def language_metric(value):
    return {"pass": 1.0, "fail": 0.0, "uncertain": None}.get(value)


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
            query=dict(id=log.id, status=log.status, answer=log.answer, citations=log.citations, trace=log.trace,
                context_trace=log.config.get('context_trace', []), generation_validation=log.config.get('generation_validation', {}),
                timings=log.timings, usage=log.usage, error=log.error)))
    return dict(id=run.id, snapshot=run.snapshot, config=run.config, metrics=run.metrics, human_summary=human_summary(results), results=rows)
