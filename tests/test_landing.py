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


def test_forgetting_curve_chart_is_labeled_as_an_illustration():
    # 復習の説明は忘却曲線のグラフ（PC用・スマホ用）。説明用のイメージであることを必ず書く
    assert LANDING.count('class="curve-svg') == 2
    assert 'イメージ図' in LANDING
    for day in ['1日後', '3日後', '7日後', '14日後', '30日後']:
        assert day in LANDING


def test_feyn_introduction_section_was_removed():
    assert 'ちょっと生意気な、同じ受験生' not in LANDING


def test_ogp_has_japanese_locale():
    assert '<meta property="og:locale" content="ja_JP">' in LANDING


def test_does_not_claim_to_be_completely_free():
    # 以前のLPは有料プランがあるのに「完全無料」と書いていた
    assert '完全無料' not in LANDING


def test_price_table_scrolls_sideways_on_phones():
    # スマホでは無料プランと受験生プランを左右に並べたまま、表を横にスワイプして見比べる
    assert 'class="ptable-scroll"' in LANDING
    assert '横にスワイプして見比べてね' in LANDING
    css = LANDING[LANDING.index('<style>'):LANDING.index('</style>')]
    assert 'overflow-x: auto' in css
    assert 'position: sticky; left: 0' in css   # 項目名の列は左に残す
    assert '.ptable thead { display: none; }' not in css   # 以前の縦積み表示に戻っていない


def test_fact_numbers_are_not_shrunk_by_the_caption_style():
    # 数字のカウントアップは数字を span で包む。説明文の小さい文字の指定がその span に効かないこと
    css = LANDING[LANDING.index('<style>'):LANDING.index('</style>')]
    assert '.fact > span {' in css
    assert '.fact span {' not in css


def test_teacher_section_is_reachable_from_header_footer_and_faq():
    assert '<section id="teachers" class="teachers">' in LANDING
    assert '<a href="#teachers" class="nav-link">先生の方へ</a>' in LANDING
    assert '<a href="#teachers">塾・学校の先生へ</a>' in LANDING
    faq = LANDING[LANDING.index('id="faq"'):LANDING.index('id="finalCta"')]
    assert 'href="#teachers"' in faq
    assert LANDING.index('id="price"') < LANDING.index('id="teachers"') < LANDING.index('id="faq"')


def test_teacher_section_lists_only_real_features():
    section = LANDING[LANDING.index('id="teachers"'):LANDING.index('id="faq"')]
    for feature in ['学習状況', '苦手ノート', '会話ログ', '課題を出す', 'パスワードの再発行']:
        assert feature in section


def test_contact_button_uses_a_single_mailto_address():
    # 相談先のアドレスは1か所だけ。差し替え忘れに気づけるよう【要確認】の印を付けておく
    assert len(re.findall(r'href="mailto:', LANDING)) == 1
    assert 'id="contactBtn"' in LANDING
    contact = LANDING[LANDING.index('id="contactBtn"') - 400:LANDING.index('id="contactBtn"')]
    assert '【要確認】' in contact


def test_real_app_screenshots_exist_and_are_served(client):
    images = re.findall(r'<img src="(/static/lp/[^"]+\.webp)" width="\d+" height="\d+"[^>]*alt="[^"]+"', LANDING)
    assert len(images) >= 5
    for src in images:
        resp = client.get(src)
        assert resp.status_code == 200, src
        assert resp.mimetype == 'image/webp'


def test_feyn_is_described_consistently_as_the_student_you_teach():
    # Feynは「あなたが教える相手の生徒役（鋭く質問してくる）」。ライバル・あなたが先生、の言い方を混ぜない
    for old in ['ライバル', 'あなたが先生として', '赤ペンで']:
        assert old not in LANDING, old
    assert '生徒役' in LANDING
    assert 'Feynからの質問' in LANDING


def test_first_view_says_really_only_once_and_keeps_the_lead_short():
    body = LANDING[LANDING.index('<body>'):]
    assert body.count('ほんとに？') == 1
    lead = re.search(r'<p class="hero-lead">(.*?)</p>', LANDING).group(1)
    assert lead.count('。') <= 2


@pytest.mark.parametrize('svg_class, top, bottom', [('curve-svg pc', 30, 250), ('curve-svg sp', 30, 290)])
def test_forgetting_curve_does_not_overstate(svg_class, top, bottom):
    # 復習しない線はゼロに張り付かない。復習で戻る所も100%ちょうどにしない
    svg = LANDING[LANDING.index(f'<svg class="{svg_class}"'):]
    svg = svg[:svg.index('</svg>')]
    gray = re.search(r'class="curve-none" pathLength="1" d="([^"]+)"', svg).group(1)
    gray_y = [float(y) for y in re.findall(r'[ML][\d.]+ ([\d.]+)', gray)]
    assert max(gray_y) < bottom - (bottom - top) * 0.15
    dots_y = [float(y) for y in re.findall(r'class="rv-dot"[^>]*cy="([\d.]+)"', svg)]
    assert len(dots_y) == 5 and all(y > top + 5 for y in dots_y)


def test_forgetting_text_is_not_absolute():
    assert 'ほとんど忘れ' not in LANDING
    assert '数日でほとんど' not in LANDING
    assert 'イメージ図' in LANDING
