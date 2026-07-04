# -*- coding: utf-8 -*-
"""db.py（SQLite/Postgres抽象化層）のテスト

DATABASE_URLの解釈はモジュール読み込み時に決まるため、importlib.reloadで
環境変数を切り替えながら検証する。実際のDB接続はしない（get_db()は呼ばない）。
"""
import importlib

import pytest

import db as db_module


def _reload_with_env(monkeypatch, database_url):
    if database_url is None:
        monkeypatch.delenv('DATABASE_URL', raising=False)
    else:
        monkeypatch.setenv('DATABASE_URL', database_url)
    importlib.reload(db_module)
    return db_module


@pytest.fixture(autouse=True)
def _restore_real_db_module():
    """テストがdbモジュールの状態を書き換えるので、他のテストへ影響しないよう毎回実環境に戻す"""
    yield
    importlib.reload(db_module)


def test_no_database_url_defaults_to_sqlite(monkeypatch):
    mod = _reload_with_env(monkeypatch, None)
    assert mod.USE_POSTGRES is False
    assert mod.PK == 'INTEGER PRIMARY KEY AUTOINCREMENT'


def test_database_url_with_surrounding_quotes_is_stripped(monkeypatch):
    # Renderの環境変数欄に貼り付ける際、誤って引用符ごと入れてしまうミスへの対策
    mod = _reload_with_env(monkeypatch, '"postgresql://user:pass@host:5432/db"')
    assert mod.USE_POSTGRES is True
    assert mod.DATABASE_URL == 'postgresql://user:pass@host:5432/db'
    assert mod.PK == 'SERIAL PRIMARY KEY'


def test_database_url_with_surrounding_whitespace_is_stripped(monkeypatch):
    mod = _reload_with_env(monkeypatch, '  postgresql://user:pass@host:5432/db  ')
    assert mod.DATABASE_URL == 'postgresql://user:pass@host:5432/db'


def test_database_url_missing_scheme_raises_clear_error(monkeypatch):
    with pytest.raises(RuntimeError, match='postgresql://'):
        _reload_with_env(monkeypatch, 'slbfqllnozbphhjbnoza')  # 実際に起きた事故の再現


def test_database_url_postgres_scheme_is_also_accepted(monkeypatch):
    mod = _reload_with_env(monkeypatch, 'postgres://user:pass@host:5432/db')
    assert mod.USE_POSTGRES is True


def test_to_pg_params_translates_question_marks():
    assert db_module._to_pg_params('SELECT * FROM t WHERE a = ? AND b = ?') == \
        'SELECT * FROM t WHERE a = %s AND b = %s'


def test_to_pg_params_no_placeholders_unchanged():
    assert db_module._to_pg_params('SELECT * FROM t') == 'SELECT * FROM t'


def test_add_column_if_missing_uses_if_not_exists_on_postgres(monkeypatch):
    mod = _reload_with_env(monkeypatch, 'postgresql://user:pass@host:5432/db')
    calls = []
    fake_conn = type('FakeConn', (), {'execute': lambda self, sql: calls.append(sql)})()
    mod.add_column_if_missing(fake_conn, 'users', 'nickname TEXT')
    assert calls == ['ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname TEXT']


def test_add_column_if_missing_swallows_duplicate_column_error_on_sqlite(monkeypatch):
    mod = _reload_with_env(monkeypatch, None)

    class _FailingConn:
        def execute(self, sql):
            raise Exception('duplicate column name: nickname')

    # SQLiteは ADD COLUMN IF NOT EXISTS 非対応なので例外を握りつぶす設計になっている
    mod.add_column_if_missing(_FailingConn(), 'users', 'nickname TEXT')
