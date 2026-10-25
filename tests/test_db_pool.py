# -*- coding: utf-8 -*-
"""DB接続プール（db.ConnectionPool）と、返却漏れが無いことのテスト。"""
import sqlite3
import threading

import pytest

import core
import db as db_module
from conftest import FakeChatSession, _signup_payload


class _Conn:
    """プール単体テスト用の最小の接続"""
    def __init__(self, n):
        self.n = n
        self.closed = 0

    def close(self):
        self.closed = 1


def _counting_connect():
    made = []

    def connect():
        conn = _Conn(len(made))
        made.append(conn)
        return conn
    return connect, made


# =====================================================
# プール単体
# =====================================================
def test_released_connection_is_reused():
    connect, made = _counting_connect()
    pool = db_module.ConnectionPool(connect, max_size=3)
    a = pool.acquire()
    pool.release(a)
    b = pool.acquire()
    assert b is a
    assert len(made) == 1
    pool.release(b)
    assert pool.stats() == {'in_use': 0, 'idle': 1, 'max_size': 3}


def test_waits_then_times_out_when_all_connections_are_in_use():
    connect, _ = _counting_connect()
    pool = db_module.ConnectionPool(connect, max_size=2, acquire_timeout=0.2)
    pool.acquire()
    pool.acquire()
    with pytest.raises(db_module.PoolTimeout):
        pool.acquire()


def test_released_slot_lets_a_waiting_thread_continue():
    connect, _ = _counting_connect()
    pool = db_module.ConnectionPool(connect, max_size=1, acquire_timeout=5)
    first = pool.acquire()
    got = []
    t = threading.Thread(target=lambda: got.append(pool.acquire()))
    t.start()
    pool.release(first)
    t.join(timeout=5)
    assert got == [first]


def test_discarded_and_closed_connections_are_not_reused():
    connect, made = _counting_connect()
    pool = db_module.ConnectionPool(connect, max_size=3)
    a = pool.acquire()
    pool.release(a, discard=True)
    assert a.closed
    b = pool.acquire()
    b.closed = 1            # 使っている間にサーバー側で切れた
    pool.release(b)
    c = pool.acquire()
    assert c is not a and c is not b
    assert len(made) == 3


def test_long_idle_connection_is_checked_and_replaced_if_dead():
    now = [0.0]
    connect, made = _counting_connect()
    alive = {'ok': False}
    pool = db_module.ConnectionPool(connect, max_size=2, max_idle_seconds=60,
                                    is_alive=lambda conn: alive['ok'], clock=lambda: now[0])
    a = pool.acquire()
    pool.release(a)

    now[0] = 30            # 少しだけ空いた接続は確認せずに使い回す
    assert pool.acquire() is a
    pool.release(a)

    now[0] = 200           # 長く空いていて、確認したら切れていた → 捨てて作り直す
    b = pool.acquire()
    assert b is not a and a.closed
    assert len(made) == 2


def test_many_threads_never_exceed_max_and_leave_nothing_in_use():
    connect, made = _counting_connect()
    pool = db_module.ConnectionPool(connect, max_size=4, acquire_timeout=10)
    peak = {'v': 0}
    lock = threading.Lock()

    def worker():
        for _ in range(50):
            conn = pool.acquire()
            with lock:
                peak['v'] = max(peak['v'], pool.stats()['in_use'])
            pool.release(conn)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert pool.stats()['in_use'] == 0
    assert peak['v'] <= 4
    assert len(made) <= 4


# =====================================================
# _PGConnection（with文での返却）
# =====================================================
def test_with_block_commits_and_returns_connection(pg_path):
    with db_module.get_db() as conn:
        conn.execute('SELECT 1')
    assert pg_path.pool.stats()['in_use'] == 0
    assert pg_path.created[0].commits == 1


def test_error_inside_with_block_rolls_back_and_still_returns(pg_path):
    with pytest.raises(ValueError):
        with db_module.get_db() as conn:
            conn.execute("INSERT INTO schools (name, join_code) VALUES ('x', 'ROLLBACK_ME')")
            raise ValueError('途中で失敗')
    assert pg_path.pool.stats()['in_use'] == 0
    with db_module.get_db() as conn:
        assert conn.execute("SELECT id FROM schools WHERE join_code = 'ROLLBACK_ME'").fetchone() is None


def test_connection_whose_commit_fails_is_discarded(pg_path):
    conn = db_module.get_db()
    raw = conn._conn

    def broken_commit():
        raise RuntimeError('通信が切れた')
    raw.commit = broken_commit
    with pytest.raises(RuntimeError):
        with conn:
            pass
    assert raw.closed
    assert pg_path.pool.stats() == {'in_use': 0, 'idle': 0, 'max_size': 5}


# =====================================================
# アプリ全体を Postgres の経路で動かしても、接続が漏れない
# =====================================================
def test_full_app_flow_returns_every_connection(pg_path, app, monkeypatch):
    client = app.app.test_client()
    assert client.post('/api/signup', json=_signup_payload()).status_code == 200
    teacher = app.app.test_client()
    assert teacher.post('/api/signup', json=_signup_payload(
        name='先生', email='t@example.com', teacher_code=core.TEACHER_CODE)).status_code == 200

    # プールの上限（5）より多いリクエストを、正常系・エラー系まぜて送る
    for subject in ['物理', '数学', '英語']:
        key = client.post('/api/start', json={'subject': subject, 'difficulty': '大学受験'}).get_json()['session_key']
        client.post('/api/chat', json={'message': '説明', 'session_key': key})
        client.post('/api/complete', json={'session_key': key})            # まだ終わっていない → 400
    assert client.post('/api/start', json={'subject': '化学'}).status_code == 403   # ライフ切れ
    monkeypatch.setattr(FakeChatSession, 'default_reply', '納得！【会話終了】')
    client.post('/api/chat', json={'message': '説明', 'session_key': key})
    assert client.post('/api/complete', json={'session_key': key}).status_code == 200
    for path in ['/api/me', '/api/history', '/api/gaps', '/api/mypage', '/api/streak', '/api/assignments']:
        assert client.get(path).status_code == 200, path
    for path in ['/api/dashboard', '/api/dashboard/usage']:
        assert teacher.get(path).status_code == 200, path
    assert client.post('/api/login', json={'email': 'test_user@example.com', 'password': 'wrong'}).status_code == 401
    assert client.get('/api/health').status_code == 200

    stats = pg_path.pool.stats()
    assert stats['in_use'] == 0
    assert len(pg_path.created) <= stats['max_size']   # 使い回されていて、上限を超えて作っていない


# =====================================================
# SQLite（開発環境）
# =====================================================
def test_sqlite_connection_is_closed_after_with_block(app):
    with db_module.get_db() as conn:
        conn.execute('SELECT 1')
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute('SELECT 1')
