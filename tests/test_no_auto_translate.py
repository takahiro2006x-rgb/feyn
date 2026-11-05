# -*- coding: utf-8 -*-
"""Chrome の自動翻訳を止めていることのテスト。
科目名のように漢字だけの言葉が多いと、日本語のページを中国語と見まちがえて「翻訳」し、
「物理」→「物理的」、「Feyn」→「フェイン」「ファイン」のように表示が変わってしまうため"""
import pytest

PAGES = ['index.html', 'login.html', 'mypage.html', 'dashboard.html', 'student_detail.html',
         'landing.html', 'tokushoho.html', 'privacy.html', 'terms.html']


@pytest.mark.parametrize('path', PAGES)
def test_page_tells_browsers_not_to_translate(path):
    head = open(path, encoding='utf-8').read().split('</head>')[0]
    assert '<html lang="ja" translate="no"' in head
    assert '<meta name="google" content="notranslate">' in head
