# -*- coding: utf-8 -*-
"""課金（Stripe）APIのテスト。実際のStripeネットワーク呼び出しはせず、
stripeモジュールの該当関数をモックする。Webhookは STRIPE_WEBHOOK_SECRET 未設定時の
開発用フォールバック（署名検証なし）の経路を検証する。"""
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import core

WEBHOOK_SECRET = 'whsec_test_secret_for_unit_tests'


def _signed_webhook(client, event, secret=WEBHOOK_SECRET, timestamp=None):
    """Stripeと同じ方式（timestamp.payload のHMAC-SHA256）で署名して送る"""
    payload = json.dumps(event)
    ts = int(timestamp or time.time())
    sig = hmac.new(secret.encode(), f'{ts}.{payload}'.encode(), hashlib.sha256).hexdigest()
    return client.post('/api/billing/webhook', data=payload,
                       headers={'Content-Type': 'application/json',
                                'Stripe-Signature': f't={ts},v1={sig}'})


def _make_customer(email='test_user@example.com', customer='cus_sig', status='free'):
    with core.get_db() as conn:
        conn.execute("UPDATE users SET stripe_customer_id = ?, subscription_status = ? WHERE email = ?",
                     (customer, status, email))
        conn.commit()


# 本物のStripeの通知と同じく、"object": "event" などを含める（署名確認後にstripeライブラリが読む）
SUB_ACTIVE = {'id': 'evt_sig', 'object': 'event', 'type': 'customer.subscription.updated',
              'data': {'object': {'id': 'sub_sig', 'object': 'subscription', 'customer': 'cus_sig',
                                  'status': 'active'}}}


def test_create_checkout_session_requires_login(client):
    resp = client.post('/api/billing/create-checkout-session')
    assert resp.status_code == 401


def test_create_checkout_session_fails_when_price_id_missing(student_client, app, monkeypatch):
    monkeypatch.setattr(core, 'STRIPE_PRICE_ID', None)
    resp = student_client.post('/api/billing/create-checkout-session')
    assert resp.status_code == 500


def test_create_checkout_session_returns_url_when_configured(student_client, app, monkeypatch):
    monkeypatch.setattr(core, 'STRIPE_PRICE_ID', 'price_test123')
    monkeypatch.setattr(
        core.stripe.checkout.Session, 'create',
        lambda **kwargs: SimpleNamespace(url='https://checkout.stripe.com/test-session')
    )
    resp = student_client.post('/api/billing/create-checkout-session')
    assert resp.status_code == 200
    assert resp.get_json()['url'] == 'https://checkout.stripe.com/test-session'


def test_billing_portal_requires_login(client):
    assert client.post('/api/billing/portal').status_code == 401


def test_billing_portal_404_without_stripe_customer(student_client):
    resp = student_client.post('/api/billing/portal')
    assert resp.status_code == 404


def test_webhook_subscription_updated_marks_user_active(student_client, app):
    with core.get_db() as conn:
        conn.execute("UPDATE users SET stripe_customer_id = 'cus_test123' WHERE email = ?",
                     ('test_user@example.com',))
        conn.commit()

    event_payload = {
        'type': 'customer.subscription.updated',
        'data': {'object': {'id': 'sub_test123', 'customer': 'cus_test123', 'status': 'active'}},
    }
    resp = student_client.post('/api/billing/webhook', json=event_payload)
    assert resp.status_code == 200

    me = student_client.get('/api/me').get_json()
    assert me['subscription_status'] == 'active'


def test_webhook_subscription_deleted_marks_user_free(student_client, app):
    with core.get_db() as conn:
        conn.execute(
            "UPDATE users SET stripe_customer_id = 'cus_test456', subscription_status = 'active' WHERE email = ?",
            ('test_user@example.com',)
        )
        conn.commit()

    event_payload = {
        'type': 'customer.subscription.deleted',
        'data': {'object': {'id': 'sub_test456', 'customer': 'cus_test456', 'status': 'canceled'}},
    }
    resp = student_client.post('/api/billing/webhook', json=event_payload)
    assert resp.status_code == 200

    me = student_client.get('/api/me').get_json()
    assert me['subscription_status'] == 'free'


# ---------- Webhookの署名確認 ----------
def test_webhook_with_valid_signature_is_accepted(student_client, monkeypatch):
    monkeypatch.setattr(core, 'STRIPE_WEBHOOK_SECRET', WEBHOOK_SECRET)
    _make_customer()
    assert _signed_webhook(student_client, SUB_ACTIVE).status_code == 200
    assert student_client.get('/api/me').get_json()['subscription_status'] == 'active'


def test_webhook_with_wrong_secret_is_rejected(student_client, monkeypatch):
    monkeypatch.setattr(core, 'STRIPE_WEBHOOK_SECRET', WEBHOOK_SECRET)
    _make_customer()
    resp = _signed_webhook(student_client, SUB_ACTIVE, secret='whsec_attacker_guess')
    assert resp.status_code == 400
    assert student_client.get('/api/me').get_json()['subscription_status'] == 'free'


def test_webhook_without_signature_is_rejected_when_secret_is_set(student_client, monkeypatch):
    monkeypatch.setattr(core, 'STRIPE_WEBHOOK_SECRET', WEBHOOK_SECRET)
    _make_customer()
    resp = student_client.post('/api/billing/webhook', json=SUB_ACTIVE)  # 署名ヘッダーなしの偽通知
    assert resp.status_code == 400
    assert student_client.get('/api/me').get_json()['subscription_status'] == 'free'


def test_webhook_with_old_timestamp_is_rejected(student_client, monkeypatch):
    # 過去に盗み見た正規の通知を後から再送されても受け付けない（5分以上前は拒否）
    monkeypatch.setattr(core, 'STRIPE_WEBHOOK_SECRET', WEBHOOK_SECRET)
    _make_customer()
    resp = _signed_webhook(student_client, SUB_ACTIVE, timestamp=time.time() - 3600)
    assert resp.status_code == 400
    assert student_client.get('/api/me').get_json()['subscription_status'] == 'free'


def test_unsigned_webhook_is_rejected_in_production_when_secret_is_missing(student_client, monkeypatch):
    monkeypatch.setattr(core, 'STRIPE_WEBHOOK_SECRET', None)
    monkeypatch.setenv('RENDER', 'true')
    _make_customer()
    resp = student_client.post('/api/billing/webhook', json=SUB_ACTIVE)
    assert resp.status_code == 400
    assert student_client.get('/api/me').get_json()['subscription_status'] == 'free'


# ---------- 決済ページ作成に渡すメールアドレス ----------
def _capture_checkout_kwargs(monkeypatch):
    monkeypatch.setattr(core, 'STRIPE_PRICE_ID', 'price_test123')
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(url='https://checkout.stripe.com/test-session')

    monkeypatch.setattr(core.stripe.checkout.Session, 'create', fake_create)
    return captured


def test_checkout_passes_a_valid_email_to_stripe(student_client, monkeypatch):
    captured = _capture_checkout_kwargs(monkeypatch)
    assert student_client.post('/api/billing/create-checkout-session').status_code == 200
    assert captured['customer_email'] == 'test_user@example.com'


def test_checkout_omits_an_invalid_legacy_email_instead_of_failing(student_client, monkeypatch):
    # 以前の緩いチェックで登録された「aiue@okaki」のようなアカウントでも、決済に進める
    captured = _capture_checkout_kwargs(monkeypatch)
    with core.get_db() as conn:
        conn.execute("UPDATE users SET email = 'aiue@okaki' WHERE email = 'test_user@example.com'")
        conn.commit()
    assert student_client.post('/api/billing/create-checkout-session').status_code == 200
    assert captured['customer_email'] is None


# ---------- 解約予定日（「◯月◯日まで有料」の表示用） ----------
from datetime import datetime, timezone

from blueprints.billing import scheduled_cancel_date

# 2026-11-24 16:58 UTC = 日本時間では 11/25 01:58。UTCのままだと前日になってしまう境目の時刻
CANCEL_TS = int(datetime(2026, 11, 24, 16, 58, tzinfo=timezone.utc).timestamp())


def _subscription_event(**overrides):
    sub = {'id': 'sub_cx', 'object': 'subscription', 'customer': 'cus_cx', 'status': 'active'}
    sub.update(overrides)
    return {'id': 'evt_cx', 'object': 'event', 'type': 'customer.subscription.updated', 'data': {'object': sub}}


def test_scheduled_cancel_date_new_api_style_uses_cancel_at():
    sub = {'cancel_at': CANCEL_TS, 'cancel_at_period_end': False}
    assert scheduled_cancel_date(sub) == '2026-11-25'   # 日本時間の日付になる


def test_scheduled_cancel_date_old_api_style_uses_period_end():
    sub = {'cancel_at': None, 'cancel_at_period_end': True, 'current_period_end': CANCEL_TS}
    assert scheduled_cancel_date(sub) == '2026-11-25'


def test_scheduled_cancel_date_old_api_style_period_end_on_item():
    sub = {'cancel_at_period_end': True, 'items': {'data': [{'current_period_end': CANCEL_TS}]}}
    assert scheduled_cancel_date(sub) == '2026-11-25'


def test_scheduled_cancel_date_is_none_when_not_cancelling():
    assert scheduled_cancel_date({'cancel_at': None, 'cancel_at_period_end': False}) is None
    assert scheduled_cancel_date({}) is None


def test_webhook_saves_cancel_date_and_me_returns_it(student_client):
    _make_customer(customer='cus_cx', status='active')
    resp = student_client.post('/api/billing/webhook', json=_subscription_event(cancel_at=CANCEL_TS))
    assert resp.status_code == 200

    me = student_client.get('/api/me').get_json()
    assert me['subscription_status'] == 'active'          # 期間の終わりまでは有料のまま
    assert me['subscription_cancel_at'] == '2026-11-25'


def test_webhook_clears_cancel_date_when_subscription_is_resumed(student_client):
    _make_customer(customer='cus_cx', status='active')
    student_client.post('/api/billing/webhook', json=_subscription_event(cancel_at=CANCEL_TS))
    student_client.post('/api/billing/webhook', json=_subscription_event(cancel_at=None))  # 解約を取り消した
    assert student_client.get('/api/me').get_json()['subscription_cancel_at'] is None


def test_webhook_deleted_returns_to_free_and_clears_cancel_date(student_client):
    _make_customer(customer='cus_cx', status='active')
    student_client.post('/api/billing/webhook', json=_subscription_event(cancel_at=CANCEL_TS))
    deleted = _subscription_event(status='canceled')
    deleted['type'] = 'customer.subscription.deleted'
    student_client.post('/api/billing/webhook', json=deleted)

    me = student_client.get('/api/me').get_json()
    assert me['subscription_status'] == 'free'
    assert me['subscription_cancel_at'] is None


def test_free_user_has_no_cancel_date(student_client):
    assert student_client.get('/api/me').get_json()['subscription_cancel_at'] is None


def test_new_checkout_confirmation_clears_a_stale_cancel_date(student_client, monkeypatch):
    # 解約→再契約したとき、前回の解約予定日が「解約済み」表示として残らない
    with core.get_db() as conn:
        conn.execute("UPDATE users SET subscription_cancel_at = '2026-11-25' WHERE email = 'test_user@example.com'")
        conn.commit()
    me_id = student_client.get('/api/me').get_json()['id']
    monkeypatch.setattr(
        core.stripe.checkout.Session, 'retrieve',
        lambda sid: SimpleNamespace(client_reference_id=str(me_id), status='complete',
                                    customer='cus_new', subscription='sub_new'))
    assert student_client.post('/api/billing/confirm', json={'session_id': 'cs_x'}).status_code == 200

    me = student_client.get('/api/me').get_json()
    assert me['subscription_status'] == 'active'
    assert me['subscription_cancel_at'] is None
