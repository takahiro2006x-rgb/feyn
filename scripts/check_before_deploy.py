# -*- coding: utf-8 -*-
"""公開前チェック。デプロイの前に実行する。

  py scripts/check_before_deploy.py

LP・利用規約・プライバシーポリシー・特商法の表記を調べて、公開してはいけない状態のものを一覧にする。
1つでも「要対応」があれば終了コード1で終わる（「注意」だけなら0）。

要対応:
- 【 】で囲んだ、まだ埋まっていない所（【要確認】【事業者名】など）
- example.com などのダミーのメールアドレス・URL
- 「下書き」「雛形」の表記
注意（失敗にはしない）:
- 相談の送り先（環境変数 CONTACT_EMAIL）が未設定で、LPの相談ボタンが「準備中」になっている
- 「使った人の声」が、まだ data-placeholder 付き（＝表示されない）のまま

- LPは、実際に配信するときと同じ処理（landing_render.render_landing）で作ってから調べる。
  そのため CONTACT_EMAIL は、このスクリプトを実行した環境（シェル・.env）の値を見る。
  本番の値は Render の Environment に設定されているかを別に確認すること。
- DBにもAIにも触れない（アプリ本体は読み込まない）。
- pytest には入れていない（公開前の今は【要確認】が残っているのが正しい状態のため）。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, '.env'))
except ImportError:
    pass

import landing_render  # noqa: E402

FILL_IN = re.compile(r'【[^】]{0,60}】')
DUMMY_ADDRESS = re.compile(
    r'[\w.+-]*@?[\w-]*\.?(?:example\.(?:com|net|org)|[\w-]+\.(?:test|invalid))\b', re.IGNORECASE)
DRAFT = re.compile(r'下書き|雛形')
COMMENT = re.compile(r'<!--.*?-->', re.DOTALL)
PLACEHOLDER_SECTION = re.compile(r'<(section|div|aside)\b[^>]*\bdata-placeholder\b[^>]*>.*?</\1>', re.DOTALL)


def _blank(match):
    """見えない部分を消すが、行番号がずれないよう改行は残す"""
    return '\n' * match.group(0).count('\n')


def pages():
    yield 'LP（/landing）', landing_render.render_landing()
    for name, path in [('利用規約（/terms）', 'terms.html'),
                       ('プライバシーポリシー（/privacy）', 'privacy.html'),
                       ('特定商取引法の表記（/tokushoho）', 'tokushoho.html')]:
        with open(os.path.join(ROOT, path), encoding='utf-8') as f:
            yield name, f.read()


def check(name, text):
    errors, warnings = [], []
    if 'contact-pending' in text and 'id="contactBtn"' not in text:
        warnings.append('相談の送り先（CONTACT_EMAIL）が未設定、またはダミーのアドレス。LPの相談ボタンは「準備中」と表示される')
    for m in PLACEHOLDER_SECTION.finditer(text):
        if '【' in m.group(0):
            warnings.append('「使った人の声」が data-placeholder 付きのまま（中身が【要確認】のダミーなので表示されない）')
    # 利用者に見えない所（HTMLのコメント・表示されない声のセクション）は数えない
    visible = COMMENT.sub(_blank, text)
    visible = PLACEHOLDER_SECTION.sub(_blank, visible)
    for no, line in enumerate(visible.splitlines(), 1):
        plain = re.sub(r'<[^>]+>', '', line).strip()
        # アドレスはリンク（href）の中にも隠れるので、タグを消す前の行も調べる
        for label, pattern, src in [('未記入', FILL_IN, plain), ('ダミーのアドレス', DUMMY_ADDRESS, line),
                                    ('下書きの表記', DRAFT, plain)]:
            for m in pattern.finditer(src):
                errors.append(f'{no}行目 [{label}] {m.group(0)}　…{src[max(0, m.start() - 20):m.end() + 20].strip()}…')
    return errors, warnings


def main():
    total_errors = total_warnings = 0
    print('公開前チェック')
    print('=' * 60)
    for name, text in pages():
        errors, warnings = check(name, text)
        total_errors += len(errors)
        total_warnings += len(warnings)
        mark = 'OK' if not errors and not warnings else ('要対応' if errors else '注意')
        print(f'\n■ {name}: {mark}（要対応 {len(errors)}件・注意 {len(warnings)}件）')
        for e in errors:
            print(f'  ✗ {e}')
        for w in warnings:
            print(f'  △ {w}')
    print('\n' + '=' * 60)
    if total_errors:
        print(f'要対応が {total_errors} 件あります。埋めてから公開してください（注意 {total_warnings} 件）。')
        return 1
    print(f'要対応はありません（注意 {total_warnings} 件）。')
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
