# -*- coding: utf-8 -*-
"""塾・学校（組織）ごとの生徒の分け方と、運営アカウントのテスト。

- 別の組織の先生は、生徒のデータを1つも見られない・操作できない（一覧・詳細・会話ログ・苦手ノート・課題・
  パスワード再発行・CSV・つまずきマップ・利用状況の集計）
- DEFAULT の生徒は、どの先生からも見えない。DEFAULT にいる先生は、だれも見えない
- 運営アカウントは全組織（DEFAULT を含む）の生徒を見られ、詳細・会話ログ・CSVを開くと記録が残る
- 運営アカウントは普通の登録・先生コードでの登録ではできない
- 参加コード: 何人でも使える・間違い・作り直した古いコード・先生コードとの取り違え・試行回数の制限
"""
import pytest

import core
import ratelimit
import schools
from conftest import TEST_JOIN_CODE, TEST_TEACHER_CODE, _signup_payload


# ---------- 準備 ----------

def _client(app):
    return app.app.test_client()


def _signup(app, email, **overrides):
    c = _client(app)
    resp = c.post('/api/signup', json=_signup_payload(email=email, name=email.split('@')[0][:20], **overrides))
    assert resp.status_code == 200, resp.get_json()
    return c


def _uid(c):
    return c.get('/api/me').get_json()['id']


def _school_of(user_id):
    with core.get_db() as conn:
        return conn.execute('SELECT school_id FROM users WHERE id = ?', (user_id,)).fetchone()['school_id']


def _make_operator(c):
    with core.get_db() as conn:
        email = conn.execute('SELECT email FROM users WHERE id = ?', (_uid(c),)).fetchone()['email']
    schools.set_operator(email, True)


def _give_data(student_id, subject='物理'):
    """会話ログ・苦手ノート・理解度を1つずつ入れる（テーマは生徒ごとに別の名前）。会話の識別子（key）を返す"""
    topic = f'慣性の法則{student_id}'
    conv_id = f'conv-{student_id}'
    with core.get_db() as conn:
        for role, msg in [('model', 'ねえ、慣性って何？'), ('user', 'ひみつの説明文')]:
            conn.execute(
                'INSERT INTO conversation_logs (user_id, subject, difficulty, role, message, session_date, session_id) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (student_id, subject, 'basic', role, msg, '2026-11-01', conv_id)
            )
        conn.commit()
    core.save_analysis(student_id, subject, {
        'topic': topic, 'understanding_score': 40,
        'gaps': [{'gap_type': 'cause', 'description': '原因があいまい', 'evidence': '', 'suggested_question': 'なぜ？'}],
    }, '2026-11-01')
    return conv_id


@pytest.fixture
def world(app):
    """テスト塾（conftest）と別の塾B・DEFAULT に、先生と生徒を置く"""
    other = schools.create_school('B塾', join_code='BJOINCODE234', teacher_code='BTEACHCODE23')
    w = {
        'a_teacher':  _signup(app, 'ta@example.com', teacher_code=TEST_TEACHER_CODE),
        'a_student':  _signup(app, 'sa@example.com'),
        'b_teacher':  _signup(app, 'tb@example.com', teacher_code='BTEACHCODE23'),
        'b_student':  _signup(app, 'sb@example.com', join_code='BJOINCODE234'),
        'd_student':  _signup(app, 'sd@example.com', join_code=''),
        'other':      other,
    }
    for key in ('a_student', 'b_student', 'd_student'):
        w[key + '_id'] = _uid(w[key])
        w[key + '_conv'] = _give_data(w[key + '_id'])
    return w


def _list_ids(c, query=''):
    return {s['id'] for s in c.get('/api/dashboard' + query).get_json()['students']}


def _csv_text(c, query=''):
    resp = c.get('/api/dashboard/students.csv' + query)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


# ---------- 別の組織の先生は、何も見られない・操作できない ----------

def _assert_cannot_touch(teacher, student_id, conv_key, student_email):
    assert student_id not in _list_ids(teacher)
    assert teacher.get(f'/api/dashboard/student/{student_id}').status_code == 404
    assert teacher.get(f'/api/dashboard/student/{student_id}/history').status_code == 404
    assert teacher.get(f'/api/dashboard/student/{student_id}/history/messages?key={conv_key}').status_code == 404
    resp = teacher.post('/api/dashboard/assignments', json={'student_ids': [student_id], 'subject': '物理'})
    assert resp.status_code == 404
    assert teacher.post('/api/dashboard/reset-password', json={'student_id': student_id}).status_code == 404
    assert student_email not in _csv_text(teacher)
    stumble = teacher.get('/api/dashboard/stumble-map').get_json()
    assert all(t['topic'] != f'慣性の法則{student_id}' for t in stumble['topics'])
    with core.get_db() as conn:
        assert conn.execute('SELECT COUNT(*) AS c FROM assignments WHERE student_id = ?',
                            (student_id,)).fetchone()['c'] == 0


def test_teacher_of_another_school_sees_and_touches_nothing(world):
    _assert_cannot_touch(world['b_teacher'], world['a_student_id'], world['a_student_conv'], 'sa@example.com')
    _assert_cannot_touch(world['a_teacher'], world['b_student_id'], world['b_student_conv'], 'sb@example.com')


def test_teacher_sees_own_school_students_only(world):
    assert _list_ids(world['a_teacher']) == {world['a_student_id']}
    assert _list_ids(world['b_teacher']) == {world['b_student_id']}
    detail = world['b_teacher'].get(f"/api/dashboard/student/{world['b_student_id']}").get_json()
    assert detail['school'] == 'B塾'
    assert detail['gaps'] and detail['sessions']
    stumble = world['b_teacher'].get('/api/dashboard/stumble-map').get_json()
    assert stumble['total_students'] == 1


def test_usage_metrics_are_per_school(world):
    core.log_event('conversation_started', world['a_student_id'], conv_id='x1')
    core.log_event('conversation_started', world['b_student_id'], conv_id='x2')
    core.log_event('conversation_started', world['b_student_id'], conv_id='x3')
    assert world['a_teacher'].get('/api/dashboard/metrics').get_json()['started'] == 1
    assert world['b_teacher'].get('/api/dashboard/metrics').get_json()['started'] == 2


def test_password_reset_works_only_inside_own_school(world):
    resp = world['b_teacher'].post('/api/dashboard/reset-password', json={'student_id': world['b_student_id']})
    assert resp.status_code == 200 and resp.get_json()['temp_password']


# ---------- DEFAULT ----------

def test_default_students_are_invisible_to_every_teacher(world):
    for teacher in (world['a_teacher'], world['b_teacher']):
        _assert_cannot_touch(teacher, world['d_student_id'], world['d_student_conv'], 'sd@example.com')


def test_teacher_in_default_sees_nobody(app, world):
    legacy = _signup(app, 'legacy@example.com', teacher_code=core.TEACHER_CODE)   # 共通の先生コード → DEFAULT
    me = legacy.get('/api/dashboard').get_json()
    assert me['students'] == [] and me['viewer']['in_default'] is True
    for key in ('a_student', 'b_student', 'd_student'):
        _assert_cannot_touch(legacy, world[key + '_id'], world[key + '_conv'], f'{key[0]}@x')
    assert legacy.get('/api/dashboard/metrics').get_json()['started'] == 0
    assert legacy.get('/api/dashboard/stumble-map').get_json()['total_students'] == 0
    assert _csv_text(legacy).count('\r\n') == 1     # 見出しだけ


def test_creating_a_school_does_not_move_default_students(app, world):
    schools.create_school('C塾')
    assert _school_of(world['d_student_id']) == core.default_school_id()


def test_signup_without_code_goes_to_default(app):
    c = _signup(app, 'nocode@example.com', join_code='')
    assert _school_of(_uid(c)) == core.default_school_id()
    assert c.get('/api/school').get_json()['school'] is None


# ---------- 運営アカウント ----------

def test_operator_sees_all_schools_including_default(app, world):
    op = _signup(app, 'op@example.com', join_code='')
    _make_operator(op)
    ids = _list_ids(op)
    assert {world['a_student_id'], world['b_student_id'], world['d_student_id']} <= ids
    data = op.get('/api/dashboard').get_json()
    assert data['viewer']['kind'] == 'operator'
    names = {s['id']: s['school_name'] for s in data['students']}
    assert names[world['b_student_id']] == 'B塾'
    assert names[world['d_student_id']] == '（どこにも入っていない）'
    assert any(sc['is_default'] for sc in data['schools'])
    # 組織で絞り込む
    assert _list_ids(op, f"?school={world['other']['id']}") == {world['b_student_id']}
    assert world['d_student_id'] in _list_ids(op, f'?school={core.default_school_id()}')
    assert world['b_student_id'] not in _list_ids(op, f'?school={core.default_school_id()}')
    # 詳細・会話ログ・集計も見られる
    for key in ('a_student', 'b_student', 'd_student'):
        sid = world[key + '_id']
        assert op.get(f'/api/dashboard/student/{sid}').status_code == 200
        assert op.get(f'/api/dashboard/student/{sid}/history').status_code == 200
        assert op.get(f"/api/dashboard/student/{sid}/history/messages?key={world[key + '_conv']}").status_code == 200
    assert op.get('/api/dashboard/stumble-map').get_json()['total_students'] >= 3
    text = _csv_text(op)
    assert text.splitlines()[0].lstrip('﻿').startswith('組織,')
    assert 'sd@example.com' in text and 'sb@example.com' in text


def test_operator_views_are_logged_without_message_text(app, world):
    op = _signup(app, 'op@example.com', join_code='')
    _make_operator(op)
    sid = world['b_student_id']
    op.get(f'/api/dashboard/student/{sid}')
    op.get(f'/api/dashboard/student/{sid}/history?offset=0')
    op.get(f"/api/dashboard/student/{sid}/history/messages?key={world['b_student_conv']}")
    op.get('/api/dashboard/students.csv')
    op.get('/api/dashboard')                        # 一覧を見ただけでは記録しない

    with core.get_db() as conn:
        rows = [dict(r) for r in conn.execute(
            'SELECT operator_id, action, student_id, school_id, target FROM operator_access_logs ORDER BY id').fetchall()]
    op_id = _uid(op)
    assert [r['action'] for r in rows] == ['student_detail', 'history', 'history_messages', 'students_csv']
    assert all(r['operator_id'] == op_id for r in rows)
    assert [r['student_id'] for r in rows[:3]] == [sid, sid, sid]
    assert rows[0]['school_id'] == world['other']['id']
    assert rows[2]['target'] == world['b_student_conv']
    assert not any('ひみつの説明文' in (r['target'] or '') for r in rows)

    logs = op.get('/api/operator/access-logs').get_json()['logs']
    assert len(logs) == 4
    assert logs[0]['action'] == schools.ACCESS_ACTIONS['students_csv']
    assert logs[-1]['student_name'] == 'sb' and logs[-1]['school_name'] == 'B塾'
    assert logs[-1]['operator_email'] == 'op@example.com'


def test_teacher_views_are_not_logged(world):
    world['a_teacher'].get(f"/api/dashboard/student/{world['a_student_id']}")
    with core.get_db() as conn:
        assert conn.execute('SELECT COUNT(*) AS c FROM operator_access_logs').fetchone()['c'] == 0


def test_only_operators_can_read_access_logs(app, world):
    assert _client(app).get('/api/operator/access-logs').status_code == 401
    assert world['a_teacher'].get('/api/operator/access-logs').status_code == 403
    assert world['a_student'].get('/api/operator/access-logs').status_code == 403


def test_operator_is_read_only_unless_also_a_teacher(app, world):
    op = _signup(app, 'op@example.com', join_code='')
    _make_operator(op)
    sid = world['b_student_id']
    assert op.post('/api/dashboard/assignments', json={'student_ids': [sid], 'subject': '物理'}).status_code == 403
    assert op.post('/api/dashboard/reset-password', json={'student_id': sid}).status_code == 403


def test_operator_flag_is_checked_on_every_request(app, world):
    op = _signup(app, 'op@example.com', join_code='')
    _make_operator(op)
    assert op.get('/api/dashboard').status_code == 200
    schools.set_operator('op@example.com', False)
    assert op.get('/api/dashboard').status_code == 403      # ログインし直さなくても、すぐ外れる
    assert op.get('/dashboard').status_code == 302


def test_operator_can_open_dashboard_pages(app, world):
    op = _signup(app, 'op@example.com', join_code='')
    assert op.get('/dashboard').status_code == 302          # 生徒のままでは開けない
    _make_operator(op)
    assert op.get('/dashboard').status_code == 200
    assert op.get(f"/dashboard/student/{world['d_student_id']}").status_code == 200


def test_signup_never_creates_an_operator(app):
    plain = _signup(app, 'p@example.com', is_operator=1, role='operator')
    teacher = _signup(app, 't2@example.com', teacher_code=TEST_TEACHER_CODE, is_operator=True)
    with core.get_db() as conn:
        rows = conn.execute('SELECT role, is_operator FROM users WHERE id IN (?, ?)',
                            (_uid(plain), _uid(teacher))).fetchall()
    assert sorted(r['role'] for r in rows) == ['student', 'teacher']
    assert all(r['is_operator'] == 0 for r in rows)
    assert plain.get('/api/operator/access-logs').status_code == 403
    assert teacher.get('/api/operator/access-logs').status_code == 403


def test_login_rate_limit_applies_to_operator_accounts(app):
    op = _signup(app, 'op@example.com', join_code='')
    _make_operator(op)
    c = _client(app)
    for _ in range(ratelimit.LIMIT_PER_ACCOUNT_AND_IP):
        assert c.post('/api/login', json={'email': 'op@example.com', 'password': 'wrong-pass'}).status_code == 401
    resp = c.post('/api/login', json={'email': 'op@example.com', 'password': 'testpass123'})
    assert resp.status_code == 429      # 正しいパスワードでも、制限中は通さない


def test_operator_through_the_postgres_code_path(pg_path, app):
    schools.create_school('B塾', join_code='BJOINCODE234', teacher_code='BTEACHCODE23')
    b = _signup(app, 'sb@example.com', join_code='BJOINCODE234')
    sid = _uid(b)
    conv = _give_data(sid)
    op = _signup(app, 'op@example.com', join_code='')
    _make_operator(op)
    assert sid in _list_ids(op)
    assert op.get(f'/api/dashboard/student/{sid}/history/messages?key={conv}').status_code == 200
    assert 'sb@example.com' in _csv_text(op)
    assert len(op.get('/api/operator/access-logs').get_json()['logs']) == 2
    teacher = _signup(app, 'ta@example.com', teacher_code=TEST_TEACHER_CODE)
    assert teacher.get(f'/api/dashboard/student/{sid}').status_code == 404
    assert pg_path.pool.stats()['in_use'] == 0


# ---------- 参加コード ----------

def test_join_code_can_be_used_by_many_students_and_with_dashes(app):
    a = _signup(app, 'a@example.com', join_code='joincode2345')
    b = _signup(app, 'b@example.com', join_code='JOIN-CODE-2345')
    assert _school_of(_uid(a)) == _school_of(_uid(b)) != core.default_school_id()


def test_wrong_join_code_creates_no_account(app):
    c = _client(app)
    resp = c.post('/api/signup', json=_signup_payload(email='w@example.com', join_code='WRONGCODE234'))
    assert resp.status_code == 400
    with core.get_db() as conn:
        assert conn.execute("SELECT 1 FROM users WHERE email = 'w@example.com'").fetchone() is None


@pytest.mark.parametrize('code', ['DEFAULT', 'default', TEST_TEACHER_CODE])
def test_join_code_rejects_default_and_teacher_codes(app, code):
    resp = _client(app).post('/api/signup', json=_signup_payload(email='x@example.com', join_code=code))
    assert resp.status_code == 400


def test_student_code_does_not_make_a_teacher(app):
    resp = _client(app).post('/api/signup', json=_signup_payload(email='x@example.com', teacher_code=TEST_JOIN_CODE))
    assert resp.status_code == 400


def test_rotated_codes_stop_working_but_members_stay(app, student_client):
    school = schools.find_by_join_code(TEST_JOIN_CODE)
    new = schools.rotate_codes(school['id'], 'both')
    assert new['join_code'] != TEST_JOIN_CODE and new['teacher_code'] != TEST_TEACHER_CODE
    assert _school_of(_uid(student_client)) == school['id']
    c = _client(app)
    assert c.post('/api/signup', json=_signup_payload(email='n@example.com')).status_code == 400
    assert c.post('/api/signup', json=_signup_payload(email='n@example.com', join_code=new['join_code'])).status_code == 200


def test_generated_codes_are_long_and_unambiguous(app):
    school = schools.create_school('D塾')
    for code in (school['join_code'], school['teacher_code']):
        assert len(code) == schools.CODE_LENGTH == 12
        assert set(code) <= set(schools.CODE_ALPHABET)
        assert not set(code) & set('01OIL')
    assert school['join_code'] != school['teacher_code']
    assert schools.format_code('ABCDEFGHJKMN') == 'ABCD-EFGH-JKMN'


def test_signup_join_code_attempts_are_limited(app):
    c = _client(app)
    for i in range(ratelimit.LIMIT_PER_IP):
        c.post('/api/signup', json=_signup_payload(email=f'g{i}@example.com', join_code=f'GUESS{i:07d}'))
    resp = c.post('/api/signup', json=_signup_payload(email='ok@example.com'))
    assert resp.status_code == 429      # 正しいコードでも、制限中は通さない


# ---------- マイページ: 参加・確認・退出 ----------

def test_student_can_join_check_and_leave(app):
    schools.create_school('B塾', join_code='BJOINCODE234', teacher_code='BTEACHCODE23')
    s = _signup(app, 's@example.com', join_code='')
    assert s.get('/api/school').get_json()['school'] is None
    resp = s.post('/api/school/join', json={'code': 'bjoin-code-234'})
    assert resp.status_code == 200 and resp.get_json()['school']['name'] == 'B塾'
    assert s.get('/api/school').get_json()['school']['name'] == 'B塾'

    teacher = _signup(app, 'tb@example.com', teacher_code='BTEACHCODE23')
    assert _uid(s) in _list_ids(teacher)
    assert s.post('/api/school/leave').status_code == 200
    assert s.get('/api/school').get_json()['school'] is None
    assert _school_of(_uid(s)) == core.default_school_id()
    assert _uid(s) not in _list_ids(teacher)
    assert teacher.get(f'/api/dashboard/student/{_uid(s)}').status_code == 404


def test_joining_requires_login_and_a_valid_code(app, student_client):
    assert _client(app).post('/api/school/join', json={'code': TEST_JOIN_CODE}).status_code == 401
    assert _client(app).post('/api/school/leave').status_code == 401
    assert student_client.post('/api/school/join', json={'code': ''}).status_code == 400
    assert student_client.post('/api/school/join', json={'code': 'NOPE12345678'}).status_code == 400
    assert student_client.post('/api/school/join', json={'code': TEST_TEACHER_CODE}).status_code == 400
    me = student_client.get('/api/me').get_json()
    assert me['role'] == 'student'


def test_join_attempts_are_limited_per_account(app, student_client):
    for i in range(ratelimit.LIMIT_PER_ACCOUNT_AND_IP):
        assert student_client.post('/api/school/join', json={'code': f'GUESS{i:07d}'}).status_code == 400
    assert student_client.post('/api/school/join', json={'code': TEST_JOIN_CODE}).status_code == 429


def test_teacher_in_default_can_join_with_a_teacher_code(app, world):
    legacy = _signup(app, 'legacy@example.com', teacher_code=core.TEACHER_CODE)
    assert legacy.post('/api/school/join', json={'code': 'BJOINCODE234'}).status_code == 400   # 生徒用のコードではだめ
    assert legacy.post('/api/school/join', json={'code': 'BTEACHCODE23'}).status_code == 200
    assert _list_ids(legacy) == {world['b_student_id']}
    assert legacy.post('/api/school/leave').status_code == 403


# ---------- コマンド（scripts/orgs.py） ----------

def test_orgs_script_creates_school_and_grants_operator(app, capsys):
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts', 'orgs.py')
    spec = importlib.util.spec_from_file_location('orgs_script', path)
    orgs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(orgs)

    assert orgs.main(['create', '野村塾']) == 0
    out = capsys.readouterr().out
    assert '野村塾' in out and '先生コード' in out and '参加コード' in out
    school = [sc for sc in schools.list_schools() if sc['name'] == '野村塾'][0]
    assert schools.format_code(school['join_code']) in out

    _signup(app, 'me@example.com', join_code='')
    assert orgs.main(['grant-operator', 'ME@example.com']) == 0
    assert [o['email'] for o in schools.list_operators()] == ['me@example.com']
    assert orgs.main(['revoke-operator', 'me@example.com']) == 0
    assert schools.list_operators() == []
    assert orgs.main(['grant-operator', 'nobody@example.com']) == 1
    assert orgs.main(['rotate', str(core.default_school_id())]) == 1    # DEFAULT にはコードを出さない
    assert orgs.main(['list']) == 0


# ---------- 画面（入力欄・表示の置き場があるか） ----------

def _read(name):
    import os
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), name), encoding='utf-8') as f:
        return f.read()


def test_screens_have_the_school_controls():
    login = _read('login.html')
    assert 'id="joinCodeInput"' in login and 'join_code' in login
    mypage = _read('mypage.html')
    assert 'id="schoolZone"' in mypage and '/api/school/join' in mypage and '/api/school/leave' in mypage
    dashboard = _read('dashboard.html')
    for needle in ('id="scopeBar"', 'id="accessLogSection"', '/api/operator/access-logs', 'id="teacherJoinForm"'):
        assert needle in dashboard
