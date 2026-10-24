# -*- coding: utf-8 -*-
"""学習のきろく（一覧のページ分け・1つの会話の中身）と、インデックスのテスト。"""
import pytest

import core
from conftest import _signup_payload


def _uid(client):
    return client.get('/api/me').get_json()['id']


def _add_conversation(user_id, conv_id, subject='物理', date='2026-10-20', replies=1, legacy=False):
    """会話ログを直接入れる。legacy=True なら session_id 無しの過去データ形式"""
    with core.get_db() as conn:
        conn.execute(
            'INSERT INTO conversation_logs (user_id, subject, difficulty, role, message, session_date, session_id, unit) '
            "VALUES (?, ?, '大学受験', 'feyn', ?, ?, ?, NULL)",
            (user_id, subject, f'{conv_id}の問い', date, None if legacy else conv_id))
        for i in range(replies):
            conn.execute(
                'INSERT INTO conversation_logs (user_id, subject, difficulty, role, message, session_date, session_id, unit) '
                "VALUES (?, ?, '大学受験', 'user', ?, ?, ?, NULL)",
                (user_id, subject, f'{conv_id}の説明{i}', date, None if legacy else conv_id))
        conn.commit()


def test_empty_history(student_client):
    data = student_client.get('/api/history').get_json()
    assert data == {'sessions': [], 'has_more': False}


def test_history_is_paged_newest_first_without_message_bodies(student_client):
    uid = _uid(student_client)
    for i in range(25):
        _add_conversation(uid, f'conv{i:02d}')

    first = student_client.get('/api/history').get_json()
    assert len(first['sessions']) == 20
    assert first['has_more'] is True
    assert first['sessions'][0]['session_id'] == 'conv24'      # 新しい順
    assert first['sessions'][0]['message_count'] == 2
    assert 'messages' not in first['sessions'][0]              # 一覧には本文を含めない

    second = student_client.get('/api/history?offset=20').get_json()
    assert [s['session_id'] for s in second['sessions']] == ['conv04', 'conv03', 'conv02', 'conv01', 'conv00']
    assert second['has_more'] is False


def test_conversation_without_any_reply_is_not_listed(student_client):
    uid = _uid(student_client)
    _add_conversation(uid, 'answered')
    _add_conversation(uid, 'ignored', replies=0)
    sessions = student_client.get('/api/history').get_json()['sessions']
    assert [s['session_id'] for s in sessions] == ['answered']


def test_messages_of_one_conversation(student_client):
    uid = _uid(student_client)
    _add_conversation(uid, 'convA', replies=2)
    resp = student_client.get('/api/history/messages?key=convA')
    assert resp.status_code == 200
    assert [m['message'] for m in resp.get_json()['messages']] == ['convAの問い', 'convAの説明0', 'convAの説明1']


def test_completed_conversation_shows_topic(student_client):
    uid = _uid(student_client)
    _add_conversation(uid, 'done1')
    _add_conversation(uid, 'paused')
    with core.get_db() as conn:
        conn.execute("INSERT INTO session_logs (user_id, subject, difficulty, session_id, topic) "
                     "VALUES (?, '物理', '大学受験', 'done1', '慣性の法則')", (uid,))
        conn.commit()
    by_id = {s['session_id']: s for s in student_client.get('/api/history').get_json()['sessions']}
    assert by_id['done1']['completed'] is True
    assert by_id['done1']['topic'] == '慣性の法則'
    assert by_id['paused']['completed'] is False


def test_legacy_conversations_are_grouped_by_date_and_subject(student_client):
    uid = _uid(student_client)
    _add_conversation(uid, 'x', subject='数学', date='2026-01-05', legacy=True)
    _add_conversation(uid, 'y', subject='数学', date='2026-01-05', legacy=True)   # 同じ日・同じ科目 → 1つにまとまる
    with core.get_db() as conn:
        conn.execute("INSERT INTO session_logs (user_id, subject, difficulty, completed_at) "
                     "VALUES (?, '数学', '大学受験', '2026-01-05 10:00:00')", (uid,))
        conn.commit()

    sessions = student_client.get('/api/history').get_json()['sessions']
    assert len(sessions) == 1
    assert sessions[0]['key'] == '2026-01-05|数学'
    assert sessions[0]['session_id'] is None
    assert sessions[0]['message_count'] == 4
    assert sessions[0]['completed'] is True

    messages = student_client.get('/api/history/messages?key=2026-01-05|数学').get_json()['messages']
    assert len(messages) == 4


def test_cannot_read_another_students_conversation(app, student_client):
    uid = _uid(student_client)
    _add_conversation(uid, 'secret')
    other = app.app.test_client()
    other.post('/api/signup', json=_signup_payload(email='other@example.com'))
    assert other.get('/api/history/messages?key=secret').status_code == 404
    assert other.get('/api/history').get_json()['sessions'] == []


def test_history_requires_login(client):
    assert client.get('/api/history').status_code == 401
    assert client.get('/api/history/messages?key=x').status_code == 401


# =====================================================
# 先生が見る会話ログ
# =====================================================
def test_teacher_student_detail_returns_first_page_of_headings(student_client, teacher_client):
    uid = _uid(student_client)
    for i in range(22):
        _add_conversation(uid, f'c{i:02d}')
    data = teacher_client.get(f'/api/dashboard/student/{uid}').get_json()
    assert len(data['sessions']) == 20
    assert data['sessions_has_more'] is True

    rest = teacher_client.get(f'/api/dashboard/student/{uid}/history?offset=20').get_json()
    assert len(rest['sessions']) == 2
    messages = teacher_client.get(f'/api/dashboard/student/{uid}/history/messages?key=c00').get_json()['messages']
    assert len(messages) == 2


def test_teacher_history_api_authorization(app, student_client, teacher_client):
    uid = _uid(student_client)
    _add_conversation(uid, 'c1')
    urls = [f'/api/dashboard/student/{uid}/history', f'/api/dashboard/student/{uid}/history/messages?key=c1']

    for url in urls:
        assert app.app.test_client().get(url).status_code == 401   # 未ログイン
        assert student_client.get(url).status_code == 403          # 生徒は見られない

    # 別の組織の生徒は見られない
    with core.get_db() as conn:
        conn.execute("INSERT INTO schools (name, join_code) VALUES ('別の塾', 'OTHER')")
        other_school = conn.execute("SELECT id FROM schools WHERE join_code = 'OTHER'").fetchone()['id']
        conn.execute('UPDATE users SET school_id = ? WHERE id = ?', (other_school, uid))
        conn.commit()
    for url in urls:
        assert teacher_client.get(url).status_code == 404


# =====================================================
# 本番と同じPostgresの経路でも動く・インデックス
# =====================================================
def test_history_works_through_the_postgres_code_path(pg_path, app):
    client = app.app.test_client()
    client.post('/api/signup', json=_signup_payload())
    uid = _uid(client)
    _add_conversation(uid, 'pg1')
    _add_conversation(uid, 'x', subject='数学', date='2026-01-05', legacy=True)
    data = client.get('/api/history').get_json()
    assert {s['key'] for s in data['sessions']} == {'pg1', '2026-01-05|数学'}
    assert client.get('/api/history/messages?key=pg1').status_code == 200
    assert pg_path.pool.stats()['in_use'] == 0


@pytest.mark.parametrize('name', [
    'idx_conversation_logs_user_session', 'idx_conversation_logs_user_subject_date',
    'idx_session_logs_user_session', 'idx_knowledge_gaps_user_subject_topic',
    'idx_assignments_student_status', 'idx_assignments_teacher_status', 'idx_users_school_role',
])
def test_indexes_exist(app, name):
    with core.get_db() as conn:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)).fetchone()
    assert row is not None


def test_init_db_can_run_twice(app):
    core.init_db()   # 既にあるテーブル・インデックスがあってもエラーにならない
