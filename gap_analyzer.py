# =====================================================
# ナレッジギャップ分析エンジン
#
# セッションの対話履歴を入力に、学生（=Feynに教える側の「先生」）の
# 知識ギャップを構造化して抽出する。
#
# 仕組み:
#   1. ヒューリスティック: 曖昧語の使用などのシグナルを機械的に検出し、
#      分析プロンプトのヒントとして渡す
#   2. LLM分析: Geminiの構造化出力（JSONスキーマ強制）で、
#      ギャップを3類型 + 理解度スコアとして抽出する
# =====================================================
import difflib
import json

from google.genai import types

# ギャップの3類型
GAP_TYPES = ['misconception', 'vague', 'missing_prerequisite']

# 構造化出力のスキーマ（これに合致するJSON以外は返せない）
ANALYSIS_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'topic': {
            'type': 'STRING',
            'description': 'このセッションで扱った中心テーマ（例: 慣性の法則、条件付き確率）',
        },
        'understanding_score': {
            'type': 'INTEGER',
            'description': '学生の理解度 0-100。根拠なく高くしない',
        },
        'summary': {
            'type': 'STRING',
            'description': '学生の理解状態の要約（1〜2文、日本語）',
        },
        'gaps': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {
                    'gap_type': {'type': 'STRING', 'enum': GAP_TYPES},
                    'description': {
                        'type': 'STRING',
                        'description': '何をどう誤解/曖昧に理解しているか（1文、日本語）',
                    },
                    'evidence': {
                        'type': 'STRING',
                        'description': '根拠となる学生の発言の引用（原文のまま短く）',
                    },
                    'suggested_question': {
                        'type': 'STRING',
                        'description': 'このギャップを埋めるために次回Feynが投げるべき問い（日本語）',
                    },
                },
                'required': ['gap_type', 'description', 'evidence', 'suggested_question'],
            },
        },
    },
    'required': ['topic', 'understanding_score', 'summary', 'gaps'],
}

# 曖昧な理解のシグナルになりやすい表現
HEDGING_WORDS = ['なんとなく', 'たぶん', 'よくわからない', 'わかんない', '気がする',
                 'そういうもの', '〜的な', 'っぽい', 'だと思う', 'かもしれない']


def detect_signals(transcript_rows):
    """対話履歴からルールベースで曖昧さのシグナルを検出する

    transcript_rows: [{'role': 'user'|'feyn', 'message': str}, ...]
    """
    signals = []
    user_msgs = [r['message'] for r in transcript_rows if r['role'] == 'user']
    for msg in user_msgs:
        for word in HEDGING_WORDS:
            if word in msg:
                signals.append(f'学生の発言「{msg[:40]}…」に曖昧語「{word}」')
    if user_msgs and sum(len(m) for m in user_msgs) / len(user_msgs) < 30:
        signals.append('学生の説明が全体的に短く、言語化を避けている可能性')
    return signals


def format_transcript(transcript_rows):
    """LLMに渡す対話履歴テキストを組み立てる"""
    lines = []
    for r in transcript_rows:
        speaker = '学生' if r['role'] == 'user' else 'Feyn'
        lines.append(f"{speaker}: {r['message']}")
    return '\n'.join(lines)


def normalize_topic(topic, existing_topics):
    """既存テーマと十分似ていれば既存の文字列に寄せる（表記ゆれで進捗の行が分裂するのを防ぐ）"""
    if not topic or not existing_topics:
        return topic
    match = difflib.get_close_matches(topic, existing_topics, n=1, cutoff=0.65)
    return match[0] if match else topic


def build_analysis_prompt(subject, difficulty, transcript_rows, existing_topics=None):
    signals = detect_signals(transcript_rows)
    signals_text = ('\n'.join(f'- {s}' for s in signals)) if signals else '- （機械検出では特になし）'
    existing_text = ''
    if existing_topics:
        topics_list = '\n'.join(f'- {t}' for t in existing_topics)
        existing_text = f"""
【既存のテーマ名】
この学生には既に以下のテーマの記録がある。今回の対話が実質的に同じテーマを扱っているなら、
新しい言い回しを作らず、該当するテーマ名を一字一句そのまま topic に使うこと:
{topics_list}
"""
    return f"""あなたは教育診断の専門家です。
以下は、学生がAIキャラ「Feyn」に{subject}（{difficulty}レベル）を「教える」ことで学ぶアプリの対話ログです。
学生は「先生役」として説明しています。この学生の知識ギャップを分析してください。

【機械検出済みのシグナル（参考情報。鵜呑みにしない）】
{signals_text}
{existing_text}

【分析の基準】
- gap_type は次の3類型: misconception（明確に誤解している）/ vague（言葉は知っているが曖昧）/ missing_prerequisite（説明に必要な前提知識が抜けている）
- 対話ログに根拠のあるギャップだけを挙げる。推測で水増ししない（0件でもよい、最大3件）
- evidence には学生の発言をそのまま短く引用する
- suggested_question は「答えを教えずに考えさせる問い」にする
- understanding_score: Feynの突っ込みにどこまで自力で答えられたかを重視する

【対話ログ】
{format_transcript(transcript_rows)}
"""


def analyze_session(client, models, subject, difficulty, transcript_rows, existing_topics=None, on_attempt=None):
    """対話履歴を分析して知識ギャップを構造化して返す

    client: genai.Client / models: フォールバック順のモデル名リスト
    existing_topics: 既存テーマ名のリスト（topicの表記ゆれを寄せるために使う）
    on_attempt: 呼び出しごとに on_attempt(model, success) を呼ぶコールバック（使用量記録用、任意）
    戻り値: {'topic', 'understanding_score', 'summary', 'gaps': [...]} または None（全モデル枯渇・失敗時）
    """
    user_turns = [r for r in transcript_rows if r['role'] == 'user']
    if not user_turns:
        return None  # 学生が一度も説明していないなら分析できない

    prompt = build_analysis_prompt(subject, difficulty, transcript_rows, existing_topics)

    for model in models:
        config_kwargs = {
            'response_mime_type': 'application/json',
            'response_schema': ANALYSIS_SCHEMA,
        }
        if model.startswith('gemini-2.5'):
            config_kwargs['thinking_config'] = types.ThinkingConfig(thinking_budget=0)
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            result = json.loads(response.text)
            if on_attempt:
                on_attempt(model, True)
        except Exception as e:
            if on_attempt:
                on_attempt(model, False)
            err = str(e)
            # 枠切れ(429)・過負荷(503)はモデル単位の問題なので次のモデルで再挑戦する
            if '429' in err or '503' in err or 'UNAVAILABLE' in err:
                print(f'[gap_analyzer] {model} busy ({err[:60]}), trying next model')
                continue
            print(f'[gap_analyzer] {model} failed: {err[:200]}')
            return None

        # スキーマ強制していても念のため形を検証する
        if not isinstance(result.get('gaps'), list):
            return None
        result['gaps'] = [
            g for g in result['gaps']
            if g.get('gap_type') in GAP_TYPES and g.get('description') and g.get('suggested_question')
        ][:3]
        result['understanding_score'] = max(0, min(100, int(result.get('understanding_score', 0))))
        # プロンプト指示で寄せきれなかった表記ゆれをコード側でも吸収する
        result['topic'] = normalize_topic(result.get('topic', ''), existing_topics or [])
        return result

    return None
