# =====================================================
# データベース接続層
#
# 開発時はSQLite（feyn.db）、本番（Render等）ではDATABASE_URL環境変数を
# 設定するとPostgreSQL（Supabase等）を使う。Renderの無料プランはローカル
# ディスクが再デプロイ・再起動のたびに消えるため、本番では永続DBが必須。
#
# app.py側のコードは get_db() が返すオブジェクトを
#   with get_db() as conn:
#       row = conn.execute('SELECT ... WHERE x = ?', (x,)).fetchone()
# という sqlite3.Connection と同じ流儀で使えることを前提にしている。
# ここではPostgres利用時も同じインターフェースになるよう薄いラッパーを被せる。
# =====================================================
import os
import sqlite3
import threading
import time

DB_PATH = os.path.join(os.path.dirname(__file__), 'feyn.db')

# Renderなどの環境変数入力欄に貼り付ける際、前後の空白や引用符が
# 誤って混入しやすいため、接続文字列として無効な文字を自動で取り除く
_raw_database_url = os.environ.get('DATABASE_URL', '').strip()
if len(_raw_database_url) >= 2 and _raw_database_url[0] == _raw_database_url[-1] and _raw_database_url[0] in ('"', "'"):
    _raw_database_url = _raw_database_url[1:-1].strip()
DATABASE_URL = _raw_database_url or None
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    if not DATABASE_URL.startswith(('postgres://', 'postgresql://')):
        raise RuntimeError(
            'DATABASE_URLが postgresql:// または postgres:// で始まっていません。'
            'Supabaseの接続文字列をそのまま（引用符なしで）環境変数に設定してください。'
            f' 現在の先頭部分: {DATABASE_URL[:20]!r}'
        )
    import psycopg2
    import psycopg2.extras
    IntegrityError = psycopg2.IntegrityError
    # SQLiteの AUTOINCREMENT に相当するPostgresの主キー定義
    PK = 'SERIAL PRIMARY KEY'
else:
    IntegrityError = sqlite3.IntegrityError
    PK = 'INTEGER PRIMARY KEY AUTOINCREMENT'


def _to_pg_params(sql):
    """SQLiteの '?' プレースホルダをPostgresの '%s' に変換する"""
    return sql.replace('?', '%s')


class _PGCursor:
    """sqlite3のCursorのように execute() の戻り値へ直接 fetchone()/fetchall() を呼べるようにする"""

    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount


class PoolTimeout(RuntimeError):
    """プールの接続がすべて使用中で、待っても空かなかった"""


class ConnectionPool:
    """DB接続を使い回すための小さなプール（スレッド対応）。

    以前は get_db() のたびにSupabaseへ新しく接続していた。Postgresへの接続は、
    暗号化（TLS）の準備などで1回あたり数十〜数百ミリ秒かかるので、1回の画面表示で何度も
    DBを使うと、それだけで体感の遅さになる。ここで一度作った接続を取っておき、次に使い回す。

    - gunicornは1プロセス・複数スレッドで動かしている（gunicorn.conf.py）。プールはプロセスに1つ
    - 同時に使える数は max_size まで。全部使用中なら、空くまで最大 acquire_timeout 秒待つ
    - しばらく使っていなかった接続は、Supabase側で切られていることがあるので、使う前に軽く確認する
    - 返すときに壊れていた接続は捨て、次は新しく作る
    """

    def __init__(self, connect, max_size=10, max_idle_seconds=60, acquire_timeout=30,
                 is_alive=None, clock=time.monotonic):
        self._connect = connect
        self._max_size = max_size
        self._max_idle_seconds = max_idle_seconds
        self._acquire_timeout = acquire_timeout
        self._is_alive = is_alive or (lambda conn: True)
        self._clock = clock
        self._idle = []            # [(接続, 最後に返された時刻)]。後ろほど新しい
        self._in_use = 0
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(max_size)

    def acquire(self):
        if not self._slots.acquire(timeout=self._acquire_timeout):
            raise PoolTimeout('データベースが混み合っています。少し待ってからもう一度試してください。')
        try:
            conn = self._take_idle_or_connect()
        except Exception:
            self._slots.release()
            raise
        with self._lock:
            self._in_use += 1
        return conn

    def _take_idle_or_connect(self):
        while True:
            with self._lock:
                item = self._idle.pop() if self._idle else None
            if item is None:
                return self._connect()
            conn, returned_at = item
            if getattr(conn, 'closed', False):
                continue
            if self._clock() - returned_at > self._max_idle_seconds and not self._is_alive(conn):
                _close_quietly(conn)
                continue
            return conn

    def release(self, conn, discard=False):
        with self._lock:
            self._in_use -= 1
        try:
            if discard or getattr(conn, 'closed', False):
                _close_quietly(conn)
            else:
                with self._lock:
                    self._idle.append((conn, self._clock()))
        finally:
            self._slots.release()

    def stats(self):
        """テストや死活確認用。使用中の数と、使い回し待ちの数"""
        with self._lock:
            return {'in_use': self._in_use, 'idle': len(self._idle), 'max_size': self._max_size}

    def close_all(self):
        with self._lock:
            idle, self._idle = self._idle, []
        for conn, _ in idle:
            _close_quietly(conn)


def _close_quietly(conn):
    try:
        conn.close()
    except Exception:
        pass


def _pg_is_alive(conn):
    """しばらく使っていなかった接続が、まだ使えるかを確かめる"""
    try:
        cur = conn.cursor()
        cur.execute('SELECT 1')
        cur.fetchone()
        cur.close()
        conn.rollback()
        return True
    except Exception:
        return False


class _PGConnection:
    """sqlite3.Connectionの使い勝手（execute()の直接呼び出し・with文でのcommit/rollback）を再現する。
    with を抜けるときに、接続を閉じずにプールへ返す"""

    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool
        self._released = False

    def execute(self, sql, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(_to_pg_params(sql), params)
        return _PGCursor(cur)

    def commit(self):
        self._conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        broken = False
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        except Exception:
            # commit/rollback自体に失敗した接続（通信切れなど）は使い回さない
            broken = True
            if exc_type is None:
                raise
        finally:
            self._release(discard=broken)
        return False

    def _release(self, discard=False):
        if not self._released:
            self._released = True
            self._pool.release(self._conn, discard=discard)

    def __del__(self):
        # with を使わずに作られて放置された場合でも、プールの枠を取りっぱなしにしない
        try:
            if not self._released:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                self._release(discard=False)
        except Exception:
            pass


class _ClosingSQLiteConnection(sqlite3.Connection):
    """with を抜けたら閉じるSQLite接続。
    標準の sqlite3.Connection は with を抜けてもcommitするだけで閉じず、閉じるのはガベージコレクション任せになる"""

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


# Postgresの接続プール（最初に使うときに作る。gunicornがプロセスを作った後に作られるようにするため）
POOL_SIZE = int(os.environ.get('DB_POOL_SIZE', '10'))
_pool = None
_pool_lock = threading.Lock()


def get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool(lambda: psycopg2.connect(DATABASE_URL),
                                       max_size=POOL_SIZE, is_alive=_pg_is_alive)
    return _pool


def get_db():
    if USE_POSTGRES:
        pool = get_pool()
        return _PGConnection(pool.acquire(), pool)
    conn = sqlite3.connect(DB_PATH, factory=_ClosingSQLiteConnection)
    conn.row_factory = sqlite3.Row
    return conn


def add_column_if_missing(conn, table, column_def):
    """既存テーブルへのカラム追加（マイグレーション）。Postgresは IF NOT EXISTS で安全に、
    SQLiteは ADD COLUMN IF NOT EXISTS 未対応なので try/except で吸収する。"""
    if USE_POSTGRES:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column_def}')
    else:
        try:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {column_def}')
        except Exception:
            pass
