# -*- coding: utf-8 -*-
"""tutoring.py（ソクラテス式チュータリング基盤）のテスト"""
import tutoring


def test_build_instruction_includes_subject_and_difficulty():
    instruction = tutoring.build_instruction('物理', '大学受験', '先生太郎')
    assert '物理' in instruction
    assert '大学受験' in instruction
    assert '先生太郎' in instruction
    assert '【会話終了】' in instruction  # 終了合図のルールが必ず含まれること


def test_build_instruction_uses_unit_scope_when_given():
    instruction = tutoring.build_instruction('数学', '大学受験', '先生', unit='積分法')
    assert '「積分法」' in instruction


def test_build_instruction_mentions_exam_pattern_focus():
    # 「なぜ〜なのか」ではなく入試問題パターン・引っかかりやすいポイントを軸にする方針
    instruction = tutoring.build_instruction('英語', '大学受験', '先生')
    assert '入試' in instruction
    assert '引っかかりやすい' in instruction


def test_build_kickoff_includes_subject_or_unit():
    assert '物理' in tutoring.build_kickoff('物理')
    kickoff_with_unit = tutoring.build_kickoff('数学', unit='確率')
    assert '確率' in kickoff_with_unit
    assert '数学' not in kickoff_with_unit  # unit指定時は科目名でなく単元名で絞り込む


def test_build_review_instruction_includes_gap_details():
    gap = {
        'gap_type': 'misconception',
        'topic': '慣性の法則',
        'description': '静止物体には力が働かないと誤解している',
        'suggested_question': '静止している物体に働く力を全部挙げてみて',
    }
    instruction = tutoring.build_review_instruction('物理', '大学受験', '先生', gap)
    assert gap['topic'] in instruction
    assert gap['description'] in instruction
    assert gap['suggested_question'] in instruction
    assert tutoring.GAP_TYPE_LABELS['misconception'] in instruction


def test_build_review_kickoff_includes_topic():
    gap = {'topic': '有効数字'}
    assert '有効数字' in tutoring.build_review_kickoff(gap)


def test_gap_type_labels_cover_all_known_types():
    # gap_analyzer側のGAP_TYPESと1対1で対応している必要がある
    import gap_analyzer
    assert set(tutoring.GAP_TYPE_LABELS.keys()) == set(gap_analyzer.GAP_TYPES)
