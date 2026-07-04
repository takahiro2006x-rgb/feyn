# -*- coding: utf-8 -*-
"""gap_analyzer.py（ナレッジギャップ分析エンジン）のテスト"""
import json

import gap_analyzer


# ===== detect_signals =====

def test_detect_signals_finds_hedging_words():
    transcript = [{'role': 'user', 'message': 'なんとなくそう思う'}]
    signals = gap_analyzer.detect_signals(transcript)
    assert any('曖昧語' in s for s in signals)


def test_detect_signals_empty_for_confident_answer():
    transcript = [{'role': 'user', 'message': 'F=maだから、質量が大きいほど加速度は小さくなる。'
                                              'これは運動方程式そのものの定義から直接導かれる。'}]
    signals = gap_analyzer.detect_signals(transcript)
    assert signals == []


def test_detect_signals_ignores_feyn_messages():
    transcript = [{'role': 'feyn', 'message': 'なんとなく納得いかないんだけど'}]
    # Feyn自身の発言中の曖昧語は学生の発言としてカウントしない
    signals = gap_analyzer.detect_signals(transcript)
    assert signals == []


# ===== normalize_topic =====

def test_normalize_topic_merges_similar_names():
    existing = ['運動方程式と質量ゼロの物体（光）']
    similar  = '運動方程式と質量ゼロの物体の運動'
    assert gap_analyzer.normalize_topic(similar, existing) == existing[0]


def test_normalize_topic_keeps_unrelated_topic():
    existing = ['運動方程式と質量ゼロの物体（光）']
    assert gap_analyzer.normalize_topic('モル計算', existing) == 'モル計算'


def test_normalize_topic_handles_empty_inputs():
    assert gap_analyzer.normalize_topic('慣性の法則', []) == '慣性の法則'
    assert gap_analyzer.normalize_topic('', ['何か']) == ''


# ===== build_analysis_prompt =====

def test_prompt_includes_existing_topics_when_given():
    transcript = [{'role': 'user', 'message': 'テスト発言'}]
    prompt = gap_analyzer.build_analysis_prompt('物理', '大学受験', transcript, existing_topics=['力学'])
    assert '既存のテーマ名' in prompt
    assert '力学' in prompt


def test_prompt_omits_existing_topics_section_when_none():
    transcript = [{'role': 'user', 'message': 'テスト発言'}]
    prompt = gap_analyzer.build_analysis_prompt('物理', '大学受験', transcript)
    assert '既存のテーマ名' not in prompt


# ===== analyze_session（フェイククライアントでフォールバック挙動を検証） =====

class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeGeminiModels:
    """1つ目のモデルは429、2つ目のモデルは正しいJSONで成功する状況を再現する"""
    def __init__(self):
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        if model == 'gemini-fail':
            raise Exception('429 RESOURCE_EXHAUSTED')
        return _FakeResponse(json.dumps({
            'topic': 'テストテーマ',
            'understanding_score': 40,
            'summary': '要約',
            'gaps': [{
                'gap_type': 'vague',
                'description': '曖昧な理解をしている',
                'evidence': '発言引用',
                'suggested_question': '次の問い',
            }],
        }))


class _FakeGeminiClient:
    def __init__(self):
        self.models = _FakeGeminiModels()


def test_analyze_session_returns_none_without_user_turns():
    transcript = [{'role': 'feyn', 'message': '質問だよ'}]
    result = gap_analyzer.analyze_session(_FakeGeminiClient(), ['gemini-fail'], '物理', '大学受験', transcript)
    assert result is None


def test_analyze_session_falls_back_to_next_model_on_429():
    client = _FakeGeminiClient()
    transcript = [
        {'role': 'feyn', 'message': '質問だよ'},
        {'role': 'user', 'message': '説明したよ'},
    ]
    attempts = []
    result = gap_analyzer.analyze_session(
        client, ['gemini-fail', 'gemini-ok'], '物理', '大学受験', transcript,
        on_attempt=lambda model, ok: attempts.append((model, ok)),
    )
    assert result is not None
    assert result['topic'] == 'テストテーマ'
    assert result['gaps'][0]['gap_type'] == 'vague'
    assert attempts == [('gemini-fail', False), ('gemini-ok', True)]
    assert client.models.calls == ['gemini-fail', 'gemini-ok']


def test_analyze_session_returns_none_when_all_models_exhausted():
    client = _FakeGeminiClient()
    transcript = [
        {'role': 'feyn', 'message': '質問だよ'},
        {'role': 'user', 'message': '説明したよ'},
    ]
    result = gap_analyzer.analyze_session(client, ['gemini-fail', 'gemini-fail'], '物理', '大学受験', transcript)
    assert result is None
