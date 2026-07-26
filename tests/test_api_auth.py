# -*- coding: utf-8 -*-
"""認証系APIのテスト（/api/signup, /api/login, /api/logout, /api/me, パスワードリセット）"""
import core


def test_signup_creates_student_by_default(client):
    resp = client.post('/api/signup', json={
        'name': 'テスト太郎', 'email': 'a@example.com', 'password': 'testpass123',
        'security_question': '好きな食べ物は？', 'security_answer': 'apple',
    })
    assert resp.status_code == 200
    assert resp.get_json()['role'] == 'student'


def test_signup_with_correct_teacher_code_creates_teacher(client, app):
    resp = client.post('/api/signup', json={
        'name': 'テスト先生', 'email': 'b@example.com', 'password': 'testpass123',
        'security_question': '好きな食べ物は？', 'security_answer': 'apple',
        'teacher_code': core.TEACHER_CODE,
    })
    assert resp.status_code == 200
    assert resp.get_json()['role'] == 'teacher'


def test_signup_with_wrong_teacher_code_is_rejected(client):
    resp = client.post('/api/signup', json={
        'name': 'テスト太郎', 'email': 'c@example.com', 'password': 'testpass123',
        'security_question': '好きな食べ物は？', 'security_answer': 'apple',
        'teacher_code': 'wrong-code',
    })
    assert resp.status_code == 400


def test_signup_rejects_short_password(client):
    resp = client.post('/api/signup', json={
        'name': 'テスト太郎', 'email': 'd@example.com', 'password': 'short',
        'security_question': '好きな食べ物は？', 'security_answer': 'apple',
    })
    assert resp.status_code == 400


def test_signup_duplicate_email_returns_409(client):
    payload = {
        'name': 'テスト太郎', 'email': 'dup@example.com', 'password': 'testpass123',
        'security_question': '好きな食べ物は？', 'security_answer': 'apple',
    }
    assert client.post('/api/signup', json=payload).status_code == 200
    resp = client.post('/api/signup', json=payload)
    assert resp.status_code == 409


def test_login_with_correct_password_succeeds(client):
    client.post('/api/signup', json={
        'name': 'テスト太郎', 'email': 'e@example.com', 'password': 'testpass123',
        'security_question': '好きな食べ物は？', 'security_answer': 'apple',
    })
    client.post('/api/logout')
    resp = client.post('/api/login', json={'email': 'e@example.com', 'password': 'testpass123'})
    assert resp.status_code == 200


def test_login_with_wrong_password_fails(client):
    client.post('/api/signup', json={
        'name': 'テスト太郎', 'email': 'f@example.com', 'password': 'testpass123',
        'security_question': '好きな食べ物は？', 'security_answer': 'apple',
    })
    client.post('/api/logout')
    resp = client.post('/api/login', json={'email': 'f@example.com', 'password': 'wrongpass'})
    assert resp.status_code == 401


def test_me_requires_login(client):
    resp = client.get('/api/me')
    assert resp.status_code == 401


def test_me_returns_fresh_student_with_full_lives(student_client):
    resp = student_client.get('/api/me')
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['role'] == 'student'
    assert data['subscription_status'] == 'free'
    assert data['lives_status'] == {'lives': 3, 'max_lives': 3, 'next_recovery_seconds': None}


def test_me_returns_unlimited_lives_for_teacher(teacher_client):
    resp = teacher_client.get('/api/me')
    assert resp.get_json()['lives_status'] is None


def test_logout_clears_session(student_client):
    student_client.post('/api/logout')
    resp = student_client.get('/api/me')
    assert resp.status_code == 401


def test_password_reset_flow(client):
    client.post('/api/signup', json={
        'name': 'テスト太郎', 'email': 'reset@example.com', 'password': 'testpass123',
        'security_question': '好きな食べ物は？', 'security_answer': 'apple',
    })

    q_resp = client.post('/api/security-question', json={'email': 'reset@example.com'})
    assert q_resp.get_json()['question'] == '好きな食べ物は？'

    wrong = client.post('/api/reset-password', json={
        'email': 'reset@example.com', 'answer': 'banana', 'new_password': 'newpass123',
    })
    assert wrong.status_code == 401

    right = client.post('/api/reset-password', json={
        'email': 'reset@example.com', 'answer': 'apple', 'new_password': 'newpass123',
    })
    assert right.status_code == 200

    client.post('/api/logout')
    login = client.post('/api/login', json={'email': 'reset@example.com', 'password': 'newpass123'})
    assert login.status_code == 200


def test_security_question_lookup_does_not_leak_unknown_email(client):
    resp = client.post('/api/security-question', json={'email': 'nobody@example.com'})
    assert resp.status_code == 404
