import json
from types import SimpleNamespace
import pytest
from flask import Flask
from app.services import rag
from app.models import uid
from conftest import FakeGemini


class Answers:
    def __init__(self, *answers):
        self.answers = iter(answers)
        self.calls = 0
    def generate(self, *args):
        self.calls += 1
        value = next(self.answers)
        if isinstance(value, Exception):
            raise value
        return value


def answer(text='Журмын дагуу таван ажлын өдөр байна. [S1]', sources=None):
    return json.dumps(dict(status='answered', answer=text, sources=['S1'] if sources is None else sources), ensure_ascii=False)


def test_invalid_repair_abstains_without_citations():
    client = Answers('broken', 'broken again')
    result, validation = rag._model_answer(client, '{}', 'leave', [{'label':'S1', 'title':'Policy'}])
    assert result['status'] == 'insufficient_evidence' and result['sources'] == []
    assert client.calls == 2
    assert validation['first_citation_valid'] is False
    assert validation['fallback_reason'] == 'invalid_or_failed_repair'
    assert validation['delivered_citation_valid'] is None


@pytest.mark.parametrize('message,expected', [
    ('Set OPENAI_API_KEY in .env', 'provider_key_missing'),
    ('OpenAI API quota or billing limit reached', 'provider_quota'),
    ('Request deadline exceeded', 'request_deadline'),
    ('Embedding input exceeds its limit', 'question_too_long')])
def test_safe_error_codes_and_localized_message(message, expected):
    from app.routes.chat import public_chat_error
    assert rag.error_code(ValueError(message)) == expected
    log = SimpleNamespace(error=expected, config=dict(error_code=expected))
    text = public_chat_error(log)
    assert text and message not in text


def test_english_first_answer_does_not_become_model_language_success():
    client = Answers(answer('The policy says to request leave five days before. байна [S1]'), answer())
    result, validation = rag._model_answer(client, '{}', 'leave', [{'label':'S1', 'title':'Policy'}])
    assert validation['first_language'] == 'fail'
    assert validation['repair_attempted'] and validation['repair_language'] == 'pass'
    assert result['sources'] == ['S1']


def test_repair_cannot_change_protected_numbers():
    client = Answers(answer('Request leave 5 days before байна [S1]'), answer('Журмын дагуу 7 ажлын өдөр байна. [S1]'))
    result, validation = rag._model_answer(client, '{}', 'leave', [{'label':'S1', 'title':'Policy'}])
    assert result['sources'] == []
    assert validation['fallback_reason'] == 'repair_changed_evidence'


def test_model_abstention_is_not_a_citation_success():
    raw = json.dumps(dict(status='insufficient_evidence', answer='Баримтын нотолгоо хангалтгүй байна.', sources=[]), ensure_ascii=False)
    result, validation = rag._model_answer(Answers(raw), '{}', 'leave', [])
    assert result['status'] == 'insufficient_evidence'
    assert validation['first_citation_valid'] is None
    assert validation['delivered_citation_valid'] is None


def hit(id_, text, version='v1', start=0, block=1):
    return dict(id=id_, title='Policy', version_number=1, text=text, metadata=dict(
        version_id=version, document_id='doc', char_start=start, char_end=start+len(text),
        block_start=block, block_end=block, heading='Rule'))


def test_verified_overlap_trims_only_repeated_substring():
    first, second = hit('a', 'abcDEF'), hit('b', 'DEFghi', start=3)
    excerpt, location = rag._context_excerpt(second, [first])
    assert excerpt == 'ghi' and location['context_char_start'] == 6
    assert second['text'] == 'DEFghi'
    assert rag._context_excerpt(hit('c', 'DEFghi', version='another', start=3), [first])[0] == 'DEFghi'
    assert rag._context_excerpt(hit('d', 'DEFghi', start=3, block=2), [first])[0] == 'DEFghi'


def test_context_uses_pool_beyond_duplicate_and_oversized_hits():
    app = Flask(__name__)
    app.config.update(HISTORY_TOKENS=100, CONTEXT_TOKENS=600, INPUT_TOKENS=5000, CONTEXT_CHUNKS=2)
    client = SimpleNamespace(count=lambda text: len(text))
    items = [hit('large', 'x'*2000), hit('a','first rule'), hit('dup','first rule'), hit('b','second rule', version='v2')]
    with app.app_context():
        _, context = rag.build_context('question', items, [], client)
    assert [item['text'] for item in context] == ['first rule', 'second rule']
    assert not items[0].get('selected') and items[3]['selected']


def test_context_exposes_supplied_substring_bounds():
    app = Flask(__name__)
    app.config.update(HISTORY_TOKENS=100, CONTEXT_TOKENS=2000, INPUT_TOKENS=5000, CONTEXT_CHUNKS=2)
    items = [hit('a', 'abcDEF'), hit('b', 'DEFghi', start=3)]
    with app.app_context():
        _, context = rag.build_context('question', items, [], SimpleNamespace(count=len))
    assert context[1]['text'] == 'ghi'
    assert items[1]['context_location']['context_char_start'] == 6
    assert items[1]['metadata']['char_start'] == 3


def test_context_can_cite_extra_hit_without_changing_final_k(app, ready, client):
    from app.services.ingestion import upload, process, approve
    from conftest import docx_file, csrf
    version, _ = upload(docx_file('Another leave policy with a distinct exception.'), 'Another policy', 'en')
    process(version.id)
    approve(version.id)
    process(version.id)
    class CiteSecond(FakeGemini):
        def generate(self, *args):
            return answer('Журмын дагуу таван ажлын өдөр байна. [S2]', ['S2'])
    app.config['GEMINI_FACTORY'] = CiteSecond
    response = client.post('/chat/messages', headers={'X-CSRF-Token':csrf(client)}, json={'question':'leave', 'top_k':1})
    assert response.status_code == 200, response.json
    assert len(response.json['trace']) == 1
    assert len(response.json['context_trace']) == 2
    assert response.json['citations'][0]['label'] == 'S2'
    page = client.get(response.json['details_url'])
    assert page.status_code == 200
    assert 'Нэмэлт context нотолгоо' in page.text


def test_rewrite_failure_preserves_original_and_closes_client(app, ready):
    class RewriteFailure(FakeGemini):
        closed = 0
        def rewrite_query(self, question):
            raise ValueError('quota')
        def close(self):
            RewriteFailure.closed += 1
    app.config.update(QUERY_REWRITING_ENABLED=True, GEMINI_FACTORY=RewriteFailure)
    log, hits = rag.ask('unrecognized', uid())
    assert log.status == 'answered' and hits
    assert log.config['rewrite_outcome'] == 'provider_fallback'
    assert log.config['query_candidates'] == ['unrecognized']
    assert RewriteFailure.closed == 1


def test_provider_quota_has_safe_mongolian_error_in_chat_and_detail(app, ready, client):
    from conftest import csrf
    class Quota(FakeGemini):
        def generate(self, *args):
            raise ValueError('OpenAI API quota or billing limit reached. SECRET_PAYLOAD')
    app.config['GEMINI_FACTORY'] = Quota
    response = client.post('/chat/messages', headers={'X-CSRF-Token':csrf(client)}, json={'question':'leave'})
    assert response.status_code == 502
    assert 'квот' in response.json['error']
    detail = client.get(response.json['details_url'])
    assert 'квот' in detail.text
    assert 'SECRET_PAYLOAD' not in detail.text


def test_client_closed_when_final_log_commit_fails(monkeypatch):
    app = Flask(__name__)
    from app.config import settings
    app.config.update(settings('/unused'))
    record = SimpleNamespace()
    def make_log(**values):
        record.__dict__.update(values, timings={}, trace=[], answer='', citations=[])
        return record
    class Session:
        def add(self, obj):
            pass
        def commit(self):
            if record.timings:
                raise RuntimeError('synthetic persistence failure')
        def rollback(self):
            pass
        def get(self, *args):
            return record
    client = SimpleNamespace(attempts=0, usage={}, closed=False)
    client.close = lambda: setattr(client, 'closed', True)
    monkeypatch.setattr(rag, 'QueryLog', make_log)
    monkeypatch.setattr(rag, 'db', SimpleNamespace(session=Session()))
    import app.services.retrieval as retrieval
    monkeypatch.setattr(retrieval, 'active_versions', lambda *args: [1])
    monkeypatch.setattr(rag, 'provider', lambda **kwargs: client)
    monkeypatch.setattr(rag, 'retrieve', lambda *args, **kwargs: ([], {'candidate_pool':[]}))
    monkeypatch.setattr(rag, 'still_active', lambda *args: True)
    with app.app_context(), pytest.raises(RuntimeError, match='persistence'):
        rag.ask('test', 'conversation', retrieval_only=True)
    assert client.closed
