# Feyn（フェイン）

ファインマン・テクニック（人に教えることで理解を深める学習法）をAIで再現する受験対策アプリ。
生徒が「ライバル受験生キャラのFeyn」に教える形で対話し、Feynがソクラテス式問答で理解の穴を突いていく。

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

## 構成

| ファイル | 役割 |
|---|---|
| `app.py` | Flaskアプリ本体（ルーティング・API） |
| `db.py` | SQLite/Postgres抽象化層（`DATABASE_URL`があればPostgres） |
| `llm.py` | Gemini/Groqの抽象化層（フォールバックチェーン） |
| `tutoring.py` | Feynのソクラテス式問答プロンプト |
| `gap_analyzer.py` | 対話ログからの知識ギャップ自動分析 |
| `report.py` | 疑似偏差値の計算ロジック |
| `index.html` / `script.js` / `style.css` | 生徒側チャット画面 |
| `dashboard.html` | 講師ダッシュボード |
| `mypage.html` | 生徒マイページ（偏差値・苦手ノート・学習のきろく・プラン） |
| `student_detail.html` | 講師から見る生徒詳細ページ |
| `tests/` | pytestテストスイート |

## 開発の進め方

- 直接 `master` にはpushせず、作業ブランチを切って Pull Request を作成してください
- `.env` は絶対にコミットしない（`.gitignore` 済み）。実際のAPIキーは各自で取得するか、Slack等の安全な手段で共有する
- DBスキーマを変更するときは `app.py` の `init_db()` に `add_column_if_missing` でマイグレーションを追加する（SQLite/Postgres両対応の書き方については既存のコードを参考にすること）
