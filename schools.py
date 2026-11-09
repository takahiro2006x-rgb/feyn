# -*- coding: utf-8 -*-
"""塾・学校（組織）と、参加のためのコード。運営アカウントの付与もここ。

- 組織ごとに「先生コード」（teacher_code）と「生徒の参加コード」（join_code）を持つ。
  先生コードで登録した人はその組織の先生に、参加コードを入れた生徒はその組織の生徒になる
- どこにも入っていない人は DEFAULT 組織（join_code = 'DEFAULT'）にいる。
  DEFAULT の生徒は、どの先生からも見えない（見られるのは運営アカウントだけ）
- コードは推測されにくいよう、紛らわしい文字（0/O・1/I/L）を除いた31種類の文字を12文字並べる（約8×10^17通り）。
  入力の間違いが続いたら ratelimit で止める（blueprints 側）
- 組織とコードの発行・運営アカウントの付与は scripts/orgs.py から行う（管理画面は作らない）

DBにしか触れない（AIや課金のモジュールは読み込まない）ので、スクリプトからも軽く使える。
"""
import re
import secrets

import db

DEFAULT_JOIN_CODE = 'DEFAULT'
CODE_ALPHABET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'
CODE_LENGTH = 12


def normalize_code(raw):
    """入力されたコードを、保存している形（英大文字と数字だけ）にそろえる。
    「abcd-efgh-jkmn」「ABCD EFGH JKMN」のように、区切りや小文字で入れても通るようにする"""
    return re.sub(r'[^0-9A-Za-z]', '', raw or '').upper()


def format_code(code):
    """画面やコマンドで見せるときは、4文字ずつハイフンで区切る（例: ABCD-EFGH-JKMN）"""
    if not code:
        return ''
    return '-'.join(code[i:i + 4] for i in range(0, len(code), 4))


def _random_code():
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _code_in_use(conn, code):
    return conn.execute(
        'SELECT 1 FROM schools WHERE join_code = ? OR teacher_code = ?', (code, code)
    ).fetchone() is not None


def _new_code(conn):
    """まだどの組織のコード（先生用・生徒用とも）にも使われていない新しいコード"""
    while True:
        code = _random_code()
        if not _code_in_use(conn, code):
            return code


def _row_to_dict(row):
    return {
        'id':           row['id'],
        'name':         row['name'],
        'join_code':    row['join_code'],
        'teacher_code': row['teacher_code'],
        'is_default':   row['join_code'] == DEFAULT_JOIN_CODE,
    }


def create_school(name, join_code=None, teacher_code=None):
    """組織を作り、先生コードと生徒の参加コードを発行する。コードを指定するのはテストのときだけ"""
    name = (name or '').strip()
    if not name:
        raise ValueError('組織の名前を入れてください')
    if len(name) > 50:
        raise ValueError('組織の名前は50文字以内にしてください')
    with db.get_db() as conn:
        join_code = normalize_code(join_code) if join_code else _new_code(conn)
        teacher_code = normalize_code(teacher_code) if teacher_code else _new_code(conn)
        if join_code == teacher_code or join_code == DEFAULT_JOIN_CODE or teacher_code == DEFAULT_JOIN_CODE:
            raise ValueError('先生コードと参加コードには、別々の値を使ってください')
        for code in (join_code, teacher_code):
            if _code_in_use(conn, code):
                raise ValueError(f'そのコード（{code}）はすでに使われています')
        conn.execute(
            'INSERT INTO schools (name, join_code, teacher_code) VALUES (?, ?, ?)',
            (name, join_code, teacher_code)
        )
        conn.commit()
        row = conn.execute(
            'SELECT id, name, join_code, teacher_code FROM schools WHERE join_code = ?', (join_code,)
        ).fetchone()
    return _row_to_dict(row)


def rotate_codes(school_id, which='both'):
    """コードを新しくする（古いコードは使えなくなる。すでに入っている先生・生徒はそのまま）。
    which: 'join'（生徒の参加コード）・'teacher'（先生コード）・'both'"""
    if which not in ('join', 'teacher', 'both'):
        raise ValueError("which は 'join'・'teacher'・'both' のどれか")
    with db.get_db() as conn:
        row = conn.execute('SELECT id, join_code FROM schools WHERE id = ?', (school_id,)).fetchone()
        if row is None:
            raise ValueError(f'組織が見つかりません（id={school_id}）')
        if row['join_code'] == DEFAULT_JOIN_CODE:
            raise ValueError('DEFAULT（どこにも入っていない人の置き場）にはコードを発行できません')
        if which in ('join', 'both'):
            conn.execute('UPDATE schools SET join_code = ? WHERE id = ?', (_new_code(conn), school_id))
        if which in ('teacher', 'both'):
            conn.execute('UPDATE schools SET teacher_code = ? WHERE id = ?', (_new_code(conn), school_id))
        conn.commit()
    return get_school(school_id)


def get_school(school_id):
    if school_id is None:
        return None
    with db.get_db() as conn:
        row = conn.execute(
            'SELECT id, name, join_code, teacher_code FROM schools WHERE id = ?', (school_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def find_by_join_code(raw):
    """生徒の参加コードから組織を探す（DEFAULT は対象外）。見つからなければNone"""
    code = normalize_code(raw)
    if not code or code == DEFAULT_JOIN_CODE:
        return None
    with db.get_db() as conn:
        row = conn.execute(
            'SELECT id, name, join_code, teacher_code FROM schools WHERE join_code = ? AND join_code != ?',
            (code, DEFAULT_JOIN_CODE)
        ).fetchone()
    return _row_to_dict(row) if row else None


def find_by_teacher_code(raw):
    """先生コードから組織を探す。見つからなければNone"""
    code = normalize_code(raw)
    if not code:
        return None
    with db.get_db() as conn:
        row = conn.execute(
            'SELECT id, name, join_code, teacher_code FROM schools WHERE teacher_code = ? AND join_code != ?',
            (code, DEFAULT_JOIN_CODE)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_schools():
    """全組織と、先生・生徒の人数（DEFAULT を含む。DEFAULT が先頭）"""
    with db.get_db() as conn:
        rows = conn.execute('SELECT id, name, join_code, teacher_code FROM schools ORDER BY id').fetchall()
        counts = conn.execute(
            'SELECT school_id, role, COUNT(*) AS cnt FROM users GROUP BY school_id, role'
        ).fetchall()
    by_school = {}
    for c in counts:
        by_school.setdefault(c['school_id'], {})[c['role']] = c['cnt']
    result = []
    for r in rows:
        d = _row_to_dict(r)
        d['students'] = by_school.get(r['id'], {}).get('student', 0)
        d['teachers'] = by_school.get(r['id'], {}).get('teacher', 0)
        result.append(d)
    result.sort(key=lambda d: (not d['is_default'], d['id']))
    return result


# ---------- 運営アカウント ----------
# 先生（role = 'teacher'）とは別の印（users.is_operator）。普通の登録では付かず、scripts/orgs.py で付ける。
# role はそのまま残るので、生徒として学習しているアカウントに付けても、学習はそのまま続けられる

def set_operator(email, on=True):
    """メールアドレスのアカウントに運営の印を付ける（on=False で外す）。戻り値は対象の行（辞書）"""
    email = (email or '').strip().lower()
    with db.get_db() as conn:
        row = conn.execute('SELECT id, email, name, role FROM users WHERE email = ?', (email,)).fetchone()
        if row is None:
            raise ValueError(f'そのメールアドレスのアカウントがありません: {email}')
        conn.execute('UPDATE users SET is_operator = ? WHERE id = ?', (1 if on else 0, row['id']))
        conn.commit()
    return dict(row)


def list_operators():
    with db.get_db() as conn:
        rows = conn.execute(
            'SELECT id, email, name, role FROM users WHERE is_operator = 1 ORDER BY id'
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- 運営アカウントの閲覧の記録 ----------
# 「いつ・どの運営アカウントが・どの生徒の・何を見たか」。会話の本文や苦手の中身は入れない
ACCESS_ACTIONS = {
    'student_detail':   '生徒の詳細（苦手ノート・理解度・課題・会話ログの一覧）',
    'history':          '会話ログの一覧（続き）',
    'history_messages': '会話ログの中身',
    'students_csv':     '生徒一覧のCSV',
}


def log_operator_access(operator_id, action, student_id=None, school_id=None, target=None):
    """運営アカウントの閲覧を1件記録する。記録に失敗したら例外を外に出す
    （記録できないまま見せることがないよう、呼び出し側は見せる前に呼ぶ）"""
    if action not in ACCESS_ACTIONS:
        raise ValueError(f'unknown action: {action}')
    with db.get_db() as conn:
        conn.execute(
            'INSERT INTO operator_access_logs (operator_id, action, student_id, school_id, target) '
            'VALUES (?, ?, ?, ?, ?)',
            (operator_id, action, student_id, school_id, (target or '')[:100] or None)
        )
        conn.commit()


def list_operator_access(limit=200, offset=0):
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    with db.get_db() as conn:
        rows = conn.execute(
            'SELECT l.id, l.created_at, l.action, l.target, l.operator_id, l.student_id, l.school_id, '
            'o.name AS operator_name, o.email AS operator_email, s.name AS student_name, sc.name AS school_name '
            'FROM operator_access_logs l '
            'LEFT JOIN users o ON o.id = l.operator_id '
            'LEFT JOIN users s ON s.id = l.student_id '
            'LEFT JOIN schools sc ON sc.id = l.school_id '
            'ORDER BY l.id DESC LIMIT ? OFFSET ?',
            (limit + 1, offset)
        ).fetchall()
    return [dict(r) for r in rows[:limit]], len(rows) > limit
