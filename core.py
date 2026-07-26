# -*- coding: utf-8 -*-
"""アプリ全体で共有するインフラとヘルパー（DB接続・LLMクライアント・チャットセッション管理など）。

Flaskのルート定義は持たない。blueprints/ 配下の各Blueprintと app.py がここを import して使う。
"""
import base64
import os
import time
import uuid
from datetime import date, datetime, timedelta

from dotenv import load_dotenv

import db
import gap_analyzer
import llm
import report
import tutoring

load_dotenv()

TEACHER_CODE = os.environ.get('TEACHER_CODE', 'FEYN_TEACHER_2024')
SUBJECTS = ['物理', '数学', '英語', '化学', '生物', '国語', '歴史']

# 無料プランの生徒のライフ制（先生アカウント・有料プランは対象外）
# 新しい会話を開始するたびに1つ消費、0になると一定時間ごとに1つ回復する
MAX_LIVES = 3
LIFE_RECOVERY_HOURS = 2

# 忘却曲線ベースの復習間隔（日）。復習に成功するたびに次の間隔へ進む
REVIEW_INTERVALS = [1, 3, 7, 14, 30]
SECURITY_QUESTIONS = [
    '小学校の名前は？',
    '初めて飼ったペットの名前は？',
    '好きな食べ物は？',
    '生まれた市区町村は？',
    '母親の旧姓は？',
]


# --- DB初期化 ---
get_db = db.get_db


def init_db():
    pk = db.PK
    with get_db() as conn:
        # 1. まず全テーブルを作成する（ALTER TABLEより先に必ず存在させる）
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS users (
                id {pk},
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'student',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS conversation_logs (
                id {pk},
                user_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                session_date TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS session_logs (
                id {pk},
                user_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS knowledge_gaps (
                id {pk},
                user_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                topic TEXT NOT NULL,
                gap_type TEXT NOT NULL,
                description TEXT NOT NULL,
                evidence TEXT,
                suggested_question TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                session_date TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS topic_progress (
                id {pk},
                user_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                topic TEXT NOT NULL,
                understanding INTEGER NOT NULL DEFAULT 0,
                sessions_count INTEGER NOT NULL DEFAULT 0,
                last_studied TEXT,
                UNIQUE(user_id, subject, topic),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        # Gemini無料枠の使用量を先生ダッシュボードで確認できるようにするための記録
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS api_calls (
                id {pk},
                model TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                success INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # 先生が生徒に出す課題（科目・単元を指定して出題する）
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS assignments (
                id {pk},
                teacher_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                unit TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (teacher_id) REFERENCES users(id),
                FOREIGN KEY (student_id) REFERENCES users(id)
            )
        ''')
        # クラス分け（塾・組織）の下地。招待コードで先生・生徒を束ねる想定だが、
        # 実際に複数の先生が使うようになるまでは全員を1つのデフォルト組織に入れておく
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS schools (
                id {pk},
                name TEXT NOT NULL,
                join_code TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 2. 全テーブルが存在する状態で、あとから追加したカラムをマイグレーションする
        db.add_column_if_missing(conn, 'users', "role TEXT NOT NULL DEFAULT 'student'")
        db.add_column_if_missing(conn, 'users', 'security_question TEXT')
        db.add_column_if_missing(conn, 'users', 'security_answer_hash TEXT')
        db.add_column_if_missing(conn, 'knowledge_gaps', 'review_count INTEGER NOT NULL DEFAULT 0')
        db.add_column_if_missing(conn, 'knowledge_gaps', 'next_review TEXT')
        db.add_column_if_missing(conn, 'users', 'last_login TIMESTAMP')
        db.add_column_if_missing(conn, 'users', 'school_id INTEGER')
        # 同じ科目でも「会話を始めた回」ごとに区別するための識別子（過去データはNULLのまま=日付+科目でまとめる）
        db.add_column_if_missing(conn, 'conversation_logs', 'session_id TEXT')
        db.add_column_if_missing(conn, 'session_logs', 'session_id TEXT')
        # 会話ログの見出しに単元を表示できるようにする（過去データはNULL＝単元表示なし）
        db.add_column_if_missing(conn, 'conversation_logs', 'unit TEXT')
        # 会話ログの見出しに「単元」だけでなく、分析で分かった具体的なテーマも出せるようにする
        db.add_column_if_missing(conn, 'session_logs', 'topic TEXT')
        # 課金（Stripe）関連
        db.add_column_if_missing(conn, 'users', "subscription_status TEXT NOT NULL DEFAULT 'free'")
        db.add_column_if_missing(conn, 'users', 'stripe_customer_id TEXT')
        db.add_column_if_missing(conn, 'users', 'stripe_subscription_id TEXT')
        # 無料プランのライフ制（3個スタート、0になると2時間ごとに1回復）
        db.add_column_if_missing(conn, 'users', 'lives INTEGER NOT NULL DEFAULT 3')
        db.add_column_if_missing(conn, 'users', 'last_life_lost_at TIMESTAMP')

        conn.commit()

init_db()


def ensure_default_school():
    """デフォルト組織を用意し、まだどの組織にも属していないユーザーを入れる（既存の挙動を維持するため）"""
    with get_db() as conn:
        row = conn.execute("SELECT id FROM schools WHERE join_code = 'DEFAULT'").fetchone()
        if row is None:
            conn.execute("INSERT INTO schools (name, join_code) VALUES (?, ?)", ('デフォルト', 'DEFAULT'))
            conn.commit()
            row = conn.execute("SELECT id FROM schools WHERE join_code = 'DEFAULT'").fetchone()
        default_id = row['id']
        conn.execute('UPDATE users SET school_id = ? WHERE school_id IS NULL', (default_id,))
        conn.commit()
        return default_id


def default_school_id():
    """デフォルト組織のIDを返す。呼び出しごとに毎回確認するため、モジュール変数のキャッシュと違い
    テスト等でDBが切り替わっても古い値を参照し続ける心配がない。"""
    return ensure_default_school()


# --- Gemini クライアント ---
from google import genai  # noqa: E402

client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))

# 無料枠の利用上限（429）はモデルごとに別カウントのため、
# 上限に達したら次のモデルへ自動で切り替える
GEMINI_MODELS = [
    'gemini-2.5-flash',
    'gemini-2.0-flash',
    'gemini-2.5-flash-lite',
    'gemini-2.0-flash-lite',
]

# --- Groq クライアント（任意。GROQ_API_KEY未設定なら使わない） ---
_groq_api_key = os.environ.get('GROQ_API_KEY')
groq_client = None
if _groq_api_key:
    import groq
    groq_client = groq.Groq(api_key=_groq_api_key)

# Gemini全モデルの枠が尽きたときの最終フォールバックとしてGroqを末尾に追加
ALL_MODELS = GEMINI_MODELS + (llm.GROQ_MODELS if groq_client else [])

# --- Stripe（課金） ---
import stripe  # noqa: E402
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
STRIPE_PRICE_ID       = os.environ.get('STRIPE_PRICE_ID')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET')


def create_chat(model, instruction, history=None):
    return llm.create_chat(client, groq_client, model, instruction, history=history)


def log_api_call(model, endpoint, success):
    """先生ダッシュボードでの無料枠使用量確認のため、Gemini呼び出しの成否を記録する"""
    try:
        with get_db() as conn:
            conn.execute(
                'INSERT INTO api_calls (model, endpoint, success) VALUES (?, ?, ?)',
                (model, endpoint, 1 if success else 0)
            )
            conn.commit()
    except Exception:
        pass  # 記録の失敗が本来の処理を止めないようにする


def _recover_lives(lives, last_life_lost_at):
    """満タンでない間だけ進んでいる回復タイマーから、経過時間分のライフを回復させる。
    戻り値は (回復後のライフ数, 更新後のタイマー起点 or 満タンならNone)。"""
    if lives >= MAX_LIVES or not last_life_lost_at:
        return MAX_LIVES, None

    anchor = datetime.fromisoformat(last_life_lost_at)
    recovered = int((datetime.now() - anchor).total_seconds() // (LIFE_RECOVERY_HOURS * 3600))
    if recovered <= 0:
        return lives, last_life_lost_at

    new_lives = min(MAX_LIVES, lives + recovered)
    if new_lives >= MAX_LIVES:
        return new_lives, None
    # 消費した回復分だけタイマーの起点を進める（余り時間は次の回復に引き継ぐ）
    new_anchor = anchor + timedelta(hours=LIFE_RECOVERY_HOURS * recovered)
    return new_lives, new_anchor.isoformat()


def check_and_consume_life(user_id):
    """無料プランの生徒のライフを確認し、残っていれば1つ消費する。
    先生アカウントや有料プランは対象外。ライフが無ければエラーメッセージを返す（Noneなら開始可）。"""
    with get_db() as conn:
        user = conn.execute(
            'SELECT role, subscription_status, lives, last_life_lost_at FROM users WHERE id = ?',
            (user_id,)
        ).fetchone()
        if not user or user['role'] != 'student' or user['subscription_status'] == 'active':
            return None

        lives, last_life_lost_at = _recover_lives(user['lives'], user['last_life_lost_at'])

        if lives <= 0:
            anchor = datetime.fromisoformat(last_life_lost_at)
            remaining = anchor + timedelta(hours=LIFE_RECOVERY_HOURS) - datetime.now()
            minutes_left = max(1, int(remaining.total_seconds() // 60) + 1)
            conn.execute('UPDATE users SET lives = ?, last_life_lost_at = ? WHERE id = ?',
                         (lives, last_life_lost_at, user_id))
            conn.commit()
            return (
                f'ライフがなくなりました。あと{minutes_left}分で1つ回復します。'
                '今すぐ続けるには、有料プランにアップグレードしてください。'
            )

        was_full = lives >= MAX_LIVES
        new_lives = lives - 1
        new_anchor = datetime.now().isoformat() if was_full else last_life_lost_at
        conn.execute('UPDATE users SET lives = ?, last_life_lost_at = ? WHERE id = ?',
                     (new_lives, new_anchor, user_id))
        conn.commit()
    return None


def get_lives_status(user_id):
    """フロント表示用に、現在のライフ数と次に回復するまでの秒数を返す。
    先生・有料プランはNoneを返し、フロントは「無制限」表示に切り替える。"""
    with get_db() as conn:
        user = conn.execute(
            'SELECT role, subscription_status, lives, last_life_lost_at FROM users WHERE id = ?',
            (user_id,)
        ).fetchone()
    if not user or user['role'] != 'student' or user['subscription_status'] == 'active':
        return None

    lives, last_life_lost_at = _recover_lives(user['lives'], user['last_life_lost_at'])
    next_recovery_seconds = None
    if lives < MAX_LIVES and last_life_lost_at:
        anchor = datetime.fromisoformat(last_life_lost_at)
        remaining = anchor + timedelta(hours=LIFE_RECOVERY_HOURS) - datetime.now()
        next_recovery_seconds = max(0, int(remaining.total_seconds()))

    return {
        'lives': lives,
        'max_lives': MAX_LIVES,
        'next_recovery_seconds': next_recovery_seconds,
    }


def generate_once(instruction, message, endpoint_name):
    """ヒント・答え表示など、1回だけの単発応答をフォールバックチェーンで取得する
    （メインの対話セッションの履歴には残さない）"""
    for model in ALL_MODELS:
        try:
            chat = create_chat(model, instruction)
            response = chat.send_message(message)
            log_api_call(model, endpoint_name, True)
            return response.text or ''
        except Exception as e:
            log_api_call(model, endpoint_name, False)
            err = str(e)
            if '429' in err or '503' in err or 'UNAVAILABLE' in err:
                continue
            break
    return None


# --- チャットセッションを管理する辞書 ---
chat_sessions = {}


def get_last_feyn_message(session_key):
    """現在のセッションでFeynが直前に言った発言（＝今まさに答えるべき質問）を取り出す"""
    sess = chat_sessions.get(session_key)
    if not sess:
        return None
    history = sess['chat'].get_history()
    for h in reversed(history):
        if h['role'] == 'model':
            return h['message']
    return None


# --- サーバー再起動などでメモリ上のセッションが消えた場合、DBの会話ログから復元する ---
# target_date を指定すると、当日以外（学習のきろくからの「続きから再開」）の会話も復元できる。
# conv_id を指定すると、同じ日付・科目に複数回分の会話があっても「その回」だけを取り出せる。
def restore_chat_session(session_key, user_id, teacher_name, target_date=None, conv_id=None):
    subject = session_key[len(f"{user_id}_"):]
    if subject not in SUBJECTS:
        return None

    target_date = target_date or str(date.today())
    with get_db() as conn:
        if conv_id:
            rows = conn.execute(
                'SELECT role, message, difficulty, unit FROM conversation_logs '
                'WHERE user_id = ? AND subject = ? AND session_id = ? ORDER BY id',
                (user_id, subject, conv_id)
            ).fetchall()
        else:
            # conv_id未指定（サーバー再起動時の自動復元など）の場合は、
            # その日付・科目の中で「一番最後に始まった回」だけを復元する
            latest = conn.execute(
                'SELECT session_id FROM conversation_logs '
                'WHERE user_id = ? AND subject = ? AND session_date = ? '
                'ORDER BY id DESC LIMIT 1',
                (user_id, subject, target_date)
            ).fetchone()
            latest_conv_id = latest['session_id'] if latest else None
            if latest_conv_id:
                rows = conn.execute(
                    'SELECT role, message, difficulty, unit FROM conversation_logs '
                    'WHERE user_id = ? AND subject = ? AND session_id = ? ORDER BY id',
                    (user_id, subject, latest_conv_id)
                ).fetchall()
                conv_id = latest_conv_id
            else:
                # session_id が無い過去データ（マイグレーション前）への後方互換
                rows = conn.execute(
                    'SELECT role, message, difficulty, unit FROM conversation_logs '
                    'WHERE user_id = ? AND subject = ? AND session_date = ? ORDER BY id',
                    (user_id, subject, target_date)
                ).fetchall()
    if not rows:
        return None

    difficulty = rows[-1]['difficulty']
    unit       = rows[-1]['unit']

    # 会話はuser発言から始まる必要があるため、/api/start で送る起点メッセージを先頭に補う
    history = [{'role': 'user', 'message': tutoring.build_kickoff(subject)}]
    for row in rows:
        history.append({
            'role':    'user' if row['role'] == 'user' else 'model',
            'message': row['message'],
        })

    instruction = tutoring.build_instruction(subject, difficulty, teacher_name)
    chat = create_chat(ALL_MODELS[0], instruction, history=history)
    turns = min(sum(1 for row in rows if row['role'] == 'user'), 3)
    return {'chat': chat, 'model': ALL_MODELS[0], 'instruction': instruction, 'conv_id': conv_id,
            'turns': turns, 'subject': subject, 'difficulty': difficulty, 'unit': unit}


# ========================================
# 学習履歴（会話ログを日付×科目でまとめて返す）
# ========================================
def get_history_sessions(user_id):
    with get_db() as conn:
        rows = conn.execute(
            'SELECT subject, difficulty, role, message, session_date, session_id, unit FROM conversation_logs '
            'WHERE user_id = ? ORDER BY id',
            (user_id,)
        ).fetchall()
        completed_rows = conn.execute(
            'SELECT subject, completed_at, session_id, topic FROM session_logs WHERE user_id = ?',
            (user_id,)
        ).fetchall()

    # 「会話終了」まで到達した（＝完了記録がある）会話。session_idがあればそれで、
    # 過去データ（session_id無し）は日付×科目でまとめて判定する
    completed_conv_ids = {r['session_id'] for r in completed_rows if r['session_id']}
    completed_legacy_keys = {f"{str(r['completed_at'])[:10]}|{r['subject']}" for r in completed_rows if not r['session_id']}
    # 分析で分かった具体的なテーマ（会話ログの見出しに使う）
    topic_by_conv_id = {r['session_id']: r['topic'] for r in completed_rows if r['session_id'] and r['topic']}

    grouped = {}
    order = []
    for row in rows:
        # session_idがある会話は「開始した回」ごとに独立させ、無い過去データは日付×科目でまとめる
        key = row['session_id'] or f"{row['session_date']}|{row['subject']}"
        if key not in grouped:
            if row['session_id']:
                completed = row['session_id'] in completed_conv_ids
            else:
                completed = key in completed_legacy_keys
            grouped[key] = {
                'session_id': row['session_id'],
                'date':       row['session_date'],
                'subject':    row['subject'],
                'difficulty': row['difficulty'],
                'unit':       row['unit'],
                'topic':      topic_by_conv_id.get(row['session_id']),
                'messages':   [],
                'completed':  completed,
            }
            order.append(key)
        grouped[key]['messages'].append({'role': row['role'], 'message': row['message']})

    # Feynの最初の問いを見ただけで一度も返信していないセッションは「学習」とみなさず除外する
    order = [k for k in order if any(m['role'] == 'user' for m in grouped[k]['messages'])]

    # 新しい方が先頭に来るように逆順で返す
    return [grouped[k] for k in reversed(order)]


# ========================================
# セッション完了の記録 ＋ ナレッジギャップ自動分析
# ========================================
def save_analysis(user_id, subject, analysis, today):
    """分析結果を knowledge_gaps / topic_progress に保存する"""
    with get_db() as conn:
        for g in analysis['gaps']:
            # 同じ弱点が未解決のまま残っていれば重複登録しない
            dup = conn.execute(
                "SELECT id FROM knowledge_gaps WHERE user_id = ? AND subject = ? AND topic = ? AND gap_type = ? AND status != 'resolved'",
                (user_id, subject, analysis['topic'], g['gap_type'])
            ).fetchone()
            if dup:
                continue
            conn.execute(
                'INSERT INTO knowledge_gaps (user_id, subject, topic, gap_type, description, evidence, suggested_question, session_date) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (user_id, subject, analysis['topic'], g['gap_type'],
                 g['description'], g.get('evidence', ''), g['suggested_question'], today)
            )
        conn.execute(
            'INSERT INTO topic_progress (user_id, subject, topic, understanding, sessions_count, last_studied) '
            'VALUES (?, ?, ?, ?, 1, ?) '
            'ON CONFLICT(user_id, subject, topic) '
            'DO UPDATE SET understanding = excluded.understanding, '
            '              sessions_count = sessions_count + 1, '
            '              last_studied = excluded.last_studied',
            (user_id, subject, analysis['topic'], analysis['understanding_score'], today)
        )
        conn.commit()
