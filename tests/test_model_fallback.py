# -*- coding: utf-8 -*-
"""予備モデルへの切り替えのテスト。

- 提供終了した gemini-2.0 系を予備の一覧から外したこと
- 「モデルが見つからない（404）」でも、枠切れ・過負荷と同じく次の予備へ進むこと
  （以前は 404 で打ち切っていたので、一覧に提供終了したモデルが残っているとそこで止まっていた）
- 入力の誤り（400）など、次のモデルでも同じ結果になりやすいエラーでは打ち切ること
"""
import json
from types import SimpleNamespace

import pytest
from google.genai import errors

import core
import gap_analyzer
import llm


def _gemini_error(code, status, message='error'):
    """本物の google-genai が投げるのと同じ形の例外"""
    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {'error': {'code': code, 'message': message, 'status': status}})


NOT_FOUND = lambda: _gemini_error(404, 'NOT_FOUND', 'models/gemini-2.0-flash is not found for API version v1beta')
QUOTA     = lambda: _gemini_error(429, 'RESOURCE_EXHAUSTED', 'quota exceeded')
OVERLOAD  = lambda: _gemini_error(503, 'UNAVAILABLE', 'The model is overloaded')
INVALID   = lambda: _gemini_error(400, 'INVALID_ARGUMENT', 'Invalid argument')


class _GroqLikeError(Exception):
    """Groq（OpenAI互換ライブラリ）の例外は status_code を持つ"""
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


# =====================================================
# モデルの一覧・エラーの分け方
# =====================================================
def test_retired_gemini_2_0_models_are_not_used():
    assert not any(m.startswith('gemini-2.0') for m in core.GEMINI_MODELS)
    assert core.GEMINI_MODELS[0] == 'gemini-2.5-flash'
    assert not any(m.startswith('gemini-2.0') for m in core.ALL_MODELS)


@pytest.mark.parametrize('make_error, expected', [
    (NOT_FOUND, llm.ERROR_NOT_FOUND),
    (QUOTA, llm.ERROR_QUOTA),
    (OVERLOAD, llm.ERROR_OVERLOADED),
    (INVALID, llm.ERROR_OTHER),
    (lambda: _GroqLikeError(404, 'The model `llama-x` does not exist'), llm.ERROR_NOT_FOUND),
    (lambda: _GroqLikeError(429, 'Rate limit reached'), llm.ERROR_QUOTA),
    (lambda: Exception('model is no longer available'), llm.ERROR_NOT_FOUND),
    (lambda: Exception('connection reset'), llm.ERROR_OTHER),
])
def test_classify_error(make_error, expected):
    assert llm.classify_error(make_error()) == expected


def test_only_input_errors_stop_the_fallback():
    assert llm.RETRYABLE_ON_NEXT_MODEL == {llm.ERROR_QUOTA, llm.ERROR_OVERLOADED, llm.ERROR_NOT_FOUND}


# =====================================================
# 会話の開始・会話中・ヒント（偽のAIで、モデルごとに失敗の仕方を変える）
# =====================================================
class _ScriptedChat:
    """モデルごとに決めた動き（成功 or 例外）をする偽のチャット"""
    def __init__(self, model, behavior, history=None):
        self.model = model
        self.behavior = behavior
        self.history = list(history or [])

    def send_message(self, text, image=None):
        action = self.behavior.get(self.model, 'ok')
        if action != 'ok':
            raise action()
        self.history.append({'role': 'user', 'message': text})
        reply = f'{self.model}の返答'
        self.history.append({'role': 'model', 'message': reply})
        return SimpleNamespace(text=reply)

    def get_history(self):
        return list(self.history)


@pytest.fixture
def scripted(monkeypatch):
    """予備の一覧を m1 → m2 → m3 にして、モデルごとの動きを behavior で決められるようにする"""
    behavior = {}
    calls = []

    def create_chat(gemini_client, groq_client, model, instruction, history=None):
        calls.append(model)
        return _ScriptedChat(model, behavior, history)

    monkeypatch.setattr(llm, 'create_chat', create_chat)
    monkeypatch.setattr(core, 'GEMINI_MODELS', ['m1', 'm2', 'm3'])
    monkeypatch.setattr(core, 'ALL_MODELS', ['m1', 'm2', 'm3'])
    return SimpleNamespace(behavior=behavior, calls=calls)


def _start(client):
    return client.post('/api/start', json={'subject': '物理', 'difficulty': '大学受験'})


def test_start_skips_a_model_that_no_longer_exists(student_client, scripted):
    scripted.behavior['m1'] = NOT_FOUND
    resp = _start(student_client)
    assert resp.status_code == 200
    assert resp.get_json()['reply'] == 'm2の返答'


def test_start_passes_quota_then_missing_model(student_client, scripted):
    """以前の不具合の再現: 1つ目が枠切れ、2つ目が提供終了でも、3つ目まで進む"""
    scripted.behavior.update({'m1': QUOTA, 'm2': NOT_FOUND})
    resp = _start(student_client)
    assert resp.status_code == 200
    assert resp.get_json()['reply'] == 'm3の返答'
    assert scripted.calls == ['m1', 'm2', 'm3']


def test_start_stops_on_input_errors_and_refunds_the_life(student_client, scripted):
    scripted.behavior['m1'] = INVALID
    resp = _start(student_client)
    assert resp.status_code == 503
    assert scripted.calls == ['m1']                     # 入力の誤りでは他のモデルを試さない
    assert student_client.get('/api/me').get_json()['lives_status']['lives'] == 3


def test_start_when_every_model_is_missing_is_a_503_not_a_quota_message(student_client, scripted):
    scripted.behavior.update({'m1': NOT_FOUND, 'm2': NOT_FOUND, 'm3': NOT_FOUND})
    resp = _start(student_client)
    assert resp.status_code == 503


def test_chat_switches_past_a_missing_model_and_keeps_the_history(student_client, scripted):
    key = _start(student_client).get_json()['session_key']      # m1 で始まる
    scripted.behavior.update({'m1': QUOTA, 'm2': NOT_FOUND})
    resp = student_client.post('/api/chat', json={'message': '説明です', 'session_key': key})
    assert resp.status_code == 200
    assert resp.get_json()['reply'] == 'm3の返答'
    sess = core.chat_sessions[key]
    assert sess['model'] == 'm3'
    # 切り替えても、それまでの会話（最初の問い）は引き継がれている
    assert any(h['message'] == 'm1の返答' for h in sess['chat'].get_history())


def test_chat_on_a_model_removed_from_the_list_starts_over_from_the_first(student_client, scripted):
    """一覧から外した古いモデルで会話中だった場合も、先頭のモデルへ切り替えられる"""
    key = _start(student_client).get_json()['session_key']
    core.chat_sessions[key]['model'] = 'gemini-2.0-flash'
    core.chat_sessions[key]['chat'] = _ScriptedChat('gemini-2.0-flash', {'gemini-2.0-flash': NOT_FOUND})
    resp = student_client.post('/api/chat', json={'message': '説明です', 'session_key': key})
    assert resp.status_code == 200
    assert core.chat_sessions[key]['model'] == 'm1'


def test_chat_returns_quota_message_when_the_last_model_is_out_of_quota(student_client, scripted):
    key = _start(student_client).get_json()['session_key']
    scripted.behavior.update({'m1': QUOTA, 'm2': QUOTA, 'm3': QUOTA})
    resp = student_client.post('/api/chat', json={'message': '説明です', 'session_key': key})
    assert resp.status_code == 429


def test_hint_skips_a_missing_model(student_client, scripted):
    key = _start(student_client).get_json()['session_key']
    scripted.behavior['m1'] = NOT_FOUND
    resp = student_client.post('/api/hint', json={'session_key': key})
    assert resp.status_code == 200
    assert resp.get_json()['hint'] == 'm2の返答'


# =====================================================
# 苦手分析
# =====================================================
class _AnalysisModels:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        action = self.behavior.get(model, 'ok')
        if action != 'ok':
            raise action()
        return SimpleNamespace(text=json.dumps({
            'topic': '慣性', 'understanding_score': 70, 'summary': 's',
            'gaps': [{'gap_type': 'vague', 'description': 'd', 'evidence': 'e', 'suggested_question': 'q'}],
        }))


TRANSCRIPT = [{'role': 'feyn', 'message': '問い'}, {'role': 'user', 'message': '説明'}]


def test_analysis_skips_a_missing_model():
    models = _AnalysisModels({'gemini-old': NOT_FOUND})
    result = gap_analyzer.analyze_session(SimpleNamespace(models=models), ['gemini-old', 'gemini-new'],
                                          '物理', '大学受験', TRANSCRIPT)
    assert result is not None and result['topic'] == '慣性'
    assert models.calls == ['gemini-old', 'gemini-new']


def test_analysis_stops_on_input_errors():
    models = _AnalysisModels({'gemini-a': INVALID})
    result = gap_analyzer.analyze_session(SimpleNamespace(models=models), ['gemini-a', 'gemini-b'],
                                          '物理', '大学受験', TRANSCRIPT)
    assert result is None
    assert models.calls == ['gemini-a']
