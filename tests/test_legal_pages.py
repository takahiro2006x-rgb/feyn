# -*- coding: utf-8 -*-
"""プライバシーポリシー・利用規約（下書き）のページのテスト"""
import pytest

LANDING = open('landing.html', encoding='utf-8').read()


@pytest.mark.parametrize('path', ['/privacy', '/terms'])
def test_legal_pages_are_served_and_marked_as_drafts(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert '下書き・公開前に要確認' in text
    assert '【' in text                       # まだ埋まっていない所が分かる
    assert 'name="robots" content="noindex"' in text


def test_privacy_policy_covers_the_required_topics(client):
    text = client.get('/privacy').get_data(as_text=True)
    for topic in ['未成年', 'Gemini', 'Groq', 'Stripe', 'アカウントの削除']:
        assert topic in text, topic


def test_footer_links_to_privacy_and_terms():
    footer = LANDING[LANDING.index('<footer'):]
    assert 'href="/privacy"' in footer and 'href="/terms"' in footer


def test_pages_are_up_to_date_with_the_drafts():
    # 下書き（.md）を直したら scripts/build_legal_pages.py を実行し直すこと
    import importlib.util
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location('build_legal_pages', os.path.join(root, 'scripts', 'build_legal_pages.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for src, out, title in mod.PAGES:
        built = open(os.path.join(root, out), encoding='utf-8').read()
        assert built == mod.build(src, title), f'{out} が {src} と食い違っています'
