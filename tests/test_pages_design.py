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
