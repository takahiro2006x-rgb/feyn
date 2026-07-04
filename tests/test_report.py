# -*- coding: utf-8 -*-
"""report.py（マイページの疑似偏差値ロジック）のテスト"""
import report


def test_compute_subject_score_no_data_returns_none():
    assert report.compute_subject_score([], []) is None


def test_compute_subject_score_blends_understanding_and_gap_resolution():
    topics = [{'understanding': 60}, {'understanding': 60}]
    gaps   = [{'status': 'resolved'}, {'status': 'resolved'}]
    score = report.compute_subject_score(topics, gaps)
    # base=60 * 0.7 + gap_bonus(100) * 0.3 = 42 + 30 = 72
    assert score == 72.0


def test_compute_subject_score_low_understanding_no_resolution():
    topics = [{'understanding': 30}]
    gaps   = [{'status': 'open'}, {'status': 'open'}]
    score = report.compute_subject_score(topics, gaps)
    # base=30 * 0.7 + gap_bonus(0) * 0.3 = 21
    assert score == 21.0


def test_compute_subject_score_no_gaps_yet_uses_base_as_neutral_bonus():
    topics = [{'understanding': 90}]
    score = report.compute_subject_score(topics, [])
    # gapが1件もなければ加点も減点もしない（base自体をbonusに使う）
    assert score == 90.0


def test_score_to_hensachi_boundaries():
    assert report.score_to_hensachi(0) == 35
    assert report.score_to_hensachi(50) == round(35 + 17.5)
    assert report.score_to_hensachi(100) == 70


def test_hensachi_to_university_picks_highest_matching_band():
    assert report.hensachi_to_university(80)['name'] == report.UNIVERSITY_BANDS[0][1]
    assert report.hensachi_to_university(70)['name'] == report.UNIVERSITY_BANDS[0][1]
    assert report.hensachi_to_university(69)['name'] == report.UNIVERSITY_BANDS[1][1]
    assert report.hensachi_to_university(0)['name'] == report.UNIVERSITY_BANDS[-1][1]


def test_hensachi_to_university_bands_are_sorted_descending():
    thresholds = [band[0] for band in report.UNIVERSITY_BANDS]
    assert thresholds == sorted(thresholds, reverse=True)
