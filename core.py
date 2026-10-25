# -*- coding: utf-8 -*-
"""アプリ全体で共有するインフラとヘルパー（DB接続・LLMクライアント・チャットセッション管理など）。

Flaskのルート定義は持たない。blueprints/ 配下の各Blueprintと app.py がここを import して使う。
"""
import base64
import os
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv

import db
import gap_analyzer
import llm
import report
import tutoring

load_dotenv()

DEFAULT_TEACHER_CODE = 'FEYN_TEACHER_2024'
TEACHER_CODE = os.environ.get('TEACHER_CODE') or DEFAULT_TEACHER_CODE


def is_production():
    """Renderで動いているか（RenderはRENDER=trueを自動で設定する）"""
    return bool(os.environ.get('RENDER'))


def teacher_signup_enabled():
    """先生アカウントの新規登録を受け付けてよいか。
    先生コードの既定値はGitHubに公開されているので、本番で既定値のままだと誰でも先生になれて
    全生徒の会話やメールアドレスを見られてしまう。本番では独自の値が設定されているときだけ受け付ける"""
    return not is_production() or TEACHER_CODE != DEFAULT_TEACHER_CODE


# --- 日付・時刻（日本時間） ---
# サーバー（Render）の時計はUTCなので、date.today() だと日本時間の0〜9時が「前日」扱いになる。
# 学習日・復習日・連続学習日数など、利用者に見える「日付」はすべて日本時間で数える。
JST = timezone(timedelta(hours=9))


def today_jst():
    return datetime.now(JST).date()


def today_jst_str():
    return today_jst().isoformat()


def _as_utc_datetime(value):
    """DBの CURRENT_TIMESTAMP（UTCで記録される）を、タイムゾーン付きのdatetimeにする。
    SQLiteは 'YYYY-MM-DD HH:MM:SS' の文字列、Postgresはdatetimeで返ってくるので両方に対応する"""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        return None
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_jst_date(value):
    """DBに記録された時刻（UTC）が、日本時間で何日にあたるか"""
    dt = _as_utc_datetime(value)
    return dt.astimezone(JST).date() if dt else None


def to_jst_str(value):
    """DBに記録された時刻（UTC）を、画面表示用の日本時間 'YYYY-MM-DD HH:MM' にする"""
    dt = _as_utc_datetime(value)
    return dt.astimezone(JST).strftime('%Y-%m-%d %H:%M') if dt else None


# 「aiue@okaki」のようにドメインにドットが無いものは、Stripeなど外部サービスに拒否される
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$')


def looks_like_email(value):
    return bool(value and _EMAIL_RE.match(value))
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

        # 利用状況の計測（会話の開始・完了・何往復目で離脱したか・有料化など）。
        # 会話本文・名前・メールなど、個人を特定できる内容は入れない（log_event を参照）
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS events (
                id {pk},
                event_type TEXT NOT NULL,
                user_id INTEGER,
                conv_id TEXT,
                subject TEXT,
                mode TEXT,
                turn INTEGER,
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
        # 初回の使い方ガイドを見終わったか（1=表示済み・不要）。DEFAULT 1なので既存ユーザーには出さず、
        # 新規登録した生徒だけsignup時に0で入れる
        db.add_column_if_missing(conn, 'users', 'onboarded INTEGER NOT NULL DEFAULT 1')
        # 解約を予約済みの有料会員が「いつまで有料か」を表示するための日付（YYYY-MM-DD。予約なしはNULL）
        db.add_column_if_missing(conn, 'users', 'subscription_cancel_at TEXT')

        # 3. よく使う検索のためのインデックス（追加のみ。IF NOT EXISTS なので何度実行しても安全）
        for index_sql in INDEXES:
            conn.execute(index_sql)

        conn.commit()


# よく使う検索のためのインデックス。SQLite・Postgresの両方で同じ書き方が使える
INDEXES = [
    # 会話ログ: 1つの会話の取得（session_id）・学習のきろくの一覧（user_idで絞って会話ごとにまとめる）
    'CREATE INDEX IF NOT EXISTS idx_conversation_logs_user_session ON conversation_logs (user_id, session_id)',
    # 会話ログ: session_id が無い過去データ・再起動後の復元（日付×科目で探す）
    'CREATE INDEX IF NOT EXISTS idx_conversation_logs_user_subject_date ON conversation_logs (user_id, subject, session_date)',
    # 完了記録: 連続学習日数・クリア数・完了済みかの確認
    'CREATE INDEX IF NOT EXISTS idx_session_logs_user_session ON session_logs (user_id, session_id)',
    # 苦手ノート: 本人の一覧・復習どきの件数・重複チェック
    'CREATE INDEX IF NOT EXISTS idx_knowledge_gaps_user_subject_topic ON knowledge_gaps (user_id, subject, topic)',
    # 課題: 生徒の未完了の課題・先生の提出一覧
    'CREATE INDEX IF NOT EXISTS idx_assignments_student_status ON assignments (student_id, status)',
    'CREATE INDEX IF NOT EXISTS idx_assignments_teacher_status ON assignments (teacher_id, status)',
    # 先生ダッシュボード: 組織の生徒一覧
    'CREATE INDEX IF NOT EXISTS idx_users_school_role ON users (school_id, role)',
    # 利用状況の計測: 期間での集計・本人のデータ削除
    'CREATE INDEX IF NOT EXISTS idx_events_created ON events (created_at)',
    'CREATE INDEX IF NOT EXISTS idx_events_user ON events (user_id)',
]

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
# 上限に達したら次のモデルへ自動で切り替える。
# gemini-2.0-flash / 2.0-flash-lite は提供終了日（最短 2026-06-01）を過ぎたので外した。
# 提供終了の予定は https://ai.google.dev/gemini-api/docs/deprecations で確認できる
GEMINI_MODELS = [
    'gemini-2.5-flash',
    'gemini-2.5-flash-lite',
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


# ========================================
# 利用状況の計測
# ========================================
# 記録するのは「何が起きたか」だけ。会話本文・名前・メールアドレスなど、個人を特定できる内容は入れない。
# user_id はアプリ内の番号で、アカウント削除のときに一緒に消す。
EVENT_TYPES = {
    'signup',                  # 生徒の新規登録
    'conversation_started',    # 会話を始めた（mode: normal / review / assignment / photo）
    'conversation_turn',       # 生徒が1回返信した（turn: 何往復目か）
    'conversation_completed',  # Feynが納得して会話が終わった（turn: 何往復で終わったか）
    'life_blocked',            # 無料プランのライフ切れで始められなかった
    'subscription_started',    # 無料 → 有料になった
    'subscription_ended',      # 有料 → 無料に戻った（解約・支払い失敗など）
}


def log_event(event_type, user_id=None, conv_id=None, subject=None, mode=None, turn=None):
    """利用状況のイベントを1件記録する。記録の失敗で本来の処理を止めないよう、例外は外に出さない。
    DBの接続を自分で開くので、with get_db() の中からは呼ばないこと（接続を二重に使わないため）"""
    if event_type not in EVENT_TYPES:
        return
    try:
        with get_db() as conn:
            conn.execute(
                'INSERT INTO events (event_type, user_id, conv_id, subject, mode, turn) VALUES (?, ?, ?, ?, ?, ?)',
                (event_type, user_id, conv_id, subject, mode, turn)
            )
            conn.commit()
    except Exception:
        pass


# 始めてからこの時間が経っていない会話は「まだ続いているかもしれない」ので、離脱の集計に入れない
IN_PROGRESS_HOURS = 1


def compute_usage_metrics(school_id, days=30, now=None):
    """先生ダッシュボード用に、組織の生徒の利用状況を集計する（直近 days 日）"""
    days = max(1, min(int(days), 365))
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')   # DBの時刻はUTC
    in_progress_after = now - timedelta(hours=IN_PROGRESS_HOURS)

    with get_db() as conn:
        rows = conn.execute(
            'SELECT e.event_type, e.user_id, e.conv_id, e.mode, e.turn, e.created_at FROM events e '
            'JOIN users u ON u.id = e.user_id '
            "WHERE u.school_id = ? AND u.role = 'student' AND e.created_at >= ?",
            (school_id, since)
        ).fetchall()
        plan_rows = conn.execute(
            "SELECT subscription_status, COUNT(*) AS cnt FROM users WHERE school_id = ? AND role = 'student' "
            'GROUP BY subscription_status',
            (school_id,)
        ).fetchall()

    started = {}          # conv_id -> 始めた時刻
    completed = {}        # conv_id -> 何往復で終わったか
    max_turn = {}         # conv_id -> 何往復目まで進んだか
    modes = {}
    active_users, blocked_users = set(), set()
    counts = {t: 0 for t in EVENT_TYPES}
    for r in rows:
        counts[r['event_type']] = counts.get(r['event_type'], 0) + 1
        conv_id = r['conv_id']
        if r['event_type'] == 'conversation_started':
            active_users.add(r['user_id'])
            modes[r['mode'] or 'normal'] = modes.get(r['mode'] or 'normal', 0) + 1
            if conv_id:
                started[conv_id] = _as_utc_datetime(r['created_at'])
        elif r['event_type'] == 'conversation_turn' and conv_id:
            max_turn[conv_id] = max(max_turn.get(conv_id, 0), r['turn'] or 0)
        elif r['event_type'] == 'conversation_completed' and conv_id:
            completed[conv_id] = r['turn'] or 0
        elif r['event_type'] == 'life_blocked':
            blocked_users.add(r['user_id'])

    # 離脱: 始めたが終わっていない会話を、何往復目で止まったかで分ける（始めたばかりの会話は除く）
    dropout = {'0': 0, '1': 0, '2': 0, '3+': 0}
    in_progress = 0
    for conv_id, started_at in started.items():
        if conv_id in completed:
            continue
        if started_at and started_at > in_progress_after:
            in_progress += 1
            continue
        turn = max_turn.get(conv_id, 0)
        dropout['3+' if turn >= 3 else str(turn)] += 1

    finished_started = [c for c in started if c in completed]
    completed_turns = list(completed.values())
    plans = {r['subscription_status']: r['cnt'] for r in plan_rows}
    return {
        'days':                days,
        'since':               to_jst_str(since)[:10],
        'active_students':     len(active_users),
        'started':             len(started),
        'completed':           len(finished_started),
        'completion_rate':     round(len(finished_started) / len(started) * 100) if started else None,
        'in_progress':         in_progress,
        'dropout_by_turn':     dropout,
        'avg_turns_to_complete': round(sum(completed_turns) / len(completed_turns), 1) if completed_turns else None,
        'started_by_mode':     modes,
        'signups':             counts['signup'],
        'subscriptions_started': counts['subscription_started'],
        'subscriptions_ended': counts['subscription_ended'],
        'life_blocked':        counts['life_blocked'],
        'life_blocked_students': len(blocked_users),
        'plans':               {'free': plans.get('free', 0), 'active': plans.get('active', 0)},
    }


# ========================================
# つまずきマップ（テーマごとに、何割の生徒の苦手ノートに入っているか）
# ========================================
STUMBLE_MAP_LIMIT = 20


def compute_stumble_map(school_id, limit=STUMBLE_MAP_LIMIT):
    """組織の生徒について、苦手ノートに「未解決」で残っているテーマを、生徒の割合が高い順に返す。
    テーマ名は会話の分析時に既存の名前へそろえているので、それを単元のまとまりとして扱う"""
    joined = "JOIN users u ON u.id = {col} WHERE u.school_id = ? AND u.role = 'student'"
    with get_db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS cnt FROM users WHERE school_id = ? AND role = 'student'", (school_id,)
        ).fetchone()['cnt']
        gap_rows = conn.execute(
            'SELECT g.subject, g.topic, COUNT(DISTINCT g.user_id) AS students FROM knowledge_gaps g '
            + joined.format(col='g.user_id') + " AND g.status != 'resolved' GROUP BY g.subject, g.topic",
            (school_id,)
        ).fetchall()
        studied_rows = conn.execute(
            'SELECT t.subject, t.topic, COUNT(DISTINCT t.user_id) AS students FROM topic_progress t '
            + joined.format(col='t.user_id') + ' GROUP BY t.subject, t.topic',
            (school_id,)
        ).fetchall()

    studied = {(r['subject'], r['topic']): r['students'] for r in studied_rows}
    topics = []
    for r in gap_rows:
        topics.append({
            'subject':  r['subject'],
            'topic':    r['topic'],
            'students': r['students'],
            'studied':  max(studied.get((r['subject'], r['topic']), 0), r['students']),
            'rate':     round(r['students'] / total * 100) if total else 0,
        })
    topics.sort(key=lambda t: (-t['students'], t['subject'], t['topic']))
    return {'total_students': total, 'topics': topics[:limit], 'more': max(0, len(topics) - limit)}


# ========================================
# 先生向けCSV出力（生徒ごとの進捗・クリア数。会話本文は含めない）
# ========================================
CSV_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def csv_safe(value):
    """Excelで開いたときに、=や+で始まる文字（ニックネームなど）が数式として実行されないようにする"""
    if value is None:
        return ''
    text = str(value)
    if text.startswith(CSV_FORMULA_PREFIXES):
        return "'" + text
    return text


def build_students_csv_rows(school_id):
    """組織の生徒ごとに1行の、CSV用の行（見出し行を含む）を返す"""
    student_filter = "JOIN users u ON u.id = {col} WHERE u.school_id = ? AND u.role = 'student'"
    with get_db() as conn:
        students = conn.execute(
            'SELECT id, name, email, created_at, last_login, subscription_status FROM users '
            "WHERE school_id = ? AND role = 'student' ORDER BY id",
            (school_id,)
        ).fetchall()
        clears = conn.execute(
            'SELECT s.user_id, s.subject, COUNT(*) AS cnt, MAX(s.completed_at) AS last_at FROM session_logs s '
            + student_filter.format(col='s.user_id') + ' GROUP BY s.user_id, s.subject',
            (school_id,)
        ).fetchall()
        topics = conn.execute(
            'SELECT t.user_id, AVG(t.understanding) AS avg_understanding, COUNT(*) AS cnt FROM topic_progress t '
            + student_filter.format(col='t.user_id') + ' GROUP BY t.user_id',
            (school_id,)
        ).fetchall()
        gaps = conn.execute(
            'SELECT g.user_id, g.status, COUNT(*) AS cnt FROM knowledge_gaps g '
            + student_filter.format(col='g.user_id') + ' GROUP BY g.user_id, g.status',
            (school_id,)
        ).fetchall()
        assignments = conn.execute(
            'SELECT a.student_id, a.status, COUNT(*) AS cnt FROM assignments a '
            + student_filter.format(col='a.student_id') + ' GROUP BY a.student_id, a.status',
            (school_id,)
        ).fetchall()

    clears_by = {}
    last_by = {}
    for r in clears:
        clears_by.setdefault(r['user_id'], {})[r['subject']] = r['cnt']
        last = _as_utc_datetime(r['last_at'])
        if last and (r['user_id'] not in last_by or last > last_by[r['user_id']]):
            last_by[r['user_id']] = last
    topics_by = {r['user_id']: r for r in topics}
    gaps_by = {}
    for r in gaps:
        key = 'resolved' if r['status'] == 'resolved' else 'open'
        gaps_by.setdefault(r['user_id'], {'open': 0, 'resolved': 0})[key] += r['cnt']
    assign_by = {}
    for r in assignments:
        key = 'completed' if r['status'] == 'completed' else 'open'
        assign_by.setdefault(r['student_id'], {'open': 0, 'completed': 0})[key] += r['cnt']

    header = (['生徒ID', 'ニックネーム', 'メールアドレス', 'プラン', '登録日時', '最終ログイン', '最終学習',
               '総クリア数'] + [f'{s}クリア数' for s in SUBJECTS]
              + ['理解度の平均(0-100)', '学習したテーマ数', '苦手（未解決）', '苦手（解決済み）',
                 '課題（未完了）', '課題（完了）'])
    rows = [header]
    for st in students:
        sid = st['id']
        per_subject = clears_by.get(sid, {})
        topic = topics_by.get(sid)
        # round() は 70.5 を 70 にする（偶数への丸め）ので、一般的な四捨五入にする
        avg = int(float(topic['avg_understanding']) + 0.5) if topic and topic['avg_understanding'] is not None else ''
        g = gaps_by.get(sid, {'open': 0, 'resolved': 0})
        a = assign_by.get(sid, {'open': 0, 'completed': 0})
        last = last_by.get(sid)
        rows.append([
            sid, csv_safe(st['name']), csv_safe(st['email']),
            '有料' if st['subscription_status'] == 'active' else '無料',
            to_jst_str(st['created_at']) or '', to_jst_str(st['last_login']) or '',
            to_jst_str(last) or '',
            sum(per_subject.values()),
        ] + [per_subject.get(s, 0) for s in SUBJECTS] + [
            avg, topic['cnt'] if topic else 0, g['open'], g['resolved'], a['open'], a['completed'],
        ])
    return rows


# ========================================
# アカウント削除
# ========================================
# 本人のデータを、参照している側（子）から順に消す。Postgresは「users を参照している行」が残っていると
# users を消せないので、この順番を崩さないこと。テーブルを足したときはここにも足す
USER_DATA_TABLES = [
    ('conversation_logs', 'user_id'),
    ('session_logs',      'user_id'),
    ('knowledge_gaps',    'user_id'),
    ('topic_progress',    'user_id'),
    ('assignments',       'student_id'),
    ('events',            'user_id'),
]


def delete_user_data(user_id):
    """生徒本人のデータとアカウントを、1つのトランザクションでまとめて削除する（途中で失敗したら全部取り消す）。
    戻り値は、テーブルごとに消した行数"""
    deleted = {}
    with get_db() as conn:
        for table, column in USER_DATA_TABLES:
            deleted[table] = conn.execute(f'DELETE FROM {table} WHERE {column} = ?', (user_id,)).rowcount
        deleted['users'] = conn.execute('DELETE FROM users WHERE id = ?', (user_id,)).rowcount
        conn.commit()
    # メモリ上の会話の途中経過も消す
    for key in [k for k in list(chat_sessions) if k.startswith(f'{user_id}_')]:
        chat_sessions.pop(key, None)
    return deleted


def _lives_anchor(value):
    """回復タイマーの起点を、datetime.now()と比べられるdatetime（サーバー時刻・タイムゾーンなし）にする。
    SQLiteは文字列、Postgresはdatetimeで返ってくるので両方に対応する
    （文字列だけを想定して fromisoformat すると、本番のPostgresでエラーになる）"""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _recover_lives(lives, last_life_lost_at):
    """満タンでない間だけ進んでいる回復タイマーから、経過時間分のライフを回復させる。
    戻り値は (回復後のライフ数, 更新後のタイマー起点（ISO文字列） or 満タンならNone)。"""
    if lives >= MAX_LIVES or not last_life_lost_at:
        return MAX_LIVES, None

    anchor = _lives_anchor(last_life_lost_at)
    recovered = int((datetime.now() - anchor).total_seconds() // (LIFE_RECOVERY_HOURS * 3600))
    if recovered <= 0:
        return lives, anchor.isoformat()

    new_lives = min(MAX_LIVES, lives + recovered)
    if new_lives >= MAX_LIVES:
        return new_lives, None
    # 消費した回復分だけタイマーの起点を進める（余り時間は次の回復に引き継ぐ）
    new_anchor = anchor + timedelta(hours=LIFE_RECOVERY_HOURS * recovered)
    return new_lives, new_anchor.isoformat()


# gunicornはスレッドで同時に処理するので、ライフの「読んで→減らす/戻す」が重ならないようにする
# （連打で同時に2回始めたときに、1つ分しか減らない・マイナスになる、といったずれを防ぐ）
_lives_lock = threading.Lock()


def check_and_consume_life(user_id):
    """無料プランの生徒のライフを確認し、残っていれば1つ消費する。
    先生アカウントや有料プランは対象外。ライフが無ければエラーメッセージを返す（Noneなら開始可）。"""
    with _lives_lock, get_db() as conn:
        user = conn.execute(
            'SELECT role, subscription_status, lives, last_life_lost_at FROM users WHERE id = ?',
            (user_id,)
        ).fetchone()
        if not user or user['role'] != 'student' or user['subscription_status'] == 'active':
            return None

        lives, last_life_lost_at = _recover_lives(user['lives'], user['last_life_lost_at'])

        if lives <= 0:
            anchor = _lives_anchor(last_life_lost_at)
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


def refund_life(user_id):
    """check_and_consume_life で消費したライフを1つ戻す（AIの呼び出しに失敗して会話を始められなかったとき用）。
    消費の対象外（先生・有料プラン）なら何もしない"""
    with _lives_lock, get_db() as conn:
        user = conn.execute(
            'SELECT role, subscription_status, lives, last_life_lost_at FROM users WHERE id = ?',
            (user_id,)
        ).fetchone()
        if not user or user['role'] != 'student' or user['subscription_status'] == 'active':
            return
        lives, last_life_lost_at = _recover_lives(user['lives'], user['last_life_lost_at'])
        new_lives = min(MAX_LIVES, lives + 1)
        # 満タンに戻ったら回復タイマーも止める（消費前の状態に戻す）
        new_anchor = None if new_lives >= MAX_LIVES else last_life_lost_at
        conn.execute('UPDATE users SET lives = ?, last_life_lost_at = ? WHERE id = ?',
                     (new_lives, new_anchor, user_id))
        conn.commit()


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
        anchor = _lives_anchor(last_life_lost_at)
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
            if llm.classify_error(e) in llm.RETRYABLE_ON_NEXT_MODEL:
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

    target_date = target_date or today_jst_str()
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

    # すでに完了記録がある会話なら「終わった会話」として復元する（続きの発言や二重の完了を受け付けない）
    done = False
    if conv_id:
        with get_db() as conn:
            done = conn.execute(
                'SELECT 1 FROM session_logs WHERE user_id = ? AND session_id = ?', (user_id, conv_id)
            ).fetchone() is not None

    instruction = tutoring.build_instruction(subject, difficulty, teacher_name)
    chat = create_chat(ALL_MODELS[0], instruction, history=history)
    user_turns = sum(1 for row in rows if row['role'] == 'user')
    turns = min(user_turns, 3)
    return {'chat': chat, 'model': ALL_MODELS[0], 'instruction': instruction, 'conv_id': conv_id,
            'turns': turns, 'user_turns': user_turns, 'subject': subject, 'difficulty': difficulty, 'unit': unit,
            # 復習・課題のIDはメモリにしか持っていないので、復元した会話では引き継がない
            'gap_id': None, 'assignment_id': None, 'done': done}


# ========================================
# 学習のきろく（会話ごとの一覧と、1つの会話の中身）
# ========================================
# 以前は全会話の全メッセージを毎回読み込んでいたので、使うほど重くなっていた。
# 一覧は「会話ごとの見出し」だけをページ単位で返し、中身は開いたときに1つずつ取りに行く。
#
# 会話の見分け方（key）:
#   - session_id がある会話（今の形式）: session_id そのもの
#   - session_id が無い過去データ: 「日付|科目」（同じ日・同じ科目をひとまとめにしていた頃の形式）
HISTORY_PAGE_SIZE = 20
_CONV_KEY_SQL = "COALESCE(NULLIF(session_id, ''), session_date || '|' || subject)"


def get_history_page(user_id, offset=0, limit=HISTORY_PAGE_SIZE):
    """学習のきろくの一覧（新しい順）。戻り値は {'sessions': [見出し...], 'has_more': bool}。
    Feynの最初の問いを見ただけで一度も返信していない会話は「学習」とみなさず除外する"""
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 100))
    with get_db() as conn:
        # SQLite・Postgresの両方で動くよう、まとめ方の式は内側で1回だけ作り、外側で GROUP BY する
        groups = conn.execute(
            'SELECT conv_key, MIN(id) AS first_id, COUNT(*) AS message_count '
            f'FROM (SELECT {_CONV_KEY_SQL} AS conv_key, id, role FROM conversation_logs WHERE user_id = ?) t '
            'GROUP BY conv_key '
            "HAVING SUM(CASE WHEN role = 'user' THEN 1 ELSE 0 END) > 0 "
            'ORDER BY MIN(id) DESC LIMIT ? OFFSET ?',
            (user_id, limit + 1, offset)
        ).fetchall()
        has_more = len(groups) > limit
        groups = groups[:limit]
        if not groups:
            return {'sessions': [], 'has_more': False}

        # 見出しに使う科目・難易度・単元は、その会話の最初の行から取る
        first_ids = [g['first_id'] for g in groups]
        placeholders = ','.join('?' * len(first_ids))
        first_rows = conn.execute(
            'SELECT id, subject, difficulty, unit, session_date, session_id FROM conversation_logs '
            f'WHERE id IN ({placeholders})',
            first_ids
        ).fetchall()
        first_by_id = {r['id']: r for r in first_rows}

        conv_ids = [r['session_id'] for r in first_rows if r['session_id']]
        completed_rows = []
        if conv_ids:
            placeholders = ','.join('?' * len(conv_ids))
            completed_rows = conn.execute(
                f'SELECT session_id, topic FROM session_logs WHERE user_id = ? AND session_id IN ({placeholders})',
                [user_id] + conv_ids
            ).fetchall()
        has_legacy = any(not r['session_id'] for r in first_rows)
        legacy_rows = conn.execute(
            'SELECT subject, completed_at FROM session_logs WHERE user_id = ? AND session_id IS NULL',
            (user_id,)
        ).fetchall() if has_legacy else []

    completed_conv_ids = {r['session_id'] for r in completed_rows}
    topic_by_conv_id = {r['session_id']: r['topic'] for r in completed_rows if r['topic']}
    # 過去データ（session_id無し）は日付×科目で完了を判定する
    completed_legacy_keys = {f"{str(r['completed_at'])[:10]}|{r['subject']}" for r in legacy_rows}

    sessions = []
    for g in groups:
        first = first_by_id[g['first_id']]
        conv_id = first['session_id'] or None
        sessions.append({
            'key':           g['conv_key'],
            'session_id':    conv_id,
            'date':          first['session_date'],
            'subject':       first['subject'],
            'difficulty':    first['difficulty'],
            'unit':          first['unit'],
            'topic':         topic_by_conv_id.get(conv_id) if conv_id else None,
            'message_count': g['message_count'],
            'completed':     (conv_id in completed_conv_ids) if conv_id else (g['conv_key'] in completed_legacy_keys),
        })
    return {'sessions': sessions, 'has_more': has_more}


def get_history_messages(user_id, key):
    """1つの会話の中身（古い順）。key は get_history_page が返した 'key'。見つからなければ空のリスト"""
    key = (key or '').strip()
    if not key:
        return []
    with get_db() as conn:
        if '|' in key:
            session_date, subject = key.split('|', 1)
            rows = conn.execute(
                'SELECT role, message FROM conversation_logs '
                "WHERE user_id = ? AND (session_id IS NULL OR session_id = '') "
                'AND session_date = ? AND subject = ? ORDER BY id',
                (user_id, session_date, subject)
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT role, message FROM conversation_logs WHERE user_id = ? AND session_id = ? ORDER BY id',
                (user_id, key)
            ).fetchall()
    return [{'role': r['role'], 'message': r['message']} for r in rows]


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
            # テーブル名を付けて書く。付けないとPostgresでは「更新前の値」と「入れようとした値」のどちらか
            # 曖昧だとしてエラーになり、同じ処理内の苦手の保存も取り消されてしまう（SQLiteは許すので気づきにくい）
            '              sessions_count = topic_progress.sessions_count + 1, '
            '              last_studied = excluded.last_studied',
            (user_id, subject, analysis['topic'], analysis['understanding_score'], today)
        )
        conn.commit()
