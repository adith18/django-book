"""Razorpay integration with a dev mock when keys are not configured."""
import hashlib
import hmac
import json
import uuid

from django.conf import settings

from .models import PaymentTransaction


def razorpay_enabled() -> bool:
    return bool(getattr(settings, 'RAZORPAY_KEY_ID', '') and getattr(settings, 'RAZORPAY_KEY_SECRET', ''))


def create_provider_order(payment_txn: PaymentTransaction) -> dict:
    """
    Create a Razorpay order or return mock checkout payload for local development.
    """
    amount_paise = int(payment_txn.amount * 100)
    receipt = f'txn_{payment_txn.id}'

    if razorpay_enabled():
        import razorpay

        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        order = client.order.create({
            'amount': amount_paise,
            'currency': payment_txn.currency,
            'receipt': receipt,
            'payment_capture': 1,
            'notes': {
                'payment_transaction_id': str(payment_txn.id),
                'user_id': str(payment_txn.user_id),
                'showtime_id': str(payment_txn.show_time_id),
            },
        })
        payment_txn.provider_order_id = order['id']
        payment_txn.save(update_fields=['provider_order_id', 'updated_at'])
        return {
            'mock': False,
            'key_id': settings.RAZORPAY_KEY_ID,
            'order_id': order['id'],
            'amount': amount_paise,
            'currency': payment_txn.currency,
            'payment_transaction_id': payment_txn.id,
        }

    mock_order_id = f'order_mock_{payment_txn.id}_{uuid.uuid4().hex[:8]}'
    payment_txn.provider_order_id = mock_order_id
    payment_txn.save(update_fields=['provider_order_id', 'updated_at'])
    return {
        'mock': True,
        'key_id': '',
        'order_id': mock_order_id,
        'amount': amount_paise,
        'currency': payment_txn.currency,
        'payment_transaction_id': payment_txn.id,
        'verify_url': f'/movies/payment/{payment_txn.id}/verify-mock/',
    }


def verify_razorpay_signature(order_id: str, payment_id: str, signature: str) -> bool:
    secret = getattr(settings, 'RAZORPAY_KEY_SECRET', '')
    if not secret:
        return False
    payload = f'{order_id}|{payment_id}'
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    secret = getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', '') or getattr(settings, 'RAZORPAY_KEY_SECRET', '')
    if not secret:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_webhook_event(body: bytes) -> dict:
    return json.loads(body.decode('utf-8'))
