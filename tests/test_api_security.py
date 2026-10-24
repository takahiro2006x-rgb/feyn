# -*- coding: utf-8 -*-
"""CSRF対策・クッキー設定・ログイン試行回数の制限のAPIテスト"""
import core

OK_USER = {'email': 'victim@example.com', 'password': 'testpass123'}


def _signup(client, **overrides):
    payload = {
        'name': 'テスト太郎', 'email': OK_USER['email'], 'password': OK_USER['password'],
        'security_question': '好きな食べ物は？', 'security_answer': 'apple',
    }
    payload.update(overrides)
    return client.post('/api/signup', json=payload)


def _login(client, password, ip=None, email=OK_USER['email']):
    kwargs = {'environ_overrides': {'REMOTE_ADDR': ip}} if ip else {}
    return client.post('/api/login', json={'email': email, 'password': password}, **kwargs)


# ---------- ログイン試行回数の制限 ----------
def test_login_is_blocked_after_five_wrong_passwords(client):
    _signup(client)
    client.post('/api/logout')

    for _ in range(5):
        assert _login(client, 'wrong-password').status_code == 401

    # 6回目は、正しいパスワードでも受け付けない
    blocked = _login(client, OK_USER['password'])
    assert blocked.status_code == 429
    assert '分後' in blocked.get_json()['error']


def test_block_is_per_ip_so_the_real_owner_can_still_log_in(client):
    _signup(client)
    client.post('/api/logout')

    for _ in range(5):
        _login(client, 'wrong-password', ip='203.0.113.9')   # 攻撃者のIP
    assert _login(client, OK_USER['password'], ip='203.0.113.9').status_code == 429

    # 本人は別のIPなのでログインできる（アカウントを巻き込んでロックされない）
    assert _login(client, OK_USER['password'], ip='198.51.100.7').status_code == 200


def test_successful_login_resets_the_failure_count(client):
    _signup(client)
    client.post('/api/logout')

    for _ in range(4):
        _login(client, 'wrong-password')
    assert _login(client, OK_USER['password']).status_code == 200
    client.post('/api/logout')

    for _ in range(4):
        assert _login(client, 'wrong-password').status_code == 401  # 数え直しなのでまだ制限されない


def test_on_render_the_cloudflare_client_ip_header_is_used(client, monkeypatch):
    monkeypatch.setenv('RENDER', 'true')  # 本番(Render)ではCloudflareの内側にいる
    _signup(client)
    client.post('/api/logout')

    def login_as(ip, password):
        return client.post('/api/login', json={'email': OK_USER['email'], 'password': password},
                           headers={'CF-Connecting-IP': ip})

    for _ in range(5):
        login_as('203.0.113.9', 'wrong-password')   # 攻撃者
    assert login_as('203.0.113.9', OK_USER['password']).status_code == 429
    # リクエスト元（remote_addr）は全員同じ共有IPに見えても、本人は別の利用者IPなので通る
    assert login_as('198.51.100.7', OK_USER['password']).status_code == 200


def test_client_ip_header_is_ignored_outside_production(client):
    # ローカルでは偽装されたヘッダーでIP別の制限を回避できない
    _signup(client)
    client.post('/api/logout')
    for i in range(5):
        client.post('/api/login', json={'email': OK_USER['email'], 'password': 'wrong'},
                    headers={'CF-Connecting-IP': f'203.0.113.{i}'})
    r = client.post('/api/login', json={'email': OK_USER['email'], 'password': OK_USER['password']},
                    headers={'CF-Connecting-IP': '203.0.113.200'})
    assert r.status_code == 429


def test_distributed_attack_on_one_account_is_blocked(client):
    _signup(client)
    client.post('/api/logout')

    # IPを変えながら同じアカウントを狙う（1つのIPあたりは制限に届かない）
    for i in range(30):
        _login(client, 'wrong-password', ip=f'203.0.113.{i}')
    assert _login(client, OK_USER['password'], ip='198.51.100.7').status_code == 429


def test_password_reset_answer_guessing_is_blocked(client):
    _signup(client)
    client.post('/api/logout')

    for _ in range(5):
        r = client.post('/api/reset-password', json={
            'email': OK_USER['email'], 'answer': 'wrong', 'new_password': 'newpass123'})
        assert r.status_code == 401

    r = client.post('/api/reset-password', json={
        'email': OK_USER['email'], 'answer': 'apple', 'new_password': 'newpass123'})
    assert r.status_code == 429  # 正しい答えでも、制限中は受け付けない


def test_teacher_code_guessing_is_blocked(client):
    for _ in range(20):
        r = _signup(client, email=f'x{_}@example.com', teacher_code='wrong-code')
        assert r.status_code == 400

    r = _signup(client, email='real-teacher@example.com', teacher_code=core.TEACHER_CODE)
    assert r.status_code == 429


# ---------- CSRF（別サイトからの送信を拒否） ----------
def test_cross_site_post_is_rejected(student_client):
    resp = student_client.post('/api/update-name', json={'name': 'ハッキング'},
                               headers={'Origin': 'https://evil.example.com'})
    assert resp.status_code == 403
    assert student_client.get('/api/me').get_json()['name'] == 'テスト太郎'  # 書き換わっていない


def test_same_site_post_is_accepted(student_client):
    resp = student_client.post('/api/update-name', json={'name': '新しい名前'},
                               headers={'Origin': 'http://localhost'})
    assert resp.status_code == 200


def test_post_without_origin_header_is_accepted(student_client):
    # curlやテストなどOriginを付けないリクエストは、ブラウザ経由のCSRFではないので通す
    assert student_client.post('/api/update-name', json={'name': 'no-origin'}).status_code == 200


def test_opaque_origin_is_rejected(student_client):
    resp = student_client.post('/api/update-name', json={'name': 'x'}, headers={'Origin': 'null'})
    assert resp.status_code == 403


def test_get_requests_are_not_affected_by_origin_check(student_client):
    resp = student_client.get('/api/me', headers={'Origin': 'https://evil.example.com'})
    assert resp.status_code == 200


def test_stripe_webhook_is_exempt_from_origin_check(client):
    event = {'type': 'customer.subscription.deleted',
             'data': {'object': {'id': 'sub_x', 'customer': 'cus_x', 'status': 'canceled'}}}
    resp = client.post('/api/billing/webhook', json=event, headers={'Origin': 'https://stripe.com'})
    assert resp.status_code == 200


# ---------- クッキーの設定 ----------
def test_session_cookie_has_samesite_lax_and_httponly(client):
    resp = _signup(client)
    cookie = resp.headers.get('Set-Cookie', '')
    assert 'SameSite=Lax' in cookie
    assert 'HttpOnly' in cookie
