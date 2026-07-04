# =====================================================
# マイページ（学力レポート）
#
# 学習データから「ゲーム的な疑似偏差値」を算出する。
# これは模試などの母集団と比較した統計的な偏差値ではなく、
# このアプリでの学習成果をモチベーション目的でスコア化したものであることに注意。
# =====================================================

# 疑似偏差値 → 大学レベルの対応表（あくまで目安。上から順に判定する）
UNIVERSITY_BANDS = [
    (70, '東京大学・京都大学クラス',        '最難関。この調子で突き詰めよう！'),
    (65, '一橋大学・東京工業大学・早稲田・慶應クラス', '難関大学が狙える実力'),
    (60, '大阪大学・東北大学・上智・東京理科大クラス',  '上位大学が狙える実力'),
    (55, 'MARCH・関関同立クラス',            '安定して得点できている'),
    (50, '日東駒専・産近甲龍クラス',          '平均的な理解度。基礎は固まってきた'),
    (45, '成成明学獨國武クラス',              'あと一歩で平均ライン'),
    (0,  'まずは基礎固めから',                'これからどんどん伸ばしていこう！'),
]


def compute_subject_score(topic_rows, gap_rows):
    """topic_progress・knowledge_gapsの行から、その科目の実力スコア(0-100)を算出する
    topic_rows: [{'understanding': int}, ...] / gap_rows: [{'status': str}, ...]
    データが1件もなければ None（まだ評価できない）
    """
    if not topic_rows:
        return None

    base = sum(r['understanding'] for r in topic_rows) / len(topic_rows)

    total_gaps = len(gap_rows)
    resolved   = sum(1 for g in gap_rows if g['status'] == 'resolved')
    # 苦手が1つも記録されていない（＝まだ弱点が見つかっていない）場合は加点も減点もしない
    gap_bonus  = (resolved / total_gaps * 100) if total_gaps > 0 else base

    return base * 0.7 + gap_bonus * 0.3


def score_to_hensachi(score):
    """0-100の実力スコアを、ゲーム的な疑似偏差値（35〜70程度のレンジ）に変換する"""
    return round(35 + (score / 100) * 35)


def hensachi_to_university(hensachi):
    for min_h, name, note in UNIVERSITY_BANDS:
        if hensachi >= min_h:
            return {'name': name, 'note': note}
    return {'name': UNIVERSITY_BANDS[-1][1], 'note': UNIVERSITY_BANDS[-1][2]}
