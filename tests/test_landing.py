# -*- coding: utf-8 -*-
"""LP（landing.html）の、壊れると困る約束事のテスト。

見た目はブラウザで確認するので、ここでは「導線」と「事実の表記」だけを確かめる。
"""
import re

import pytest

LANDING = open('landing.html', encoding='utf-8').read()
LOGIN = open('login.html', encoding='utf-8').read()


def test_landing_page_is_served(client):
    resp = client.get('/landing')
    assert resp.status_code == 200
    assert 'その「わかった」、' in resp.get_data(as_text=True)


def test_start_buttons_go_straight_to_signup():
    starts = re.findall(r'<a [^>]*href="([^"]+)"[^>]*>無料で始める</a>', LANDING)
    assert starts, '「無料で始める」ボタンが見つからない'
    assert set(starts) == {'/login?signup=1'}


def test_login_page_opens_in_signup_mode_from_the_landing_page():
    assert "get('signup') === '1'" in LOGIN
    assert "setMode('signup')" in LOGIN


def test_buttons_switch_to_back_to_app_when_logged_in():
    # ログイン中は「アプリへ戻る」に変わる（data-auth-cta の付いたリンクを /api/me の結果で書き換える）
    assert LANDING.count('data-loggedin-text="アプリへ戻る"') >= 4
    assert 'data-loggedin-hide>ログイン</a>' in LANDING
    assert "fetch('/api/me')" in LANDING


def test_legal_link_and_theme_toggle_are_kept():
    assert 'href="/tokushoho"' in LANDING
    assert 'id="themeToggle"' in LANDING
    assert "localStorage.setItem('feynTheme'" in LANDING   # アプリ本体と同じ保存先


@pytest.mark.parametrize('fact', [
    '¥980',          # 受験生プランの価格
    '月（税込）',
    'ライフ3つ',      # 無料プランの制限
    '2時間で1つ回復',
])
def test_pricing_facts_match_the_app(fact):
    assert fact in LANDING


def test_mobile_sticky_button_starts_hidden_and_goes_to_signup():
    # スマホの画面下の固定ボタン。最初は隠しておき、ページ内の申し込みボタンが見えていないときだけ出す
    sticky = LANDING[LANDING.index('id="stickyCta"') - 40:LANDING.index('id="stickyCta"') + 400]
    assert 'is-hidden' in sticky
    assert 'href="/login?signup=1"' in sticky
    assert 'IntersectionObserver' in LANDING


def test_animations_are_off_for_reduced_motion_and_without_js():
    # 演出の「隠れた初期状態」は、html に js の印が付いたときだけ効く。
    # 印は「動きを減らす」設定の人には付けない → その人と、JSが動かない環境では最初から全部見える
    head = LANDING[:LANDING.index('</head>')]
    assert "prefers-reduced-motion: reduce" in head
    assert "classList.add('js')" in head
    css = LANDING[LANDING.index('<style>'):LANDING.index('</style>')]
    hiding = re.compile(r'opacity:\s*0\s*[;}]|clip-path:\s*inset\(0 100%|stroke-dashoffset:\s*1\s*[;}]')
    hidden_rules = [line for line in css.splitlines()
                    if hiding.search(line) and '{' in line
                    and not line.strip().startswith(('from', 'to', '0%', '60%', '100%', '@keyframes'))]
    assert hidden_rules, '演出の初期状態が見つからない'
    for rule in hidden_rules:
        assert rule.strip().startswith('.js '), f'.js が付いていない隠しルール: {rule.strip()}'


def test_ogp_has_japanese_locale():
    assert '<meta property="og:locale" content="ja_JP">' in LANDING


def test_does_not_claim_to_be_completely_free():
    # 以前のLPは有料プランがあるのに「完全無料」と書いていた
    assert '完全無料' not in LANDING
