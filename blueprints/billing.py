# -*- coding: utf-8 -*-
"""課金（Stripe）API"""
import os
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, request, session

import core

billing_bp = Blueprint('billing', __name__)

JST = timezone(timedelta(hours=9))


def _field(obj, name):
    """Stripeのオブジェクトから項目を取り出す（無ければNone）"""
    try:
        return obj[name]
    except (KeyError, TypeError):
        return None


def scheduled_cancel_date(subscription):
    """解約が予約されていれば、「その日まで有料」になる日付（日本時間のYYYY-MM-DD）を返す。無ければNone。
    APIのバージョンによって予約の表し方が違うため、両方に対応する:
      - 新: cancel_at に終了日時が入る（cancel_at_period_endはFalseのまま）
      - 旧: cancel_at_period_end が True で、期間の終わり（current_period_end）が終了日時"""
    ts = _field(subscription, 'cancel_at')
    if not ts and _field(subscription, 'cancel_at_period_end'):
        ts = _field(subscription, 'current_period_end')
        if not ts:
            items = _field(_field(subscription, 'items'), 'data') or []
            ts = _field(items[0], 'current_period_end') if items else None
    if not ts:
        return None
    return datetime.fromtimestamp(ts, JST).date().isoformat()


@billing_bp.route('/api/billing/create-checkout-session', methods=['POST'])
def create_checkout_session():
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401
    if not core.STRIPE_PRICE_ID:
        return jsonify({'error': '決済がまだ設定されていません'}), 500

    with core.get_db() as conn:
        user = conn.execute(
            'SELECT email, stripe_customer_id FROM users WHERE id = ?', (session['user_id'],)
        ).fetchone()

    base_url = request.host_url.rstrip('/')
    try:
        checkout_session = core.stripe.checkout.Session.create(
            mode='subscription',
            line_items=[{'price': core.STRIPE_PRICE_ID, 'quantity': 1}],
            customer=user['stripe_customer_id'] or None,
            # 形が正しくないメールはStripeに拒否されるので渡さない（決済画面でStripeが入力を求める）。
            # 過去に緩いチェックで登録された既存アカウントでも、決済に進めるようにするため
            customer_email=(user['email'] if not user['stripe_customer_id']
                            and core.looks_like_email(user['email']) else None),
            client_reference_id=str(session['user_id']),
            success_url=f'{base_url}/mypage?checkout=success&session_id={{CHECKOUT_SESSION_ID}}',
            cancel_url=f'{base_url}/mypage?checkout=cancel',
        )
        return jsonify({'url': checkout_session.url})
    except Exception:
        # 原因（鍵の誤り・価格IDの不一致など）をRenderのLogsで確認できるようにする
        current_app.logger.exception('Stripe checkout session creation failed')
        return jsonify({'error': '決済ページの作成に失敗しました。少し待ってからもう一度試してください。'}), 500


@billing_bp.route('/api/billing/confirm', methods=['POST'])
def confirm_checkout():
    """Stripe Checkoutからの戻り先（success_url）で、支払い状況を確認してDBに反映する"""
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    checkout_session_id = request.get_json().get('session_id')
    if not checkout_session_id:
        return jsonify({'error': 'セッションIDがありません'}), 400

    try:
        checkout_session = core.stripe.checkout.Session.retrieve(checkout_session_id)
    except Exception:
        current_app.logger.exception('Stripe checkout session retrieval failed')
        return jsonify({'error': '確認に失敗しました'}), 400

    if checkout_session.client_reference_id != str(session['user_id']):
        return jsonify({'error': '権限がありません'}), 403

    status = 'active' if checkout_session.status == 'complete' else 'free'
    if status == 'active':
        with core.get_db() as conn:
            # 買い直し・再契約のときに、前回の解約予定日が残らないようNULLにする
            conn.execute(
                'UPDATE users SET subscription_status = ?, stripe_customer_id = ?, stripe_subscription_id = ?, '
                'subscription_cancel_at = NULL WHERE id = ?',
                ('active', checkout_session.customer, checkout_session.subscription, session['user_id'])
            )
            conn.commit()
    return jsonify({'ok': True, 'status': status})


@billing_bp.route('/api/billing/portal', methods=['POST'])
def billing_portal():
    """契約中のプランの変更・解約ができるStripeカスタマーポータルへのURLを発行する"""
    if not session.get('user_id'):
        return jsonify({'error': 'ログインしてください'}), 401

    with core.get_db() as conn:
        user = conn.execute(
            'SELECT stripe_customer_id FROM users WHERE id = ?', (session['user_id'],)
        ).fetchone()
    if not user or not user['stripe_customer_id']:
        return jsonify({'error': '契約情報が見つかりません'}), 404

    base_url = request.host_url.rstrip('/')
    try:
        portal_session = core.stripe.billing_portal.Session.create(
            customer=user['stripe_customer_id'],
            return_url=f'{base_url}/mypage',
        )
        return jsonify({'url': portal_session.url})
    except Exception:
        current_app.logger.exception('Stripe billing portal session creation failed')
        return jsonify({'error': 'プラン管理ページの作成に失敗しました'}), 500


@billing_bp.route('/api/billing/webhook', methods=['POST'])
def billing_webhook():
    """StripeからのWebhook。サブスクリプションの更新・解約をDBに反映する"""
    payload    = request.data
    sig_header = request.headers.get('Stripe-Signature', '')

    if not core.STRIPE_WEBHOOK_SECRET and os.environ.get('RENDER'):
        # 署名を確認できないWebhookを本番で受け付けると、誰でも偽の通知で有料化・解約を起こせてしまう。
        # 設定漏れに気づけるよう、ログに残したうえで拒否する
        current_app.logger.error('STRIPE_WEBHOOK_SECRET is not set; rejecting webhook')
        return '', 400

    try:
        if core.STRIPE_WEBHOOK_SECRET:
            event = core.stripe.Webhook.construct_event(payload, sig_header, core.STRIPE_WEBHOOK_SECRET)
        else:
            # ローカル開発用フォールバック（署名の確認なし）
            event = core.stripe.Event.construct_from(request.get_json(), core.stripe.api_key)
    except Exception:
        return '', 400

    event_type = event['type']
    obj        = event['data']['object']

    with core.get_db() as conn:
        if event_type == 'checkout.session.completed':
            # 決済画面から戻ってくる前にタブを閉じると /api/billing/confirm が呼ばれず、
            # 「払ったのに無料のまま」になってしまう。Stripeからの通知でも同じ反映を行う。
            # どの利用者の決済かは、決済ページを作るときに渡した client_reference_id（ユーザーID）で分かる
            user_id = str(_field(obj, 'client_reference_id') or '')
            if (_field(obj, 'mode') == 'subscription' and _field(obj, 'status') == 'complete'
                    and user_id.isdigit()):
                conn.execute(
                    'UPDATE users SET subscription_status = ?, stripe_customer_id = ?, stripe_subscription_id = ?, '
                    'subscription_cancel_at = NULL WHERE id = ?',
                    ('active', _field(obj, 'customer'), _field(obj, 'subscription'), int(user_id))
                )
                conn.commit()
        elif event_type in ('customer.subscription.updated', 'customer.subscription.created'):
            status = 'active' if obj['status'] in ('active', 'trialing') else 'free'
            # 有料のままで解約だけ予約されている場合は、いつまで使えるかを覚えておく（再開されたらNULLに戻る）
            cancel_at = scheduled_cancel_date(obj) if status == 'active' else None
            conn.execute(
                'UPDATE users SET subscription_status = ?, stripe_subscription_id = ?, subscription_cancel_at = ? '
                'WHERE stripe_customer_id = ?',
                (status, obj['id'], cancel_at, obj['customer'])
            )
            conn.commit()
        elif event_type == 'customer.subscription.deleted':
            conn.execute(
                "UPDATE users SET subscription_status = 'free', subscription_cancel_at = NULL "
                "WHERE stripe_customer_id = ?",
                (obj['customer'],)
            )
            conn.commit()

    return jsonify({'received': True})
