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

DB_PATH = os.path.join(os.path.dirname(__file__), 'feyn.db')
DATABASE_URL = os.environ.get('DATABASE_URL')
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
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


class _PGConnection:
    """sqlite3.Connectionの使い勝手（execute()の直接呼び出し・with文でのcommit/rollback）を再現する"""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(_to_pg_params(sql), params)
        return _PGCursor(cur)

    def commit(self):
        self._conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()
        return False


def get_db():
    if USE_POSTGRES:
        return _PGConnection(psycopg2.connect(DATABASE_URL))
    conn = sqlite3.connect(DB_PATH)
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
