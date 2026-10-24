# gunicornの設定。gunicornは起動したフォルダの gunicorn.conf.py を自動で読むので、
# Renderの「Start Command」が `gunicorn app:app` のままでもこの設定が使われる。
import os

# 会話の途中経過（chat_sessions）やログイン失敗の回数はプロセスのメモリに持っているので、
# プロセスは1つのままにする（2つ以上にすると、リクエストごとに別のプロセスに行って会話が見つからなくなる）
workers = 1

# その代わりスレッドで同時に処理する。AIの返事を待っている間（数秒〜十数秒）も、
# 他の人のリクエストを止めずに受け付けられる
worker_class = 'gthread'
threads = 8

# AIのモデル切り替えや再試行が重なると30秒（gunicornの既定値）を超えることがある。
# 打ち切られるとプロセスが作り直され、メモリ上の会話がすべて消えてしまうので長めにする
timeout = 120

# Renderが渡すPORTで待ち受ける（Start Commandで --bind を指定していればそちらが優先される）
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
