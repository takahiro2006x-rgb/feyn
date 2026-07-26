# -*- coding: utf-8 -*-
"""生徒の学習進捗系API（ストリーク・学習履歴・苦手ノート・偏差値・自分の課題一覧）"""
from datetime import date, timedelta

from flask import Blueprint, jsonify, request, session

import core
import report
import tutoring

progress_bp = Blueprint('progress', __name__)


@progress_bp.route('/api/assignments')
def assignments_api():
    """自分に出されている未完了の課題一覧（アプリのトップで受け取る用）"""
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    with core.get_db() as conn:
        rows = conn.execute(
            "SELECT id, subject, unit, created_at FROM assignments "
            "WHERE student_id = ? AND status = 'open' ORDER BY id",
            (session['user_id'],)
        ).fetchall()

    return jsonify({'assignments': [dict(r) for r in rows]})


@progress_bp.route('/api/streak')
def streak():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    with core.get_db() as conn:
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


@progress_bp.route('/api/history')
def history_api():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401
    return jsonify({'sessions': core.get_history_sessions(session['user_id'])})


@progress_bp.route('/api/gaps')
def gaps_api():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    today = str(date.today())
    with core.get_db() as conn:
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


@progress_bp.route('/api/mypage')
def mypage_api():
    """科目別・総合の疑似偏差値と対応する大学レベルの目安を返す"""
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    with core.get_db() as conn:
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
    for subject in core.SUBJECTS:
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


@progress_bp.route('/api/gaps/resolve', methods=['POST'])
def resolve_gap():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    data   = request.get_json()
    gap_id = data.get('gap_id')

    with core.get_db() as conn:
        updated = conn.execute(
            "UPDATE knowledge_gaps SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (gap_id, session['user_id'])
        ).rowcount
        conn.commit()

    if updated == 0:
        return jsonify({'error': '項目が見つかりません'}), 404
    return jsonify({'ok': True})
