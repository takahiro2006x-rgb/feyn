from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
from google import genai
from google.genai import types
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
import time
import random
import string
from datetime import date, timedelta
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'feyn-dev-secret-2024')
CORS(app, supports_credentials=True)

DB_PATH = os.path.join(os.path.dirname(__file__), 'feyn.db')
TEACHER_CODE = 'FEYN_TEACHER_2024'
SUBJECTS = ['物理', '数学', '英語', '化学', '生物', '国語', '歴史']
SECURITY_QUESTIONS = [
    '小学校の名前は？',
    '初めて飼ったペットの名前は？',
    '好きな食べ物は？',
    '生まれた市区町村は？',
    '母親の旧姓は？',
]


# --- DB初期化 ---
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'student',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        try:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'student'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN security_question TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN security_answer_hash TEXT")
        except Exception:
            pass
        conn.execute('''
            CREATE TABLE IF NOT EXISTS conversation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        conn.execute('''
            CREATE TABLE IF NOT EXISTS session_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        conn.commit()

init_db()


# --- Gemini クライアント ---
client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))


# --- Feyn のシステムプロンプトを組み立てる ---
def build_instruction(subject, difficulty, teacher_name):
    return f"""
あなたは教育アプリ「Feyn（フェイン）」のキャラクターです。
少し生意気だけど憎めない、Duolingoのキャラのような「圧」があるライバル受験生です。
今回は、{teacher_name}という名前の先生を相手に、以下の設定で対話しています。

【科目】{subject}
【難易度】{difficulty}

【役割】
1. 最初の発言では、{subject}の範囲から「直感とズレていて納得いかないこと」を1つ選び、生意気に質問してください。
2. ユーザーの説明に対して、論理の穴や「なんでそうなるの？」という疑問を鋭く1〜2回突っ込んでください。
3. 本質的な説明をもらえたら賢く納得して感謝してください。

会話が完結したら、セリフの最後に必ず【会話終了】を付けてください。
"""

# --- チャットセッションを管理する辞書 ---
chat_sessions = {}


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
    if session.get('user_id'):
        return redirect('/')
    return send_from_directory('.', 'landing.html')

@app.route('/login')
def login_page():
    if session.get('user_id'):
        return redirect('/')
    return send_from_directory('.', 'login.html')

@app.route('/dashboard')
def dashboard_page():
    if not session.get('user_id'):
        return redirect('/login')
    if session.get('user_role') != 'teacher':
        return redirect('/')
    return send_from_directory('.', 'dashboard.html')

@app.route('/<path:path>')
def static_files(path):
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
    if len(password) < 4:
        return jsonify({'error': 'パスワードは4文字以上にしてください'}), 400
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
                'INSERT INTO users (email, name, password_hash, role, security_question, security_answer_hash) VALUES (?, ?, ?, ?, ?, ?)',
                (email, name, generate_password_hash(password), role,
                 security_question, generate_password_hash(security_answer))
            )
            conn.commit()
            user = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
            session['user_id']   = user['id']
            session['user_name'] = name
            session['user_role'] = role
        return jsonify({'ok': True, 'name': name, 'role': role})
    except sqlite3.IntegrityError:
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

    session['user_id']   = user['id']
    session['user_name'] = user['name']
    session['user_role'] = user['role']
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
    if not user:
        return jsonify({'error': 'メールアドレスが見つかりません'}), 404
    if not user['security_question']:
        return jsonify({'error': '秘密の質問が設定されていません。ログイン後に設定してください'}), 404
    return jsonify({'question': user['security_question']})


@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    data         = request.get_json()
    email        = data.get('email', '').strip().lower()
    answer       = data.get('answer', '').strip().lower()
    new_password = data.get('new_password', '')

    if len(new_password) < 4:
        return jsonify({'error': 'パスワードは4文字以上にしてください'}), 400

    with get_db() as conn:
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

    if not user or not user['security_answer_hash']:
        return jsonify({'error': 'メールアドレスが見つかりません'}), 404
    if not check_password_hash(user['security_answer_hash'], answer):
        return jsonify({'error': '答えが正しくありません'}), 401

    with get_db() as conn:
        conn.execute(
            'UPDATE users SET password_hash = ? WHERE email = ?',
            (generate_password_hash(new_password), email)
        )
        conn.commit()
    return jsonify({'ok': True})


@app.route('/api/dashboard/reset-password', methods=['POST'])
def teacher_reset_password():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401
    if session.get('user_role') != 'teacher':
        return jsonify({'error': '権限がありません'}), 403

    data       = request.get_json()
    student_id = data.get('student_id')

    temp_pass = 'Feyn' + ''.join(random.choices(string.digits, k=4))

    with get_db() as conn:
        updated = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ? AND role = 'student'",
            (generate_password_hash(temp_pass), student_id)
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
    with get_db() as conn:
        user  = conn.execute('SELECT security_question FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        total = conn.execute('SELECT COUNT(*) as cnt FROM session_logs WHERE user_id = ?', (session['user_id'],)).fetchone()['cnt']
    return jsonify({
        'id':                    session['user_id'],
        'name':                  session['user_name'],
        'role':                  session.get('user_role', 'student'),
        'has_security_question': bool(user and user['security_question']),
        'total_clears':          total,
    })


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
# セッション完了の記録
# ========================================
@app.route('/api/complete', methods=['POST'])
def complete():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    data       = request.get_json()
    subject    = data.get('subject', '')
    difficulty = data.get('difficulty', '')

    if not subject:
        return jsonify({'error': '科目が指定されていません'}), 400

    with get_db() as conn:
        conn.execute(
            'INSERT INTO session_logs (user_id, subject, difficulty) VALUES (?, ?, ?)',
            (session['user_id'], subject, difficulty)
        )
        conn.commit()

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

    with get_db() as conn:
        students_rows = conn.execute(
            "SELECT id, name, email, created_at FROM users WHERE role = 'student' ORDER BY created_at DESC"
        ).fetchall()

        result = []
        for s in students_rows:
            counts = {}
            for subj in SUBJECTS:
                row = conn.execute(
                    'SELECT COUNT(*) as cnt FROM session_logs WHERE user_id = ? AND subject = ?',
                    (s['id'], subj)
                ).fetchone()
                counts[subj] = row['cnt']

            last_row = conn.execute(
                'SELECT completed_at FROM session_logs WHERE user_id = ? ORDER BY completed_at DESC LIMIT 1',
                (s['id'],)
            ).fetchone()

            result.append({
                'id':         s['id'],
                'name':       s['name'],
                'email':      s['email'],
                'created_at': s['created_at'],
                'last_active': last_row['completed_at'] if last_row else None,
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
    })


# ========================================
# Feyn の最初のメッセージを取得
# ========================================
@app.route('/api/start', methods=['POST'])
def start():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    data         = request.get_json()
    subject      = data.get('subject', '物理')
    difficulty   = data.get('difficulty', '大学受験')
    teacher_name = data.get('teacher_name', session.get('user_name', '先生'))

    session_key = f"{session['user_id']}_{subject}"

    chat_sessions[session_key] = {
        'chat': client.chats.create(
            model="gemini-2.0-flash",
            config=types.GenerateContentConfig(
                system_instruction=build_instruction(subject, difficulty, teacher_name)
            )
        ),
        'turns':      0,
        'subject':    subject,
        'difficulty': difficulty,
    }

    try:
        response = chat_sessions[session_key]['chat'].send_message(
            f"{subject}の範囲から、あなたが今モヤモヤしているテーマを1つ選んで質問してください。"
        )
        today = str(date.today())
        with get_db() as conn:
            conn.execute(
                'INSERT INTO conversation_logs (user_id, subject, difficulty, role, message, session_date) VALUES (?, ?, ?, ?, ?, ?)',
                (session['user_id'], subject, difficulty, 'feyn', response.text, today)
            )
            conn.commit()
        return jsonify({'reply': response.text, 'session_key': session_key})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# ユーザーの解説を受け取ってFeynが返答
# ========================================
@app.route('/api/chat', methods=['POST'])
def chat():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    data        = request.get_json()
    message     = data.get('message', '')
    session_key = data.get('session_key', '')

    if not message:
        return jsonify({'error': 'メッセージが空です'}), 400

    if session_key not in chat_sessions:
        return jsonify({'error': 'セッションが見つかりません。リロードしてください。'}), 400

    sess = chat_sessions[session_key]

    today = str(date.today())
    subject    = sess.get('subject', '')
    difficulty = sess.get('difficulty', '')

    for attempt in range(3):
        try:
            response = sess['chat'].send_message(message)
            reply    = response.text

            is_done = '【会話終了】' in reply
            reply   = reply.replace('【会話終了】', '').strip()

            if not is_done:
                sess['turns'] = min(sess['turns'] + 1, 3)
            progress = 100 if is_done else sess['turns'] * 25

            with get_db() as conn:
                conn.execute(
                    'INSERT INTO conversation_logs (user_id, subject, difficulty, role, message, session_date) VALUES (?, ?, ?, ?, ?, ?)',
                    (session['user_id'], subject, difficulty, 'user', message, today)
                )
                conn.execute(
                    'INSERT INTO conversation_logs (user_id, subject, difficulty, role, message, session_date) VALUES (?, ?, ?, ?, ?, ?)',
                    (session['user_id'], subject, difficulty, 'feyn', reply, today)
                )
                conn.commit()

            return jsonify({'reply': reply, 'is_done': is_done, 'progress': progress})

        except Exception as e:
            err = str(e)
            if '503' in err and attempt < 2:
                time.sleep(2)
                continue
            if '429' in err:
                return jsonify({'error': '本日のAPI利用上限に達しました。'}), 429
            return jsonify({'error': 'AIサーバーが混み合っています。'}), 503


# ========================================
# サーバー起動
# ========================================
if __name__ == '__main__':
    print("Feyn server running at http://localhost:5000")
    app.run(debug=True, port=5000)
