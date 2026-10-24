# -*- coding: utf-8 -*-
"""本番のPostgresでだけ起きた不具合の再発防止テスト。

PostgresはTIMESTAMP列をdatetimeオブジェクトで返すが、SQLiteは文字列で返す。
ローカルのSQLiteだけでテストしていると、「文字列だと思って fromisoformat する」ようなコードが
本番でだけ落ちる。ここではSQLiteにも「TIMESTAMP列をdatetimeで返す」設定をして、Postgresと同じ状況を再現する。
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

import core
import db as db_module

sqlite3.register_converter('TIMESTAMP', lambda raw: datetime.fromisoformat(raw.decode()))


@pytest.fixture
def pg_like_db(app, monkeypatch):
    def get_db():
        conn = sqlite3.connect(db_module.DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        return conn
    monkeypatch.setattr(core, 'get_db', get_db)
    return get_db


def _start(client, subject='物理'):
    return client.post('/api/start', json={'subject': subject, 'difficulty': '大学受験'})


def _stored_anchor(user_id):
    with core.get_db() as conn:
        return conn.execute('SELECT last_life_lost_at FROM users WHERE id = ?', (user_id,)).fetchone()[0]


def test_the_fake_really_returns_datetime_objects(pg_like_db, student_client):
    # このテスト自体の前提確認: タイマーの起点が文字列ではなくdatetimeで返ってくること
    _start(student_client)
    me_id = student_client.get('/api/me').get_json()['id']
    assert isinstance(_stored_anchor(me_id), datetime)


def test_me_still_works_after_a_life_is_used(pg_like_db, student_client):
    # 以前はここで /api/me が500になり、マイページが「プラン情報の読み込みに失敗」、アプリはログイン画面へ戻された
    assert _start(student_client).status_code == 200
    resp = student_client.get('/api/me')
    assert resp.status_code == 200
    lives = resp.get_json()['lives_status']
    assert lives['lives'] == 2
    assert lives['next_recovery_seconds'] > 0


def test_can_keep_starting_sessions_with_a_stored_timer(pg_like_db, student_client):
    for subject in ['物理', '数学', '英語']:
        assert _start(student_client, subject).status_code == 200


def test_blocked_message_with_remaining_minutes_works(pg_like_db, student_client):
    for subject in ['物理', '数学', '英語']:
        _start(student_client, subject)
    resp = _start(student_client, '化学')
    assert resp.status_code == 403
    assert '分' in resp.get_json()['error']
    assert student_client.get('/api/me').status_code == 200


def test_refund_works_with_a_stored_timer(pg_like_db, student_client):
    _start(student_client)
    _start(student_client, '数学')
    me_id = student_client.get('/api/me').get_json()['id']
    core.refund_life(me_id)
    assert student_client.get('/api/me').get_json()['lives_status']['lives'] == 2


def test_recover_lives_accepts_datetime_and_string_anchors():
    three_hours_ago = datetime.now() - timedelta(hours=3)
    assert core._recover_lives(0, three_hours_ago)[0] == 1
    assert core._recover_lives(0, three_hours_ago.isoformat())[0] == 1


def test_lives_anchor_handles_timezone_aware_values():
    from datetime import timezone
    aware = datetime.now(timezone.utc) - timedelta(minutes=30)
    anchor = core._lives_anchor(aware)
    assert anchor.tzinfo is None
    assert abs((datetime.now() - anchor) - timedelta(minutes=30)) < timedelta(seconds=5)


# ---------- 分析結果の保存（Postgresで「曖昧な列」エラーになった書き方の再発防止） ----------
ANALYSIS = {
    'topic': '積分法', 'understanding_score': 70, 'summary': 'x',
    'gaps': [{'gap_type': 'missing_link', 'description': '面積の公式の前提', 'evidence': 'e',
              'suggested_question': 'なぜ差が2次式？'}],
}


def test_save_analysis_twice_counts_sessions_and_does_not_duplicate_gaps(app, student_client):
    me_id = student_client.get('/api/me').get_json()['id']
    core.save_analysis(me_id, '数学', ANALYSIS, '2026-10-25')
    core.save_analysis(me_id, '数学', {**ANALYSIS, 'understanding_score': 90}, '2026-10-26')

    with core.get_db() as conn:
        progress = conn.execute(
            "SELECT understanding, sessions_count, last_studied FROM topic_progress WHERE user_id = ?",
            (me_id,)).fetchall()
        gaps = conn.execute("SELECT COUNT(*) FROM knowledge_gaps WHERE user_id = ?", (me_id,)).fetchone()[0]
    assert len(progress) == 1
    assert progress[0]['sessions_count'] == 2
    assert progress[0]['understanding'] == 90
    assert progress[0]['last_studied'] == '2026-10-26'
    assert gaps == 1  # 同じ未解決の弱点は重複登録しない


def test_upsert_sql_qualifies_the_counter_column():
    # Postgresは、テーブル名なしの sessions_count を曖昧としてエラーにする。書き換えで戻さないための確認
    import inspect
    source = inspect.getsource(core.save_analysis)
    assert 'sessions_count = topic_progress.sessions_count + 1' in source
