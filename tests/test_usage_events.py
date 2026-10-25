# -*- coding: utf-8 -*-
"""利用状況の計測（events テーブル）と、先生ダッシュボードの集計のテスト。"""
import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import core
from conftest import FakeChatSession, _signup_payload

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _uid(client):
    return client.get('/api/me').get_json()['id']


def _events(user_id=None, event_type=None):
    sql = 'SELECT event_type, user_id, conv_id, subject, mode, turn FROM events WHERE 1 = 1'
    params = []
    if user_id is not None:
        sql += ' AND user_id = ?'
        params.append(user_id)
    if event_type:
        sql += ' AND event_type = ?'
        params.append(event_type)
    with core.get_db() as conn:
        return [dict(r) for r in conn.execute(sql + ' ORDER BY id', params).fetchall()]


def _start(client, **extra):
    return client.post('/api/start', json={'subject': '物理', 'difficulty': '大学受験', **extra})


# =====================================================
# 記録
# =====================================================
def test_signup_is_recorded_for_students_only(student_client, teacher_client):
    assert [e['event_type'] for e in _events(_uid(student_client))] == ['signup']
    assert _events(_uid(teacher_client)) == []


def test_conversation_start_turns_and_completion_are_recorded(student_client, monkeypatch):
    uid = _uid(student_client)
    key = _start(student_client).get_json()['session_key']
    student_client.post('/api/chat', json={'message': '1回目の説明', 'session_key': key})
    monkeypatch.setattr(FakeChatSession, 'default_reply', '納得！【会話終了】')
    student_client.post('/api/chat', json={'message': '2回目の説明', 'session_key': key})

    events = [e for e in _events(uid) if e['event_type'] != 'signup']
    assert [(e['event_type'], e['turn']) for e in events] == [
        ('conversation_started', None), ('conversation_turn', 1),
        ('conversation_turn', 2), ('conversation_completed', 2),
    ]
    assert events[0]['mode'] == 'normal' and events[0]['subject'] == '物理'
    assert len({e['conv_id'] for e in events}) == 1


def test_conversation_text_is_never_recorded(student_client):
    key = _start(student_client).get_json()['session_key']
    student_client.post('/api/chat', json={'message': 'ひみつの説明文12345', 'session_key': key})
    with core.get_db() as conn:
        dump = str([dict(r) for r in conn.execute('SELECT * FROM events').fetchall()])
    assert 'ひみつの説明文12345' not in dump
    assert 'test_user@example.com' not in dump and 'テスト太郎' not in dump


def test_start_modes_are_recorded(student_client, teacher_client):
    uid = _uid(student_client)
    teacher_client.post('/api/dashboard/assignments', json={'student_ids': [uid], 'subject': '数学'})
    with core.get_db() as conn:
        assignment_id = conn.execute('SELECT id FROM assignments').fetchone()['id']
        conn.execute("UPDATE users SET subscription_status = 'active' WHERE id = ?", (uid,))  # ライフ切れを避ける
        conn.execute("INSERT INTO knowledge_gaps (user_id, subject, topic, gap_type, description, suggested_question, "
                     "session_date) VALUES (?, '物理', '慣性', 'vague', '説明', '問い', '2026-10-20')", (uid,))
        conn.commit()
        gap_id = conn.execute('SELECT id FROM knowledge_gaps').fetchone()['id']

    _start(student_client)
    _start(student_client, gap_id=gap_id)
    _start(student_client, assignment_id=assignment_id)
    _start(student_client, photo=base64.b64encode(b'fake-image').decode(), photo_mime='image/png')
    modes = [e['mode'] for e in _events(uid, 'conversation_started')]
    assert modes == ['normal', 'review', 'assignment', 'photo']


def test_life_blocked_is_recorded(student_client):
    for _ in range(3):
        _start(student_client)
    assert _start(student_client).status_code == 403
    assert len(_events(_uid(student_client), 'life_blocked')) == 1


def test_upgrade_is_counted_once_even_if_both_confirm_and_webhook_arrive(student_client, monkeypatch):
    uid = _uid(student_client)
    monkeypatch.delenv('RENDER', raising=False)
    monkeypatch.setattr(core, 'STRIPE_WEBHOOK_SECRET', None)
    monkeypatch.setattr(core.stripe.checkout.Session, 'retrieve',
                        lambda sid: SimpleNamespace(client_reference_id=str(uid), status='complete',
                                                    customer='cus_ev', subscription='sub_ev'))
    student_client.post('/api/billing/confirm', json={'session_id': 'cs_1'})
    student_client.post('/api/billing/confirm', json={'session_id': 'cs_1'})          # 画面の再読み込み
    student_client.post('/api/billing/webhook', json={                                # Stripeからの通知も届く
        'type': 'checkout.session.completed',
        'data': {'object': {'mode': 'subscription', 'status': 'complete', 'client_reference_id': str(uid),
                            'customer': 'cus_ev', 'subscription': 'sub_ev'}}})
    student_client.post('/api/billing/webhook', json={
        'type': 'customer.subscription.updated',
        'data': {'object': {'id': 'sub_ev', 'customer': 'cus_ev', 'status': 'active'}}})
    assert len(_events(uid, 'subscription_started')) == 1

    student_client.post('/api/billing/webhook', json={
        'type': 'customer.subscription.deleted',
        'data': {'object': {'id': 'sub_ev', 'customer': 'cus_ev', 'status': 'canceled'}}})
    assert len(_events(uid, 'subscription_ended')) == 1


def test_log_event_never_breaks_the_caller(app, monkeypatch):
    core.log_event('unknown_type', 1)        # 知らない種類は記録しない
    assert _events() == []

    def broken_db():
        raise RuntimeError('DBに繋がらない')
    monkeypatch.setattr(core, 'get_db', broken_db)
    core.log_event('signup', 1)              # 例外を外に出さない


# =====================================================
# 集計
# =====================================================
def _insert(event_type, user_id, conv_id=None, turn=None, at=None, mode=None):
    at = (at or NOW).strftime('%Y-%m-%d %H:%M:%S')
    with core.get_db() as conn:
        conn.execute('INSERT INTO events (event_type, user_id, conv_id, mode, turn, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                     (event_type, user_id, conv_id, mode, turn, at))
        conn.commit()


def _scenario(student_id, teacher_id, other_school_student_id):
    two_days_ago = NOW - timedelta(days=2)
    # A: 3往復で完了
    _insert('conversation_started', student_id, 'A', at=two_days_ago, mode='normal')
    for t in (1, 2, 3):
        _insert('conversation_turn', student_id, 'A', turn=t, at=two_days_ago)
    _insert('conversation_completed', student_id, 'A', turn=3, at=two_days_ago)
    # B: 1往復でやめた / C: 返信せずにやめた
    _insert('conversation_started', student_id, 'B', at=two_days_ago, mode='review')
    _insert('conversation_turn', student_id, 'B', turn=1, at=two_days_ago)
    _insert('conversation_started', student_id, 'C', at=two_days_ago, mode='normal')
    # D: 始めたばかり（進行中）
    _insert('conversation_started', student_id, 'D', at=NOW - timedelta(minutes=10), mode='normal')
    _insert('life_blocked', student_id, at=two_days_ago)
    _insert('subscription_started', student_id, at=two_days_ago)
    # 集計に入れないもの: 30日より前 / 先生自身 / 別の組織の生徒
    _insert('conversation_started', student_id, 'OLD', at=NOW - timedelta(days=40))
    _insert('conversation_started', teacher_id, 'T1', at=two_days_ago)
    _insert('conversation_started', other_school_student_id, 'X1', at=two_days_ago)


def test_usage_metrics(app, student_client, teacher_client):
    sid, tid = _uid(student_client), _uid(teacher_client)
    other = app.app.test_client()
    other.post('/api/signup', json=_signup_payload(email='other@example.com'))
    oid = _uid(other)
    with core.get_db() as conn:
        conn.execute("INSERT INTO schools (name, join_code) VALUES ('別の塾', 'OTHER')")
        other_school = conn.execute("SELECT id FROM schools WHERE join_code = 'OTHER'").fetchone()['id']
        conn.execute('UPDATE users SET school_id = ? WHERE id = ?', (other_school, oid))
        conn.execute("DELETE FROM events")    # 登録時のイベントは使わず、シナリオだけで確かめる
        conn.commit()
    _scenario(sid, tid, oid)

    m = core.compute_usage_metrics(core.default_school_id(), days=30, now=NOW)
    assert m['started'] == 4
    assert m['completed'] == 1
    assert m['completion_rate'] == 25
    assert m['in_progress'] == 1
    assert m['dropout_by_turn'] == {'0': 1, '1': 1, '2': 0, '3+': 0}
    assert m['avg_turns_to_complete'] == 3
    assert m['started_by_mode'] == {'normal': 3, 'review': 1}
    assert m['active_students'] == 1
    assert m['life_blocked'] == 1 and m['life_blocked_students'] == 1
    assert m['subscriptions_started'] == 1
    assert m['plans'] == {'free': 1, 'active': 0}


def test_metrics_with_no_data(app, teacher_client):
    m = teacher_client.get('/api/dashboard/metrics').get_json()
    assert m['started'] == 0 and m['completion_rate'] is None and m['avg_turns_to_complete'] is None


def test_metrics_authorization(app, student_client):
    assert app.app.test_client().get('/api/dashboard/metrics').status_code == 401
    assert student_client.get('/api/dashboard/metrics').status_code == 403


def test_metrics_through_the_postgres_code_path(pg_path, app, monkeypatch):
    """Postgresは時刻を datetime で返す。その形でも集計できる"""
    student = app.app.test_client()
    student.post('/api/signup', json=_signup_payload())
    teacher = app.app.test_client()
    teacher.post('/api/signup', json=_signup_payload(email='t@example.com', teacher_code=core.TEACHER_CODE))
    key = _start(student).get_json()['session_key']
    monkeypatch.setattr(FakeChatSession, 'default_reply', '納得！【会話終了】')
    student.post('/api/chat', json={'message': '説明', 'session_key': key})

    m = teacher.get('/api/dashboard/metrics').get_json()
    assert m['started'] == 1 and m['completed'] == 1 and m['signups'] == 1
    assert pg_path.pool.stats()['in_use'] == 0


@pytest.mark.parametrize('days', [0, -5, 9999])
def test_metrics_days_is_clamped(teacher_client, days):
    assert teacher_client.get(f'/api/dashboard/metrics?days={days}').status_code == 200
