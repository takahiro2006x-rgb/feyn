# -*- coding: utf-8 -*-
"""LP用の画像を作るスクリプト（開発用。本番では動かない）。

  py scripts/lp_shots.py app          本物のアプリ画面（デモデータ）を撮って static/lp/ に WebP で保存
  py scripts/lp_shots.py lp OUTDIR    LPのページ全体を 375px と 1280px で撮って OUTDIR に保存
  py scripts/lp_shots.py ogp          OGP画像（1200×630）を static/lp/ogp.png に書き出す

安全のために:
- Render 上（RENDER）や DATABASE_URL が設定された環境では、何もせずに止まる
- データは一時フォルダの使い捨て SQLite に作る。feyn.db にも本番DBにも触れない
- AI（Gemini/Groq）は一度も呼ばない。会話・分析・ヒントは下の台本で置き換える
- 名前は「デモ生徒A」などの架空のもの、メールは example.com

必要なもの: pip install playwright pillow（ブラウザはPCに入っている Chrome を使う）
"""
import os
import sys

if os.environ.get('RENDER') or (os.environ.get('DATABASE_URL') or '').strip():
    sys.exit('本番の環境（RENDER または DATABASE_URL が設定済み）では実行できません。')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tempfile  # noqa: E402
import threading  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import db as db_module  # noqa: E402

if db_module.USE_POSTGRES:
    sys.exit('Postgres に接続する設定になっています。デモは SQLite でしか動かしません。')

_tmpdir = tempfile.mkdtemp(prefix='feyn-demo-')
db_module.DB_PATH = os.path.join(_tmpdir, 'demo.db')

import app as app_module  # noqa: E402
import core  # noqa: E402
import gap_analyzer  # noqa: E402
import llm  # noqa: E402

assert not db_module.USE_POSTGRES and db_module.DB_PATH.startswith(_tmpdir)

PORT = 5077
BASE = f'http://127.0.0.1:{PORT}'
OUT_STATIC = os.path.join(ROOT, 'static', 'lp')
PASSWORD = 'demo-pass-1234'   # 使い捨てDBの中だけで使う

# ========================================
# 台本（AIの代わりに返す文章）
# ========================================
SCRIPTS = {
    '物理': [
        'ボールが壁にぶつかって、同じ速さ 5 m/s ではね返ったとする。速さが変わってないなら、ボールの運動量も保存されてるってこと？',
        'じゃあ聞くけど、運動量が変わったのは何のせい？ 壁はボールに何をしてるの？',
        'なるほど…！ 壁から力を受けたなら、ボールだけを見ると保存されないんだね。ボールと壁をまとめて見ればいいのか。ありがとう、納得した！【会話終了】',
    ],
    '数学': [
        '放物線 y = x² と直線 y = x + 2 で囲まれた面積、公式で一発って聞いたけど、なんでその公式が使えるの？',
        'その公式、交点が2つあるときだけって言ったよね。じゃあ、なんで交点の x 座標が積分の範囲になるの？',
        'そっか、上の線から下の線を引いて積分してるんだ。やっとつながった！【会話終了】',
    ],
    '英語': [
        'If I were you の were って、主語が I なのになんで was じゃないの？',
        '「事実と違うから過去にずらす」って、今の話なのに過去形を使う理由になる？',
        '現実から距離がある気持ちを時制で表してるのか。なるほど、わかった！【会話終了】',
    ],
    '化学': [
        'pH が 1 小さくなると、水素イオンの濃度は何倍になるの？ 1 増えるだけじゃないの？',
        'じゃあ、なんで log を使うと「10倍」が「1」になるの？',
        'そういうことか！ 桁を数えてるだけなんだね。ありがとう！【会話終了】',
    ],
}

STUDENT_LINES = {
    '物理': [
        '向きが逆になるから、運動量は保存されないと思う。なんとなく、壁が力を加えるから。',
        '壁がボールに力積を加えるから、その分だけ運動量が変わる。外から力がはたらかなければ保存される。',
    ],
    '数学': [
        'なんとなく公式に当てはめた。1/6 公式は交点が2つあるときに使える。',
        '交点で2つのグラフが入れかわるから、そこが範囲の端になる。',
    ],
    '英語': [
        '事実と違うことを言う仮定法だから、形を過去にずらしてる。',
        '現実から距離がある、っていう気持ちを時制で表してるから。',
    ],
    '化学': [
        '10倍になる。pH は水素イオン濃度の log だから。',
        'log は桁の数を表すから、10倍になるたびに1ずつ変わる。',
    ],
}

ANALYSES = {
    '物理': {
        'topic': '運動量保存則', 'understanding_score': 64,
        'summary': '「保存されない」ことには気づけたが、どこまでを1つの物体系と見るかがあいまい。',
        'gaps': [
            {'gap_type': 'vague', 'description': '「外から力がはたらかない」の範囲（どこまでを1つの物体系と見るか）',
             'evidence': 'なんとなく、壁が力を加えるから。', 'suggested_question': 'ボールと壁をまとめて見たらどうなる？'},
            {'gap_type': 'missing_prerequisite', 'description': '運動量は向きを持つ量（ベクトル）であること',
             'evidence': '向きが逆になるから、運動量は保存されないと思う。', 'suggested_question': '向きが変わると運動量はどう変わる？'},
        ],
    },
    '数学': {
        'topic': '放物線と直線で囲まれた面積', 'understanding_score': 58,
        'summary': '公式は使えるが、なぜ使えるのかの説明が抜けている。',
        'gaps': [
            {'gap_type': 'vague', 'description': '1/6公式が「交点が2つあるとき」だけ使える理由',
             'evidence': 'なんとなく公式に当てはめた。', 'suggested_question': 'なぜ交点が2つ必要？'},
            {'gap_type': 'missing_prerequisite', 'description': '定積分と面積の関係',
             'evidence': '交点で2つのグラフが入れかわるから。', 'suggested_question': '面積を積分で表すと？'},
        ],
    },
    '英語': {
        'topic': '仮定法過去', 'understanding_score': 78,
        'summary': '形の説明はできている。were になる理由をもう一歩深めたい。',
        'gaps': [
            {'gap_type': 'misconception', 'description': '仮定法の were を「過去の出来事」と混同している',
             'evidence': '形を過去にずらしてる。', 'suggested_question': '今の話なのに過去形なのはなぜ？'},
        ],
    },
    '化学': {
        'topic': 'pHと水素イオン濃度', 'understanding_score': 70,
        'summary': '10倍の関係は言えたが、log の意味の説明が弱い。',
        'gaps': [
            {'gap_type': 'vague', 'description': '常用対数（log）が「桁」を表すこと',
             'evidence': 'pH は水素イオン濃度の log だから。', 'suggested_question': 'log10(100) はいくつ？'},
        ],
    },
}


def _subject_of(instruction):
    for subject in SCRIPTS:
        if subject in instruction:
            return subject
    return '物理'


class ScriptedChat:
    """llm.create_chat の代わり。何回目の返事かは会話履歴の長さで決める"""

    def __init__(self, instruction, history=None):
        self.subject = _subject_of(instruction)
        self.history = list(history) if history else []

    def send_message(self, text, image=None):
        lines = SCRIPTS[self.subject]
        n = sum(1 for h in self.history if h['role'] == 'user')
        reply = lines[min(n, len(lines) - 1)]
        self.history.append({'role': 'user', 'message': text})
        self.history.append({'role': 'model', 'message': reply})
        return SimpleNamespace(text=reply)

    def get_history(self):
        return list(self.history)


def install_mocks():
    llm.create_chat = lambda gemini_client, groq_client, model, instruction, history=None: \
        ScriptedChat(instruction, history)
    gap_analyzer.analyze_session = lambda client, models, subject, *a, **k: ANALYSES.get(subject)
    core.generate_once = lambda instruction, text, kind: (
        '力積、物体系' if kind == 'hint' else
        'ボールだけを見ると、壁から力積を受けるので運動量は変わります。ボールと壁をまとめて1つの物体系と見れば、外から力がはたらかないので保存されます。')


def start_server():
    from werkzeug.serving import make_server
    core.init_db()
    server = make_server('127.0.0.1', PORT, app_module.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ========================================
# デモデータ（アプリのAPIを通して作るので、本物と同じ形になる）
# ========================================
def _signup(client, email, name, teacher=False):
    body = {'email': email, 'name': name, 'password': PASSWORD,
            'security_question': core.SECURITY_QUESTIONS[0], 'security_answer': 'demo'}
    if teacher:
        body['teacher_code'] = core.TEACHER_CODE
    res = client.post('/api/signup', json=body)
    assert res.status_code == 200, res.get_json()
    with core.get_db() as conn:   # 初回の説明（オンボーディング）は撮影の邪魔なので済みにする
        conn.execute('UPDATE users SET onboarded = 1 WHERE email = ?', (email,))
        conn.commit()


def _run_conversation(client, subject):
    res = client.post('/api/start', json={'subject': subject, 'difficulty': '大学受験', 'teacher_name': 'デモ'})
    key = res.get_json()['session_key']
    for line in STUDENT_LINES[subject]:
        client.post('/api/chat', json={'message': line, 'session_key': key})
    client.post('/api/complete', json={'session_key': key})


def seed():
    teacher = app_module.app.test_client()
    _signup(teacher, 'demo-teacher@example.com', 'デモ先生', teacher=True)
    for letter, subjects in [('B', ['数学', '英語']), ('C', ['英語']), ('D', ['化学', '数学'])]:
        c = app_module.app.test_client()
        _signup(c, f'demo-{letter.lower()}@example.com', f'デモ生徒{letter}')
        for subject in subjects:
            _run_conversation(c, subject)
    # 撮影用の生徒（会話はブラウザで実際に操作する）
    _signup(app_module.app.test_client(), 'demo-a@example.com', 'デモ生徒A')
    with core.get_db() as conn:
        student_b = conn.execute("SELECT id FROM users WHERE email = 'demo-b@example.com'").fetchone()['id']
    teacher.post('/api/dashboard/assignments', json={'student_id': student_b, 'subject': '物理', 'unit': '運動量'})


# ========================================
# 撮影
# ========================================
def _browser(p):
    return p.chromium.launch(channel='chrome', headless=True)


def _login(context, email):
    res = context.request.post(BASE + '/api/login', data={'email': email, 'password': PASSWORD})
    assert res.ok, res.text()


def _save_webp(png_path, name, width=520):
    """表示サイズの2倍までに縮めて、WebPで保存する（スマホ画面は枠の中で約260px → 520px）"""
    from PIL import Image
    img = Image.open(png_path).convert('RGB')
    if img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    out = os.path.join(OUT_STATIC, name)
    img.save(out, 'WEBP', quality=80, method=6)
    print('saved', os.path.relpath(out, ROOT), img.size, f'{os.path.getsize(out) // 1024}KB')


def shoot_app():
    from playwright.sync_api import sync_playwright
    install_mocks()
    start_server()
    seed()
    os.makedirs(OUT_STATIC, exist_ok=True)
    tmp_png = os.path.join(_tmpdir, 'shot.png')
    phone = dict(viewport={'width': 375, 'height': 760}, device_scale_factor=2, is_mobile=True,
                 has_touch=True, reduced_motion='reduce', locale='ja-JP')

    with sync_playwright() as p:
        browser = _browser(p)

        # --- 生徒: 会話 → 結果 → 苦手ノート ---
        ctx = browser.new_context(**phone)
        _login(ctx, 'demo-a@example.com')
        page = ctx.new_page()
        page.goto(BASE + '/')
        page.wait_for_load_state('networkidle')
        page.evaluate('window.launchConfetti = () => {}')   # 紙吹雪が画面にかぶらないように
        page.locator('.subject[data-subject="物理"]').click()
        page.get_by_text('Feynにおまかせ').click()
        page.wait_for_selector('#messages .message.feyn .bubble >> text=5 m/s')
        page.locator('#hintBtn').click()
        page.wait_for_timeout(600)
        # 会話画面: 1回目の説明に、Feynが次の問いを返したところで撮る
        first, second = STUDENT_LINES['物理']
        page.fill('.input-area textarea', first)
        page.locator('.input-area .send-btn').click()
        page.wait_for_selector('#messages .bubble >> text=壁はボールに何をしてるの', timeout=15000)
        page.wait_for_timeout(500)
        page.screenshot(path=tmp_png)
        _save_webp(tmp_png, 'app-chat.webp')
        # 結果画面（会話が終わると自動で開く）
        page.fill('.input-area textarea', second)
        page.locator('.input-area .send-btn').click()
        page.wait_for_selector('#resultOverlay', state='visible', timeout=15000)
        page.wait_for_timeout(600)
        page.screenshot(path=tmp_png)
        _save_webp(tmp_png, 'app-result.webp')

        page.goto(BASE + '/mypage?tab=gaps')
        page.wait_for_timeout(1200)
        page.get_by_text('テーマ別の理解度').first.evaluate("el => window.scrollTo(0, el.getBoundingClientRect().top + scrollY - 130)")
        page.wait_for_timeout(300)
        page.screenshot(path=tmp_png)
        _save_webp(tmp_png, 'app-gaps.webp')
        ctx.close()

        # --- 先生: ダッシュボード → 生徒の詳細 ---
        # 先生の画面はPCで使う作りなので、PCの幅で撮る（LPではブラウザの枠に入れて見せる）
        ctx = browser.new_context(viewport={'width': 1180, 'height': 800}, device_scale_factor=1,
                                  reduced_motion='reduce', locale='ja-JP')
        _login(ctx, 'demo-teacher@example.com')
        page = ctx.new_page()
        page.goto(BASE + '/dashboard')
        page.wait_for_selector('#tableWrap table', timeout=15000)
        page.wait_for_timeout(800)
        # LPで見せたい「つまずきマップ」と「生徒一覧」だけを残す（運営向けの使用量や空の欄は隠す）
        page.evaluate("""() => {
          for (const id of ['summary', 'metricsWrap', 'usageWrap', 'submissionsWrap']) {
            const el = document.getElementById(id);
            if (!el) continue;
            const head = el.previousElementSibling;
            if (head && head.classList.contains('section-header')) head.style.display = 'none';
            el.style.display = 'none';
          }
        }""")
        page.wait_for_timeout(300)
        bottom = page.evaluate("document.getElementById('tableWrap').getBoundingClientRect().bottom + scrollY + 24")
        page.screenshot(path=tmp_png, clip={'x': 0, 'y': 0, 'width': 1180, 'height': min(bottom, 900)})
        _save_webp(tmp_png, 'teacher-dashboard.webp', width=1120)
        with core.get_db() as conn:
            sid = conn.execute("SELECT id FROM users WHERE email = 'demo-b@example.com'").fetchone()['id']
        page.goto(f'{BASE}/dashboard/student/{sid}')
        page.wait_for_timeout(1500)
        page.locator('.tab-btn[data-tab="gaps"]').click()
        page.wait_for_timeout(500)
        page.screenshot(path=tmp_png, clip={'x': 0, 'y': 0, 'width': 1180, 'height': 760})
        _save_webp(tmp_png, 'teacher-student.webp', width=1120)
        ctx.close()
        browser.close()


def shoot_lp(out_dir, prefix=''):
    """LPのページ全体。動きを減らす設定で開き、演出の途中で写らないようにする"""
    from playwright.sync_api import sync_playwright
    start_server()
    os.makedirs(out_dir, exist_ok=True)
    with sync_playwright() as p:
        browser = _browser(p)
        for name, opts in [('mobile-375', dict(viewport={'width': 375, 'height': 812}, device_scale_factor=1,
                                                is_mobile=True, has_touch=True)),
                           ('pc-1280', dict(viewport={'width': 1280, 'height': 800}, device_scale_factor=1))]:
            ctx = browser.new_context(reduced_motion='reduce', locale='ja-JP', color_scheme='light', **opts)
            page = ctx.new_page()
            page.goto(BASE + '/landing')
            # 遅延読み込みの画像も写るように、先に全部読み込ませる
            page.evaluate("() => { for (const img of document.images) { img.loading = 'eager'; img.decoding = 'sync'; } }")
            page.wait_for_function('[...document.images].every(i => i.complete && i.naturalWidth > 0)', timeout=15000)
            page.wait_for_timeout(800)
            path = os.path.join(out_dir, f'{prefix}{name}-full.png')
            page.screenshot(path=path, full_page=True)
            print('saved', os.path.relpath(path, ROOT))
            ctx.close()
        browser.close()


def shoot_ogp():
    from playwright.sync_api import sync_playwright
    start_server()
    os.makedirs(OUT_STATIC, exist_ok=True)
    with sync_playwright() as p:
        browser = _browser(p)
        ctx = browser.new_context(viewport={'width': 1200, 'height': 630}, device_scale_factor=1,
                                  reduced_motion='reduce', color_scheme='light')
        page = ctx.new_page()
        page.goto('file:///' + os.path.join(ROOT, 'scripts', 'ogp.html').replace('\\', '/'))
        page.wait_for_timeout(1500)   # Webフォントの読み込みを待つ
        out = os.path.join(OUT_STATIC, 'ogp.png')
        page.screenshot(path=out)
        print('saved', os.path.relpath(out, ROOT))
        browser.close()


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'app':
        shoot_app()
    elif cmd == 'lp' and len(sys.argv) > 2:
        shoot_lp(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else '')
    elif cmd == 'ogp':
        shoot_ogp()
    else:
        sys.exit(__doc__)
