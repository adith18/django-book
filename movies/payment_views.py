import json
import uuid

import datetime
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .booking_service import BookingFinalizeError, finalize_payment_transaction, release_payment_failure
from .models import Booking, PaymentTransaction, Seat, SeatReservation, ShowTime
from .payments import (
    create_provider_order,
    parse_webhook_event,
    razorpay_enabled,
    verify_razorpay_signature,
    verify_webhook_signature,
)


def _validate_checkout_seats(user, show_time, selected_seat_ids):
    if show_time.has_started:
        raise ValueError('This show has already started.')

    SeatReservation.cleanup_expired(show_time=show_time)

    locked_seats = list(
        Seat.objects.select_for_update().filter(id__in=selected_seat_ids, screen=show_time.screen)
    )
    if len(locked_seats) != len(selected_seat_ids):
        raise ValueError('One or more selected seats were not found.')

    booked_ids = set(
        Booking.objects.filter(show_time=show_time).values_list('seat_id', flat=True)
    )
    already = [s.seat_number for s in locked_seats if s.id in booked_ids]
    if already:
        raise ValueError(f"The following seat(s) are already booked: {', '.join(already)}")

    other_holds = SeatReservation.objects.select_for_update().filter(
        show_time=show_time,
        status='HELD',
        expires_at__gt=timezone.now(),
        seats__in=locked_seats,
    ).exclude(user=user).distinct()
    if other_holds.exists():
        raise ValueError('One or more selected seats are temporarily reserved by another user.')

    user_res = SeatReservation.objects.select_for_update().filter(
        show_time=show_time,
        user=user,
        status='HELD',
        expires_at__gt=timezone.now(),
    ).first()
    if not user_res:
        raise ValueError('Your seat hold has expired. Please select and hold seats again before paying.')

    held_ids = set(user_res.seats.values_list('id', flat=True))
    if held_ids != set(selected_seat_ids):
        raise ValueError('Submitted seats do not match your held reservation. Please refresh and try again.')

    total = sum(float(s.price) for s in locked_seats)
    return locked_seats, user_res, total


def create_checkout_for_user(user, show_time, seat_ids):
    with transaction.atomic():
        _validate_checkout_seats(user, show_time, seat_ids)
        idem = uuid.uuid4().hex
        total = sum(
            float(s.price)
            for s in Seat.objects.filter(id__in=seat_ids, screen=show_time.screen)
        )
        user_res = SeatReservation.objects.filter(
            show_time=show_time,
            user=user,
            status='HELD',
            expires_at__gt=timezone.now(),
        ).first()
        payment_txn = PaymentTransaction.objects.create(
            user=user,
            show_time=show_time,
            reservation=user_res,
            seat_ids=seat_ids,
            amount=round(total, 2),
            idempotency_key=idem,
        )
        checkout = create_provider_order(payment_txn)
    return payment_txn, checkout


@login_required(login_url='login')
@require_POST
def initiate_payment(request, showtime_id):
    """Create a pending payment + Razorpay order after seats are held."""
    show_time = get_object_or_404(ShowTime, id=showtime_id)

    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        data = request.POST
    seat_ids = data.get('seats', request.POST.getlist('seats'))
    try:
        seat_ids = [int(s) for s in seat_ids]
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid seat IDs.'}, status=400)

    if not seat_ids:
        return JsonResponse({'success': False, 'error': 'Please select at least one seat.'}, status=400)

    try:
        payment_txn, checkout = create_checkout_for_user(request.user, show_time, seat_ids)
    except ValueError as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)

    return JsonResponse({
        'success': True,
        'requires_payment': True,
        'checkout': checkout,
        'amount_display': float(payment_txn.amount),
    })


@login_required(login_url='login')
@require_POST
def verify_payment(request, payment_id):
    """Client-side callback: verify Razorpay signature and confirm booking."""
    payment_txn = get_object_or_404(PaymentTransaction, pk=payment_id, user=request.user)

    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        data = request.POST

    order_id = data.get('razorpay_order_id', payment_txn.provider_order_id)
    pay_id = data.get('razorpay_payment_id', '')
    signature = data.get('razorpay_signature', '')

    if payment_txn.status == PaymentTransaction.Status.SUCCESS:
        return JsonResponse({
            'success': True,
            'message': 'Already confirmed.',
            'redirect_url': '/user/profile/',
            'booking_order_id': payment_txn.booking_order_id,
        })

    if razorpay_enabled():
        if not verify_razorpay_signature(order_id, pay_id, signature):
            release_payment_failure(payment_txn, 'Signature verification failed')
            return JsonResponse({'success': False, 'error': 'Payment verification failed.'}, status=400)
    else:
        pay_id = pay_id or f'pay_mock_{payment_txn.id}'

    try:
        order = finalize_payment_transaction(payment_txn, provider_payment_id=pay_id)
    except BookingFinalizeError as exc:
        release_payment_failure(payment_txn, str(exc))
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)

    _queue_ticket_email(order.id)

    return JsonResponse({
        'success': True,
        'message': f'Booking confirmed! Total: ₹{payment_txn.amount:.2f}',
        'redirect_url': '/user/profile/',
        'booking_order_id': order.id,
    })


@login_required(login_url='login')
@require_POST
def verify_mock_payment(request, payment_id):
    """Development checkout when Razorpay keys are not configured."""
    if razorpay_enabled():
        return JsonResponse({'success': False, 'error': 'Mock payments disabled when Razorpay is configured.'}, status=400)
    return verify_payment(request, payment_id)


@login_required(login_url='login')
@require_POST
def payment_failed(request, payment_id):
    payment_txn = get_object_or_404(PaymentTransaction, pk=payment_id, user=request.user)
    reason = ''
    try:
        data = json.loads(request.body) if request.body else {}
        reason = data.get('reason', '')
    except json.JSONDecodeError:
        pass
    release_payment_failure(payment_txn, reason or 'Payment cancelled or failed')
    return JsonResponse({'success': True, 'message': 'Payment failed; seats released.'})


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    signature = request.headers.get('X-Razorpay-Signature', '')
    if not verify_webhook_signature(request.body, signature):
        return HttpResponse(status=400)

    event = parse_webhook_event(request.body)
    event_type = event.get('event', '')
    payload = event.get('payload', {}).get('payment', {}).get('entity', {})
    order_id = payload.get('order_id')
    pay_id = payload.get('id')
    status = payload.get('status')

    if not order_id or not pay_id:
        return HttpResponse(status=200)

    payment_txn = PaymentTransaction.objects.filter(provider_order_id=order_id).first()
    if not payment_txn:
        return HttpResponse(status=200)

    if event_type == 'payment.failed' or status == 'failed':
        release_payment_failure(payment_txn, 'Webhook: payment failed')
        return HttpResponse(status=200)

    if status != 'captured':
        return HttpResponse(status=200)

    try:
        order = finalize_payment_transaction(payment_txn, provider_payment_id=pay_id)
    except BookingFinalizeError as exc:
        release_payment_failure(payment_txn, str(exc))
        return HttpResponse(status=200)

    _queue_ticket_email(order.id)
    return HttpResponse(status=200)


def _queue_ticket_email(booking_order_id):
    from .tasks import send_ticket_email_task

    try:
        send_ticket_email_task.delay(booking_order_id)
    except Exception:
        send_ticket_email_task(booking_order_id)


@login_required(login_url='login')
def download_ticket(request, order_id):
    from .models import BookingOrder
    from .tickets import generate_ticket_pdf

    order = get_object_or_404(BookingOrder, pk=order_id, user=request.user)
    pdf = generate_ticket_pdf(order)
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="ticket-{order.reference}.pdf"'
    return response
