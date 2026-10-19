"""Offline experiment planning. This module never constructs an AI client."""
from .evaluation import load_questions, METRIC_REVISION
from .services.language import query_candidates
from .services.storage import fingerprint


def benchmark_plan(dataset, config, split='dev', k=5):
    if k not in (1, 3, 5, 10):
        raise ValueError('Choose final K 1, 3, 5, or 10.')
    questions = load_questions(dataset)
    selected = [q for q in questions if q['split'] == split]
    if not selected:
        raise ValueError('No questions in the requested split.')
    reviewed = all(q.get('review_status') == 'reviewed' for q in selected)
    conditional = sum(query_candidates(q['question'])['language'] in ('mixed', 'unknown') for q in selected)
    modes = []
    for name, retrieval, rerank, rewrite in (
            ('dense', 'dense', False, False), ('hybrid', 'hybrid', False, False),
            ('hybrid_reranker', 'hybrid', True, False), ('hybrid_reranker_rewrite', 'hybrid', True, True)):
        pending = []
        if not reviewed:
            pending.append('reviewed_gold_labels')
        # A configured path is not evidence of model usability or Mongolian quality.
        if rerank:
            pending.append('verified_local_reranker')
        modes.append(dict(name=name, retrieval_mode=retrieval, reranking_enabled=rerank,
            query_rewriting_enabled=rewrite, top_k=k, pending=pending,
            estimated_embedding_requests=len(selected),
            estimated_rewrite_requests=conditional if rewrite else 0,
            generation_requests=0))
    return dict(dry_run=True, live_request_budget=0, model_download_budget=0,
        dataset_hash=fingerprint(questions), metric_revision=METRIC_REVISION, split=split,
        questions=len(selected), reviewed=reviewed, final_k=k, modes=modes,
        reranker_path_configured=bool(config.get('RERANKER_MODEL_PATH')),
        requirements=['same immutable corpus and embedding space', 'complete dense and sparse indexes',
            'same final K and context limits', 'no degraded runs counted as successful optional modes',
            'separate explicit approval for billable execution', 'held-out per-intent and per-language review'],
        estimate_method='one embedding batch per question; conditional rewrite at most once; excludes failures/retries',
        quality_acceptance='pending reviewed corpus, local model and authorized live evaluation')
