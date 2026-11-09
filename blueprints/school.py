# -*- coding: utf-8 -*-
"""塾・学校（組織）への参加と退出、および「だれが見ているか」の確認。

- 生徒: マイページで参加コードを入れると、その組織に入る。抜けると DEFAULT に戻る
- 先生: 先生コードを入れると、その組織の先生になる（共通の先生コードで登録した先生向け）
- load_viewer(): 先生・運営アカウントの画面とAPIで、権限と見られる範囲を毎回DBから確かめる
  （ログイン時にクッキーへ入れた値だと、組織を移ったり運営の印を外したりしても古いままになるため）
"""
from flask import Blueprint, jsonify, request, session

import core
import ratelimit
import schools

school_bp = Blueprint('school', __name__)


def load_viewer():
    """ログイン中の人の、役割と組織。ログインしていない・アカウントが無ければNone。
    戻り値: {'id', 'role', 'school_id', 'is_operator', 'in_default'}"""
    user_id = session.get('user_id')
    if not user_id:
        return None
    with core.get_db() as conn:
        row = conn.execute(
            'SELECT id, role, school_id, is_operator FROM users WHERE id = ?', (user_id,)
        ).fetchone()
    if row is None:
        return None
    default_id = core.default_school_id()
    school_id = row['school_id'] or default_id
    return {
        'id':          row['id'],
        'role':        row['role'],
        'school_id':   school_id,
        'is_operator': bool(row['is_operator']),
        'in_default':  school_id == default_id,
    }


def can_open_dashboard(viewer):
    """先生の画面（ダッシュボード・生徒の詳細）を開けるか"""
    return bool(viewer) and (viewer['is_operator'] or viewer['role'] == 'teacher')


def _school_info(viewer):
    if viewer['in_default']:
        return None
    school = schools.get_school(viewer['school_id'])
    return {'id': school['id'], 'name': school['name']} if school else None


@school_bp.route('/api/school')
def my_school():
    """今どの組織に入っているか（DEFAULT なら school は null）"""
    viewer = load_viewer()
    if not viewer:
        return jsonify({'error': 'ログインしてください'}), 401
    return jsonify({'school': _school_info(viewer), 'role': viewer['role']})


@school_bp.route('/api/school/join', methods=['POST'])
def join_school():
    """生徒は参加コード、先生は先生コードで組織に入る（別の組織にいたら移る）"""
    viewer = load_viewer()
    if not viewer:
        return jsonify({'error': 'ログインしてください'}), 401
    if viewer['role'] not in ('student', 'teacher'):
        return jsonify({'error': '権限がありません'}), 403

    data = request.get_json(silent=True) or {}
    code = (data.get('code') or '').strip()
    if not code:
        return jsonify({'error': 'コードを入力してください'}), 400

    # 総当たりで他の塾のコードを当てられないよう、間違いが続いたら一定時間受け付けない
    scope = 'join-code' if viewer['role'] == 'student' else 'teacher-code'
    account = f"user{viewer['id']}"
    wait = ratelimit.check(scope, ratelimit.client_ip(), account)
    if wait:
        return jsonify({'error': ratelimit.wait_message(wait)}), 429

    if viewer['role'] == 'student':
        school = schools.find_by_join_code(code)
        error = '参加コードが正しくありません。塾・学校から受け取ったコードを確認してください'
    else:
        school = schools.find_by_teacher_code(code)
        error = '先生コードが正しくありません'
    if not school:
        ratelimit.record_failure(scope, ratelimit.client_ip(), account)
        return jsonify({'error': error}), 400
    ratelimit.record_success(scope, ratelimit.client_ip(), account)

    with core.get_db() as conn:
        conn.execute('UPDATE users SET school_id = ? WHERE id = ?', (school['id'], viewer['id']))
        conn.commit()
    session['school_id'] = school['id']
    return jsonify({'ok': True, 'school': {'id': school['id'], 'name': school['name']}})


@school_bp.route('/api/school/leave', methods=['POST'])
def leave_school():
    """生徒が組織から抜ける（DEFAULT に戻る。学習の記録はそのまま本人に残る）"""
    viewer = load_viewer()
    if not viewer:
        return jsonify({'error': 'ログインしてください'}), 401
    if viewer['role'] != 'student':
        return jsonify({'error': '先生アカウントは組織から抜けられません。運営に相談してください'}), 403
    default_id = core.default_school_id()
    with core.get_db() as conn:
        conn.execute('UPDATE users SET school_id = ? WHERE id = ?', (default_id, viewer['id']))
        conn.commit()
    session['school_id'] = default_id
    return jsonify({'ok': True, 'school': None})
