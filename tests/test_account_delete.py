# -*- coding: utf-8 -*-
"""アカウント削除（/api/account/delete）のテスト。"""
import sqlite3
from types import SimpleNamespace

import pytest

import core
import db as db_module
from conftest import FakeChatSession, _signup_payload

PASSWORD = 'testpass123'   # conftest の _signup_payload と同じ


def _uid(client):
    return client.get('/api/me').get_json()['id']


def _delete(client, password=PASSWORD):
    return client.post('/api/account/delete', json={'password': password})


def _fill_with_data(client, monkeypatch, teacher_client=None):
    """会話・完了・苦手・理解度・課題・利用状況の記録まで、ひととおりのデータを作る"""
    uid = _uid(client)
    if teacher_client:
        teacher_client.post('/api/dashboard/assignments', json={'student_ids': [uid], 'subject': '数学'})
    key = client.post('/api/start', json={'subject': '物理', 'difficulty': '大学受験'}).get_json()['session_key']
    monkeypatch.setattr(FakeChatSession, 'default_reply', '納得！【会話終了】')
    client.post('/api/chat', json={'message': '説明', 'session_key': key})
    core.save_analysis(uid, '物理', {'topic': '慣性', 'understanding_score': 50, 'gaps': [
        {'gap_type': 'vague', 'description': 'd', 'evidence': 'e', 'suggested_question': 'q'}]}, '2026-10-25')
    monkeypatch.setattr(FakeChatSession, 'default_reply', FakeChatSession.default_reply)
    client.post('/api/start', json={'subject': '数学', 'difficulty': '大学受験'})   # 途中の会話（メモリに残る）
    return uid


def _counts(user_id):
    with core.get_db() as conn:
        out = {}
        for table, column in core.USER_DATA_TABLES + [('users', 'id')]:
            out[table] = conn.execute(f'SELECT COUNT(*) AS cnt FROM {table} WHERE {column} = ?', (user_id,)).fetchone()['cnt']
    return out


def test_requires_login(client):
    assert _delete(client).status_code == 401


def test_deletes_all_of_the_students_data_and_logs_out(student_client, teacher_client, monkeypatch):
    uid = _fill_with_data(student_client, monkeypatch, teacher_client)
    before = _counts(uid)
    assert all(before[t] > 0 for t in before), before   # 全テーブルにデータがある状態で試す

    assert _delete(student_client).status_code == 200
    assert all(v == 0 for v in _counts(uid).values())
    assert not any(k.startswith(f'{uid}_') for k in core.chat_sessions)
    assert student_client.get('/api/me').status_code == 401        # ログアウトされている


def test_other_users_data_is_untouched(app, student_client, monkeypatch):
    other = app.app.test_client()
    other.post('/api/signup', json=_signup_payload(email='other@example.com'))
    other_id = _fill_with_data(other, monkeypatch)
    before = _counts(other_id)

    _fill_with_data(student_client, monkeypatch)
    assert _delete(student_client).status_code == 200
    assert _counts(other_id) == before


def test_can_sign_up_again_with_the_same_email(app, student_client):
    assert _delete(student_client).status_code == 200
    again = app.app.test_client()
    assert again.post('/api/signup', json=_signup_payload()).status_code == 200


def test_wrong_password_keeps_everything(student_client):
    uid = _uid(student_client)
    resp = _delete(student_client, 'wrong-password')
    assert resp.status_code == 401
    assert _counts(uid)['users'] == 1
    assert student_client.get('/api/me').status_code == 200


def test_repeated_wrong_passwords_are_rate_limited(student_client):
    for _ in range(5):
        assert _delete(student_client, 'wrong').status_code == 401
    assert _delete(student_client).status_code == 429   # 正しいパスワードでも一定時間は受け付けない
    assert _counts(_uid(student_client))['users'] == 1


def test_teacher_account_cannot_be_deleted_yet(teacher_client):
    assert _delete(teacher_client).status_code == 403


def test_active_subscription_blocks_deletion(student_client):
    uid = _uid(student_client)
    with core.get_db() as conn:
        conn.execute("UPDATE users SET subscription_status = 'active' WHERE id = ?", (uid,))
        conn.commit()
    resp = _delete(student_client)
    assert resp.status_code == 409
    assert resp.get_json()['subscription_active'] is True
    assert '解約' in resp.get_json()['error']
    assert _counts(uid)['users'] == 1


@pytest.mark.parametrize('stripe_status, expected', [
    ('past_due', 409),    # 支払いが滞っているだけで、請求は続いている
    ('unpaid', 409),
    ('canceled', 200),    # 本当に終わっている
    ('incomplete_expired', 200),
])
def test_leftover_stripe_subscription_is_checked(student_client, monkeypatch, stripe_status, expected):
    uid = _uid(student_client)
    with core.get_db() as conn:
        conn.execute("UPDATE users SET stripe_subscription_id = 'sub_old' WHERE id = ?", (uid,))
        conn.commit()
    monkeypatch.setattr(core.stripe.Subscription, 'retrieve', lambda sid: {'id': sid, 'status': stripe_status})
    assert _delete(student_client).status_code == expected


def test_deletion_is_blocked_when_stripe_cannot_be_reached(student_client, monkeypatch):
    uid = _uid(student_client)
    with core.get_db() as conn:
        conn.execute("UPDATE users SET stripe_subscription_id = 'sub_old' WHERE id = ?", (uid,))
        conn.commit()

    def unreachable(sid):
        raise RuntimeError('network error')
    monkeypatch.setattr(core.stripe.Subscription, 'retrieve', unreachable)
    assert _delete(student_client).status_code == 409
    assert _counts(uid)['users'] == 1


def test_deletion_order_satisfies_foreign_keys_like_postgres(student_client, teacher_client, monkeypatch):
    """Postgresは users を参照している行が残っていると users を消せない。SQLiteでも外部キーの確認を
    有効にして、本番と同じ条件で消せることを確かめる"""
    uid = _fill_with_data(student_client, monkeypatch, teacher_client)

    def strict_get_db():
        conn = sqlite3.connect(db_module.DB_PATH, factory=db_module._ClosingSQLiteConnection)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        return conn
    monkeypatch.setattr(core, 'get_db', strict_get_db)

    # 前提確認: 外部キーの確認が本当に効いている（いきなり users を消すと失敗する）
    with pytest.raises(sqlite3.IntegrityError):
        with strict_get_db() as conn:
            conn.execute('DELETE FROM users WHERE id = ?', (uid,))

    core.delete_user_data(uid)
    assert all(v == 0 for v in _counts(uid).values())


def test_failure_midway_deletes_nothing(student_client, monkeypatch):
    """途中で失敗したら、全部取り消される（一部だけ消えた状態にならない）"""
    uid = _fill_with_data(student_client, monkeypatch)
    before = _counts(uid)
    original = core.USER_DATA_TABLES
    # 最後のテーブルで失敗させる（monkeypatch.undo() は一時DBへの切り替えまで戻してしまうので使わない）
    core.USER_DATA_TABLES = original + [('no_such_table', 'user_id')]
    try:
        with pytest.raises(Exception):
            core.delete_user_data(uid)
    finally:
        core.USER_DATA_TABLES = original
    assert _counts(uid) == before


def test_works_through_the_postgres_code_path(pg_path, app, monkeypatch):
    client = app.app.test_client()
    client.post('/api/signup', json=_signup_payload())
    uid = _fill_with_data(client, monkeypatch)
    assert _delete(client).status_code == 200
    assert pg_path.pool.stats()['in_use'] == 0
    with db_module.get_db() as conn:
        assert conn.execute('SELECT COUNT(*) AS cnt FROM users WHERE id = ?', (uid,)).fetchone()['cnt'] == 0
