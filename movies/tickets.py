"""PDF ticket generation (reportlab) with a signed QR code for verification."""
import hashlib
import hmac
import io

import qrcode
from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from .models import BookingOrder

NAVY = colors.HexColor('#1a1a2e')
ACCENT = colors.HexColor('#20c997')
MUTED = colors.HexColor('#777777')
LIGHT = colors.HexColor('#f4f6f8')


def _signature(order: BookingOrder) -> str:
    """Short HMAC so a QR code can't be forged by just guessing an order id."""
    msg = f'{order.id}:{order.reference}'.encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()[:12]


def _qr_payload(order: BookingOrder) -> str:
    return f'BMS|{order.id}|{order.reference}|{_signature(order)}'


def _qr_image(payload: str) -> ImageReader:
    img = qrcode.make(payload, box_size=8, border=1)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return ImageReader(buf)


def _payment_reference(order: BookingOrder) -> str:
    txn = order.payment_transactions.order_by('-created_at').first()
    if txn and txn.provider_payment_id:
        return txn.provider_payment_id
    return order.reference


def _fit(text, max_chars):
    text = str(text)
    return text if len(text) <= max_chars else text[: max_chars - 1] + '...'


def generate_ticket_pdf(order: BookingOrder) -> bytes:
    show = order.show_time
    movie = show.movie
    seats = sorted(
        (b.seat.seat_number for b in order.bookings.select_related('seat') if b.seat),
        key=lambda s: (''.join(c for c in s if c.isalpha()), int(''.join(c for c in s if c.isdigit()) or 0)),
    )

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f'Ticket - {movie.name}')
    width, height = A4
    left = 20 * mm
    right = width - 20 * mm

    # ── Header band ──
    c.setFillColor(NAVY)
    c.rect(0, height - 42 * mm, width, 42 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(left, height - 14 * mm, 'BOOKMYSEAT  |  E-TICKET')
    c.setFont('Helvetica-Bold', 22)
    c.drawString(left, height - 28 * mm, _fit(movie.name.upper(), 36))
    c.setFont('Helvetica', 10)
    c.setFillColor(ACCENT)
    c.drawString(left, height - 36 * mm, 'BOOKING CONFIRMED')

    # ── Detail grid ──
    def field(x, y, label, value, size=12):
        c.setFillColor(MUTED)
        c.setFont('Helvetica-Bold', 8)
        c.drawString(x, y, label.upper())
        c.setFillColor(NAVY)
        c.setFont('Helvetica-Bold', size)
        c.drawString(x, y - 6 * mm, str(value))

    col2 = left + 85 * mm
    y = height - 62 * mm
    field(left, y, 'Theater', _fit(show.theater.name, 30))
    field(col2, y, 'Screen', f'{show.screen.name} ({show.screen.get_screen_type_display()})')
    y -= 20 * mm
    field(left, y, 'Date', show.date.strftime('%a, %d %b %Y'))
    field(col2, y, 'Show time', show.start_time.strftime('%I:%M %p').lstrip('0'))
    y -= 20 * mm
    field(left, y, 'Booking ID', f'BMS-{order.id:06d}')
    field(col2, y, 'Payment reference', _fit(_payment_reference(order), 28), size=10)
    y -= 20 * mm
    field(left, y, 'Amount paid', f'Rs. {order.total_amount:.2f}')
    field(col2, y, 'Booked on', order.created_at.strftime('%d %b %Y, %I:%M %p'))

    # ── Seats box (wraps onto extra lines so no seat is ever hidden) ──
    y -= 26 * mm
    seat_font, seat_size = 'Helvetica-Bold', 14
    max_w = (right - left) - 10 * mm
    lines, current = [], ''
    for seat in seats or ['-']:
        trial = f'{current}, {seat}' if current else seat
        if pdfmetrics.stringWidth(trial, seat_font, seat_size) <= max_w:
            current = trial
        else:
            lines.append(current + ',')
            current = seat
    lines.append(current)

    line_h = 7 * mm
    box_h = 14 * mm + len(lines) * line_h
    c.setFillColor(LIGHT)
    c.roundRect(left, y + 8 * mm - box_h, right - left, box_h, 3 * mm, stroke=0, fill=1)
    c.setFillColor(MUTED)
    c.setFont('Helvetica-Bold', 8)
    c.drawString(left + 5 * mm, y + 3 * mm, f'SEATS ({len(seats)})')
    c.setFillColor(NAVY)
    c.setFont(seat_font, seat_size)
    for i, line in enumerate(lines):
        c.drawString(left + 5 * mm, y - 6 * mm - i * line_h, line)
    y -= (len(lines) - 1) * line_h

    # ── Tear line + QR ──
    y -= 32 * mm
    c.setStrokeColor(colors.HexColor('#cccccc'))
    c.setDash(3, 3)
    c.line(left, y, right, y)
    c.setDash()

    qr_size = 48 * mm
    qr_y = y - qr_size - 8 * mm
    c.drawImage(_qr_image(_qr_payload(order)), left, qr_y, qr_size, qr_size)
    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(left + qr_size + 10 * mm, qr_y + qr_size - 8 * mm, 'Scan at the entrance')
    c.setFillColor(MUTED)
    c.setFont('Helvetica', 9)
    notes = [
        'Show this QR code (printed or on your phone) at the theater.',
        'Please arrive at least 15 minutes before the show.',
        'Tickets once booked are non-cancellable.',
        f'Reference: {_fit(order.reference, 40)}',
    ]
    for i, line in enumerate(notes):
        c.drawString(left + qr_size + 10 * mm, qr_y + qr_size - (16 + i * 6) * mm, line)

    # ── Footer ──
    c.setFillColor(MUTED)
    c.setFont('Helvetica', 8)
    c.drawCentredString(width / 2, 12 * mm, 'This is a computer-generated ticket. Thank you for booking with BookMySeat.')

    c.showPage()
    c.save()
    return buf.getvalue()