# -*- coding: utf-8 -*-
"""/api/start, /api/chat, /api/resume, /api/complete, /api/hint, /api/reveal のテスト。
ライフ制（無料プランの回数制限）の挙動もここで検証する。"""
import core
from conftest import FakeChatSession


def _start(client, subject='物理'):
    return client.post('/api/start', json={'subject': subject, 'difficulty': '大学受験'})


def test_start_requires_login(client):
    assert _start(client).status_code == 401


def test_start_succeeds_and_returns_session_key(student_client):
    resp = _start(student_client)
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['reply'] == FakeChatSession.default_reply
    assert data['session_key'].endswith('_物理')


def test_free_student_is_blocked_after_max_lives_used(student_client):
    for subject in ['物理', '数学', '英語']:
        assert _start(student_client, subject).status_code == 200

    resp = _start(student_client, '化学')
    data = resp.get_json()
    assert resp.status_code == 403
    assert data['upgrade_required'] is True
    assert 'ライフ' in data['error']


def test_teacher_is_never_blocked_by_life_limit(teacher_client):
    for subject in ['物理', '数学', '英語', '化学']:
        assert _start(teacher_client, subject).status_code == 200


def test_active_subscription_student_is_never_blocked(student_client, app, tmp_path):
    # 3回使い切ってから有料プランへアップグレードした状況を再現する
    for subject in ['物理', '数学', '英語']:
        _start(student_client, subject)

    with core.get_db() as conn:
        conn.execute("UPDATE users SET subscription_status = 'active' WHERE email = ?",
                     ('test_user@example.com',))
        conn.commit()

    resp = _start(student_client, '化学')
    assert resp.status_code == 200


def test_resume_does_not_consume_a_life(student_client, app):
    start_data = _start(student_client, '物理').get_json()
    student_client.post('/api/chat', json={
        'message': 'テスト説明', 'session_key': start_data['session_key'],
    })

    me_before = student_client.get('/api/me').get_json()
    assert me_before['lives_status']['lives'] == 2  # startで3→2

    with core.get_db() as conn:
        row = conn.execute(
            "SELECT session_date FROM conversation_logs WHERE user_id = ? AND subject = '物理' LIMIT 1",
            (me_before['id'],)
        ).fetchone()
    today = row['session_date']

    resume_resp = student_client.post('/api/resume', json={'subject': '物理', 'date': today})
    assert resume_resp.status_code == 200

    me_after = student_client.get('/api/me').get_json()
    assert me_after['lives_status']['lives'] == 2  # レジュームでは減らない


def test_chat_rejects_message_for_other_users_session_key(student_client):
    _start(student_client, '物理')
    resp = student_client.post('/api/chat', json={'message': 'test', 'session_key': '999_物理'})
    assert resp.status_code == 400


def test_chat_happy_path_returns_reply_and_progress(student_client):
    start_data = _start(student_client, '物理').get_json()
    resp = student_client.post('/api/chat', json={
        'message': 'エネルギーは仕事をする能力です', 'session_key': start_data['session_key'],
    })
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['reply'] == FakeChatSession.default_reply
    assert data['is_done'] is False
    assert data['progress'] == 25


def test_complete_records_session_in_db(student_client, app):
    start_data = _start(student_client, '物理').get_json()
    resp = student_client.post('/api/complete', json={
        'subject': '物理', 'difficulty': '大学受験', 'session_key': start_data['session_key'],
    })
    assert resp.status_code == 200

    me = student_client.get('/api/me').get_json()
    with core.get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM session_logs WHERE user_id = ? AND subject = '物理'",
            (me['id'],)
        ).fetchone()
    assert row['cnt'] == 1


def test_hint_requires_active_session(student_client):
    resp = student_client.post('/api/hint', json={'session_key': 'nope'})
    assert resp.status_code == 400


def test_hint_and_reveal_return_text_for_active_session(student_client):
    start_data = _start(student_client, '物理').get_json()
    hint_resp = student_client.post('/api/hint', json={'session_key': start_data['session_key']})
    assert hint_resp.status_code == 200
    assert hint_resp.get_json()['hint']

    reveal_resp = student_client.post('/api/reveal', json={'session_key': start_data['session_key']})
    assert reveal_resp.status_code == 200
    assert reveal_resp.get_json()['answer']
