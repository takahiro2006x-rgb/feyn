# -*- coding: utf-8 -*-
"""初回の使い方ガイド（オンボーディング）のAPIテスト"""
import core


def test_new_student_sees_onboarding(student_client):
    assert student_client.get('/api/me').get_json()['show_onboarding'] is True


def test_teacher_does_not_see_onboarding(teacher_client):
    assert teacher_client.get('/api/me').get_json()['show_onboarding'] is False


def test_completing_onboarding_hides_it_afterwards(student_client):
    resp = student_client.post('/api/onboarding/complete')
    assert resp.status_code == 200
    assert student_client.get('/api/me').get_json()['show_onboarding'] is False


def test_onboarding_state_survives_logout_and_login(student_client):
    student_client.post('/api/onboarding/complete')
    student_client.post('/api/logout')
    student_client.post('/api/login', json={'email': 'test_user@example.com', 'password': 'testpass123'})
    assert student_client.get('/api/me').get_json()['show_onboarding'] is False


def test_unseen_onboarding_is_shown_again_on_next_login(student_client):
    # 途中で閉じた（保存されなかった）場合は、次回もう一度出る
    student_client.post('/api/logout')
    student_client.post('/api/login', json={'email': 'test_user@example.com', 'password': 'testpass123'})
    assert student_client.get('/api/me').get_json()['show_onboarding'] is True


def test_complete_requires_login(client):
    assert client.post('/api/onboarding/complete').status_code == 401


def test_existing_users_are_not_shown_onboarding(client):
    # 導入前から居たユーザー（onboardedを指定せずに作られた行）には出さない
    with core.get_db() as conn:
        conn.execute("INSERT INTO users (email, name, password_hash, role) VALUES (?, ?, ?, ?)",
                     ('old@example.com', '古参', 'x', 'student'))
        conn.commit()
        row = conn.execute("SELECT onboarded FROM users WHERE email = 'old@example.com'").fetchone()
    assert row['onboarded'] == 1
