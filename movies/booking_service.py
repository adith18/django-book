"""Atomic booking finalization after verified payment."""
import uuid

from django.db import transaction
from django.utils import timezone

from .models import Booking, BookingOrder, PaymentTransaction, Seat, SeatReservation


class BookingFinalizeError(ValueError):
    pass


@transaction.atomic
def finalize_payment_transaction(payment_txn: PaymentTransaction) -> BookingOrder:
    """
    Create BookingOrder + Booking rows from a SUCCESS payment.
    Idempotent: safe to call twice (webhook + client verify).
    """
    payment_txn = PaymentTransaction.objects.select_for_update().get(pk=payment_txn.pk)

    if payment_txn.status == PaymentTransaction.Status.SUCCESS and payment_txn.booking_order_id:
        return payment_txn.booking_order

    if payment_txn.status != PaymentTransaction.Status.SUCCESS:
        raise BookingFinalizeError('Payment is not successful.')

    if payment_txn.booking_order_id:
        return payment_txn.booking_order

    show_time = payment_txn.show_time
    seat_ids = payment_txn.seat_ids or []
    if not seat_ids:
        raise BookingFinalizeError('No seats on payment record.')

    SeatReservation.cleanup_expired(show_time=show_time)

    locked_seats = list(
        Seat.objects.select_for_update().filter(id__in=seat_ids, screen=show_time.screen)
    )
    if len(locked_seats) != len(seat_ids):
        raise BookingFinalizeError('One or more seats were not found.')

    booked_ids = set(
        Booking.objects.filter(show_time=show_time).values_list('seat_id', flat=True)
    )
    already = [s.seat_number for s in locked_seats if s.id in booked_ids]
    if already:
        raise BookingFinalizeError(f'Seats already booked: {", ".join(already)}')

    other_holds = SeatReservation.objects.select_for_update().filter(
        show_time=show_time,
        status='HELD',
        expires_at__gt=timezone.now(),
        seats__in=locked_seats,
    ).exclude(user=payment_txn.user).distinct()
    if other_holds.exists():
        raise BookingFinalizeError('Seats are held by another user.')

    user_res = SeatReservation.objects.select_for_update().filter(
        pk=payment_txn.reservation_id,
        user=payment_txn.user,
        show_time=show_time,
        status='HELD',
    ).first()
    if user_res and user_res.expires_at <= timezone.now():
        user_res = None
    if not user_res:
        user_res = SeatReservation.objects.select_for_update().filter(
            show_time=show_time,
            user=payment_txn.user,
            status='HELD',
            expires_at__gt=timezone.now(),
        ).first()
    if user_res:
        held_ids = set(user_res.seats.values_list('id', flat=True))
        if held_ids != set(seat_ids):
            raise BookingFinalizeError('Held seats do not match payment.')
        user_res.status = 'COMPLETED'
        user_res.save(update_fields=['status'])

    reference = payment_txn.provider_payment_id or f'BMS-{uuid.uuid4().hex[:12].upper()}'
    order = BookingOrder.objects.create(
        user=payment_txn.user,
        show_time=show_time,
        reference=reference[:64],
        total_amount=payment_txn.amount,
        status=BookingOrder.Status.CONFIRMED,
    )

    for seat in locked_seats:
        Booking.objects.create(
            user=payment_txn.user,
            seat=seat,
            show_time=show_time,
            theater=show_time.theater,
            movie=show_time.movie,
            total_price=seat.price,
            booking_order=order,
            payment_transaction=payment_txn,
        )

    payment_txn.booking_order = order
    payment_txn.save(update_fields=['booking_order'])
    return order


@transaction.atomic
def release_payment_failure(payment_txn: PaymentTransaction, reason: str = '') -> None:
    """Mark payment failed and release any active seat hold for this user/show."""
    payment_txn = PaymentTransaction.objects.select_for_update().get(pk=payment_txn.pk)
    if payment_txn.status == PaymentTransaction.Status.SUCCESS:
        return
    payment_txn.status = PaymentTransaction.Status.FAILED
    payment_txn.failure_reason = (reason or payment_txn.failure_reason)[:500]
    payment_txn.save(update_fields=['status', 'failure_reason', 'updated_at'])

    if payment_txn.reservation_id:
        SeatReservation.objects.filter(
            pk=payment_txn.reservation_id,
            user=payment_txn.user,
            status='HELD',
        ).update(status='CANCELLED')
