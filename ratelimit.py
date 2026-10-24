# -*- coding: utf-8 -*-
"""失敗回数の制限（ブルートフォース対策）。

プロセス内のメモリで数えるだけの簡易版。gunicornを単一ワーカーで動かす前提で、
再起動すると記録は消える（chat_sessionsと同じ前提）。複数ワーカーにする場合はDBやRedisに移す必要がある。
"""
import os
import time
from collections import deque

from flask import request

# 記録が増えすぎてメモリを食わないよう、キー数がこれを超えたら古いものから掃除する
_MAX_KEYS = 10000


class FailureLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._failures = {}  # key -> 失敗した時刻のdeque

    def _prune(self, key, window):
        q = self._failures.get(key)
        if q is None:
            return None
        cutoff = self._clock() - window
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            del self._failures[key]
            return None
        return q

    def retry_after(self, key, limit, window):
        """limit回以上失敗していれば、あと何秒待てばよいかを返す（制限中でなければ0）"""
        q = self._prune(key, window)
        if q is None or len(q) < limit:
            return 0
        # 古い失敗が窓から外れて、limit未満に戻るまでの時間
        release_at = q[len(q) - limit] + window
        return max(1, int(release_at - self._clock()) + 1)

    def record_failure(self, key):
        if len(self._failures) >= _MAX_KEYS:
            self._failures.clear()
        self._failures.setdefault(key, deque()).append(self._clock())

    def reset(self, key):
        self._failures.pop(key, None)

    def reset_all(self):
        self._failures.clear()


# 画面の入力ミスで引っかからず、機械的な総当たりは止まる程度の値
WINDOW_SECONDS = 15 * 60
LIMIT_PER_ACCOUNT_AND_IP = 5    # 同じメール × 同じIP
LIMIT_PER_IP = 20               # 同じIPからの失敗の合計
LIMIT_PER_ACCOUNT = 30          # 同じメールへの失敗の合計（複数IPからの分散攻撃対策）

limiter = FailureLimiter()


def client_ip():
    """リクエスト元のIPアドレス。
    本番（Render）はCloudflareの内側にあり、そのまま取ると全員が同じ共有IPに見えてしまうことがある。
    Cloudflareが必ず上書きして付ける CF-Connecting-IP を優先する（外から偽装できない）。
    ローカル開発では、ヘッダーを偽装されても困らないが、念のため無視してremote_addrを使う。"""
    if os.environ.get('RENDER'):
        cf_ip = request.headers.get('CF-Connecting-IP')
        if cf_ip:
            return cf_ip.strip()
    return request.remote_addr


def _keys(scope, ip, account):
    keys = [(f'{scope}:ip:{ip}', LIMIT_PER_IP)]
    if account:
        keys.append((f'{scope}:acct-ip:{account}:{ip}', LIMIT_PER_ACCOUNT_AND_IP))
        keys.append((f'{scope}:acct:{account}', LIMIT_PER_ACCOUNT))
    return keys


def check(scope, ip, account=None):
    """制限中ならあと何秒待つべきかを返す。問題なければ0。"""
    return max((limiter.retry_after(k, limit, WINDOW_SECONDS) for k, limit in _keys(scope, ip, account)),
               default=0)


def record_failure(scope, ip, account=None):
    for k, _ in _keys(scope, ip, account):
        limiter.record_failure(k)


def record_success(scope, ip, account):
    """成功したら、その人（メール×IP）の失敗カウントだけ消す。IP全体とメール全体の累計は残す"""
    limiter.reset(f'{scope}:acct-ip:{account}:{ip}')


def wait_message(seconds):
    minutes = max(1, (seconds + 59) // 60)
    return f'失敗が続いたため、しばらく操作できません。約{minutes}分後にもう一度お試しください。'
