# -*- coding: utf-8 -*-
"""つまずきマップ（/api/dashboard/stumble-map）のテスト。"""
import core
from conftest import _signup_payload

URL = '/api/dashboard/stumble-map'


def _student(app, n):
    c = app.app.test_client()
    c.post('/api/signup', json=_signup_payload(email=f's{n}@example.com', name=f'生徒{n}'))
    return c.get('/api/me').get_json()['id']


def _gap(user_id, subject, topic, status='open', gap_type='vague'):
    with core.get_db() as conn:
        conn.execute(
            'INSERT INTO knowledge_gaps (user_id, subject, topic, gap_type, description, suggested_question, '
            "session_date, status) VALUES (?, ?, ?, ?, '説明', '問い', '2026-10-20', ?)",
            (user_id, subject, topic, gap_type, status))
        conn.commit()


def _studied(user_id, subject, topic):
    core.save_analysis(user_id, subject, {'topic': topic, 'understanding_score': 50, 'gaps': []}, '2026-10-25')


def test_requires_teacher(app, client, student_client):
    assert client.get(URL).status_code == 401
    assert student_client.get(URL).status_code == 403


def test_empty_map(teacher_client):
    data = teacher_client.get(URL).get_json()
    assert data == {'total_students': 0, 'topics': [], 'more': 0}


def test_rate_is_share_of_students_with_unresolved_gaps(app, teacher_client):
    a, b, c, d = (_student(app, i) for i in range(4))
    _gap(a, '物理', '慣性の法則')
    _gap(a, '物理', '慣性の法則', gap_type='misconception')   # 同じ生徒の2つ目の穴は1人として数える
    _gap(b, '物理', '慣性の法則')
    _gap(c, '物理', '慣性の法則', status='resolved')          # 解決済みは数えない
    _gap(c, '数学', '条件付き確率')
    for s in (a, b, c, d):
        _studied(s, '物理', '慣性の法則')

    data = teacher_client.get(URL).get_json()
    assert data['total_students'] == 4
    first = data['topics'][0]
    assert (first['subject'], first['topic']) == ('物理', '慣性の法則')
    assert first['students'] == 2
    assert first['rate'] == 50
    assert first['studied'] == 4
    assert [t['topic'] for t in data['topics']] == ['慣性の法則', '条件付き確率']


def test_other_schools_and_teachers_are_excluded(app, teacher_client):
    mine = _student(app, 1)
    other = _student(app, 2)
    with core.get_db() as conn:
        conn.execute("INSERT INTO schools (name, join_code) VALUES ('別の塾', 'OTHER')")
        school = conn.execute("SELECT id FROM schools WHERE join_code = 'OTHER'").fetchone()['id']
        conn.execute('UPDATE users SET school_id = ? WHERE id = ?', (school, other))
        conn.commit()
    teacher_id = teacher_client.get('/api/me').get_json()['id']
    _gap(mine, '化学', 'モル濃度')
    _gap(other, '化学', '酸化還元')
    _gap(teacher_id, '化学', '電池')

    data = teacher_client.get(URL).get_json()
    assert data['total_students'] == 1
    assert [t['topic'] for t in data['topics']] == ['モル濃度']
    assert data['topics'][0]['rate'] == 100


def test_only_the_top_topics_are_returned(app, teacher_client):
    sid = _student(app, 1)
    for i in range(core.STUMBLE_MAP_LIMIT + 3):
        _gap(sid, '英語', f'文法{i:02d}')
    data = teacher_client.get(URL).get_json()
    assert len(data['topics']) == core.STUMBLE_MAP_LIMIT
    assert data['more'] == 3


def test_works_through_the_postgres_code_path(pg_path, app):
    teacher = app.app.test_client()
    teacher.post('/api/signup', json=_signup_payload(email='t@example.com', teacher_code=core.TEACHER_CODE))
    sid = _student(app, 1)
    _gap(sid, '生物', '光合成')
    data = teacher.get(URL).get_json()
    assert data['topics'][0]['topic'] == '光合成'
    assert pg_path.pool.stats()['in_use'] == 0
