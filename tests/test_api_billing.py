# -*- coding: utf-8 -*-
"""課金（Stripe）APIのテスト。実際のStripeネットワーク呼び出しはせず、
stripeモジュールの該当関数をモックする。Webhookは STRIPE_WEBHOOK_SECRET 未設定時の
開発用フォールバック（署名検証なし）の経路を検証する。"""
from types import SimpleNamespace

import core


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
