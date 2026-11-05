# -*- coding: utf-8 -*-
"""公開前チェック（scripts/check_before_deploy.py）の見つけ方のテスト。
本物のページが「合格」かどうかは調べない（公開前の今は【要確認】が残っているのが正しいため）"""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location('check_before_deploy', os.path.join(ROOT, 'scripts', 'check_before_deploy.py'))
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def test_finds_fill_ins_dummy_addresses_and_drafts():
    text = '<p>【事業者名】が運営</p>\n<a href="mailto:info@example.com">連絡</a>\n<p>これは下書きです</p>\n<p>雛形</p>'
    errors, warnings = checker.check('x', text)
    labels = ' '.join(errors)
    assert '[未記入] 【事業者名】' in labels
    assert '[ダミーのアドレス] info@example.com' in labels
    assert labels.count('[下書きの表記]') == 2
    assert warnings == []


def test_ignores_html_comments_and_hidden_voices_but_warns_about_them():
    text = ('<!-- 【要確認】ここはメモ -->\n'
            '<section class="voices" data-placeholder>\n<p>【要確認：実際の利用者からもらう】</p>\n</section>\n'
            '<p class="teacher-note contact-pending">準備中</p>')
    errors, warnings = checker.check('LP', text)
    assert errors == []
    assert any('data-placeholder' in w for w in warnings)
    assert any('CONTACT_EMAIL' in w for w in warnings)


def test_clean_page_passes():
    errors, warnings = checker.check('x', '<p>Feynは学習アプリです。お問い合わせ: hello@feyn-school.jp</p>')
    assert errors == [] and warnings == []


def test_lp_with_real_contact_address_has_no_contact_warning(monkeypatch):
    monkeypatch.setenv('CONTACT_EMAIL', 'hello@feyn-school.jp')
    errors, warnings = checker.check('LP', checker.landing_render.render_landing())
    assert not any('CONTACT_EMAIL' in w for w in warnings)
    assert not any('feyn-school' in e for e in errors)
