import os
from datetime import timedelta

from flask import Flask
from flask_cors import CORS

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
CORS(app, supports_credentials=True)

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
