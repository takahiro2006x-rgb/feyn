# -*- coding: utf-8 -*-
"""チャット画面（index.html・style.css・script.js）が docs/DESIGN.md のルールを守っていることのテスト。
見た目はブラウザで確認するので、ここでは「壊れると困る約束事」だけを確かめる"""
import json
import re
import shutil
import subprocess

import pytest

HTML = open('index.html', encoding='utf-8').read()
CSS = open('style.css', encoding='utf-8').read()
JS = open('script.js', encoding='utf-8').read()
NODE = shutil.which('node')


def _css_outside_tokens():
    """色のトークンを決めている所（:root と [data-theme="dark"]）を除いたCSS"""
    body = re.sub(r'/\*.*?\*/', '', CSS, flags=re.S)
    body = re.sub(r':root\s*\{[^}]*\}', '', body, count=1)
    body = re.sub(r'\[data-theme="dark"\]\s*\{[^}]*\}', '', body, count=1)
    return body


def test_colors_come_only_from_the_tokens():
    # 色は :root のトークンだけ。部品のCSSに色を直接書かない（docs/DESIGN.md 2）
    stray = re.findall(r'#[0-9A-Fa-f]{3,8}\b', _css_outside_tokens())
    assert stray == [], stray


def test_radius_and_shadow_use_the_steps():
    body = _css_outside_tokens()
    radii = set(re.findall(r'border-radius:\s*([^;]+);', body))
    allowed = re.compile(r'^(var\(--r-(sm|md|lg|pill)\)|50%|0)( (var\(--r-(sm|md|lg|pill)\)|0))*$')
    assert all(allowed.match(r.strip()) for r in radii), radii
    shadows = set(re.findall(r'box-shadow:\s*([^;]+);', body))
    for sh in shadows:
        assert sh.startswith(('var(--shadow-', 'inset 0 0 0', '0 0 0 3px var(--grid-line)', '0 1px 0 var(--red-dark)',
                              '0 0 0 0 color-mix(in srgb, var(--red)', '0 0 0 6px transparent', 'none')), sh
    assert 'rgba(' not in body.replace('rgba(0, 0, 0', ''), 'rgba の直書きはトークンにする'


def test_dark_mode_is_the_night_blackboard_like_the_lp():
    dark = re.search(r'\[data-theme="dark"\]\s*\{([^}]*)\}', CSS).group(1)
    assert '--paper:      #131A16' in dark and '--red:        #FF6B5E' in dark
    lp = open('landing.html', encoding='utf-8').read()
    assert '--paper:      #131A16' in lp     # LPと同じ値


def test_font_is_the_same_as_the_lp():
    assert 'family=Zen+Kaku+Gothic+New' in HTML and 'M+PLUS+Rounded' not in HTML
    assert '--sans: "Zen Kaku Gothic New"' in CSS


def test_buttons_have_no_emoji_and_icon_buttons_have_labels():
    emoji = re.compile('[\U0001F300-\U0001FAFF☀-➿]')
    for tag in re.findall(r'<button[^>]*>.*?</button>', HTML, flags=re.S):
        text = re.sub(r'<svg.*?</svg>', '', tag, flags=re.S)
        assert not emoji.search(text), tag[:80]
    for btn_id in ['mathBtn', 'micBtn', 'menuBtn']:
        tag = re.search(rf'<button[^>]*id="{btn_id}"[^>]*>', HTML).group(0)
        assert 'aria-label="' in tag, btn_id
    assert re.search(r'<button class="send-btn"[^>]*aria-label="送信"', HTML)


def test_tap_targets_are_at_least_44px():
    for sel in ['.icon-btn', '.send-btn']:
        rule = re.search(re.escape(sel) + r' \{([^}]*)\}', CSS).group(1)
        size = int(re.search(r'(?:width|height): (\d+)px', rule).group(1))
        assert size >= 44, sel


def test_features_are_all_still_on_the_page():
    # 納得度・ライフ・ヒント・答え・写真・音声・数式・結果・使い方・テーマ・マイページ
    for marker in ['class="xp-fill"', 'class="xp-percent"', 'id="livesDisplay"', 'id="hintBtn"', 'id="revealBtn"',
                   'id="photoUploadInput"', 'id="micBtn"', 'id="mathBtn"', 'id="mathModalOverlay"', 'id="resultOverlay"',
                   'id="onboardingOverlay"', 'id="themeToggle"', 'id="mypageBtn"', 'id="logoutBtn"', 'id="securityModal"',
                   'rel="manifest"', 'katex', 'mathlive']:
        assert marker in HTML, marker


def test_clear_celebration_is_skipped_for_reduced_motion():
    fn = JS[JS.index('function launchConfetti()'):]
    assert fn.index("prefers-reduced-motion: reduce") < fn.index("getElementById('confettiCanvas')")
    assert 'clear-stamp' not in JS and 'result-emoji' not in JS    # 丸い「納得！」の判子はやめた
    assert '@media (prefers-reduced-motion: reduce)' in CSS


def test_phone_menu_is_reachable_and_closable():
    assert 'aria-controls="sideMore"' in HTML and 'aria-expanded="false"' in HTML
    assert ".sidebar.is-open .side-more { display: flex; }" in CSS
    assert "e.key === 'Escape' && sidebar.classList.contains('is-open')" in JS


@pytest.mark.skipif(NODE is None, reason='Node.js が無い環境ではスキップ')
@pytest.mark.parametrize('text, expected', [
    ('じゃあ聞くけど、何のせい？ 壁は何をしてるの？', 'じゃあ聞くけど、何のせい？ <span class="ask">壁は何をしてるの？</span>'),
    ('なるほど！ やっとわかった。', 'なるほど！ やっとわかった。'),                                   # 質問が無ければ印を付けない
    ('力積は $F\\Delta t$ だよね。いくつになる？', '力積は $F\\Delta t$ だよね。<span class="ask">いくつになる？</span>'),
    ('$\\Delta p$ はいくつ？', '$\\Delta p$ はいくつ？'),                                             # 数式を含む文は印を付けない
    ('a &lt; b ってどうして？', '<span class="ask">a &lt; b ってどうして？</span>'),
])
def test_feyn_question_is_marked_with_red_pen(text, expected):
    fn = JS[JS.index('function markQuestion'):JS.index('function addMessage')]
    program = fn + f'\nconsole.log(JSON.stringify(markQuestion({json.dumps(text)})));'
    out = subprocess.run([NODE, '-e', program], capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == expected


def test_font_sizes_use_the_scale():
    # 12（注記）・14（小）・16（本文）・18・20・24（見出し）と、大きな数字・判子・数式だけ
    sizes = {int(x) for x in re.findall(r'font-size: (\d+)px', CSS)}
    assert sizes <= {12, 14, 16, 18, 20, 22, 24, 30, 32}, sorted(sizes)


def test_chat_is_a_ruled_notebook_with_red_pen():
    # 2026-11-05 に選んだC案「ノートと赤ペン」: 罫線ノートの地・左の赤い余白線・Feynの質問は赤ペンの字
    assert 'repeating-linear-gradient(transparent 0 31px, var(--note-rule) 31px 32px)' in CSS
    assert 'var(--margin-line)' in CSS
    feyn = re.search(r'\.message\.feyn \.bubble \{([^}]*)\}', CSS).group(1)
    assert 'border-left: 3px solid var(--red)' in feyn
    ask = re.search(r'\.bubble \.ask \{([^}]*)\}', CSS).group(1)
    assert 'color: var(--red)' in ask
