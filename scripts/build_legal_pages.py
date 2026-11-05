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
  <link href="https://fonts.googleapis.com/css2?family=M+PLUS+Rounded+1c:wght@400;700;800&display=swap" rel="stylesheet">
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    :root {{
      --green:   #58CC02;
      --text:    #3C3C3C;
      --subtext: #6E6E6E;
      --bg:      #F0F4F8;
      --card:    #FFFFFF;
      --border:  #E5E5E5;
      --red:     #D93636;
    }}
    [data-theme="dark"] {{
      --text:    #E2E8F0;
      --subtext: #9AA4AF;
      --bg:      #0D1117;
      --card:    #161B22;
      --border:  #30363D;
      --red:     #FF7B7B;
    }}
    body {{ font-family: "M PLUS Rounded 1c", "Hiragino Sans", sans-serif; background: var(--bg); color: var(--text); }}
    a {{ color: inherit; }}
    :focus-visible {{ outline: 3px solid var(--green); outline-offset: 2px; border-radius: 4px; }}
    .header {{ background: var(--card); border-bottom: 2px solid var(--border); padding: 0 24px; height: 60px; display: flex; align-items: center; gap: 16px; }}
    .header-logo {{ font-size: 20px; font-weight: 800; color: var(--green); text-decoration: none; }}
    .header-title {{ font-size: 14px; font-weight: 800; color: var(--subtext); flex: 1; }}
    .back-link {{ margin-left: auto; font-size: 13px; font-weight: 700; color: var(--subtext); text-decoration: none; }}
    .back-link:hover {{ color: var(--text); }}
    .container {{ max-width: 760px; margin: 0 auto; padding: 32px 24px 64px; }}
    .todo-banner {{ background: #FFF3CD; border: 2px solid #FFE28A; color: #6B5300; border-radius: 14px; padding: 14px 18px; font-size: 13px; font-weight: 700; line-height: 1.8; margin-bottom: 28px; }}
    .todo-banner p + p {{ margin-top: 6px; }}
    [data-theme="dark"] .todo-banner {{ background: #3A2E00; border-color: #6B5300; color: #FFD966; }}
    .doc {{ background: var(--card); border: 2px solid var(--border); border-radius: 16px; padding: 28px 28px 32px; font-size: 14px; font-weight: 700; line-height: 1.9; }}
    .doc h1 {{ font-size: 22px; font-weight: 800; margin-bottom: 16px; }}
    .doc h2 {{ font-size: 17px; font-weight: 800; margin: 28px 0 8px; padding-top: 18px; border-top: 2px solid var(--border); }}
    .doc p {{ margin: 8px 0; }}
    .doc ul, .doc ol {{ margin: 8px 0 8px 1.6em; }}
    .doc li {{ margin: 4px 0; }}
    .doc hr {{ display: none; }}
    .doc table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 12.5px; display: block; overflow-x: auto; }}
    .doc th, .doc td {{ border: 1px solid var(--border); padding: 8px 10px; text-align: left; vertical-align: top; }}
    .doc th {{ background: var(--bg); color: var(--subtext); white-space: nowrap; }}
    .doc code {{ font-size: 12px; background: var(--bg); padding: 1px 5px; border-radius: 4px; }}
    .fill-in {{ color: var(--red); }}
    @media (max-width: 600px) {{
      .header {{ padding: 0 16px; }}
      .header-title {{ display: none; }}
      .container {{ padding: 20px 16px 48px; }}
      .doc {{ padding: 20px 16px 24px; }}
    }}
  </style>
</head>
<body>

  <header class="header">
    <a href="/landing" class="header-logo">🎓 Feyn</a>
    <div class="header-title">{title}</div>
    <a href="/landing" class="back-link">← トップへ戻る</a>
  </header>

  <main class="container">
    <div class="todo-banner" role="note">
      <p>⚠️ 下書き・公開前に要確認。このページはまだ確定していません。<span class="fill-in">赤字の【 】</span>の所を埋め、専門家の確認を受けてから公開してください。</p>
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
