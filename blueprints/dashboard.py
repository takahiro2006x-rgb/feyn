# -*- coding: utf-8 -*-
"""先生ダッシュボードAPI（生徒一覧・生徒詳細・課題出題・パスワード再発行・使用量）"""
import random
import string
from datetime import date

from flask import Blueprint, jsonify, request, session
from werkzeug.security import generate_password_hash

import core
import tutoring

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/api/dashboard/student/<int:student_id>')
def dashboard_student(student_id):
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401
    if session.get('user_role') != 'teacher':
        return jsonify({'error': '権限がありません'}), 403

    school_id = session.get('school_id', core.default_school_id())
    with core.get_db() as conn:
        student = conn.execute(
            "SELECT id, name, email, subscription_status FROM users WHERE id = ? AND role = 'student' AND school_id = ?",
            (student_id, school_id)
        ).fetchone()
        if not student:
            return jsonify({'error': '生徒が見つかりません'}), 404

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
        assignment_rows = conn.execute(
            "SELECT id, subject, unit, status, created_at, completed_at "
            "FROM assignments WHERE student_id = ? ORDER BY (status = 'completed'), id DESC",
            (student_id,)
        ).fetchall()

    return jsonify({
        'student':     dict(student),
        'gaps':        [dict(r) for r in gap_rows],
        'progress':    [dict(r) for r in progress_rows],
        'sessions':    core.get_history_sessions(student_id),
        'assignments': [dict(r) for r in assignment_rows],
        'labels':      tutoring.GAP_TYPE_LABELS,
    })


@dashboard_bp.route('/api/dashboard/assignments', methods=['POST'])
def create_assignment():
    """先生が生徒に科目・単元を指定して課題を出す"""
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401
    if session.get('user_role') != 'teacher':
        return jsonify({'error': '権限がありません'}), 403

    data        = request.get_json()
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

    school_id = session.get('school_id', core.default_school_id())
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
                (session['user_id'], sid, subject, unit)
            )
        conn.commit()

    return jsonify({'ok': True, 'count': len(valid_ids)})


@dashboard_bp.route('/api/dashboard/reset-password', methods=['POST'])
def teacher_reset_password():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401
    if session.get('user_role') != 'teacher':
        return jsonify({'error': '権限がありません'}), 403

    data       = request.get_json()
    student_id = data.get('student_id')
    school_id  = session.get('school_id', core.default_school_id())

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
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401
    if session.get('user_role') != 'teacher':
        return jsonify({'error': '権限がありません'}), 403

    school_id = session.get('school_id', core.default_school_id())
    with core.get_db() as conn:
        students_rows = conn.execute(
            "SELECT id, name, email, created_at, last_login, subscription_status FROM users "
            "WHERE role = 'student' AND school_id = ? ORDER BY created_at DESC",
            (school_id,)
        ).fetchall()
        count_rows = conn.execute(
            'SELECT user_id, subject, COUNT(*) as cnt FROM session_logs GROUP BY user_id, subject'
        ).fetchall()
        last_rows = conn.execute(
            'SELECT user_id, MAX(completed_at) as last_at FROM session_logs GROUP BY user_id'
        ).fetchall()
        submission_rows = conn.execute(
            "SELECT a.subject, a.unit, a.completed_at, u.name as student_name "
            "FROM assignments a JOIN users u ON u.id = a.student_id "
            "WHERE a.status = 'completed' AND a.teacher_id = ? "
            "ORDER BY a.completed_at DESC LIMIT 20",
            (session['user_id'],)
        ).fetchall()

    counts_by_user = {}
    for row in count_rows:
        counts_by_user.setdefault(row['user_id'], {})[row['subject']] = row['cnt']
    last_by_user = {row['user_id']: row['last_at'] for row in last_rows}

    result = []
    for s in students_rows:
        user_counts = counts_by_user.get(s['id'], {})
        counts = {subj: user_counts.get(subj, 0) for subj in core.SUBJECTS}
        result.append({
            'id':         s['id'],
            'name':       s['name'],
            'email':      s['email'],
            'created_at': s['created_at'],
            'last_active': last_by_user.get(s['id']),
            'last_login': s['last_login'],
            'subscription_status': s['subscription_status'],
            'subjects':   counts,
            'total':      sum(counts.values()),
        })

    total_clears = sum(s['total'] for s in result)
    top_subject  = max(core.SUBJECTS, key=lambda subj: sum(s['subjects'][subj] for s in result)) if result else '—'

    return jsonify({
        'students': result,
        'summary': {
            'total_students': len(result),
            'total_clears':   total_clears,
            'top_subject':    top_subject,
        },
        'recent_submissions': [dict(r) for r in submission_rows],
    })


@dashboard_bp.route('/api/dashboard/usage')
def dashboard_usage():
    """Gemini無料枠の今日の使用状況（モデル別の成功/失敗回数）"""
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401
    if session.get('user_role') != 'teacher':
        return jsonify({'error': '権限がありません'}), 403

    today = str(date.today())
    with core.get_db() as conn:
        rows = conn.execute(
            'SELECT model, endpoint, success, created_at FROM api_calls ORDER BY id DESC LIMIT 2000'
        ).fetchall()

    # created_atはSQLiteでは文字列、Postgresではdatetimeで返るため str() で先頭10文字を比較する
    today_rows = [r for r in rows if str(r['created_at'])[:10] == today]

    by_model = {}
    for r in today_rows:
        m = by_model.setdefault(r['model'], {'success': 0, 'fail': 0})
        m['success' if r['success'] else 'fail'] += 1

    return jsonify({
        'date':        today,
        'total_calls': len(today_rows),
        'by_model':    by_model,
        'models_order': core.ALL_MODELS,
    })
