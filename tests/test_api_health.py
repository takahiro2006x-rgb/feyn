# -*- coding: utf-8 -*-
"""死活確認用 /api/health のテスト"""
import core


def test_health_returns_ok_without_login(client):
    resp = client.get('/api/health')
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True}


def test_health_returns_503_when_database_is_unreachable(client, monkeypatch):
    def broken_db():
        raise RuntimeError('db is down')
    monkeypatch.setattr(core, 'get_db', broken_db)
    resp = client.get('/api/health')
    assert resp.status_code == 503
    assert resp.get_json() == {'ok': False}


def test_health_does_not_expose_internal_details(client, monkeypatch):
    monkeypatch.setattr(core, 'get_db', lambda: (_ for _ in ()).throw(RuntimeError('postgresql://user:secret@host/db')))
    resp = client.get('/api/health')
    assert 'secret' not in resp.get_data(as_text=True)
