# -*- coding: utf-8 -*-
"""会話終了後の結果画面に必要なデータを /api/complete が返すことのテスト。
（画面そのものは script.js の showResult。ここでは返す中身と、画面の部品があることを確認する）"""
import gap_analyzer
import tutoring
from conftest import FakeChatSession

ANALYSIS = {
    'topic': '慣性の法則',
    'understanding_score': 64,
    'summary': '止まっている物体の例は言えるが、動いている物体の説明が曖昧。',
    'gaps': [{'gap_type': 'vague', 'description': '等速直線運動の理由があやふや',
              'evidence': 'なんとなく止まると思う', 'suggested_question': '摩擦がなかったら？'}],
}


def _finish(client, monkeypatch):
    key = client.post('/api/start', json={'subject': '物理', 'difficulty': '大学受験'}).get_json()['session_key']
    monkeypatch.setattr(FakeChatSession, 'default_reply', '納得！【会話終了】')
    client.post('/api/chat', json={'message': '説明', 'session_key': key})
    return key


def test_complete_returns_analysis_and_gap_labels(student_client, monkeypatch):
    monkeypatch.setattr(gap_analyzer, 'analyze_session', lambda *a, **k: ANALYSIS)
    key = _finish(student_client, monkeypatch)
    data = student_client.post('/api/complete', json={'session_key': key}).get_json()
    assert data['ok'] is True
    assert data['analysis']['understanding_score'] == 64
    assert data['analysis']['gaps'][0]['gap_type'] == 'vague'
    assert data['labels'] == tutoring.GAP_TYPE_LABELS


def test_complete_without_analysis_still_returns_labels(student_client, monkeypatch):
    key = _finish(student_client, monkeypatch)   # conftest の設定で分析は None になる
    data = student_client.post('/api/complete', json={'session_key': key}).get_json()
    assert data['analysis'] is None
    assert set(data['labels']) == {'misconception', 'vague', 'missing_prerequisite'}


def test_chat_page_has_the_result_dialog():
    html = open('index.html', encoding='utf-8').read()
    js = open('script.js', encoding='utf-8').read()
    assert 'id="resultOverlay"' in html and 'id="resultCard"' in html
    assert 'function showResult' in js
    # 復習で聞く問いは、結果画面に先に出さない
    result_section = js[js.index('function resultHtml'):js.index('function showResult')]
    assert 'suggested_question' not in result_section
