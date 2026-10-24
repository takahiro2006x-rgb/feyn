# -*- coding: utf-8 -*-
"""失敗回数制限（ratelimit.py）の単体テスト。時計を差し替えて時間経過を再現する"""
import ratelimit


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_limiter():
    clock = FakeClock()
    return ratelimit.FailureLimiter(clock=clock), clock


def test_not_limited_below_the_limit():
    limiter, _ = make_limiter()
    for _ in range(4):
        limiter.record_failure('k')
    assert limiter.retry_after('k', limit=5, window=900) == 0


def test_limited_at_the_limit_and_reports_wait_time():
    limiter, clock = make_limiter()
    for _ in range(5):
        limiter.record_failure('k')
    clock.now += 100
    wait = limiter.retry_after('k', limit=5, window=900)
    assert 790 <= wait <= 801  # 最初の失敗から900秒後に解除される（100秒経過済み）


def test_failures_expire_after_the_window():
    limiter, clock = make_limiter()
    for _ in range(5):
        limiter.record_failure('k')
    clock.now += 901
    assert limiter.retry_after('k', limit=5, window=900) == 0


def test_release_happens_when_oldest_failure_leaves_the_window():
    limiter, clock = make_limiter()
    limiter.record_failure('k')       # t=1000
    clock.now += 500
    for _ in range(4):
        limiter.record_failure('k')   # t=1500
    clock.now += 401                  # t=1901: 最初の1件だけ窓(900秒)から外れた
    assert limiter.retry_after('k', limit=5, window=900) == 0


def test_reset_clears_only_that_key():
    limiter, _ = make_limiter()
    for _ in range(5):
        limiter.record_failure('a')
        limiter.record_failure('b')
    limiter.reset('a')
    assert limiter.retry_after('a', limit=5, window=900) == 0
    assert limiter.retry_after('b', limit=5, window=900) > 0


def test_wait_message_rounds_up_to_minutes():
    assert '1分' in ratelimit.wait_message(1)
    assert '2分' in ratelimit.wait_message(61)
