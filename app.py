import os
from datetime import timedelta
from urllib.parse import urlparse

from flask import Flask, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

import core
from blueprints.auth import auth_bp
from blueprints.billing import billing_bp
from blueprints.chat import chat_bp
from blueprints.dashboard import dashboard_bp
from blueprints.pages import pages_bp
from blueprints.progress import progress_bp

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'feyn-dev-secret-2024')
# ブラウザを閉じるたびに再ログインを求めないよう、ログインセッションを30日間保持する
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# --- CSRF・クッキーまわりの対策 ---
# 他のサイトから送られたリクエストにはログイン用クッキーを付けない（CSRFの主な防御）。
# Stripeから戻ってくる画面遷移（外部サイト→/mypageへのGET）はLaxでもクッキーが付くので影響しない。
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
# RenderはRENDER=trueを設定する。本番（HTTPS）でだけSecureにして、ローカルのhttp開発は妨げない
IS_PRODUCTION = bool(os.environ.get('RENDER'))
app.config['SESSION_COOKIE_SECURE'] = IS_PRODUCTION

# Renderのプロキシ越しでも、接続元のIPアドレス（ログイン試行回数の制限に使う）を正しく取る
if IS_PRODUCTION:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

# フロントとAPIは同じサーバーから配信しているので、CORS（別サイトからの利用許可）は不要。
# 以前の CORS(supports_credentials=True) は、どのサイトからでもログイン付きでAPIを読めてしまう設定だったため外した。

# StripeのWebhookはブラウザではなくStripeのサーバーから届くので、送信元の確認から外す
CSRF_EXEMPT_PATHS = {'/api/billing/webhook'}
UNSAFE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}


@app.before_request
def reject_cross_site_requests():
    """データを変更するAPIは、同じサイトの画面から送られたものだけ受け付ける。
    ブラウザは別サイトからのPOSTにも必ずOriginヘッダーを付けるので、それが自分のホストと違えば拒否する。
    Originが無いリクエスト（curlやテスト、サーバー間通信）はブラウザ経由のCSRFではないので通す。"""
    if request.method not in UNSAFE_METHODS:
        return None
    if not request.path.startswith('/api/') or request.path in CSRF_EXEMPT_PATHS:
        return None

    origin = request.headers.get('Origin')
    if origin is None:
        return None
    if urlparse(origin).netloc != request.host:
        return jsonify({'error': '不正なリクエストです'}), 403
    return None


@app.route('/api/health')
def health():
    """死活確認用。データベースに軽い問い合わせを1回だけして、結果を返す（個人情報は返さない）。
    GitHub Actionsから定期的に呼び、Supabase無料プランの「長期間使われていない」自動停止を防ぐ。
    DBに繋がらないときは503を返すので、呼び出し側が失敗として気づける"""
    try:
        with core.get_db() as conn:
            conn.execute('SELECT 1').fetchone()
    except Exception:
        app.logger.exception('health check: database is unreachable')
        return jsonify({'ok': False}), 503
    return jsonify({'ok': True})


app.register_blueprint(pages_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(billing_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(progress_bp)


if __name__ == '__main__':
    print("Feyn server running at http://localhost:5000")
    print("同じWi-Fiのスマホからは http://<このPCのIPアドレス>:5000 でアクセスできます")
    app.run(debug=True, port=5000, host='0.0.0.0')
