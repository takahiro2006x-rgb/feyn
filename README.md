# Feyn（フェイン）

ファインマン・テクニック（人に教えることで理解を深める学習法）をAIで再現する受験対策アプリ。
生徒が「あなたに教わる生徒役のFeyn（少し生意気で憎めないキャラクター）」に教える形で対話し、Feynがソクラテス式問答で理解の穴を突いていく。

## セットアップ

```bash
git clone https://github.com/takahiro2006x-rgb/feyn.git
cd feyn
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux

pip install -r requirements-dev.txt
cp .env.example .env
```

`.env` を開いて、最低限 `GEMINI_API_KEY` と `FLASK_SECRET_KEY` を埋めてください。
各項目の説明・取得先は `.env.example` のコメントを参照してください。

主な設定項目（本番では Render の Environment に設定）:

| 項目 | 内容 |
|---|---|
| `GEMINI_API_KEY` | 必須。Gemini のAPIキー |
| `FLASK_SECRET_KEY` | 必須。ログイン情報の署名用（本番で未設定だと起動しない） |
| `TEACHER_CODE` | 先生アカウントの登録に使う合言葉（本番では必ず独自の値に） |
| `CONTACT_EMAIL` | LPの「導入について相談する」の送り先。未設定・ダミーのときはボタンを出さず「準備中」と表示 |
| `DATABASE_URL` | 本番のPostgres。未設定ならSQLite |
| `STRIPE_*` | 決済（Stripe）を使う場合 |

## 起動

```bash
python app.py
```

`http://localhost:5000` で起動します（デフォルトはSQLite、`feyn.db` が自動生成されます）。

## テスト

```bash
pytest
```

外部API（Gemini/Groq）は呼ばずにfakeクライアントでテストしているので、無料枠は消費しません。

## デプロイの前に

```bash
python scripts/check_before_deploy.py
```

LP・利用規約・プライバシーポリシー・特商法の表記に、まだ埋まっていない【 】・ダミーのアドレス（example.com など）・「下書き」の表記が残っていないかを調べます。
残っていれば一覧を出して終了コード1で終わるので、0になってからデプロイしてください。
相談の送り先（`CONTACT_EMAIL`）は、このコマンドを実行した環境の値を見ます。本番の値は Render の Environment に入っているかを別に確認してください。
埋める項目の説明は [`docs/lp/TODO_LEGAL.md`](docs/lp/TODO_LEGAL.md) にあります。

## 構成

| ファイル | 役割 |
|---|---|
| `app.py` | Flaskアプリ本体（ルーティング・API） |
| `db.py` | SQLite/Postgres抽象化層（`DATABASE_URL`があればPostgres） |
| `llm.py` | Gemini/Groqの抽象化層（フォールバックチェーン） |
| `tutoring.py` | Feynのソクラテス式問答プロンプト |
| `gap_analyzer.py` | 対話ログからの知識ギャップ自動分析 |
| `report.py` | 疑似偏差値の計算ロジック |
| `landing.html` / `landing_render.py` | LP（相談ボタンの欄だけ、`CONTACT_EMAIL` に合わせてサーバーで差し替える） |
| `scripts/` | 公開前チェック・LP用の画面の撮影・規約ページの生成 |
| `index.html` / `script.js` / `style.css` | 生徒側チャット画面 |
| `dashboard.html` | 講師ダッシュボード |
| `mypage.html` | 生徒マイページ（偏差値・苦手ノート・学習のきろく・プラン） |
| `student_detail.html` | 講師から見る生徒詳細ページ |
| `tests/` | pytestテストスイート |

## 開発の進め方

- 直接 `master` にはpushせず、作業ブランチを切って Pull Request を作成してください
- `.env` は絶対にコミットしない（`.gitignore` 済み）。実際のAPIキーは各自で取得するか、Slack等の安全な手段で共有する
- DBスキーマを変更するときは `app.py` の `init_db()` に `add_column_if_missing` でマイグレーションを追加する（SQLite/Postgres両対応の書き方については既存のコードを参考にすること）
