# -*- coding: utf-8 -*-
"""先生向けCSV出力（/api/dashboard/students.csv）のテスト。認可を中心に確かめる。"""
import csv
import io

import core
from conftest import FakeChatSession, _signup_payload

URL = '/api/dashboard/students.csv'


def _uid(client):
    return client.get('/api/me').get_json()['id']


def _rows(resp):
    text = resp.get_data().decode('utf-8')
    assert text.startswith('﻿')                    # Excelで文字化けしないBOM付き
    return list(csv.reader(io.StringIO(text.lstrip('﻿'))))


def _by_id(rows):
    header = rows[0]
    return {int(r[0]): dict(zip(header, r)) for r in rows[1:]}


# =====================================================
# 認可
# =====================================================
def test_requires_login(client):
    assert client.get(URL).status_code == 401


def test_students_cannot_download(student_client):
    assert student_client.get(URL).status_code == 403


def test_only_students_of_the_teachers_own_school_are_included(app, student_client, teacher_client):
    other = app.app.test_client()
    other.post('/api/signup', json=_signup_payload(email='other@example.com', name='別の塾の生徒'))
    other_id = _uid(other)
    with core.get_db() as conn:
        conn.execute("INSERT INTO schools (name, join_code) VALUES ('別の塾', 'OTHER')")
        school = conn.execute("SELECT id FROM schools WHERE join_code = 'OTHER'").fetchone()['id']
        conn.execute('UPDATE users SET school_id = ? WHERE id = ?', (school, other_id))
        conn.commit()

    rows = _by_id(_rows(teacher_client.get(URL)))
    assert set(rows) == {_uid(student_client)}           # 先生自身・別の組織の生徒は入らない


def test_download_headers(teacher_client):
    resp = teacher_client.get(URL)
    assert resp.status_code == 200
    assert resp.headers['Content-Type'] == 'text/csv; charset=utf-8'
    assert resp.headers['Content-Disposition'].startswith('attachment; filename="feyn_students_')
    assert resp.headers['Cache-Control'] == 'no-store'


# =====================================================
# 中身
# =====================================================
def test_progress_and_clear_counts(student_client, teacher_client, monkeypatch):
    uid = _uid(student_client)
    teacher_client.post('/api/dashboard/assignments', json={'student_ids': [uid], 'subject': '数学'})
    key = student_client.post('/api/start', json={'subject': '物理', 'difficulty': '大学受験'}).get_json()['session_key']
    monkeypatch.setattr(FakeChatSession, 'default_reply', '納得！【会話終了】')
    student_client.post('/api/chat', json={'message': 'ひみつの説明文12345', 'session_key': key})
    core.save_analysis(uid, '物理', {'topic': '慣性', 'understanding_score': 60, 'gaps': [
        {'gap_type': 'vague', 'description': 'd', 'evidence': 'e', 'suggested_question': 'q'}]}, '2026-10-25')
    core.save_analysis(uid, '物理', {'topic': '運動量', 'understanding_score': 81, 'gaps': []}, '2026-10-25')

    resp = teacher_client.get(URL)
    row = _by_id(_rows(resp))[uid]
    assert row['ニックネーム'] == 'テスト太郎'
    assert row['総クリア数'] == '1'
    assert row['物理クリア数'] == '1' and row['数学クリア数'] == '0'
    assert row['理解度の平均(0-100)'] == '71'                # (60 + 81) / 2 = 70.5 → 四捨五入で71
    assert row['学習したテーマ数'] == '2'
    assert row['苦手（未解決）'] == '1' and row['苦手（解決済み）'] == '0'
    assert row['課題（未完了）'] == '1' and row['課題（完了）'] == '0'
    assert row['最終学習'] != ''
    assert 'ひみつの説明文12345' not in resp.get_data().decode('utf-8')   # 会話本文は含めない


def test_student_without_any_data_has_empty_values(student_client, teacher_client):
    row = _by_id(_rows(teacher_client.get(URL)))[_uid(student_client)]
    assert row['総クリア数'] == '0'
    assert row['理解度の平均(0-100)'] == ''
    assert row['最終学習'] == ''
    assert row['プラン'] == '無料'


def test_formula_like_nicknames_are_neutralized(app, teacher_client):
    """ニックネームが = や + で始まっても、Excelで数式として実行されない"""
    evil = app.app.test_client()
    evil.post('/api/signup', json=_signup_payload(email='evil@example.com', name='=HYPERLINK("x")'))
    row = _by_id(_rows(teacher_client.get(URL)))[_uid(evil)]
    assert row['ニックネーム'] == "'=HYPERLINK(\"x\")"


def test_csv_safe():
    assert core.csv_safe('=1+1') == "'=1+1"
    assert core.csv_safe('+81 90') == "'+81 90"
    assert core.csv_safe('-5') == "'-5"
    assert core.csv_safe('@SUM') == "'@SUM"
    assert core.csv_safe('たろう') == 'たろう'
    assert core.csv_safe(None) == ''


def test_works_through_the_postgres_code_path(pg_path, app):
    student = app.app.test_client()
    student.post('/api/signup', json=_signup_payload())
    teacher = app.app.test_client()
    teacher.post('/api/signup', json=_signup_payload(email='t@example.com', teacher_code=core.TEACHER_CODE))
    core.save_analysis(_uid(student), '物理', {'topic': '慣性', 'understanding_score': 55, 'gaps': []}, '2026-10-25')
    rows = _rows(teacher.get(URL))
    assert len(rows) == 2
    assert pg_path.pool.stats()['in_use'] == 0
