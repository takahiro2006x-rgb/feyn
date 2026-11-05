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


def test_legal_link_is_kept_and_theme_follows_app_setting_or_os():
    # LPには切り替えボタンを置かない。アプリで保存した設定（feynTheme）か、OSの設定に従う
    assert 'href="/tokushoho"' in LANDING
    assert 'id="themeToggle"' not in LANDING and 'theme-toggle' not in LANDING
    head = LANDING[:LANDING.index('</head>')]
    assert "localStorage.getItem('feynTheme')" in head
    assert 'prefers-color-scheme: dark' in head
    assert "localStorage.setItem('feynTheme'" not in LANDING


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


def test_pricing_is_shown_as_plan_cards_with_shared_features():
    # スマホでも横スクロールさせず、プランごとのカードを縦に並べる。両プランで同じものは表の外にまとめる
    assert '<table class="ptable">' not in LANDING
    assert 'ptableScroll' not in LANDING and 'swipe-hint' not in LANDING   # 横スクロール用のCSS・JSは残さない
    assert LANDING.count('<article class="plan') == 2
    assert '<article class="plan paid"' in LANDING
    common = LANDING[LANDING.index('class="plan-common"'):]
    common = common[:common.index('</div>')]
    assert 'ヒント・答えを見る（回数の制限なし）' in common
    assert '苦手ノート' in common


def test_hints_and_answers_have_no_plan_limit_in_the_app():
    # 料金の書き方の根拠: ヒント・答えの API はプランで回数を分けていない
    src = open('blueprints/chat.py', encoding='utf-8').read()
    for route in ["'/api/hint'", "'/api/reveal'"]:
        body = src[src.index(route):]
        body = body[:body.index('@chat_bp.route', 1)] if '@chat_bp.route' in body[1:] else body
        assert 'check_and_consume_life' not in body and 'subscription' not in body


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


def test_landing_file_has_no_contact_address_in_it():
    # 送り先は設定（CONTACT_EMAIL）から入れる。ファイルには「準備中」だけを入れておき、ダミーのアドレスを置かない
    assert 'mailto:' not in LANDING and 'example.com' not in LANDING
    assert 'CONTACT:START' in LANDING and 'CONTACT:END' in LANDING
    assert '準備中' in LANDING[LANDING.index('CONTACT:START'):LANDING.index('CONTACT:END')]


@pytest.mark.parametrize('value', [None, '', 'contact@example.com', 'info@school.example.org', 'a@b.test', 'not-an-email'])
def test_contact_button_is_hidden_without_a_real_address(client, monkeypatch, value):
    if value is None:
        monkeypatch.delenv('CONTACT_EMAIL', raising=False)
    else:
        monkeypatch.setenv('CONTACT_EMAIL', value)
    page = client.get('/landing').get_data(as_text=True)
    assert 'mailto:' not in page and 'id="contactBtn"' not in page
    assert '導入のご相談の窓口は、ただいま準備中です。' in page


def test_contact_button_uses_the_configured_address(client, monkeypatch):
    monkeypatch.setenv('CONTACT_EMAIL', 'hello@feyn-school.jp')
    resp = client.get('/landing')
    assert resp.status_code == 200 and resp.mimetype == 'text/html'
    page = resp.get_data(as_text=True)
    assert page.count('href="mailto:') == 1
    assert 'href="mailto:hello@feyn-school.jp?subject=' in page
    assert 'id="contactBtn" data-track-contact' in page     # 計測もそのまま
    assert '準備中' not in page
    assert 'CONTACT:START' not in page
    # 差し替えるのは相談の欄だけ（ほかはファイルのまま）
    rest = LANDING[:LANDING.index('<!-- CONTACT:START')]
    assert page.startswith(rest)


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


def test_band_tells_what_the_reader_gets():
    band = LANDING[LANDING.index('<section class="facts"'):]
    band = band[:band.index('</section>')]
    for text in ['質問で返してくる', '苦手ノートに残る', '1・3・7・14・30日後', '7</em>科目']:
        assert text in band, text
    assert '区分' not in band and '種類' not in band   # 機能の数ではなく、何ができるか


def test_voices_section_stays_hidden_until_real_voices_are_added():
    # 架空の声は書かない。中身が【要確認】のダミーの間は data-placeholder で表示しない
    section = LANDING[LANDING.index('<section class="voices"'):]
    section = section[:section.index('</section>')]
    assert 'data-placeholder' in section.split('>')[0]
    assert '[data-placeholder] { display: none !important; }' in LANDING
    quotes = re.findall(r'<blockquote><p>(.*?)</p></blockquote>', section)
    assert quotes and all('【要確認' in q for q in quotes)


def test_parents_block_covers_card_cancel_and_data():
    parents = LANDING[LANDING.index('class="parents"'):]
    parents = parents[:parents.index('</aside>')]
    for text in ['カードの登録がいりません', 'いつでも解約', 'href="/privacy"', 'Gemini', 'Groq']:
        assert text in parents, text


def test_ogp_image_and_card_are_set(client):
    assert '<meta property="og:image" content="https://feyn-28kg.onrender.com/static/lp/ogp.png">' in LANDING
    assert '<meta property="og:url" content="https://feyn-28kg.onrender.com/landing">' in LANDING
    assert '<meta name="twitter:card" content="summary_large_image">' in LANDING
    resp = client.get('/static/lp/ogp.png')
    assert resp.status_code == 200 and resp.mimetype == 'image/png'
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(resp.data)).size == (1200, 630)


def test_contact_button_looks_like_the_other_main_buttons(client, monkeypatch):
    monkeypatch.setenv('CONTACT_EMAIL', 'hello@feyn-school.jp')
    page = client.get('/landing').get_data(as_text=True)
    assert 'class="btn btn-lg" id="contactBtn"' in page      # 赤の塗りつぶし（白地に赤枠ではない）
    assert 'btn-ghost' not in page


def test_teacher_section_opens_cleanly_from_a_direct_link():
    assert 'section[id] { scroll-margin-top:' in LANDING
    # 先生向けのセクションでは、スマホの生徒向け固定ボタンをかぶせない
    assert "document.getElementById('teachers')]" in LANDING
