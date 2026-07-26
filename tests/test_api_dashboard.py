# -*- coding: utf-8 -*-
"""先生ダッシュボードAPIの権限チェックと基本的な戻り値のテスト"""


def test_dashboard_requires_login(client):
    assert client.get('/api/dashboard').status_code == 401


def test_dashboard_rejects_student(student_client):
    resp = student_client.get('/api/dashboard')
    assert resp.status_code == 403


def test_dashboard_returns_students_for_teacher(teacher_client, student_client):
    resp = teacher_client.get('/api/dashboard')
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['summary']['total_students'] == 1
    assert data['students'][0]['email'] == 'test_user@example.com'
    assert data['students'][0]['subscription_status'] == 'free'


def test_dashboard_student_detail_404_for_unknown_student(teacher_client):
    resp = teacher_client.get('/api/dashboard/student/99999')
    assert resp.status_code == 404


def test_dashboard_student_detail_returns_student_data(teacher_client, student_client):
    me = student_client.get('/api/me').get_json()
    resp = teacher_client.get(f"/api/dashboard/student/{me['id']}")
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['student']['email'] == 'test_user@example.com'
    assert data['gaps'] == []


def test_create_assignment_requires_teacher(student_client):
    resp = student_client.post('/api/dashboard/assignments', json={
        'student_id': 1, 'subject': '物理',
    })
    assert resp.status_code == 403


def test_create_assignment_success_and_shows_up_for_student(teacher_client, student_client):
    me = student_client.get('/api/me').get_json()
    resp = teacher_client.post('/api/dashboard/assignments', json={
        'student_ids': [me['id']], 'subject': '物理', 'unit': '力学',
    })
    assert resp.status_code == 200
    assert resp.get_json()['count'] == 1

    assignments = student_client.get('/api/assignments').get_json()['assignments']
    assert len(assignments) == 1
    assert assignments[0]['subject'] == '物理'
    assert assignments[0]['unit'] == '力学'


def test_create_assignment_rejects_unknown_subject(teacher_client, student_client):
    me = student_client.get('/api/me').get_json()
    resp = teacher_client.post('/api/dashboard/assignments', json={
        'student_ids': [me['id']], 'subject': '存在しない科目',
    })
    assert resp.status_code == 400


def test_teacher_reset_password_issues_temp_password(teacher_client, student_client):
    me = student_client.get('/api/me').get_json()
    resp = teacher_client.post('/api/dashboard/reset-password', json={'student_id': me['id']})
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['temp_password'].startswith('Feyn')

    student_client.post('/api/logout')
    login = student_client.post('/api/login', json={
        'email': 'test_user@example.com', 'password': data['temp_password'],
    })
    assert login.status_code == 200
