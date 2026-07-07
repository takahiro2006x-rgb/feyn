from flask import Flask, request, jsonify, send_from_directory, session, redirect, abort
from flask_cors import CORS
from google import genai
from google.genai import types
from werkzeug.security import generate_password_hash, check_password_hash
import os
import time
import random
import string
import uuid
import base64
from datetime import date, timedelta
from dotenv import load_dotenv

import db
import llm
import tutoring
import gap_analyzer
import report

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'feyn-dev-secret-2024')
# ブラウザを閉じるたびに再ログインを求めないよう、ログインセッションを30日間保持する
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
CORS(app, supports_credentials=True)

TEACHER_CODE = os.environ.get('TEACHER_CODE', 'FEYN_TEACHER_2024')
SUBJECTS = ['物理', '数学', '英語', '化学', '生物', '国語', '歴史']

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

DEFAULT_SCHOOL_ID = ensure_default_school()


# --- Gemini クライアント ---
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


# --- チャットセッションを管理する辞書 ---
chat_sessions = {}


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
# 静的ファイルの配信
# ========================================
@app.route('/')
def index():
    if not session.get('user_id'):
        return redirect('/landing')
    return send_from_directory('.', 'index.html')

@app.route('/landing')
def landing_page():
    # ログイン中でもLPを見られるようにする（LP側でログイン状態に応じてCTAを出し分ける）
    return send_from_directory('.', 'landing.html')

@app.route('/login')
def login_page():
    if session.get('user_id'):
        return redirect('/')
    return send_from_directory('.', 'login.html')

# 学習のきろく・苦手ノートはマイページのタブに統合したため、旧URLはそちらへ誘導する
@app.route('/history')
def history_page():
    if not session.get('user_id'):
        return redirect('/login')
    return redirect('/mypage?tab=history')

@app.route('/gaps')
def gaps_page():
    if not session.get('user_id'):
        return redirect('/login')
    return redirect('/mypage?tab=gaps')

@app.route('/mypage')
def mypage_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory('.', 'mypage.html')

@app.route('/dashboard')
def dashboard_page():
    if not session.get('user_id'):
        return redirect('/login')
    if session.get('user_role') != 'teacher':
        return redirect('/')
    return send_from_directory('.', 'dashboard.html')

@app.route('/dashboard/student/<int:student_id>')
def dashboard_student_page(student_id):
    if not session.get('user_id'):
        return redirect('/login')
    if session.get('user_role') != 'teacher':
        return redirect('/')
    return send_from_directory('.', 'student_detail.html')

# DBや.envを外部に配信しないよう、公開ファイルはホワイトリスト方式にする
ALLOWED_STATIC = {'style.css', 'script.js'}

# ===== PWA用ファイル =====
# sw.jsはルート直下（/sw.js）で配信することで、スコープをアプリ全体にする
@app.route('/manifest.json')
def manifest():
    return send_from_directory('.', 'manifest.json')

@app.route('/sw.js')
def service_worker():
    return send_from_directory('.', 'sw.js')

@app.route('/icons/<path:filename>')
def icons(filename):
    return send_from_directory('icons', filename)

@app.route('/<path:path>')
def static_files(path):
    if path not in ALLOWED_STATIC:
        abort(404)
    return send_from_directory('.', path)


# ========================================
# 認証 API
# ========================================
@app.route('/api/signup', methods=['POST'])
def signup():
    data         = request.get_json()
    email        = data.get('email', '').strip().lower()
    name         = data.get('name', '').strip()
    password     = data.get('password', '')
    teacher_code = data.get('teacher_code', '').strip()

    security_question = data.get('security_question', '').strip()
    security_answer   = data.get('security_answer', '').strip().lower()

    if not email or not name or not password:
        return jsonify({'error': 'すべての項目を入力してください'}), 400
    if '@' not in email:
        return jsonify({'error': '正しいメールアドレスを入力してください'}), 400
    if len(name) > 20:
        return jsonify({'error': 'ニックネームは20文字以内にしてください'}), 400
    if len(password) < 8:
        return jsonify({'error': 'パスワードは8文字以上にしてください'}), 400
    if teacher_code and teacher_code != TEACHER_CODE:
        return jsonify({'error': '先生コードが正しくありません'}), 400
    if not security_question or security_question not in SECURITY_QUESTIONS:
        return jsonify({'error': '秘密の質問を選んでください'}), 400
    if not security_answer:
        return jsonify({'error': '秘密の質問の答えを入力してください'}), 400

    role = 'teacher' if teacher_code == TEACHER_CODE else 'student'

    try:
        with get_db() as conn:
            conn.execute(
                'INSERT INTO users (email, name, password_hash, role, security_question, security_answer_hash, last_login, school_id) '
                'VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)',
                (email, name, generate_password_hash(password), role,
                 security_question, generate_password_hash(security_answer), DEFAULT_SCHOOL_ID)
            )
            conn.commit()
            user = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
            session.permanent      = True
            session['user_id']    = user['id']
            session['user_name']  = name
            session['user_role']  = role
            session['school_id']  = DEFAULT_SCHOOL_ID
        return jsonify({'ok': True, 'name': name, 'role': role})
    except db.IntegrityError:
        return jsonify({'error': 'そのメールアドレスはすでに登録されています'}), 409


@app.route('/api/login', methods=['POST'])
def login():
    data     = request.get_json()
    email    = data.get('email', '').strip().lower()
    password = data.get('password', '')

    with get_db() as conn:
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': 'メールアドレスまたはパスワードが違います'}), 401

    with get_db() as conn:
        conn.execute('UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?', (user['id'],))
        conn.commit()

    session.permanent     = True
    session['user_id']    = user['id']
    session['user_name']  = user['name']
    session['user_role']  = user['role']
    session['school_id']  = user['school_id'] or DEFAULT_SCHOOL_ID
    return jsonify({'ok': True, 'name': user['name'], 'role': user['role']})


@app.route('/api/update-name', methods=['POST'])
def update_name():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    data = request.get_json()
    name = data.get('name', '').strip()

    if not name:
        return jsonify({'error': 'ニックネームを入力してください'}), 400
    if len(name) > 20:
        return jsonify({'error': 'ニックネームは20文字以内にしてください'}), 400

    with get_db() as conn:
        conn.execute('UPDATE users SET name = ? WHERE id = ?', (name, session['user_id']))
        conn.commit()
    session['user_name'] = name
    return jsonify({'ok': True})


@app.route('/api/set-security-question', methods=['POST'])
def set_security_question():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    data              = request.get_json()
    security_question = data.get('security_question', '').strip()
    security_answer   = data.get('security_answer', '').strip().lower()

    if not security_question or security_question not in SECURITY_QUESTIONS:
        return jsonify({'error': '秘密の質問を選んでください'}), 400
    if not security_answer:
        return jsonify({'error': '答えを入力してください'}), 400

    with get_db() as conn:
        conn.execute(
            'UPDATE users SET security_question = ?, security_answer_hash = ? WHERE id = ?',
            (security_question, generate_password_hash(security_answer), session['user_id'])
        )
        conn.commit()
    return jsonify({'ok': True})


@app.route('/api/security-question', methods=['POST'])
def security_question_api():
    data  = request.get_json()
    email = data.get('email', '').strip().lower()
    with get_db() as conn:
        user = conn.execute(
            'SELECT security_question FROM users WHERE email = ?', (email,)
        ).fetchone()
    # 登録済みメールアドレスかどうかを外部から判別できないよう、エラーは同一メッセージにする
    if not user or not user['security_question']:
        return jsonify({'error': 'このメールアドレスではリセットできません。メールアドレスを確認するか、先生に相談してください'}), 404
    return jsonify({'question': user['security_question']})


@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    data         = request.get_json()
    email        = data.get('email', '').strip().lower()
    answer       = data.get('answer', '').strip().lower()
    new_password = data.get('new_password', '')

    if len(new_password) < 8:
        return jsonify({'error': 'パスワードは8文字以上にしてください'}), 400

    with get_db() as conn:
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

    if not user or not user['security_answer_hash']:
        return jsonify({'error': 'このメールアドレスではリセットできません。メールアドレスを確認するか、先生に相談してください'}), 404
    if not check_password_hash(user['security_answer_hash'], answer):
        return jsonify({'error': '答えが正しくありません'}), 401

    with get_db() as conn:
        conn.execute(
            'UPDATE users SET password_hash = ? WHERE email = ?',
            (generate_password_hash(new_password), email)
        )
        conn.commit()
    return jsonify({'ok': True})


@app.route('/api/dashboard/student/<int:student_id>')
def dashboard_student(student_id):
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401
    if session.get('user_role') != 'teacher':
        return jsonify({'error': '権限がありません'}), 403

    school_id = session.get('school_id', DEFAULT_SCHOOL_ID)
    with get_db() as conn:
        student = conn.execute(
            "SELECT id, name, email FROM users WHERE id = ? AND role = 'student' AND school_id = ?",
            (student_id, school_id)
        ).fetchone()
        if not student:
            return jsonify({'error': '生徒が見つかりません'}), 404

        gap_rows = conn.execute(
            "SELECT id, subject, topic, gap_type, description, evidence, status, session_date "
            "FROM knowledge_gaps WHERE user_id = ? ORDER BY (status = 'resolved'), id DESC",
            (student_id,)
        ).fetchall()
        progress_rows = conn.execute(
            'SELECT subject, topic, understanding, sessions_count, last_studied '
            'FROM topic_progress WHERE user_id = ? ORDER BY understanding, last_studied DESC',
            (student_id,)
        ).fetchall()
        assignment_rows = conn.execute(
            "SELECT id, subject, unit, status, created_at, completed_at "
            "FROM assignments WHERE student_id = ? ORDER BY (status = 'completed'), id DESC",
            (student_id,)
        ).fetchall()

    return jsonify({
        'student':     dict(student),
        'gaps':        [dict(r) for r in gap_rows],
        'progress':    [dict(r) for r in progress_rows],
        'sessions':    get_history_sessions(student_id),
        'assignments': [dict(r) for r in assignment_rows],
        'labels':      tutoring.GAP_TYPE_LABELS,
    })


@app.route('/api/dashboard/assignments', methods=['POST'])
def create_assignment():
    """先生が生徒に科目・単元を指定して課題を出す"""
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401
    if session.get('user_role') != 'teacher':
        return jsonify({'error': '権限がありません'}), 403

    data        = request.get_json()
    student_ids = data.get('student_ids')
    if not student_ids:
        single_id   = data.get('student_id')
        student_ids = [single_id] if single_id else []
    subject = data.get('subject', '')
    unit    = (data.get('unit') or '').strip() or None

    if subject not in SUBJECTS:
        return jsonify({'error': '科目を指定してください'}), 400
    if not student_ids:
        return jsonify({'error': '生徒を選択してください'}), 400

    school_id = session.get('school_id', DEFAULT_SCHOOL_ID)
    with get_db() as conn:
        placeholders = ','.join('?' * len(student_ids))
        valid_rows = conn.execute(
            f"SELECT id FROM users WHERE role = 'student' AND school_id = ? AND id IN ({placeholders})",
            [school_id] + list(student_ids)
        ).fetchall()
        valid_ids = [r['id'] for r in valid_rows]
        if not valid_ids:
            return jsonify({'error': '生徒が見つかりません'}), 404

        for sid in valid_ids:
            conn.execute(
                'INSERT INTO assignments (teacher_id, student_id, subject, unit) VALUES (?, ?, ?, ?)',
                (session['user_id'], sid, subject, unit)
            )
        conn.commit()

    return jsonify({'ok': True, 'count': len(valid_ids)})


@app.route('/api/dashboard/reset-password', methods=['POST'])
def teacher_reset_password():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401
    if session.get('user_role') != 'teacher':
        return jsonify({'error': '権限がありません'}), 403

    data       = request.get_json()
    student_id = data.get('student_id')
    school_id  = session.get('school_id', DEFAULT_SCHOOL_ID)

    temp_pass = 'Feyn' + ''.join(random.choices(string.digits, k=4))

    with get_db() as conn:
        updated = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ? AND role = 'student' AND school_id = ?",
            (generate_password_hash(temp_pass), student_id, school_id)
        ).rowcount
        conn.commit()

    if updated == 0:
        return jsonify({'error': '生徒が見つかりません'}), 404
    return jsonify({'ok': True, 'temp_password': temp_pass})


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})


@app.route('/api/me')
def me():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしていません'}), 401
    today = str(date.today())
    with get_db() as conn:
        user = conn.execute('SELECT security_question FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        rows = conn.execute(
            'SELECT subject, COUNT(*) as cnt FROM session_logs WHERE user_id = ? GROUP BY subject',
            (session['user_id'],)
        ).fetchall()
        due = conn.execute(
            "SELECT COUNT(*) as cnt FROM knowledge_gaps "
            "WHERE user_id = ? AND ((status = 'resolved' AND next_review IS NOT NULL AND next_review <= ?) OR status = 'open')",
            (session['user_id'], today)
        ).fetchone()['cnt']
        open_assignments = conn.execute(
            "SELECT COUNT(*) as cnt FROM assignments WHERE student_id = ? AND status = 'open'",
            (session['user_id'],)
        ).fetchone()['cnt']
    subject_clears = {row['subject']: row['cnt'] for row in rows}
    return jsonify({
        'id':                    session['user_id'],
        'name':                  session['user_name'],
        'role':                  session.get('user_role', 'student'),
        'has_security_question': bool(user and user['security_question']),
        'total_clears':          sum(subject_clears.values()),
        'subject_clears':        subject_clears,
        'due_reviews':           due,
        'open_assignments':      open_assignments,
    })


@app.route('/api/assignments')
def assignments_api():
    """自分に出されている未完了の課題一覧（アプリのトップで受け取る用）"""
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, subject, unit, created_at FROM assignments "
            "WHERE student_id = ? AND status = 'open' ORDER BY id",
            (session['user_id'],)
        ).fetchall()

    return jsonify({'assignments': [dict(r) for r in rows]})


# ========================================
# ストリーク取得
# ========================================
@app.route('/api/streak')
def streak():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date(completed_at) as day FROM session_logs WHERE user_id = ? ORDER BY day DESC",
            (session['user_id'],)
        ).fetchall()

    if not rows:
        return jsonify({'streak': 0})

    today = date.today()
    days = [date.fromisoformat(row['day']) for row in rows]

    if days[0] < today - timedelta(days=1):
        return jsonify({'streak': 0})

    count = 1
    for i in range(1, len(days)):
        if days[i] == days[i - 1] - timedelta(days=1):
            count += 1
        else:
            break

    return jsonify({'streak': count})


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


@app.route('/api/history')
def history_api():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401
    return jsonify({'sessions': get_history_sessions(session['user_id'])})


# ========================================
# 中断した学習を続きから再開する（学習のきろくから）
# ========================================
@app.route('/api/resume', methods=['POST'])
def resume():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    data         = request.get_json()
    subject      = data.get('subject', '')
    target_date  = data.get('date', '')
    conv_id      = data.get('session_id') or None
    teacher_name = session.get('user_name', '先生')

    if subject not in SUBJECTS or not target_date:
        return jsonify({'error': '再開する学習が見つかりません'}), 400

    with get_db() as conn:
        if conv_id:
            already_done = conn.execute(
                'SELECT 1 FROM session_logs WHERE user_id = ? AND session_id = ?',
                (session['user_id'], conv_id)
            ).fetchone()
        else:
            # session_idが無い過去データへの後方互換（日付+科目で判定）
            completed_rows = conn.execute(
                'SELECT completed_at FROM session_logs WHERE user_id = ? AND subject = ?',
                (session['user_id'], subject)
            ).fetchall()
            already_done = any(str(r['completed_at'])[:10] == target_date for r in completed_rows)
    if already_done:
        return jsonify({'error': 'この学習はすでに完了しています'}), 400

    session_key = f"{session['user_id']}_{subject}"
    restored = restore_chat_session(session_key, session['user_id'], teacher_name,
                                     target_date=target_date, conv_id=conv_id)
    if restored is None:
        return jsonify({'error': '再開する学習が見つかりません'}), 404

    chat_sessions[session_key] = restored
    resolved_conv_id = restored.get('conv_id')

    with get_db() as conn:
        if resolved_conv_id:
            rows = conn.execute(
                'SELECT role, message FROM conversation_logs '
                'WHERE user_id = ? AND subject = ? AND session_id = ? ORDER BY id',
                (session['user_id'], subject, resolved_conv_id)
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT role, message FROM conversation_logs '
                'WHERE user_id = ? AND subject = ? AND session_date = ? ORDER BY id',
                (session['user_id'], subject, target_date)
            ).fetchall()

    return jsonify({
        'session_key': session_key,
        'subject':     subject,
        'difficulty':  restored['difficulty'],
        'messages':    [{'role': r['role'], 'message': r['message']} for r in rows],
        'progress':    restored['turns'] * 25,
    })


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


@app.route('/api/complete', methods=['POST'])
def complete():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    data          = request.get_json()
    subject       = data.get('subject', '')
    difficulty    = data.get('difficulty', '')
    gap_id        = data.get('gap_id')
    assignment_id = data.get('assignment_id')
    session_key   = data.get('session_key') or ''

    if not subject:
        return jsonify({'error': '科目が指定されていません'}), 400

    # 他人のセッションキーを指定できないようにする
    conv_id = None
    if session_key.startswith(f"{session['user_id']}_") and session_key in chat_sessions:
        conv_id = chat_sessions[session_key].get('conv_id')

    today = str(date.today())
    with get_db() as conn:
        conn.execute(
            'INSERT INTO session_logs (user_id, subject, difficulty, session_id) VALUES (?, ?, ?, ?)',
            (session['user_id'], subject, difficulty, conv_id)
        )
        # 課題として出されていたセッションの完了を記録する
        if assignment_id:
            conn.execute(
                "UPDATE assignments SET status = 'completed', completed_at = CURRENT_TIMESTAMP "
                'WHERE id = ? AND student_id = ?',
                (assignment_id, session['user_id'])
            )
        # 復習セッションの完了 = Feynが納得した = ギャップ解消とみなす。
        # 忘却曲線に沿って次の復習日を先送りしていく（1→3→7→14→30日）
        if gap_id:
            row = conn.execute(
                'SELECT review_count FROM knowledge_gaps WHERE id = ? AND user_id = ?',
                (gap_id, session['user_id'])
            ).fetchone()
            if row:
                count    = row['review_count'] + 1
                interval = REVIEW_INTERVALS[min(count - 1, len(REVIEW_INTERVALS) - 1)]
                conn.execute(
                    "UPDATE knowledge_gaps SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP, "
                    'review_count = ?, next_review = ? WHERE id = ? AND user_id = ?',
                    (count, str(date.today() + timedelta(days=interval)), gap_id, session['user_id'])
                )
        if conv_id:
            rows = conn.execute(
                'SELECT role, message FROM conversation_logs '
                'WHERE user_id = ? AND subject = ? AND session_id = ? ORDER BY id',
                (session['user_id'], subject, conv_id)
            ).fetchall()
        else:
            # conv_idが分からない場合（後方互換）は従来どおり日付+科目でまとめて取得する
            rows = conn.execute(
                'SELECT role, message FROM conversation_logs '
                'WHERE user_id = ? AND subject = ? AND session_date = ? ORDER BY id',
                (session['user_id'], subject, today)
            ).fetchall()
        topic_rows = conn.execute(
            'SELECT topic FROM topic_progress WHERE user_id = ? AND subject = ?',
            (session['user_id'], subject)
        ).fetchall()
        conn.commit()

    chat_sessions.pop(session_key, None)

    # 対話履歴からナレッジギャップを自動分析（失敗しても完了記録は成立させる）
    analysis = None
    try:
        analysis = gap_analyzer.analyze_session(
            client, ALL_MODELS, subject, difficulty,
            [{'role': r['role'], 'message': r['message']} for r in rows],
            existing_topics=[r['topic'] for r in topic_rows],
            on_attempt=lambda model, ok: log_api_call(model, 'analysis', ok),
            groq_client=groq_client, groq_models=llm.GROQ_MODELS,
        )
        if analysis:
            save_analysis(session['user_id'], subject, analysis, today)
            # 会話ログの見出しに具体的なテーマを出せるよう、このセッションの分析結果を紐付けておく
            if conv_id and analysis.get('topic'):
                with get_db() as conn:
                    conn.execute(
                        'UPDATE session_logs SET topic = ? WHERE user_id = ? AND session_id = ?',
                        (analysis['topic'], session['user_id'], conv_id)
                    )
                    conn.commit()
    except Exception:
        # 分析失敗はクリア記録を妨げない（原因調査用にログだけ残す）
        import traceback; traceback.print_exc()
        analysis = None

    return jsonify({'ok': True, 'analysis': analysis})


# ========================================
# 苦手ノート API（ギャップ一覧・手動解決）
# ========================================
@app.route('/api/gaps')
def gaps_api():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    today = str(date.today())
    with get_db() as conn:
        gap_rows = conn.execute(
            "SELECT id, subject, topic, gap_type, description, evidence, suggested_question, "
            "status, session_date, resolved_at, review_count, next_review "
            "FROM knowledge_gaps WHERE user_id = ? "
            "ORDER BY (status = 'resolved'), id DESC",
            (session['user_id'],)
        ).fetchall()
        progress_rows = conn.execute(
            'SELECT subject, topic, understanding, sessions_count, last_studied '
            'FROM topic_progress WHERE user_id = ? ORDER BY understanding, last_studied DESC',
            (session['user_id'],)
        ).fetchall()

    gaps = []
    for r in gap_rows:
        g = dict(r)
        # 忘却曲線: 解決済みでも次の復習日が来ていたら「復習どき」として再浮上させる
        g['due'] = bool(g['status'] == 'resolved' and g['next_review'] and g['next_review'] <= today)
        gaps.append(g)

    return jsonify({
        'gaps':     gaps,
        'progress': [dict(r) for r in progress_rows],
        'labels':   tutoring.GAP_TYPE_LABELS,
    })


@app.route('/api/mypage')
def mypage_api():
    """科目別・総合の疑似偏差値と対応する大学レベルの目安を返す"""
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    with get_db() as conn:
        topic_rows = conn.execute(
            'SELECT subject, understanding FROM topic_progress WHERE user_id = ?',
            (session['user_id'],)
        ).fetchall()
        gap_rows = conn.execute(
            'SELECT subject, status FROM knowledge_gaps WHERE user_id = ?',
            (session['user_id'],)
        ).fetchall()

    subjects_result = []
    scores = []
    for subject in SUBJECTS:
        s_topics = [dict(r) for r in topic_rows if r['subject'] == subject]
        s_gaps   = [dict(r) for r in gap_rows if r['subject'] == subject]
        score = report.compute_subject_score(s_topics, s_gaps)
        if score is None:
            continue
        hensachi = report.score_to_hensachi(score)
        scores.append(score)
        subjects_result.append({
            'subject':    subject,
            'hensachi':   hensachi,
            'university': report.hensachi_to_university(hensachi),
        })

    overall = None
    if scores:
        overall_hensachi = report.score_to_hensachi(sum(scores) / len(scores))
        overall = {
            'hensachi':   overall_hensachi,
            'university': report.hensachi_to_university(overall_hensachi),
        }

    return jsonify({'subjects': subjects_result, 'overall': overall})


@app.route('/api/gaps/resolve', methods=['POST'])
def resolve_gap():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    data   = request.get_json()
    gap_id = data.get('gap_id')

    with get_db() as conn:
        updated = conn.execute(
            "UPDATE knowledge_gaps SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (gap_id, session['user_id'])
        ).rowcount
        conn.commit()

    if updated == 0:
        return jsonify({'error': '項目が見つかりません'}), 404
    return jsonify({'ok': True})


# ========================================
# ダッシュボード API（先生専用）
# ========================================
@app.route('/api/dashboard')
def dashboard_api():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401
    if session.get('user_role') != 'teacher':
        return jsonify({'error': '権限がありません'}), 403

    school_id = session.get('school_id', DEFAULT_SCHOOL_ID)
    with get_db() as conn:
        students_rows = conn.execute(
            "SELECT id, name, email, created_at, last_login FROM users "
            "WHERE role = 'student' AND school_id = ? ORDER BY created_at DESC",
            (school_id,)
        ).fetchall()
        count_rows = conn.execute(
            'SELECT user_id, subject, COUNT(*) as cnt FROM session_logs GROUP BY user_id, subject'
        ).fetchall()
        last_rows = conn.execute(
            'SELECT user_id, MAX(completed_at) as last_at FROM session_logs GROUP BY user_id'
        ).fetchall()
        submission_rows = conn.execute(
            "SELECT a.subject, a.unit, a.completed_at, u.name as student_name "
            "FROM assignments a JOIN users u ON u.id = a.student_id "
            "WHERE a.status = 'completed' AND a.teacher_id = ? "
            "ORDER BY a.completed_at DESC LIMIT 20",
            (session['user_id'],)
        ).fetchall()

    counts_by_user = {}
    for row in count_rows:
        counts_by_user.setdefault(row['user_id'], {})[row['subject']] = row['cnt']
    last_by_user = {row['user_id']: row['last_at'] for row in last_rows}

    result = []
    for s in students_rows:
        user_counts = counts_by_user.get(s['id'], {})
        counts = {subj: user_counts.get(subj, 0) for subj in SUBJECTS}
        result.append({
            'id':         s['id'],
            'name':       s['name'],
            'email':      s['email'],
            'created_at': s['created_at'],
            'last_active': last_by_user.get(s['id']),
            'last_login': s['last_login'],
            'subjects':   counts,
            'total':      sum(counts.values()),
        })

    total_clears = sum(s['total'] for s in result)
    top_subject  = max(SUBJECTS, key=lambda subj: sum(s['subjects'][subj] for s in result)) if result else '—'

    return jsonify({
        'students': result,
        'summary': {
            'total_students': len(result),
            'total_clears':   total_clears,
            'top_subject':    top_subject,
        },
        'recent_submissions': [dict(r) for r in submission_rows],
    })


@app.route('/api/dashboard/usage')
def dashboard_usage():
    """Gemini無料枠の今日の使用状況（モデル別の成功/失敗回数）"""
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401
    if session.get('user_role') != 'teacher':
        return jsonify({'error': '権限がありません'}), 403

    today = str(date.today())
    with get_db() as conn:
        rows = conn.execute(
            'SELECT model, endpoint, success, created_at FROM api_calls ORDER BY id DESC LIMIT 2000'
        ).fetchall()

    # created_atはSQLiteでは文字列、Postgresではdatetimeで返るため str() で先頭10文字を比較する
    today_rows = [r for r in rows if str(r['created_at'])[:10] == today]

    by_model = {}
    for r in today_rows:
        m = by_model.setdefault(r['model'], {'success': 0, 'fail': 0})
        m['success' if r['success'] else 'fail'] += 1

    return jsonify({
        'date':        today,
        'total_calls': len(today_rows),
        'by_model':    by_model,
        'models_order': ALL_MODELS,
    })


# ========================================
# Feyn の最初のメッセージを取得
# ========================================
@app.route('/api/start', methods=['POST'])
def start():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    data          = request.get_json()
    subject       = data.get('subject', '物理')
    difficulty    = data.get('difficulty', '大学受験')
    teacher_name  = data.get('teacher_name', session.get('user_name', '先生'))
    unit          = (data.get('unit') or '').strip() or None
    gap_id        = data.get('gap_id')
    assignment_id = data.get('assignment_id')
    photo_b64     = data.get('photo')
    photo_mime    = data.get('photo_mime', 'image/jpeg')

    # 写真モード: 自分が今解いている問題の写真をアップロードして、それについて質問してもらう
    photo_bytes = None
    if photo_b64:
        if photo_mime not in ('image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif'):
            return jsonify({'error': '対応していない画像形式です。写真（jpg/png等）を選んでください。'}), 400
        try:
            photo_bytes = base64.b64decode(photo_b64)
        except Exception:
            return jsonify({'error': '画像の読み込みに失敗しました。もう一度試してください。'}), 400
        if len(photo_bytes) > 8 * 1024 * 1024:
            return jsonify({'error': '画像サイズが大きすぎます（8MBまで）。'}), 400
        unit = '写真の問題'

    # 復習モード: 過去に特定した知識ギャップを狙い撃ちする
    # （解決済みでも忘却曲線の「復習どき」に再挑戦できるよう、statusでは弾かない）
    gap = None
    if gap_id:
        with get_db() as conn:
            gap = conn.execute(
                'SELECT * FROM knowledge_gaps WHERE id = ? AND user_id = ?',
                (gap_id, session['user_id'])
            ).fetchone()
        if gap is None:
            return jsonify({'error': 'この復習項目は見つかりません'}), 404
        subject = gap['subject']
        unit    = gap['topic']  # 復習モードでは会話ログの見出しに「何を復習したか」が分かるようにする

    # 課題モード: 先生が指定した科目・単元で開始する
    if assignment_id:
        with get_db() as conn:
            assignment = conn.execute(
                "SELECT * FROM assignments WHERE id = ? AND student_id = ? AND status = 'open'",
                (assignment_id, session['user_id'])
            ).fetchone()
        if assignment is None:
            return jsonify({'error': 'この課題は見つかりません（すでに完了しているかもしれません）'}), 404
        subject = assignment['subject']
        unit    = assignment['unit']

    session_key = f"{session['user_id']}_{subject}"
    if photo_bytes:
        instruction = tutoring.build_photo_instruction(subject, difficulty, teacher_name)
        kickoff     = tutoring.build_photo_kickoff()
    elif gap:
        instruction = tutoring.build_review_instruction(subject, difficulty, teacher_name, gap)
        kickoff     = tutoring.build_review_kickoff(gap)
    else:
        instruction = tutoring.build_instruction(subject, difficulty, teacher_name, unit=unit)
        kickoff     = tutoring.build_kickoff(subject, unit=unit)

    # 写真モードは画像を読める必要があるため、画像非対応のGroqにはフォールバックしない
    models_to_try = GEMINI_MODELS if photo_bytes else ALL_MODELS

    response   = None
    used_model = None
    quota_only = True
    for model in models_to_try:
        try:
            chat = create_chat(model, instruction)
            if photo_bytes:
                response = chat.send_message(kickoff, image=(photo_bytes, photo_mime))
            else:
                response = chat.send_message(kickoff)
            used_model = model
            log_api_call(model, 'start', True)
            break
        except Exception as e:
            err = str(e)
            log_api_call(model, 'start', False)
            # 枠切れ(429)・過負荷(503)はモデル単位の問題なので次のモデルで再挑戦する
            if '429' in err:
                continue
            if '503' in err or 'UNAVAILABLE' in err:
                quota_only = False
                continue
            return jsonify({'error': 'AIサーバーが混み合っています。少し待ってからもう一度試してください。'}), 503

    if response is None:
        if quota_only:
            return jsonify({'error': '本日のAPI利用上限に達しました。'}), 429
        return jsonify({'error': 'AIサーバーが混み合っています。少し待ってからもう一度試してください。'}), 503

    # 同じ科目でも「今回の会話」を後から識別できるよう、開始ごとに新しいIDを振る
    conv_id = uuid.uuid4().hex[:12]
    chat_sessions[session_key] = {
        'chat':        chat,
        'model':       used_model,
        'instruction': instruction,
        'turns':       0,
        'subject':     subject,
        'difficulty':  difficulty,
        'conv_id':     conv_id,
        'unit':        unit,
    }

    reply = response.text or ''
    today = str(date.today())
    with get_db() as conn:
        if gap:
            conn.execute(
                "UPDATE knowledge_gaps SET status = 'reviewing' WHERE id = ?", (gap['id'],)
            )
        conn.execute(
            'INSERT INTO conversation_logs (user_id, subject, difficulty, role, message, session_date, session_id, unit) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (session['user_id'], subject, difficulty, 'feyn', reply, today, conv_id, unit)
        )
        conn.commit()
    return jsonify({'reply': reply, 'session_key': session_key, 'subject': subject})


# ========================================
# ユーザーの解説を受け取ってFeynが返答
# ========================================
@app.route('/api/chat', methods=['POST'])
def chat():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    data        = request.get_json()
    message     = data.get('message', '')
    session_key = data.get('session_key') or ''

    if not message:
        return jsonify({'error': 'メッセージが空です'}), 400

    # 他人のセッションキーを指定できないようにする
    if not session_key.startswith(f"{session['user_id']}_"):
        return jsonify({'error': 'セッションが見つかりません。リロードしてください。'}), 400

    if session_key not in chat_sessions:
        restored = restore_chat_session(session_key, session['user_id'], session.get('user_name', '先生'))
        if restored is None:
            return jsonify({'error': 'セッションが見つかりません。リロードしてください。'}), 400
        chat_sessions[session_key] = restored

    sess = chat_sessions[session_key]

    today = str(date.today())
    subject    = sess.get('subject', '')
    difficulty = sess.get('difficulty', '')
    conv_id    = sess.get('conv_id')
    unit       = sess.get('unit')

    retries = 0
    while True:
        try:
            response = sess['chat'].send_message(message)
            log_api_call(sess['model'], 'chat', True)
            break
        except Exception as e:
            err = str(e)
            log_api_call(sess['model'], 'chat', False)
            overloaded = '503' in err or 'UNAVAILABLE' in err

            # 過負荷はまず同じモデルで少し待って再試行する
            if overloaded and retries < 2:
                retries += 1
                time.sleep(2)
                continue

            # 枠切れ(429)・回復しない過負荷は、会話履歴を引き継いで次のモデルへ切り替える
            if '429' in err or overloaded:
                idx = ALL_MODELS.index(sess['model']) if sess.get('model') in ALL_MODELS else len(ALL_MODELS) - 1
                if idx + 1 < len(ALL_MODELS):
                    next_model = ALL_MODELS[idx + 1]
                    sess['chat']  = create_chat(next_model, sess['instruction'], history=sess['chat'].get_history())
                    sess['model'] = next_model
                    retries = 0
                    continue
                if '429' in err:
                    return jsonify({'error': '本日のAPI利用上限に達しました。'}), 429
            return jsonify({'error': 'AIサーバーが混み合っています。'}), 503

    reply   = response.text or ''
    is_done = '【会話終了】' in reply
    reply   = reply.replace('【会話終了】', '').strip()

    if not is_done:
        sess['turns'] = min(sess['turns'] + 1, 3)
    progress = 100 if is_done else sess['turns'] * 25

    # is_done時にここでchat_sessionsを消すと/api/completeがconv_idを取得できなくなるため、
    # 後始末（pop）は完了記録を書き終えた/api/complete側で行う

    with get_db() as conn:
        conn.execute(
            'INSERT INTO conversation_logs (user_id, subject, difficulty, role, message, session_date, session_id, unit) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (session['user_id'], subject, difficulty, 'user', message, today, conv_id, unit)
        )
        conn.execute(
            'INSERT INTO conversation_logs (user_id, subject, difficulty, role, message, session_date, session_id, unit) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (session['user_id'], subject, difficulty, 'feyn', reply, today, conv_id, unit)
        )
        conn.commit()

    return jsonify({'reply': reply, 'is_done': is_done, 'progress': progress})


# ========================================
# ヒント・答えの表示（メインの対話ログには残さない）
# ========================================
@app.route('/api/hint', methods=['POST'])
def hint():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    session_key = request.get_json().get('session_key') or ''
    if not session_key.startswith(f"{session['user_id']}_") or session_key not in chat_sessions:
        return jsonify({'error': 'セッションが見つかりません。リロードしてください。'}), 400

    question = get_last_feyn_message(session_key)
    if not question:
        return jsonify({'error': 'ヒントを出せる質問がまだありません。'}), 400

    instruction = (
        'あなたは学習アプリの「ヒント係」です。以下は、キャラクター「Feyn」が生徒に投げた質問です。'
        '生徒はこの質問にどう答えればいいか困っています。'
        '答えそのものや説明は絶対に言わないでください。考えるきっかけになるキーワードを1〜2つだけ、'
        '短い言葉で提示してください（例:「浮力」「エネルギー保存」）。文章での説明はしないこと。'
    )
    result = generate_once(instruction, f'Feynの質問: {question}', 'hint')
    if result is None:
        return jsonify({'error': 'ヒントを取得できませんでした。少し待ってからもう一度試してください。'}), 503
    return jsonify({'hint': result.strip()})


@app.route('/api/reveal', methods=['POST'])
def reveal():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    session_key = request.get_json().get('session_key') or ''
    if not session_key.startswith(f"{session['user_id']}_") or session_key not in chat_sessions:
        return jsonify({'error': 'セッションが見つかりません。リロードしてください。'}), 400

    subject  = chat_sessions[session_key].get('subject', '')
    question = get_last_feyn_message(session_key)
    if not question:
        return jsonify({'error': '答えを表示できる質問がまだありません。'}), 400

    instruction = (
        f'あなたは{subject}の先生です。キャラクターの演技はせず、素直な解説者として答えてください。'
        '生徒からの質問に対して、高校生にも分かるように3〜4文程度で簡潔に説明してください。'
        '数式を書くときは必ずLaTeX記法にして、インラインは $ で、独立した式は $$ で囲んでください。'
    )
    result = generate_once(instruction, f'次の質問に答えてください: {question}', 'reveal')
    if result is None:
        return jsonify({'error': '答えを取得できませんでした。少し待ってからもう一度試してください。'}), 503
    return jsonify({'answer': result.strip()})


# ========================================
# サーバー起動
# ========================================
if __name__ == '__main__':
    print("Feyn server running at http://localhost:5000")
    print("同じWi-Fiのスマホからは http://<このPCのIPアドレス>:5000 でアクセスできます")
    app.run(debug=True, port=5000, host='0.0.0.0')
