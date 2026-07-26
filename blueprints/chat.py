# -*- coding: utf-8 -*-
"""チュータリングの本体API（開始・対話・再開・完了・ヒント・答え）"""
import base64
import time
import uuid
from datetime import date, timedelta

from flask import Blueprint, jsonify, request, session

import core
import gap_analyzer
import tutoring

chat_bp = Blueprint('chat', __name__)


@chat_bp.route('/api/start', methods=['POST'])
def start():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    limit_error = core.check_and_consume_life(session['user_id'])
    if limit_error:
        return jsonify({'error': limit_error, 'upgrade_required': True}), 403

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
        with core.get_db() as conn:
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
        with core.get_db() as conn:
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
    models_to_try = core.GEMINI_MODELS if photo_bytes else core.ALL_MODELS

    response   = None
    used_model = None
    quota_only = True
    for model in models_to_try:
        try:
            chat = core.create_chat(model, instruction)
            if photo_bytes:
                response = chat.send_message(kickoff, image=(photo_bytes, photo_mime))
            else:
                response = chat.send_message(kickoff)
            used_model = model
            core.log_api_call(model, 'start', True)
            break
        except Exception as e:
            err = str(e)
            core.log_api_call(model, 'start', False)
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
    core.chat_sessions[session_key] = {
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
    with core.get_db() as conn:
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


@chat_bp.route('/api/chat', methods=['POST'])
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

    if session_key not in core.chat_sessions:
        restored = core.restore_chat_session(session_key, session['user_id'], session.get('user_name', '先生'))
        if restored is None:
            return jsonify({'error': 'セッションが見つかりません。リロードしてください。'}), 400
        core.chat_sessions[session_key] = restored

    sess = core.chat_sessions[session_key]

    today = str(date.today())
    subject    = sess.get('subject', '')
    difficulty = sess.get('difficulty', '')
    conv_id    = sess.get('conv_id')
    unit       = sess.get('unit')

    retries = 0
    while True:
        try:
            response = sess['chat'].send_message(message)
            core.log_api_call(sess['model'], 'chat', True)
            break
        except Exception as e:
            err = str(e)
            core.log_api_call(sess['model'], 'chat', False)
            overloaded = '503' in err or 'UNAVAILABLE' in err

            # 過負荷はまず同じモデルで少し待って再試行する
            if overloaded and retries < 2:
                retries += 1
                time.sleep(2)
                continue

            # 枠切れ(429)・回復しない過負荷は、会話履歴を引き継いで次のモデルへ切り替える
            if '429' in err or overloaded:
                idx = core.ALL_MODELS.index(sess['model']) if sess.get('model') in core.ALL_MODELS else len(core.ALL_MODELS) - 1
                if idx + 1 < len(core.ALL_MODELS):
                    next_model = core.ALL_MODELS[idx + 1]
                    sess['chat']  = core.create_chat(next_model, sess['instruction'], history=sess['chat'].get_history())
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

    with core.get_db() as conn:
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


@chat_bp.route('/api/resume', methods=['POST'])
def resume():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    data         = request.get_json()
    subject      = data.get('subject', '')
    target_date  = data.get('date', '')
    conv_id      = data.get('session_id') or None
    teacher_name = session.get('user_name', '先生')

    if subject not in core.SUBJECTS or not target_date:
        return jsonify({'error': '再開する学習が見つかりません'}), 400

    with core.get_db() as conn:
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
    restored = core.restore_chat_session(session_key, session['user_id'], teacher_name,
                                          target_date=target_date, conv_id=conv_id)
    if restored is None:
        return jsonify({'error': '再開する学習が見つかりません'}), 404

    core.chat_sessions[session_key] = restored
    resolved_conv_id = restored.get('conv_id')

    with core.get_db() as conn:
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


@chat_bp.route('/api/complete', methods=['POST'])
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
    if session_key.startswith(f"{session['user_id']}_") and session_key in core.chat_sessions:
        conv_id = core.chat_sessions[session_key].get('conv_id')

    today = str(date.today())
    with core.get_db() as conn:
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
                interval = core.REVIEW_INTERVALS[min(count - 1, len(core.REVIEW_INTERVALS) - 1)]
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

    core.chat_sessions.pop(session_key, None)

    # 対話履歴からナレッジギャップを自動分析（失敗しても完了記録は成立させる）
    analysis = None
    try:
        analysis = gap_analyzer.analyze_session(
            core.client, core.ALL_MODELS, subject, difficulty,
            [{'role': r['role'], 'message': r['message']} for r in rows],
            existing_topics=[r['topic'] for r in topic_rows],
            on_attempt=lambda model, ok: core.log_api_call(model, 'analysis', ok),
            groq_client=core.groq_client, groq_models=core.llm.GROQ_MODELS,
        )
        if analysis:
            core.save_analysis(session['user_id'], subject, analysis, today)
            # 会話ログの見出しに具体的なテーマを出せるよう、このセッションの分析結果を紐付けておく
            if conv_id and analysis.get('topic'):
                with core.get_db() as conn:
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


@chat_bp.route('/api/hint', methods=['POST'])
def hint():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    session_key = request.get_json().get('session_key') or ''
    if not session_key.startswith(f"{session['user_id']}_") or session_key not in core.chat_sessions:
        return jsonify({'error': 'セッションが見つかりません。リロードしてください。'}), 400

    question = core.get_last_feyn_message(session_key)
    if not question:
        return jsonify({'error': 'ヒントを出せる質問がまだありません。'}), 400

    instruction = (
        'あなたは学習アプリの「ヒント係」です。以下は、キャラクター「Feyn」が生徒に投げた質問です。'
        '生徒はこの質問にどう答えればいいか困っています。'
        '答えそのものや説明は絶対に言わないでください。考えるきっかけになるキーワードを1〜2つだけ、'
        '短い言葉で提示してください（例:「浮力」「エネルギー保存」）。文章での説明はしないこと。'
    )
    result = core.generate_once(instruction, f'Feynの質問: {question}', 'hint')
    if result is None:
        return jsonify({'error': 'ヒントを取得できませんでした。少し待ってからもう一度試してください。'}), 503
    return jsonify({'hint': result.strip()})


@chat_bp.route('/api/reveal', methods=['POST'])
def reveal():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    session_key = request.get_json().get('session_key') or ''
    if not session_key.startswith(f"{session['user_id']}_") or session_key not in core.chat_sessions:
        return jsonify({'error': 'セッションが見つかりません。リロードしてください。'}), 400

    subject  = core.chat_sessions[session_key].get('subject', '')
    question = core.get_last_feyn_message(session_key)
    if not question:
        return jsonify({'error': '答えを表示できる質問がまだありません。'}), 400

    instruction = (
        f'あなたは{subject}の先生です。キャラクターの演技はせず、素直な解説者として答えてください。'
        '生徒からの質問に対して、高校生にも分かるように3〜4文程度で簡潔に説明してください。'
        '数式を書くときは必ずLaTeX記法にして、インラインは $ で、独立した式は $$ で囲んでください。'
    )
    result = core.generate_once(instruction, f'次の質問に答えてください: {question}', 'reveal')
    if result is None:
        return jsonify({'error': '答えを取得できませんでした。少し待ってからもう一度試してください。'}), 503
    return jsonify({'answer': result.strip()})
