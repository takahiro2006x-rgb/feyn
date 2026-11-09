# -*- coding: utf-8 -*-
"""先生ダッシュボードAPI（生徒一覧・生徒詳細・課題出題・パスワード再発行・使用量）と、運営アカウント用の閲覧の記録。

見られる範囲（だれの生徒を見られるか）:
  - 先生: 自分の組織（塾・学校）の生徒だけ。DEFAULT（どこにも入っていない）にいる先生は、だれも見えない
  - 運営アカウント（users.is_operator）: DEFAULT を含む全組織の生徒。?school=<id> で組織を絞り込める。
    生徒の詳細・会話ログ・CSVを開くたびに operator_access_logs へ記録する（会話の本文は記録しない）
  - 課題を出す・パスワードを再発行する（書き換え）は先生だけ。運営アカウントは見るだけ
権限と組織は、クッキーではなく毎回DBから確かめる（blueprints/school.py の load_viewer）。
"""
import csv
import io
import random
import string

from flask import Blueprint, Response, jsonify, request
from werkzeug.security import generate_password_hash

import core
import schools
import tutoring
from blueprints.school import can_open_dashboard, load_viewer

dashboard_bp = Blueprint('dashboard', __name__)


def _viewer_or_error():
    """先生か運営アカウントとしてログインしているか確かめる。
    問題なければ (None, viewer)、だめなら (エラー応答, None)"""
    viewer = load_viewer()
    if not viewer:
        return (jsonify({'error': 'ログインしてください'}), 401), None
    if not can_open_dashboard(viewer):
        return (jsonify({'error': '権限がありません'}), 403), None
    return None, viewer


def _teacher_scope(viewer):
    """先生として見られる組織。DEFAULT にいる先生は、だれも見えない"""
    return core.NOBODY_SCHOOL_ID if viewer['in_default'] else viewer['school_id']


def _read_scope(viewer):
    """一覧・集計で見る範囲。運営アカウントは ?school= で絞り込める（無し・all は全組織）"""
    if viewer['is_operator']:
        raw = (request.args.get('school') or '').strip()
        if raw.isdigit():
            return int(raw)
        return None
    return _teacher_scope(viewer)


def _student_for_viewer(viewer, student_id, columns='id, name'):
    """その人が見てよい生徒なら行を返す（見てはいけない・いないならNone）"""
    if viewer['is_operator']:
        cond, params = '', []
    else:
        cond, params = core.school_condition('school_id', _teacher_scope(viewer))
    with core.get_db() as conn:
        return conn.execute(
            f"SELECT {columns} FROM users WHERE id = ? AND role = 'student'" + cond,
            [student_id] + params
        ).fetchone()


def _log_if_operator(viewer, action, student=None, school_id=None, target=None):
    """運営アカウントとして見るときは、見せる前に記録する（記録に失敗したら見せない）"""
    if not viewer['is_operator']:
        return
    schools.log_operator_access(viewer['id'], action,
                                student_id=student['id'] if student else None,
                                school_id=student['school_id'] if student else school_id,
                                target=target)


def _writer_or_error():
    """課題を出す・パスワードを再発行するのは先生だけ（運営アカウントは見るだけ）。
    問題なければ (None, viewer, 先生の組織)、だめなら (エラー応答, None, None)"""
    viewer = load_viewer()
    if not viewer:
        return (jsonify({'error': 'ログインしてください'}), 401), None, None
    if viewer['role'] != 'teacher':
        return (jsonify({'error': '権限がありません'}), 403), None, None
    return None, viewer, _teacher_scope(viewer)


def _school_label(school_id):
    """生徒の組織の名前（DEFAULT ならNone）"""
    school = schools.get_school(school_id)
    if not school or school['is_default']:
        return None
    return school['name']


@dashboard_bp.route('/api/dashboard/student/<int:student_id>')
def dashboard_student(student_id):
    error, viewer = _viewer_or_error()
    if error:
        return error
    student = _student_for_viewer(viewer, student_id, 'id, name, email, subscription_status, school_id')
    if not student:
        return jsonify({'error': '生徒が見つかりません'}), 404
    _log_if_operator(viewer, 'student_detail', student)

    with core.get_db() as conn:
        gap_rows = conn.execute(
            "SELECT id, subject, topic, gap_type, description, evidence, status, session_date "
            "FROM knowledge_gaps WHERE user_id = ? ORDER BY (status = 'resolved'), id DESC",
            (student_id,)
        ).fetchall()
        progress_rows = conn.execute(
            'SELECT subject, topic, understanding, sessions_count, last_studied '
            'FROM topic_progress WHERE user_id = ? ORDER BY understanding, last_studied DESC',
            (student_id,)
        ).fetchall()
        # 先生には、自分の組織の先生が出した課題だけを見せる（生徒が別の組織から移ってきた場合のため）
        if viewer['is_operator']:
            a_cond, a_params = '', []
        else:
            a_cond, a_params = ' AND teacher_id IN (SELECT id FROM users WHERE school_id = ?)', [viewer['school_id']]
        assignment_rows = conn.execute(
            "SELECT id, subject, unit, status, created_at, completed_at "
            "FROM assignments WHERE student_id = ?" + a_cond + " ORDER BY (status = 'completed'), id DESC",
            [student_id] + a_params
        ).fetchall()

    history = core.get_history_page(student_id)

    assignments = []
    for r in assignment_rows:
        a = dict(r)
        a['created_at']   = core.to_jst_str(a['created_at'])
        a['completed_at'] = core.to_jst_str(a['completed_at'])
        assignments.append(a)

    return jsonify({
        'student':     {k: student[k] for k in ('id', 'name', 'email', 'subscription_status')},
        'school':      _school_label(student['school_id']),
        'gaps':        [dict(r) for r in gap_rows],
        'progress':    [dict(r) for r in progress_rows],
        # 会話ログは最初の20件の見出しだけ。続きと中身は下の2つのAPIで取りに行く
        'sessions':    history['sessions'],
        'sessions_has_more': history['has_more'],
        'assignments': assignments,
        'labels':      tutoring.GAP_TYPE_LABELS,
    })


def _visible_student_or_error(student_id):
    """先生か運営アカウントとしてログインしていて、その生徒を見てよいか確認する。
    問題なければ (None, viewer, 生徒の行)、だめなら (エラー応答, None, None) を返す"""
    error, viewer = _viewer_or_error()
    if error:
        return error, None, None
    student = _student_for_viewer(viewer, student_id, 'id, name, school_id')
    if not student:
        return (jsonify({'error': '生徒が見つかりません'}), 404), None, None
    return None, viewer, student


@dashboard_bp.route('/api/dashboard/student/<int:student_id>/history')
def dashboard_student_history(student_id):
    """先生が見る生徒の会話ログ一覧（続きのページ）"""
    error, viewer, student = _visible_student_or_error(student_id)
    if error:
        return error
    offset = request.args.get('offset', 0, type=int) or 0
    _log_if_operator(viewer, 'history', student, target=f'offset={offset}')
    return jsonify(core.get_history_page(student_id, offset=offset))


@dashboard_bp.route('/api/dashboard/student/<int:student_id>/history/messages')
def dashboard_student_history_messages(student_id):
    """先生が見る生徒の、1つの会話の中身"""
    error, viewer, student = _visible_student_or_error(student_id)
    if error:
        return error
    key = request.args.get('key', '')
    # 記録するのは「どの会話か」の識別子だけ（本文は記録しない）
    _log_if_operator(viewer, 'history_messages', student, target=key)
    messages = core.get_history_messages(student_id, key)
    if not messages:
        return jsonify({'error': '会話が見つかりません'}), 404
    return jsonify({'messages': messages})


@dashboard_bp.route('/api/dashboard/assignments', methods=['POST'])
def create_assignment():
    """先生が生徒に科目・単元を指定して課題を出す（自分の組織の生徒にだけ）"""
    error, viewer, school_id = _writer_or_error()
    if error:
        return error

    data        = request.get_json() or {}
    student_ids = data.get('student_ids')
    if not student_ids:
        single_id   = data.get('student_id')
        student_ids = [single_id] if single_id else []
    subject = data.get('subject', '')
    unit    = (data.get('unit') or '').strip() or None

    if subject not in core.SUBJECTS:
        return jsonify({'error': '科目を指定してください'}), 400
    if not student_ids:
        return jsonify({'error': '生徒を選択してください'}), 400

    with core.get_db() as conn:
        placeholders = ','.join('?' * len(student_ids))
        valid_rows = conn.execute(
            f"SELECT id FROM users WHERE role = 'student' AND school_id = ? AND id IN ({placeholders})",
            [school_id] + list(student_ids)
        ).fetchall()
        valid_ids = [r['id'] for r in valid_rows]
        if not valid_ids:
            return jsonify({'error': '生徒が見つかりません'}), 404

        for sid in valid_ids:
            conn.execute(
                'INSERT INTO assignments (teacher_id, student_id, subject, unit) VALUES (?, ?, ?, ?)',
                (viewer['id'], sid, subject, unit)
            )
        conn.commit()

    return jsonify({'ok': True, 'count': len(valid_ids)})


@dashboard_bp.route('/api/dashboard/reset-password', methods=['POST'])
def teacher_reset_password():
    """先生が自分の組織の生徒のパスワードを、仮のパスワードに変える"""
    error, _, school_id = _writer_or_error()
    if error:
        return error

    data       = request.get_json() or {}
    student_id = data.get('student_id')

    temp_pass = 'Feyn' + ''.join(random.choices(string.digits, k=4))

    with core.get_db() as conn:
        updated = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ? AND role = 'student' AND school_id = ?",
            (generate_password_hash(temp_pass), student_id, school_id)
        ).rowcount
        conn.commit()

    if updated == 0:
        return jsonify({'error': '生徒が見つかりません'}), 404
    return jsonify({'ok': True, 'temp_password': temp_pass})


@dashboard_bp.route('/api/dashboard')
def dashboard_api():
    error, viewer = _viewer_or_error()
    if error:
        return error

    scope = _read_scope(viewer)
    cond_u, params = core.school_condition('u.school_id', scope)
    with core.get_db() as conn:
        students_rows = conn.execute(
            'SELECT u.id, u.name, u.email, u.created_at, u.last_login, u.subscription_status, u.school_id '
            "FROM users u WHERE u.role = 'student'" + cond_u + ' ORDER BY u.created_at DESC',
            params
        ).fetchall()
        count_rows = conn.execute(
            'SELECT s.user_id, s.subject, COUNT(*) as cnt FROM session_logs s JOIN users u ON u.id = s.user_id '
            "WHERE u.role = 'student'" + cond_u + ' GROUP BY s.user_id, s.subject',
            params
        ).fetchall()
        last_rows = conn.execute(
            'SELECT s.user_id, MAX(s.completed_at) as last_at FROM session_logs s JOIN users u ON u.id = s.user_id '
            "WHERE u.role = 'student'" + cond_u + ' GROUP BY s.user_id',
            params
        ).fetchall()
        # 最近の提出: 先生は自分が出した課題のうち、今も自分の組織にいる生徒の分。運営は範囲内の全部
        if viewer['is_operator']:
            sub_cond, sub_params = cond_u, params
        else:
            sub_cond, sub_params = ' AND a.teacher_id = ?' + cond_u, [viewer['id']] + params
        submission_rows = conn.execute(
            "SELECT a.subject, a.unit, a.completed_at, u.name as student_name "
            "FROM assignments a JOIN users u ON u.id = a.student_id "
            "WHERE a.status = 'completed'" + sub_cond + " "
            "ORDER BY a.completed_at DESC LIMIT 20",
            sub_params
        ).fetchall()

    counts_by_user = {}
    for row in count_rows:
        counts_by_user.setdefault(row['user_id'], {})[row['subject']] = row['cnt']
    last_by_user = {row['user_id']: row['last_at'] for row in last_rows}

    school_names = {}
    if viewer['is_operator']:
        school_list = schools.list_schools()
        school_names = {sc['id']: ('（どこにも入っていない）' if sc['is_default'] else sc['name']) for sc in school_list}

    result = []
    for s in students_rows:
        user_counts = counts_by_user.get(s['id'], {})
        counts = {subj: user_counts.get(subj, 0) for subj in core.SUBJECTS}
        # DBの時刻はUTCなので、先生に見せる前に日本時間 'YYYY-MM-DD HH:MM' にそろえる
        item = {
            'id':         s['id'],
            'name':       s['name'],
            'email':      s['email'],
            'created_at': core.to_jst_str(s['created_at']),
            'last_active': core.to_jst_str(last_by_user.get(s['id'])),
            'last_login': core.to_jst_str(s['last_login']),
            'subscription_status': s['subscription_status'],
            'subjects':   counts,
            'total':      sum(counts.values()),
        }
        if viewer['is_operator']:
            item['school_name'] = school_names.get(s['school_id'], '')
        result.append(item)

    total_clears = sum(s['total'] for s in result)
    top_subject  = max(core.SUBJECTS, key=lambda subj: sum(s['subjects'][subj] for s in result)) if result else '—'

    response = {
        'students': result,
        'summary': {
            'total_students': len(result),
            'total_clears':   total_clears,
            'top_subject':    top_subject,
        },
        'recent_submissions': [dict(r, completed_at=core.to_jst_str(r['completed_at'])) for r in submission_rows],
        'viewer': _viewer_info(viewer, scope),
    }
    if viewer['is_operator']:
        response['schools'] = [
            {'id': sc['id'], 'name': sc['name'], 'is_default': sc['is_default'],
             'students': sc['students'], 'teachers': sc['teachers']}
            for sc in school_list
        ]
    return jsonify(response)


def _viewer_info(viewer, scope):
    """画面の出し分け用: 運営アカウントか、先生ならどの組織か（DEFAULT ならだれも見えないことを伝える）"""
    if viewer['is_operator']:
        return {'kind': 'operator', 'school': None, 'in_default': False, 'scope': scope,
                'can_write': viewer['role'] == 'teacher'}
    return {'kind': 'teacher', 'school': _school_label(viewer['school_id']),
            'in_default': viewer['in_default'], 'scope': scope, 'can_write': True}


@dashboard_bp.route('/api/dashboard/stumble-map')
def dashboard_stumble_map():
    """つまずきマップ: テーマごとに、何割の生徒の苦手ノートに（未解決で）入っているか"""
    error, viewer = _viewer_or_error()
    if error:
        return error
    return jsonify(core.compute_stumble_map(_read_scope(viewer)))


@dashboard_bp.route('/api/dashboard/students.csv')
def dashboard_students_csv():
    """担当生徒の進捗・クリア数をCSVで書き出す（会話本文は含めない）"""
    error, viewer = _viewer_or_error()
    if error:
        return error

    scope = _read_scope(viewer)
    _log_if_operator(viewer, 'students_csv', school_id=scope,
                     target='全組織' if scope is None else f'school={scope}')
    buf = io.StringIO()
    csv.writer(buf, lineterminator='\r\n').writerows(
        core.build_students_csv_rows(scope, with_school_name=viewer['is_operator']))
    # 先頭にBOMを付けると、日本語版のExcelでも文字化けせずに開ける
    body = '\ufeff' + buf.getvalue()
    filename = f'feyn_students_{core.today_jst().strftime("%Y%m%d")}.csv'
    return Response(body, mimetype='text/csv', headers={   # charset=utf-8 はFlaskが付ける
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Cache-Control': 'no-store',   # 個人情報を含むので、ブラウザや途中の経路に残さない
    })


@dashboard_bp.route('/api/dashboard/metrics')
def dashboard_metrics():
    """利用状況（会話の開始・完了・離脱・有料化など）の集計。先生は自分の組織の生徒だけが対象"""
    error, viewer = _viewer_or_error()
    if error:
        return error

    days = request.args.get('days', 30, type=int) or 30
    return jsonify(core.compute_usage_metrics(_read_scope(viewer), days=days))


@dashboard_bp.route('/api/dashboard/usage')
def dashboard_usage():
    """Gemini無料枠の今日の使用状況（モデル別の成功/失敗回数）。生徒のデータは含まない"""
    error, _ = _viewer_or_error()
    if error:
        return error

    today = core.today_jst()
    with core.get_db() as conn:
        rows = conn.execute(
            'SELECT model, endpoint, success, created_at FROM api_calls ORDER BY id DESC LIMIT 2000'
        ).fetchall()

    # created_atはUTCで記録されている（SQLiteは文字列、Postgresはdatetime）ので、日本時間の日付で比べる
    today_rows = [r for r in rows if core.to_jst_date(r['created_at']) == today]

    by_model = {}
    for r in today_rows:
        m = by_model.setdefault(r['model'], {'success': 0, 'fail': 0})
        m['success' if r['success'] else 'fail'] += 1

    return jsonify({
        'date':        today.isoformat(),
        'total_calls': len(today_rows),
        'by_model':    by_model,
        'models_order': core.ALL_MODELS,
    })


@dashboard_bp.route('/api/operator/access-logs')
def operator_access_logs():
    """運営アカウントの閲覧の記録（新しい順）。運営アカウントだけが見られる"""
    viewer = load_viewer()
    if not viewer:
        return jsonify({'error': 'ログインしてください'}), 401
    if not viewer['is_operator']:
        return jsonify({'error': '権限がありません'}), 403
    offset = request.args.get('offset', 0, type=int) or 0
    rows, has_more = schools.list_operator_access(limit=100, offset=offset)
    logs = []
    for r in rows:
        logs.append({
            'at':            core.to_jst_str(r['created_at']),
            'operator_name': r['operator_name'] or f"（削除済み #{r['operator_id']}）",
            'operator_email': r['operator_email'] or '',
            'action':        schools.ACCESS_ACTIONS.get(r['action'], r['action']),
            'student_id':    r['student_id'],
            'student_name':  (r['student_name'] or f"（削除済み #{r['student_id']}）") if r['student_id'] else None,
            'school_name':   r['school_name'],
            'target':        r['target'],
        })
    return jsonify({'logs': logs, 'has_more': has_more})
