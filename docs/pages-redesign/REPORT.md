# ほかの画面をそろえた レポート（chat-redesign ブランチ）

作業日: 2026-11-05　／　ブランチ: `chat-redesign`（push・デプロイはしていません）
テスト: 全369件パス（`py -m pytest -q`。`tests/test_pages_design.py` を17件追加）

チャット画面のデザイン（`docs/chat-redesign/REPORT.md`）を「これでいい」と確認してもらったので、同じルール（`docs/DESIGN.md`）で、ほかの画面もそろえた。

---

## 変えたこと

1. **共通のトークンを1か所に**: 色・角の丸み・影のトークン（ライト・夜の黒板）を `theme.css` にまとめ、全部の画面で読み込むようにした。チャット画面の `style.css` からも、ここに移した。古いページの変数名（`--bg`・`--card`・`--border`・`--text`・`--subtext`）もトークンにつないだ。
2. **マイページ**: 紫のグラデーションのプランの案内を、黒板の緑に（決定のボタン「受験生プランにする」だけ赤）。タブはノートの見出しの線、カードは罫線の枠の紙。苦手ノートの「理解の穴」の種類は赤ペンの書き込み、学習のきろくの会話はチャット画面と同じ形。スマホでヘッダーの「マイページ」が縦に並んでいたのを直した。
3. **ログイン・新規登録**: LPと同じうっすら方眼の背景、ノートの見出しの線のカード、赤の決定ボタン。パスワードの表示ボタンは線のアイコンにし、「表示する／隠す」のラベルが切り替わる。「新規登録はこちら」をボタンにして、キーボードでも押せるように。
4. **先生のダッシュボード・生徒の詳細**: 太い枠・立体の影をやめ、角と文字の段をそろえ、緑の決定ボタンを赤に。クリア数のバッジはFeynの緑の濃さ、つまずきマップは多いほど赤ペン。**スマホで生徒の名前が縦1文字ずつになっていたのと、ダッシュボードが画面の横にはみ出していた（375pxの画面で413px）のも直した**。
5. **特商法の表記・利用規約・プライバシーポリシー**: ノートの見出しの線、「下書き・雛形」の注意書きは蛍光ペンのメモ、未記入の所は赤字。規約・ポリシーは作成スクリプトの元を直して作り直した。
6. **全部の画面で**: フォントはLPと同じ Zen Kaku Gothic New、ロゴは角帽のマーク、見出し・ボタンの絵文字をやめた。全部の画面が theme.css を読み込み、ボタンや見出しに絵文字が無いことをテストで確かめている。
7. **チャット画面の丸い「納得！」の判子をやめた**（クリアの瞬間と結果の画面の上）。クリアは納得度バーの「納得！」と短い紙吹雪で伝える。
8. **公開前チェック**: ページのCSSの中の文字（コメントなど）を数えないようにした（画面に出ないため）。

### 見た目以外で直したこと
- マイページのプランの案内にあった「ヒント・答えを見るが無制限（有料プランだけ）」を消した。どちらのプランでも回数の制限はないため（LPの料金と同じ書き方に）。

---

## スクリーンショット

撮影: `py scripts/lp_shots.py pages docs/pages-redesign/after`（改善前と同じ条件。デモの生徒B・デモ先生、AIは台本）

並べた画像は `docs/pages-redesign/compare-<画面>-<sp|pc>-<light|dark>.png`。画面は次の8つ。

| 画面 | スマホ・ライト | PC・ライト | スマホ・ダーク |
|---|---|---|---|
| マイページ（偏差値） | [compare](compare-mypage-hensachi-sp-light.png) | [compare](compare-mypage-hensachi-pc-light.png) | [compare](compare-mypage-hensachi-sp-dark.png) |
| マイページ（苦手ノート） | [compare](compare-mypage-gaps-sp-light.png) | [compare](compare-mypage-gaps-pc-light.png) | [compare](compare-mypage-gaps-sp-dark.png) |
| マイページ（学習のきろく） | [compare](compare-mypage-history-sp-light.png) | [compare](compare-mypage-history-pc-light.png) | [compare](compare-mypage-history-sp-dark.png) |
| ログイン | [compare](compare-login-sp-light.png) | [compare](compare-login-pc-light.png) | [compare](compare-login-sp-dark.png) |
| 先生のダッシュボード | [compare](compare-dashboard-sp-light.png) | [compare](compare-dashboard-pc-light.png) | [compare](compare-dashboard-sp-dark.png) |
| 生徒の詳細 | [compare](compare-student-sp-light.png) | [compare](compare-student-pc-light.png) | [compare](compare-student-sp-dark.png) |
| 特商法の表記 | [compare](compare-tokushoho-sp-light.png) | [compare](compare-tokushoho-pc-light.png) | [compare](compare-tokushoho-sp-dark.png) |
| プライバシーポリシー | [compare](compare-privacy-sp-light.png) | [compare](compare-privacy-pc-light.png) | [compare](compare-privacy-sp-dark.png) |

改善前・改善後の1枚ずつの画像は `before/`・`after/` にある（PC・ダークも）。

---

## 判断に迷って自分で決めたこと

1. **先生の画面は、自動の置き換え＋手直しにした**。CSSが長い（ダッシュボードは約500行）ので、クラス名や作りは変えずに、色・枠・影・角・文字の大きさをトークンと段に置き換え、画面を見て崩れた所（スマホのヘッダー・横のはみ出し）を手で直した。マイページとログインは、CSSを書き直した。
2. **マイページの「復習する」ボタンは赤にしなかった**。苦手のカードの数だけ並ぶので、全部を赤にすると「1画面に決定のボタンは1つ」のルールに合わない。墨の枠のボタンにした。赤の決定ボタンは、プランの「受験生プランにする」だけ。
3. **科目の絵文字の代わり**: マイページの学習のきろくでは、科目の頭文字（物・数・英…）を四角の印にした。ほかの所は科目名の文字だけ。
4. **先生の画面の「ダークモード」「アプリへ戻る」の矢印（←）は残した**。絵文字ではなく文字の記号で、行き先が分かりやすいため。
5. **PWAの設定（`manifest.json` の緑）は変えていない**。ホーム画面のアイコンの見た目に関わるので、別に決めたほうがよい。

---

## まだ残っていること

- `manifest.json` の `theme_color`（緑 `#58CC02`）と、アイコン（`icons/`）は以前のまま。
- 先生の画面のCSSは、置き換えで直したため、使っていない古い指定が少し残っている。次に先生の画面を直すときに整理する。
- 先生アカウントが全生徒を見られる問題（`docs/lp/LP_IMPROVE_REPORT.md`）は、デザインとは別にそのまま。
