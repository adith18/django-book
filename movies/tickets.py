"""PDF ticket generation with QR verification payload."""
import io
import json

from django.conf import settings

from .models import BookingOrder


def _qr_payload(order: BookingOrder) -> str:
    data = {
        'ref': order.reference,
        'order_id': order.id,
        'user': order.user_id,
        'showtime': order.show_time_id,
    }
    return json.dumps(data, separators=(',', ':'))


def generate_ticket_pdf(order: BookingOrder) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    import qrcode

    show = order.show_time
    movie = show.movie
    bookings = order.bookings.select_related('seat').order_by('seat__seat_number')
    seats = ', '.join(b.seat.seat_number for b in bookings if b.seat_id)
    payment_id = ''
    if order.payment_transactions.exists():
        payment_id = order.payment_transactions.first().provider_payment_id or ''

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 25 * mm

    c.setFont('Helvetica-Bold', 18)
    c.drawString(25 * mm, y, 'BookMySeat — E-Ticket')
    y -= 10 * mm
    c.setFont('Helvetica', 11)
    c.drawString(25 * mm, y, f'Booking ID: {order.reference}')
    y -= 7 * mm
    c.drawString(25 * mm, y, f'Payment ref: {payment_id or "—"}')
    y -= 12 * mm

    c.setFont('Helvetica-Bold', 14)
    c.drawString(25 * mm, y, movie.name)
    y -= 8 * mm
    c.setFont('Helvetica', 11)
    c.drawString(25 * mm, y, f'Theater: {show.theater.name}')
    y -= 6 * mm
    c.drawString(25 * mm, y, f'Screen: {show.screen.name}')
    y -= 6 * mm
    c.drawString(25 * mm, y, f'Date: {show.date}  |  Time: {show.start_time.strftime("%I:%M %p")}')
    y -= 6 * mm
    c.drawString(25 * mm, y, f'Seats: {seats}')
    y -= 6 * mm
    c.drawString(25 * mm, y, f'Total paid: ₹{order.total_amount:.2f}')
    y -= 15 * mm

    qr = qrcode.QRCode(box_size=4, border=2)
    qr.add_data(_qr_payload(order))
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    img_buf = io.BytesIO()
    img.save(img_buf, format='PNG')
    img_buf.seek(0)

    from reportlab.lib.utils import ImageReader

    c.drawImage(ImageReader(img_buf), width - 55 * mm, height - 55 * mm, 40 * mm, 40 * mm)
    c.setFont('Helvetica-Oblique', 9)
    c.drawString(25 * mm, 20 * mm, 'Present this QR code at the theater entrance for verification.')

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()
