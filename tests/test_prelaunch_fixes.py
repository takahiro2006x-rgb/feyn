# -*- coding: utf-8 -*-
"""公開前の修正（2026-10）のテスト。
- 日付を日本時間で数える
- AIの呼び出しに失敗したらライフを返す（入力エラーではそもそも消費しない）
- 完了API（/api/complete）はブラウザから送られた値を信じない
- Webhookの checkout.session.completed で有料化を反映する
- 本番で秘密鍵・先生コードが既定値のままなら使わせない
- gunicornの設定（1プロセス・複数スレッド・長めのタイムアウト）
"""
import runpy
import threading
from datetime import date, datetime, timezone

import pytest

import app as app_module
import core
import llm as llm_module
from conftest import FakeChatSession, _signup_payload

DONE_REPLY = 'なるほど、やっと分かった！ありがとう！【会話終了】'


def _start(client, subject='物理', **extra):
    return client.post('/api/start', json={'subject': subject, 'difficulty': '大学受験', **extra})


def _finish(client, session_key, monkeypatch):
    """Feynが納得して【会話終了】を出すところまで会話を進める"""
    monkeypatch.setattr(FakeChatSession, 'default_reply', DONE_REPLY)
    resp = client.post('/api/chat', json={'message': '説明します', 'session_key': session_key})
    assert resp.get_json()['is_done'] is True
    return resp


def _user_id(client):
    return client.get('/api/me').get_json()['id']


def _lives(client):
    return client.get('/api/me').get_json()['lives_status']['lives']


def _session_log_count(user_id):
    with core.get_db() as conn:
        return conn.execute('SELECT COUNT(*) AS cnt FROM session_logs WHERE user_id = ?',
                            (user_id,)).fetchone()['cnt']


# =====================================================
# 日本時間
# =====================================================
def test_to_jst_date_converts_utc_strings_and_datetimes():
    # UTCの16時 = 日本時間の翌日1時
    assert core.to_jst_date('2026-10-24 16:00:00') == date(2026, 10, 25)
    assert core.to_jst_date(datetime(2026, 10, 24, 16, 0)) == date(2026, 10, 25)  # Postgresの形
    assert core.to_jst_date(datetime(2026, 10, 24, 14, 59, tzinfo=timezone.utc)) == date(2026, 10, 24)
    assert core.to_jst_date(None) is None
    assert core.to_jst_date('壊れた値') is None


def test_to_jst_str_formats_for_display():
    assert core.to_jst_str('2026-10-24 16:05:30') == '2026-10-25 01:05'
    assert core.to_jst_str(datetime(2026, 10, 24, 16, 5)) == '2026-10-25 01:05'
    assert core.to_jst_str(None) is None


def test_streak_counts_days_in_japan_time(student_client, monkeypatch):
    """日本時間の同じ日（10/25の朝8時と10時）の2回は、UTCだと別の日になるが1日として数える"""
    monkeypatch.setattr(core, 'today_jst', lambda: date(2026, 10, 25))
    uid = _user_id(student_client)
    with core.get_db() as conn:
        for utc in ('2026-10-24 23:00:00', '2026-10-25 01:00:00'):
            conn.execute("INSERT INTO session_logs (user_id, subject, difficulty, completed_at) "
                         "VALUES (?, '物理', '大学受験', ?)", (uid, utc))
        conn.commit()
    assert student_client.get('/api/streak').get_json()['streak'] == 1


def test_streak_continues_across_japan_midnight(student_client, monkeypatch):
    """日本時間で10/24の夜と10/25の深夜1時に学習 → 2日連続（UTCだとどちらも10/24になってしまう）"""
    monkeypatch.setattr(core, 'today_jst', lambda: date(2026, 10, 25))
    uid = _user_id(student_client)
    with core.get_db() as conn:
        for utc in ('2026-10-24 12:00:00', '2026-10-24 16:00:00'):  # JST 10/24 21:00, 10/25 01:00
            conn.execute("INSERT INTO session_logs (user_id, subject, difficulty, completed_at) "
                         "VALUES (?, '物理', '大学受験', ?)", (uid, utc))
        conn.commit()
    assert student_client.get('/api/streak').get_json()['streak'] == 2


def test_conversation_is_logged_with_japan_date(student_client, monkeypatch):
    monkeypatch.setattr(core, 'today_jst', lambda: date(2026, 10, 25))
    _start(student_client)
    with core.get_db() as conn:
        row = conn.execute('SELECT session_date FROM conversation_logs LIMIT 1').fetchone()
    assert row['session_date'] == '2026-10-25'


def test_review_schedule_uses_japan_date(student_client, monkeypatch):
    monkeypatch.setattr(core, 'today_jst', lambda: date(2026, 10, 25))
    uid = _user_id(student_client)
    with core.get_db() as conn:
        conn.execute("INSERT INTO knowledge_gaps (user_id, subject, topic, gap_type, description, "
                     "suggested_question, session_date) VALUES (?, '物理', '慣性', 'vague', '説明', '問い', '2026-10-20')",
                     (uid,))
        conn.commit()
        gap_id = conn.execute('SELECT id FROM knowledge_gaps').fetchone()['id']

    key = _start(student_client, gap_id=gap_id).get_json()['session_key']
    _finish(student_client, key, monkeypatch)
    assert student_client.post('/api/complete', json={'session_key': key}).status_code == 200

    with core.get_db() as conn:
        gap = conn.execute('SELECT status, next_review FROM knowledge_gaps WHERE id = ?', (gap_id,)).fetchone()
    assert gap['status'] == 'resolved'
    assert gap['next_review'] == '2026-10-26'  # 1回目の復習は翌日


def test_teacher_dashboard_shows_japan_time(student_client, teacher_client):
    with core.get_db() as conn:
        conn.execute("UPDATE users SET last_login = '2026-10-24 16:00:00' WHERE email = 'test_user@example.com'")
        conn.commit()
    students = teacher_client.get('/api/dashboard').get_json()['students']
    assert students[0]['last_login'] == '2026-10-25 01:00'


# =====================================================
# ライフの返却
# =====================================================
class _QuotaExhaustedChat:
    def send_message(self, text, image=None):
        raise Exception('429 RESOURCE_EXHAUSTED')

    def get_history(self):
        return []


def test_life_is_refunded_when_every_ai_model_fails(student_client, monkeypatch):
    monkeypatch.setattr(llm_module, 'create_chat', lambda *a, **k: _QuotaExhaustedChat())
    resp = _start(student_client)
    assert resp.status_code == 429
    assert _lives(student_client) == 3


def test_refund_keeps_the_recovery_timer_when_not_full(student_client, monkeypatch):
    assert _start(student_client, '物理').status_code == 200  # 3 → 2
    uid = _user_id(student_client)
    with core.get_db() as conn:
        anchor = conn.execute('SELECT last_life_lost_at FROM users WHERE id = ?', (uid,)).fetchone()[0]

    monkeypatch.setattr(llm_module, 'create_chat', lambda *a, **k: _QuotaExhaustedChat())
    assert _start(student_client, '数学').status_code == 429

    with core.get_db() as conn:
        row = conn.execute('SELECT lives, last_life_lost_at FROM users WHERE id = ?', (uid,)).fetchone()
    assert row['lives'] == 2
    assert row['last_life_lost_at'] == anchor  # 回復までの時間はリセットされない


def test_invalid_input_does_not_consume_a_life(student_client):
    assert _start(student_client, photo='aGVsbG8=', photo_mime='application/pdf').status_code == 400
    assert _start(student_client, gap_id=99999).status_code == 404
    assert _start(student_client, assignment_id=99999).status_code == 404
    assert _lives(student_client) == 3


def test_concurrent_starts_never_consume_more_than_available(student_client):
    uid = _user_id(student_client)
    results = []

    def worker():
        results.append(core.check_and_consume_life(uid))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r in results if r is None) == 3  # 3回だけ始められる
    assert _lives(student_client) == 0


# =====================================================
# 完了APIの検証
# =====================================================
def test_complete_is_rejected_before_feyn_is_convinced(student_client):
    key = _start(student_client).get_json()['session_key']
    resp = student_client.post('/api/complete', json={'subject': '物理', 'session_key': key})
    assert resp.status_code == 400
    assert _session_log_count(_user_id(student_client)) == 0

    # 拒否されても会話は消えず、そのまま続けられる
    chat = student_client.post('/api/chat', json={'message': '続き', 'session_key': key})
    assert chat.status_code == 200


def test_session_is_recorded_once_when_feyn_is_convinced(student_client, monkeypatch):
    key = _start(student_client).get_json()['session_key']
    _finish(student_client, key, monkeypatch)
    uid = _user_id(student_client)
    assert _session_log_count(uid) == 1  # 納得した時点で記録される

    assert student_client.post('/api/complete', json={'session_key': key}).status_code == 200
    # 二重に送っても記録は増えない
    assert student_client.post('/api/complete', json={'session_key': key}).status_code == 400
    assert _session_log_count(uid) == 1


def test_chat_is_rejected_after_the_conversation_ended(student_client, monkeypatch):
    key = _start(student_client).get_json()['session_key']
    _finish(student_client, key, monkeypatch)
    resp = student_client.post('/api/chat', json={'message': 'もう一回', 'session_key': key})
    assert resp.status_code == 400
    assert _session_log_count(_user_id(student_client)) == 1


def test_complete_ignores_assignment_and_gap_ids_sent_by_the_browser(student_client, teacher_client, monkeypatch):
    uid = _user_id(student_client)
    teacher_client.post('/api/dashboard/assignments', json={'student_ids': [uid], 'subject': '数学'})
    with core.get_db() as conn:
        conn.execute("INSERT INTO knowledge_gaps (user_id, subject, topic, gap_type, description, "
                     "suggested_question, session_date) VALUES (?, '物理', '慣性', 'vague', '説明', '問い', '2026-10-20')",
                     (uid,))
        conn.commit()
        assignment_id = conn.execute('SELECT id FROM assignments').fetchone()['id']
        gap_id = conn.execute('SELECT id FROM knowledge_gaps').fetchone()['id']

    # 課題でも復習でもない普通の会話を終えて、課題・苦手のIDを勝手に付けて送る
    key = _start(student_client, '物理').get_json()['session_key']
    _finish(student_client, key, monkeypatch)
    student_client.post('/api/complete', json={
        'session_key': key, 'assignment_id': assignment_id, 'gap_id': gap_id,
    })

    with core.get_db() as conn:
        assert conn.execute('SELECT status FROM assignments WHERE id = ?', (assignment_id,)).fetchone()['status'] == 'open'
        assert conn.execute('SELECT status FROM knowledge_gaps WHERE id = ?', (gap_id,)).fetchone()['status'] == 'open'


def test_assignment_is_completed_from_server_side_state(student_client, teacher_client, monkeypatch):
    uid = _user_id(student_client)
    teacher_client.post('/api/dashboard/assignments', json={'student_ids': [uid], 'subject': '数学'})
    with core.get_db() as conn:
        assignment_id = conn.execute('SELECT id FROM assignments').fetchone()['id']

    key = _start(student_client, assignment_id=assignment_id).get_json()['session_key']
    _finish(student_client, key, monkeypatch)
    # ブラウザが課題IDを送り忘れても、サーバーが覚えているので完了になる
    assert student_client.post('/api/complete', json={'session_key': key}).status_code == 200

    with core.get_db() as conn:
        assert conn.execute('SELECT status FROM assignments WHERE id = ?', (assignment_id,)).fetchone()['status'] == 'completed'


def test_restored_finished_conversation_cannot_continue(student_client, monkeypatch):
    """サーバー再起動でメモリが消えても、完了済みの会話は「終わった会話」として復元される"""
    key = _start(student_client).get_json()['session_key']
    _finish(student_client, key, monkeypatch)
    core.chat_sessions.clear()  # 再起動を再現

    resp = student_client.post('/api/chat', json={'message': 'まだ話したい', 'session_key': key})
    assert resp.status_code == 400
    assert _session_log_count(_user_id(student_client)) == 1


# =====================================================
# Webhook: checkout.session.completed
# =====================================================
def _checkout_completed(user_id, status='complete', mode='subscription'):
    return {
        'type': 'checkout.session.completed',
        'data': {'object': {'id': 'cs_test', 'object': 'checkout.session', 'mode': mode, 'status': status,
                            'client_reference_id': str(user_id), 'customer': 'cus_new', 'subscription': 'sub_new'}},
    }


def _subscription_status(email='test_user@example.com'):
    with core.get_db() as conn:
        return conn.execute('SELECT subscription_status, stripe_customer_id FROM users WHERE email = ?',
                            (email,)).fetchone()


def test_checkout_completed_webhook_activates_user_without_returning_to_the_site(student_client, monkeypatch):
    monkeypatch.delenv('RENDER', raising=False)
    monkeypatch.setattr(core, 'STRIPE_WEBHOOK_SECRET', None)
    uid = _user_id(student_client)

    resp = student_client.post('/api/billing/webhook', json=_checkout_completed(uid))
    assert resp.status_code == 200
    row = _subscription_status()
    assert row['subscription_status'] == 'active'
    assert row['stripe_customer_id'] == 'cus_new'

    # 顧客IDが保存されたので、その後の解約通知も正しくこの人に届く
    student_client.post('/api/billing/webhook', json={
        'type': 'customer.subscription.deleted',
        'data': {'object': {'id': 'sub_new', 'customer': 'cus_new', 'status': 'canceled'}},
    })
    assert _subscription_status()['subscription_status'] == 'free'


def test_unfinished_checkout_does_not_activate(student_client, monkeypatch):
    monkeypatch.delenv('RENDER', raising=False)
    monkeypatch.setattr(core, 'STRIPE_WEBHOOK_SECRET', None)
    uid = _user_id(student_client)
    student_client.post('/api/billing/webhook', json=_checkout_completed(uid, status='open'))
    student_client.post('/api/billing/webhook', json=_checkout_completed(uid, mode='payment'))
    assert _subscription_status()['subscription_status'] == 'free'


# =====================================================
# 本番の既定値チェック
# =====================================================
def test_secret_key_must_be_set_in_production():
    with pytest.raises(RuntimeError):
        app_module.load_secret_key({}, is_production=True)
    with pytest.raises(RuntimeError):
        app_module.load_secret_key({'FLASK_SECRET_KEY': app_module.DEV_SECRET_KEY}, is_production=True)
    assert app_module.load_secret_key({'FLASK_SECRET_KEY': 'abc123'}, is_production=True) == 'abc123'
    # ローカル開発では未設定でも動く
    assert app_module.load_secret_key({}, is_production=False) == app_module.DEV_SECRET_KEY


def test_teacher_signup_is_blocked_in_production_with_the_default_code(client, monkeypatch):
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setattr(core, 'TEACHER_CODE', core.DEFAULT_TEACHER_CODE)
    resp = client.post('/api/signup', json=_signup_payload(
        email='t@example.com', teacher_code=core.DEFAULT_TEACHER_CODE))
    assert resp.status_code == 503

    # 生徒の登録は止めない
    resp = client.post('/api/signup', json=_signup_payload(email='s@example.com'))
    assert resp.status_code == 200
    assert resp.get_json()['role'] == 'student'


def test_teacher_signup_works_in_production_with_a_custom_code(client, monkeypatch):
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setattr(core, 'TEACHER_CODE', 'MY_OWN_SECRET_CODE')
    resp = client.post('/api/signup', json=_signup_payload(
        email='t@example.com', teacher_code='MY_OWN_SECRET_CODE'))
    assert resp.status_code == 200
    assert resp.get_json()['role'] == 'teacher'


# =====================================================
# gunicornの設定
# =====================================================
def test_gunicorn_runs_one_process_with_threads_and_a_long_timeout():
    conf = runpy.run_path('gunicorn.conf.py')
    assert conf['workers'] == 1          # 会話をメモリに持っているので1プロセス
    assert conf['worker_class'] == 'gthread'
    assert conf['threads'] >= 4          # AI待ちの間も他の人を受け付ける
    assert conf['timeout'] >= 60         # モデル切り替えが重なっても打ち切られない
