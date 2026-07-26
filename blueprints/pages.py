# -*- coding: utf-8 -*-
"""HTML/静的ファイルの配信ルート"""
from flask import Blueprint, abort, redirect, send_from_directory, session

pages_bp = Blueprint('pages', __name__)

# DBや.envを外部に配信しないよう、公開ファイルはホワイトリスト方式にする
ALLOWED_STATIC = {'style.css', 'script.js'}


@pages_bp.route('/')
def index():
    if not session.get('user_id'):
        return redirect('/landing')
    return send_from_directory('.', 'index.html')


@pages_bp.route('/landing')
def landing_page():
    # ログイン中でもLPを見られるようにする（LP側でログイン状態に応じてCTAを出し分ける）
    return send_from_directory('.', 'landing.html')


@pages_bp.route('/tokushoho')
def tokushoho_page():
    # 特定商取引法に基づく表記。ログイン有無に関わらず誰でも見られる
    return send_from_directory('.', 'tokushoho.html')


@pages_bp.route('/login')
def login_page():
    if session.get('user_id'):
        return redirect('/')
    return send_from_directory('.', 'login.html')


# 学習のきろく・苦手ノートはマイページのタブに統合したため、旧URLはそちらへ誘導する
@pages_bp.route('/history')
def history_page():
    if not session.get('user_id'):
        return redirect('/login')
    return redirect('/mypage?tab=history')


@pages_bp.route('/gaps')
def gaps_page():
    if not session.get('user_id'):
        return redirect('/login')
    return redirect('/mypage?tab=gaps')


@pages_bp.route('/mypage')
def mypage_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory('.', 'mypage.html')


@pages_bp.route('/dashboard')
def dashboard_page():
    if not session.get('user_id'):
        return redirect('/login')
    if session.get('user_role') != 'teacher':
        return redirect('/')
    return send_from_directory('.', 'dashboard.html')


@pages_bp.route('/dashboard/student/<int:student_id>')
def dashboard_student_page(student_id):
    if not session.get('user_id'):
        return redirect('/login')
    if session.get('user_role') != 'teacher':
        return redirect('/')
    return send_from_directory('.', 'student_detail.html')


# ===== PWA用ファイル =====
# sw.jsはルート直下（/sw.js）で配信することで、スコープをアプリ全体にする
@pages_bp.route('/manifest.json')
def manifest():
    return send_from_directory('.', 'manifest.json')


@pages_bp.route('/sw.js')
def service_worker():
    return send_from_directory('.', 'sw.js')


@pages_bp.route('/icons/<path:filename>')
def icons(filename):
    return send_from_directory('icons', filename)


@pages_bp.route('/<path:path>')
def static_files(path):
    if path not in ALLOWED_STATIC:
        abort(404)
    return send_from_directory('.', path)
