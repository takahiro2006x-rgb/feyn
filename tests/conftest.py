# -*- coding: utf-8 -*-
"""APIレベルのテスト共通フィクスチャ。

- 本物の feyn.db には絶対に触れないよう、テストごとに一時ファイルへ db.DB_PATH を差し替えたうえで
  core.init_db() を再実行し、chat_sessions もクリアする。
  Flaskアプリ本体やGemini/Groqクライアントはプロセス全体で1回だけ作られたものを再利用する
  （テストごとに再構築すると、クライアント初期化のオーバーヘッドで実行時間が大きく伸びるため）。
- Gemini/Groq への実際のAPI呼び出しはせず、llm.create_chat をテスト用の偽チャットに置き換える。
- gap_analyzer.analyze_session も同様にモックし、/api/complete がAPIを叩かないようにする。
"""
from types import SimpleNamespace

import pytest

import app as app_module
import core
import db as db_module
import gap_analyzer as gap_analyzer_module
import llm as llm_module


class FakeChatSession:
    """llm.create_chat() の戻り値（.send_message()/.get_history()）を模倣する"""

    default_reply = 'テスト用の返答です。'

    def __init__(self, history=None):
        self.history = list(history) if history else []

    def send_message(self, text, image=None):
        self.history.append({'role': 'user', 'message': text})
        reply = FakeChatSession.default_reply
        self.history.append({'role': 'model', 'message': reply})
        return SimpleNamespace(text=reply)

    def get_history(self):
        return list(self.history)


@pytest.fixture
def app(monkeypatch, tmp_path):
    """テストごとに空の一時DBへ切り替え、core側のグローバル状態をリセットする。
    llm.create_chat / gap_analyzer.analyze_session のモックもこのフィクスチャの中に閉じ込め、
    これらの実装そのものをテストしている test_llm.py / test_gap_analyzer.py に影響しないようにする。"""
    monkeypatch.setattr(db_module, 'DB_PATH', str(tmp_path / 'test_feyn.db'))
    monkeypatch.setattr(llm_module, 'create_chat',
                         lambda gemini_client, groq_client, model, instruction, history=None:
                             FakeChatSession(history=history))
    monkeypatch.setattr(gap_analyzer_module, 'analyze_session', lambda *a, **k: None)

    core.init_db()
    core.chat_sessions.clear()
    return app_module


@pytest.fixture
def client(app):
    return app.app.test_client()


def _signup_payload(**overrides):
    payload = {
        'name':              'テスト太郎',
        'email':             'test_user@example.com',
        'password':          'testpass123',
        'security_question': '好きな食べ物は？',
        'security_answer':   'apple',
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def student_client(app):
    """新規登録済み・ログイン済みの生徒アカウントとしてのtest_client（自分専用のクッキーを持つ）"""
    c = app.app.test_client()
    resp = c.post('/api/signup', json=_signup_payload())
    assert resp.status_code == 200
    return c


@pytest.fixture
def teacher_client(app):
    """新規登録済み・ログイン済みの先生アカウントとしてのtest_client（自分専用のクッキーを持つ）"""
    c = app.app.test_client()
    resp = c.post('/api/signup', json=_signup_payload(
        name='テスト先生', email='test_teacher@example.com', teacher_code=core.TEACHER_CODE,
    ))
    assert resp.status_code == 200
    return c
