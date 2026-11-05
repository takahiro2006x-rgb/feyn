# -*- coding: utf-8 -*-
"""チャット以外の画面も、docs/DESIGN.md の共通のデザイン（theme.css）を使っていることのテスト"""
import re

import pytest

PAGES = ['index.html', 'mypage.html', 'login.html', 'dashboard.html', 'student_detail.html',
         'tokushoho.html', 'privacy.html', 'terms.html']
EMOJI = re.compile('[\U0001F000-\U0001FAFF☀-⛿⭐⏩-⏺]')


@pytest.mark.parametrize('path', PAGES)
def test_page_uses_the_shared_tokens_and_font(path):
    html = open(path, encoding='utf-8').read()
    assert re.search(r'href="/?theme\.css"', html), path
    assert 'Zen+Kaku+Gothic+New' in html and 'M+PLUS+Rounded' not in html
    # ページの中で色の変数を決め直さない（theme.css のトークンを使う）
    style = ''.join(re.findall(r'<style>(.*?)</style>', html, flags=re.S))
    assert not re.search(r':root\s*\{[^}]*#[0-9A-Fa-f]{6}', style), path


@pytest.mark.parametrize('path', PAGES)
def test_buttons_and_headings_have_no_emoji(path):
    html = open(path, encoding='utf-8').read()
    for tag in re.findall(r'<(button|h1|h2|a)\b[^>]*>(.*?)</\1>', html, flags=re.S):
        text = re.sub(r'<svg.*?</svg>', '', tag[1], flags=re.S)
        assert not EMOJI.search(text), (path, text[:60])


def test_theme_css_is_served(client):
    resp = client.get('/theme.css')
    assert resp.status_code == 200 and resp.mimetype == 'text/css'


@pytest.mark.parametrize('path', ['mypage.html', 'student_detail.html'])
def test_conversation_logs_use_the_chat_bubbles(path):
    """会話ログの吹き出しは、チャット画面と同じ形。生徒の発言を赤で塗らない（赤は Feyn の質問と決定ボタンだけ）"""
    style = ''.join(re.findall(r'<style>(.*?)</style>', open(path, encoding='utf-8').read(), flags=re.S))
    user_rule = re.search(r'\.msg\.user \.bubble \{([^}]*)\}', style).group(1)
    feyn_rule = re.search(r'\.msg\.feyn \.bubble \{([^}]*)\}', style).group(1)
    assert 'var(--me-bubble)' in user_rule and 'var(--red)' not in user_rule
    assert 'var(--feyn-bubble)' in feyn_rule and 'border-left: 3px solid var(--red)' in feyn_rule


def test_student_detail_header_keeps_long_names_visible():
    """スマホで、長い名前が「…」で切れたり、プランの印が画面の外に出たりしない"""
    html = open('student_detail.html', encoding='utf-8').read()
    assert 'class="header-name"' in html
    assert '.header-title .plan-tag { flex-shrink: 0; }' in html
