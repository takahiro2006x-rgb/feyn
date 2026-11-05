# -*- coding: utf-8 -*-
"""LP（landing.html）を返すときの差し替え。DBにもAIにも触れないので、公開前チェックのスクリプトからも使える。

- 相談ボタンの送り先は環境変数 CONTACT_EMAIL。
  未設定・ダミー（example.com など）・形がおかしいときは None → LPには「準備中」の文のまま出す
"""
import html
import os
import re
from urllib.parse import quote

ROOT = os.path.dirname(os.path.abspath(__file__))
LANDING_PATH = os.path.join(ROOT, 'landing.html')

_CONTACT_EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+')
# 例示用・テスト用のドメイン（RFC 2606 / 6761）
_DUMMY_DOMAIN_RE = re.compile(
    r'(?:^|\.)(?:example\.(?:com|net|org)|[^.]+\.(?:example|test|invalid|localhost))$|^(?:example|test|invalid|localhost)$')

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


def contact_email():
    value = (os.environ.get('CONTACT_EMAIL') or '').strip()
    if not _CONTACT_EMAIL_RE.fullmatch(value):
        return None
    if _DUMMY_DOMAIN_RE.search(value.rsplit('@', 1)[1].lower()):
        return None
    return value


def render_landing():
    """LPのHTML。相談の欄（CONTACT:START〜END）だけ、設定に合わせて差し替える"""
    with open(LANDING_PATH, encoding='utf-8') as f:
        page = f.read()
    email = contact_email()
    start, end = page.find(CONTACT_START), page.find(CONTACT_END)
    if email and start != -1 and end != -1:
        button = CONTACT_BUTTON.format(email=html.escape(email, quote=True), subject=CONTACT_SUBJECT)
        page = page[:start] + button + page[end + len(CONTACT_END):]
    return page
