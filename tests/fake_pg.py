# -*- coding: utf-8 -*-
"""テスト用の「偽のPostgres接続」。

手元にPostgresが無くても、アプリを本番と同じ「Postgres用の経路」（db.get_db() → 接続プール →
_PGConnection）で動かせるようにする。中身のデータはSQLiteに保存するが、外から見た振る舞いは
psycopg2に合わせてある:
  - SQLのプレースホルダは %s
  - 行は辞書で返る（RealDictCursor相当）
  - TIMESTAMP列は文字列ではなく datetime で返る（本番だけで起きた不具合の再発防止）
  - closed 属性・commit/rollback/close を持つ
"""
import sqlite3
from datetime import datetime

sqlite3.register_converter('TIMESTAMP', lambda raw: datetime.fromisoformat(raw.decode()))


class FakePGCursor:
    def __init__(self, sqlite_conn):
        self._sqlite = sqlite_conn
        self._cur = None

    def execute(self, sql, params=()):
        self._cur = self._sqlite.execute(sql.replace('%s', '?'), tuple(params))

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]

    @property
    def rowcount(self):
        return self._cur.rowcount

    def close(self):
        pass


class FakePGConnection:
    def __init__(self, path):
        self._sqlite = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
        self._sqlite.row_factory = sqlite3.Row
        self.closed = 0
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        if self.closed:
            raise RuntimeError('connection already closed')
        return FakePGCursor(self._sqlite)

    def commit(self):
        self.commits += 1
        self._sqlite.commit()

    def rollback(self):
        self.rollbacks += 1
        self._sqlite.rollback()

    def close(self):
        if not self.closed:
            self.closed = 1
            self._sqlite.close()
