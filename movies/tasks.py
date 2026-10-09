"""Background tasks. Ticket email is sent by Celery, never inside a web request."""
import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage

from .models import BookingOrder
from .tickets import generate_ticket_pdf

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),       # SMTP errors, timeouts, etc. -> retry automatically
    retry_backoff=True,               # 1s, 2s, 4s, 8s ... between attempts
    retry_backoff_max=600,            # but never wait more than 10 minutes
    retry_jitter=True,
    max_retries=6,
    acks_late=True,                   # if the worker dies mid-send, the task is redelivered
)
def send_ticket_email_task(self, booking_order_id):
    try:
        order = BookingOrder.objects.select_related(
            'user', 'show_time__movie', 'show_time__theater', 'show_time__screen'
        ).get(pk=booking_order_id)
    except BookingOrder.DoesNotExist:
        logger.error('Ticket email: order %s does not exist; not retrying.', booking_order_id)
        return 'missing-order'

    user = order.user
    if not user.email:
        logger.warning('Ticket email: user %s has no email address; skipping.', user.pk)
        return 'no-email'

    show = order.show_time
    pdf_bytes = generate_ticket_pdf(order)

    message = EmailMessage(
        subject=f'Your ticket for {show.movie.name} (BMS-{order.id:06d})',
        body=(
            f'Hi {user.get_username()},\n\n'
            f'Your booking is confirmed!\n\n'
            f'Movie:   {show.movie.name}\n'
            f'Theater: {show.theater.name} - {show.screen.name}\n'
            f'When:    {show.date:%a, %d %b %Y} at {show.start_time:%I:%M %p}\n'
            f'Booking: BMS-{order.id:06d}\n\n'
            f'Your ticket (with QR code) is attached. You can also download it any time '
            f'from your booking history.\n\nEnjoy the show!\nBookMySeat'
        ),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        to=[user.email],
    )
    message.attach(f'ticket-BMS-{order.id:06d}.pdf', pdf_bytes, 'application/pdf')
    message.send(fail_silently=False)   # raise on failure so Celery retries
    logger.info('Ticket email sent for order %s to %s', order.id, user.email)
    return 'sent'