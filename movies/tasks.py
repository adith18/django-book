from celery import shared_task
from django.core.mail import EmailMessage
from django.conf import settings

from .models import BookingOrder
from .tickets import generate_ticket_pdf


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 5})
def send_ticket_email_task(self, booking_order_id: int):
    order = BookingOrder.objects.select_related(
        'user', 'show_time', 'show_time__movie', 'show_time__theater', 'show_time__screen',
    ).get(pk=booking_order_id)
    user = order.user
    if not user.email:
        return 'no_email'

    pdf_bytes = generate_ticket_pdf(order)
    show = order.show_time
    subject = f'Your ticket: {show.movie.name} @ {show.theater.name}'
    body = (
        f'Hi {user.username},\n\n'
        f'Your booking is confirmed. Your PDF ticket is attached.\n\n'
        f'Booking ID: {order.reference}\n'
        f'Movie: {show.movie.name}\n'
        f'Date: {show.date} at {show.start_time.strftime("%I:%M %p")}\n\n'
        f'Enjoy the show!\n'
    )
    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    email.attach(f'ticket-{order.reference}.pdf', pdf_bytes, 'application/pdf')
    email.send(fail_silently=False)
    return 'sent'
