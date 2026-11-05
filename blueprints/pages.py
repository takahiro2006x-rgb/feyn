# -*- coding: utf-8 -*-
"""HTML/静的ファイルの配信ルート"""
import html
from urllib.parse import quote

from flask import Blueprint, Response, abort, redirect, request, send_from_directory, session

import core
import ratelimit

pages_bp = Blueprint('pages', __name__)

# DBや.envを外部に配信しないよう、公開ファイルはホワイトリスト方式にする
ALLOWED_STATIC = {'style.css', 'script.js'}


@pages_bp.route('/')
def index():
    if not session.get('user_id'):
        return redirect('/landing')
    return send_from_directory('.', 'index.html')


# LPの「導入について相談する」の欄。landing.html には「準備中」の文が入っていて、
# CONTACT_EMAIL が正しく設定されているときだけ、ここでボタンに差し替える
CONTACT_START = '<!-- CONTACT:START'
CONTACT_END = '<!-- CONTACT:END -->'
CONTACT_SUBJECT = quote('Feynの導入について')
CONTACT_BUTTON = (
    '<p class="teacher-note">導入の方法は、まずはご相談ください。</p>\n'
    '          <div class="cta teacher-cta">\n'
    '            <a href="mailto:{email}?subject={subject}" class="btn btn-ghost" id="contactBtn" data-track-contact>導入について相談する</a>\n'
    '            <span class="micro">メールが開きます</span>\n'
    '          </div>'
)


def render_landing():
    """LPのHTML。相談の欄だけ、設定に合わせて差し替える（それ以外は landing.html のまま）"""
    with open('landing.html', encoding='utf-8') as f:
        page = f.read()
    email = core.contact_email()
    start, end = page.find(CONTACT_START), page.find(CONTACT_END)
    if email and start != -1 and end != -1:
        button = CONTACT_BUTTON.format(email=html.escape(email, quote=True), subject=CONTACT_SUBJECT)
        page = page[:start] + button + page[end + len(CONTACT_END):]
    return page


@pages_bp.route('/landing')
def landing_page():
    # ログイン中でもLPを見られるようにする（LP側でログイン状態に応じてCTAを出し分ける）
    return Response(render_landing(), mimetype='text/html')


@pages_bp.route('/tokushoho')
def tokushoho_page():
    # 特定商取引法に基づく表記。ログイン有無に関わらず誰でも見られる
    return send_from_directory('.', 'tokushoho.html')


@pages_bp.route('/privacy')
def privacy_page():
    # プライバシーポリシー（下書き）。docs/drafts/privacy_policy.md から scripts/build_legal_pages.py で作る
    return send_from_directory('.', 'privacy.html')


@pages_bp.route('/terms')
def terms_page():
    # 利用規約（下書き）。docs/drafts/terms_of_service.md から scripts/build_legal_pages.py で作る
    return send_from_directory('.', 'terms.html')


@pages_bp.route('/login')
def login_page():
    if session.get('user_id'):
        return redirect('/')
    return send_from_directory('.', 'login.html')


# ===== LPの効果測定 =====
# 記録するのは「LPが表示された」「どのボタンが押された」だけ。user_id・IPアドレスなど、人を特定する情報は保存しない。
# ログインなしで呼べるので、1つのIPからの記録は一定回数までにする（数はメモリ上で数えるだけで、保存しない）
LP_EVENT_LIMIT = 30
LP_EVENT_WINDOW_SECONDS = 600


@pages_bp.route('/api/lp-event', methods=['POST'])
def lp_event():
    data = request.get_json(force=True, silent=True) or {}
    kind, place = data.get('type'), data.get('place')
    if kind == 'view':
        event_type, place = 'lp_view', None
    elif kind == 'cta' and place in core.LP_CTA_PLACES:
        event_type = 'lp_cta_click'
    elif kind == 'contact':
        event_type, place = 'lp_contact_click', None
    else:
        return '', 204   # 決まった形以外は記録しない（エラーも返さない）

    key = f'lp-event:{ratelimit.client_ip()}'
    if ratelimit.limiter.retry_after(key, LP_EVENT_LIMIT, LP_EVENT_WINDOW_SECONDS) > 0:
        return '', 204
    ratelimit.limiter.record_failure(key)   # 「失敗の回数」の仕組みを、記録の回数を数えるのに使う
    core.log_event(event_type, user_id=None, mode=place)
    return '', 204


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
