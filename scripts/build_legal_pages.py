# -*- coding: utf-8 -*-
"""プライバシーポリシー・利用規約のページを、下書き（docs/drafts/*.md）から作る。

  py scripts/build_legal_pages.py

- 中身を直すときは docs/drafts/ の .md を書き換えて、このスクリプトを実行し直す
- 作られる privacy.html / terms.html は、特定商取引法の表記（tokushoho.html）と同じ作り
- 冒頭の「> 」で始まる注意書きは、黄色い「下書き・公開前に要確認」の枠になる
- 【 】で囲んだ所（まだ決まっていない所）は赤字になる
必要なもの: pip install markdown
"""
import html
import os
import re

import markdown

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PAGES = [
    ('docs/drafts/privacy_policy.md', 'privacy.html', 'プライバシーポリシー'),
    ('docs/drafts/terms_of_service.md', 'terms.html', '利用規約'),
]

TEMPLATE = '''<!DOCTYPE html>
<html lang="ja" translate="no">
<head>
  <meta charset="UTF-8">
  <meta name="google" content="notranslate">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="robots" content="noindex">
  <title>{title} - Feyn</title>
  <script>
    document.documentElement.setAttribute('data-theme',
      localStorage.getItem('feynTheme') ||
      (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  </script>
  <link rel="stylesheet" href="/theme.css">
  <link href="https://fonts.googleapis.com/css2?family=Zen+Kaku+Gothic+New:wght@500;700;900&display=swap" rel="stylesheet">
  <style>
    /* 文書のページ（特商法・利用規約・プライバシーポリシー）。ルールは docs/DESIGN.md、トークンは theme.css */
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: var(--sans); font-weight: 500; background: var(--paper-2); color: var(--ink); line-height: 1.8; }}
    a {{ color: inherit; }}
    .header {{ display: flex; align-items: center; gap: 12px; height: 56px; padding: 0 24px; background: var(--paper); border-bottom: 1px solid var(--rule); }}
    .header-logo {{ display: flex; align-items: center; gap: 8px; text-decoration: none; color: var(--ink); }}
    .logo-mark {{ width: 28px; height: 28px; border-radius: var(--r-md); background: var(--brand); display: grid; place-items: center; }}
    .logo-mark svg {{ width: 18px; height: 18px; }}
    .logo-text {{ font-size: 18px; font-weight: 900; }}
    .header-title {{ flex: 1; font-size: 14px; font-weight: 700; color: var(--ink-soft); }}
    .back-link {{ margin-left: auto; font-size: 14px; font-weight: 700; color: var(--ink-soft); text-decoration: none; }}
    .back-link:hover {{ color: var(--ink); text-decoration: underline; }}
    .container {{ max-width: 760px; margin: 0 auto; padding: 32px 24px 72px; }}
    .page-title {{ font-size: 24px; font-weight: 900; padding-bottom: 8px; margin-bottom: 8px; border-bottom: 2px solid var(--ink); }}
    .page-note {{ font-size: 14px; color: var(--ink-soft); margin-bottom: 24px; }}
    /* 公開前の注意書き: 蛍光ペンのメモ */
    .todo-banner {{ margin-bottom: 24px; padding: 12px 16px; background: var(--hint-bg); border-left: 3px solid var(--meter); border-radius: 0 var(--r-sm) var(--r-sm) 0; font-size: 14px; line-height: 1.8; }}
    .todo-banner p + p {{ margin-top: 4px; }}
    .fill-in {{ color: var(--red); font-weight: 700; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--paper); border: 1px solid var(--rule); border-radius: var(--r-sm); overflow: hidden; }}
    tr {{ border-bottom: 1px solid var(--rule); }}
    tr:last-child {{ border-bottom: none; }}
    th, td {{ text-align: left; vertical-align: top; padding: 12px 16px; font-size: 14px; line-height: 1.8; }}
    th {{ width: 34%; background: var(--paper-2); color: var(--ink-soft); font-weight: 700; white-space: nowrap; }}
    .doc {{ padding: 32px; background: var(--paper); border: 1px solid var(--rule); border-radius: var(--r-sm); font-size: 16px; line-height: 1.9; }}
    .doc h1 {{ font-size: 24px; font-weight: 900; padding-bottom: 8px; margin-bottom: 16px; border-bottom: 2px solid var(--ink); }}
    .doc h2 {{ font-size: 18px; font-weight: 900; margin: 32px 0 8px; padding-top: 16px; border-top: 1px solid var(--rule); }}
    .doc p {{ margin: 8px 0; }}
    .doc ul, .doc ol {{ margin: 8px 0 8px 24px; }}
    .doc li {{ margin: 4px 0; }}
    .doc hr {{ display: none; }}
    .doc table {{ margin: 12px 0; display: block; overflow-x: auto; border-radius: var(--r-sm); }}
    .doc th, .doc td {{ border: 1px solid var(--rule); padding: 8px 12px; font-size: 14px; }}
    .doc code {{ font-size: 14px; background: var(--paper-2); padding: 0 4px; border-radius: var(--r-sm); }}
    @media (max-width: 600px) {{
      .header {{ padding: 0 12px; }}
      .header-title, .logo-text {{ display: none; }}
      .container {{ padding: 24px 12px 48px; }}
      .doc {{ padding: 24px 16px; }}
      th, td {{ display: block; width: 100%; padding: 8px 16px; }}
      th {{ padding-bottom: 0; white-space: normal; }}
      td {{ padding-top: 4px; padding-bottom: 16px; }}
      .doc th, .doc td {{ display: table-cell; width: auto; }}
    }}
  </style>
</head>
<body>

  <header class="header">
    <a href="/landing" class="header-logo" aria-label="Feyn トップ"><span class="logo-mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 9.5 12 5l10 4.5L12 14z" fill="#FFFFFF"/><path d="M6.5 11.6V16c0 1.2 2.5 2.5 5.5 2.5s5.5-1.3 5.5-2.5v-4.4L12 14z" fill="#FFFFFF"/></svg></span><span class="logo-text">Feyn</span></a>
    <div class="header-title">{title}</div>
    <a href="/landing" class="back-link">← トップへ戻る</a>
  </header>

  <main class="container">
    <div class="todo-banner" role="note">
      <p><strong>下書き・公開前に要確認。</strong>このページはまだ確定していません。<span class="fill-in">赤字の【 】</span>の所を埋め、専門家の確認を受けてから公開してください。</p>
{notes}
    </div>
    <article class="doc">
{body}
    </article>
  </main>

</body>
</html>
'''


def build(src, title):
    text = open(os.path.join(ROOT, src), encoding='utf-8').read()
    # 冒頭の「> 」の注意書きは、本文から外して黄色い枠に入れる
    lines = text.splitlines()
    notes, rest, in_notes = [], [], True
    for line in lines:
        if in_notes and line.startswith('>'):
            notes.append(line.lstrip('> ').strip())
        elif in_notes and (line.startswith('#') or not line.strip()):
            rest.append(line)
        else:
            in_notes = False
            rest.append(line)
    body = markdown.markdown('\n'.join(rest), extensions=['tables', 'sane_lists'])
    body = body.replace('（下書き）', '')
    notes_html = '\n'.join(
        f'      <p>{markdown.markdown(n).removeprefix("<p>").removesuffix("</p>")}</p>' for n in notes if n
        and '下書きです' not in n)

    def mark(fragment):
        return re.sub(r'【([^】]*)】', r'<span class="fill-in">【\1】</span>', fragment)
    return TEMPLATE.format(title=html.escape(title), notes=mark(notes_html), body=mark(body))


if __name__ == '__main__':
    for src, out, title in PAGES:
        page = build(src, title)
        with open(os.path.join(ROOT, out), 'w', encoding='utf-8', newline='\n') as f:
            f.write(page)
        print('wrote', out)
