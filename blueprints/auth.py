# -*- coding: utf-8 -*-
"""認証API（signup/login/logout/パスワードリセット/自分の情報取得）"""
from datetime import date

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import core
import db

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/api/signup', methods=['POST'])
def signup():
    data         = request.get_json()
    email        = data.get('email', '').strip().lower()
    name         = data.get('name', '').strip()
    password     = data.get('password', '')
    teacher_code = data.get('teacher_code', '').strip()

    security_question = data.get('security_question', '').strip()
    security_answer   = data.get('security_answer', '').strip().lower()

    if not email or not name or not password:
        return jsonify({'error': 'すべての項目を入力してください'}), 400
    if '@' not in email:
        return jsonify({'error': '正しいメールアドレスを入力してください'}), 400
    if len(name) > 20:
        return jsonify({'error': 'ニックネームは20文字以内にしてください'}), 400
    if len(password) < 8:
        return jsonify({'error': 'パスワードは8文字以上にしてください'}), 400
    if teacher_code and teacher_code != core.TEACHER_CODE:
        return jsonify({'error': '先生コードが正しくありません'}), 400
    if not security_question or security_question not in core.SECURITY_QUESTIONS:
        return jsonify({'error': '秘密の質問を選んでください'}), 400
    if not security_answer:
        return jsonify({'error': '秘密の質問の答えを入力してください'}), 400

    role = 'teacher' if teacher_code == core.TEACHER_CODE else 'student'
    school_id = core.default_school_id()

    try:
        with core.get_db() as conn:
            conn.execute(
                'INSERT INTO users (email, name, password_hash, role, security_question, security_answer_hash, last_login, school_id) '
                'VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)',
                (email, name, generate_password_hash(password), role,
                 security_question, generate_password_hash(security_answer), school_id)
            )
            conn.commit()
            user = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
            session.permanent      = True
            session['user_id']    = user['id']
            session['user_name']  = name
            session['user_role']  = role
            session['school_id']  = school_id
        return jsonify({'ok': True, 'name': name, 'role': role})
    except db.IntegrityError:
        return jsonify({'error': 'そのメールアドレスはすでに登録されています'}), 409


@auth_bp.route('/api/login', methods=['POST'])
def login():
    data     = request.get_json()
    email    = data.get('email', '').strip().lower()
    password = data.get('password', '')

    with core.get_db() as conn:
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': 'メールアドレスまたはパスワードが違います'}), 401

    with core.get_db() as conn:
        conn.execute('UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?', (user['id'],))
        conn.commit()

    session.permanent     = True
    session['user_id']    = user['id']
    session['user_name']  = user['name']
    session['user_role']  = user['role']
    session['school_id']  = user['school_id'] or core.default_school_id()
    return jsonify({'ok': True, 'name': user['name'], 'role': user['role']})


@auth_bp.route('/api/update-name', methods=['POST'])
def update_name():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    data = request.get_json()
    name = data.get('name', '').strip()

    if not name:
        return jsonify({'error': 'ニックネームを入力してください'}), 400
    if len(name) > 20:
        return jsonify({'error': 'ニックネームは20文字以内にしてください'}), 400

    with core.get_db() as conn:
        conn.execute('UPDATE users SET name = ? WHERE id = ?', (name, session['user_id']))
        conn.commit()
    session['user_name'] = name
    return jsonify({'ok': True})


@auth_bp.route('/api/set-security-question', methods=['POST'])
def set_security_question():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    data              = request.get_json()
    security_question = data.get('security_question', '').strip()
    security_answer   = data.get('security_answer', '').strip().lower()

    if not security_question or security_question not in core.SECURITY_QUESTIONS:
        return jsonify({'error': '秘密の質問を選んでください'}), 400
    if not security_answer:
        return jsonify({'error': '答えを入力してください'}), 400

    with core.get_db() as conn:
        conn.execute(
            'UPDATE users SET security_question = ?, security_answer_hash = ? WHERE id = ?',
            (security_question, generate_password_hash(security_answer), session['user_id'])
        )
        conn.commit()
    return jsonify({'ok': True})


@auth_bp.route('/api/security-question', methods=['POST'])
def security_question_api():
    data  = request.get_json()
    email = data.get('email', '').strip().lower()
    with core.get_db() as conn:
        user = conn.execute(
            'SELECT security_question FROM users WHERE email = ?', (email,)
        ).fetchone()
    # 登録済みメールアドレスかどうかを外部から判別できないよう、エラーは同一メッセージにする
    if not user or not user['security_question']:
        return jsonify({'error': 'このメールアドレスではリセットできません。メールアドレスを確認するか、先生に相談してください'}), 404
    return jsonify({'question': user['security_question']})


@auth_bp.route('/api/reset-password', methods=['POST'])
def reset_password():
    data         = request.get_json()
    email        = data.get('email', '').strip().lower()
    answer       = data.get('answer', '').strip().lower()
    new_password = data.get('new_password', '')

    if len(new_password) < 8:
        return jsonify({'error': 'パスワードは8文字以上にしてください'}), 400

    with core.get_db() as conn:
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

    if not user or not user['security_answer_hash']:
        return jsonify({'error': 'このメールアドレスではリセットできません。メールアドレスを確認するか、先生に相談してください'}), 404
    if not check_password_hash(user['security_answer_hash'], answer):
        return jsonify({'error': '答えが正しくありません'}), 401

    with core.get_db() as conn:
        conn.execute(
            'UPDATE users SET password_hash = ? WHERE email = ?',
            (generate_password_hash(new_password), email)
        )
        conn.commit()
    return jsonify({'ok': True})


@auth_bp.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})


@auth_bp.route('/api/me')
def me():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしていません'}), 401
    today = str(date.today())
    with core.get_db() as conn:
        user = conn.execute(
            'SELECT security_question, subscription_status FROM users WHERE id = ?',
            (session['user_id'],)
        ).fetchone()
        rows = conn.execute(
            'SELECT subject, COUNT(*) as cnt FROM session_logs WHERE user_id = ? GROUP BY subject',
            (session['user_id'],)
        ).fetchall()
        due = conn.execute(
            "SELECT COUNT(*) as cnt FROM knowledge_gaps "
            "WHERE user_id = ? AND ((status = 'resolved' AND next_review IS NOT NULL AND next_review <= ?) OR status = 'open')",
            (session['user_id'], today)
        ).fetchone()['cnt']
        open_assignments = conn.execute(
            "SELECT COUNT(*) as cnt FROM assignments WHERE student_id = ? AND status = 'open'",
            (session['user_id'],)
        ).fetchone()['cnt']
    subject_clears = {row['subject']: row['cnt'] for row in rows}
    return jsonify({
        'id':                    session['user_id'],
        'name':                  session['user_name'],
        'role':                  session.get('user_role', 'student'),
        'has_security_question': bool(user and user['security_question']),
        'total_clears':          sum(subject_clears.values()),
        'subject_clears':        subject_clears,
        'due_reviews':           due,
        'open_assignments':      open_assignments,
        'subscription_status':   user['subscription_status'] if user else 'free',
        'lives_status':          core.get_lives_status(session['user_id']),
    })
