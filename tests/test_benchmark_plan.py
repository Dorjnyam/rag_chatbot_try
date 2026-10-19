import json
from app.benchmarking import benchmark_plan


def test_benchmark_plan_is_zero_budget_and_records_pending_requirements(tmp_path):
    dataset = tmp_path / 'draft.jsonl'
    dataset.write_text(json.dumps(dict(id='q', language='en', split='dev', answerable=False,
        question='unrecognized', review_status='draft', evidence=[])), encoding='utf8')
    result = benchmark_plan(dataset, {})
    assert result['dry_run'] and result['live_request_budget'] == 0
    assert result['model_download_budget'] == 0 and result['metric_revision']
    assert [mode['retrieval_mode'] for mode in result['modes']] == ['dense', 'hybrid', 'hybrid', 'hybrid']
    assert all(mode['top_k'] == 5 and mode['generation_requests'] == 0 for mode in result['modes'])
    assert result['modes'][-1]['estimated_rewrite_requests'] == 1
    assert 'reviewed_gold_labels' in result['modes'][0]['pending']
    assert 'verified_local_reranker' in result['modes'][-1]['pending']
    configured = benchmark_plan(dataset, {'RERANKER_MODEL_PATH':'not-a-verified-model'})
    assert configured['reranker_path_configured']
    assert 'verified_local_reranker' in configured['modes'][-1]['pending']


def test_benchmark_cli_needs_no_database_or_provider(tmp_path):
    from flask import Flask
    from app.cli import register
    dataset = tmp_path / 'draft.jsonl'
    dataset.write_text(json.dumps(dict(id='q', language='en', split='dev', answerable=False,
        question='unrecognized', evidence=[])), encoding='utf8')
    app = Flask(__name__)
    # No SQLAlchemy extension, database URI or AI factory is initialized.
    register(app)
    result = app.test_cli_runner().invoke(args=['benchmark-plan', str(dataset)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['live_request_budget'] == 0
