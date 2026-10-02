"""Admin analytics using ORM aggregations (no full-table loads)."""
import csv
from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.db.models import Avg, Count, F, Q, Sum
from django.db.models.functions import ExtractHour, TruncDate, TruncMonth
from django.utils import timezone

from .models import Booking, BookingOrder, Movie, PaymentTransaction, Screen, ShowTime, Theater


def _parse_date_range(request_get):
    today = timezone.localdate()
    start = request_get.get('start_date')
    end = request_get.get('end_date')
    try:
        start_date = timezone.datetime.fromisoformat(start).date() if start else today - timedelta(days=30)
    except ValueError:
        start_date = today - timedelta(days=30)
    try:
        end_date = timezone.datetime.fromisoformat(end).date() if end else today
    except ValueError:
        end_date = today
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    return start_date, end_date


def _revenue_qs(start_date, end_date):
    return PaymentTransaction.objects.filter(
        status=PaymentTransaction.Status.SUCCESS,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    )


def build_dashboard_context(request_get):
    start_date, end_date = _parse_date_range(request_get)
    today = timezone.localdate()

    success_payments = _revenue_qs(start_date, end_date)

    revenue_total = success_payments.aggregate(total=Sum('amount'))['total'] or 0

    def _period_sum(days):
        s = today - timedelta(days=days)
        return (
            PaymentTransaction.objects.filter(
                status=PaymentTransaction.Status.SUCCESS,
                created_at__date__gte=s,
                created_at__date__lte=today,
            ).aggregate(t=Sum('amount'))['t']
            or 0
        )

    revenue_daily = _period_sum(1)
    revenue_weekly = _period_sum(7)
    revenue_monthly = _period_sum(30)
    revenue_yearly = _period_sum(365)

    booking_trends = (
        Booking.objects.filter(
            booked_at__date__gte=start_date,
            booked_at__date__lte=end_date,
        )
        .annotate(day=TruncDate('booked_at'))
        .values('day')
        .annotate(count=Count('id'), revenue=Sum('total_price'))
        .order_by('day')
    )

    theater_occupancy = []
    theaters = Theater.objects.annotate(screen_count=Count('screens')).order_by('name')
    showtimes_in_range = ShowTime.objects.filter(
        date__gte=start_date,
        date__lte=end_date,
        is_cancelled=False,
    )
    booked_by_theater = (
        Booking.objects.filter(
            show_time__in=showtimes_in_range,
        )
        .values('theater_id')
        .annotate(booked=Count('id'))
    )
    booked_map = {row['theater_id']: row['booked'] for row in booked_by_theater}

    cap_rows = Screen.objects.values('theater_id').annotate(capacity=Sum('total_seats'))
    cap_map = {row['theater_id']: row['capacity'] or 0 for row in cap_rows}

    shows_by_theater = (
        showtimes_in_range.values('theater_id')
        .annotate(show_count=Count('id'))
    )
    shows_map = {row['theater_id']: row['show_count'] for row in shows_by_theater}

    for th in theaters:
        shows = shows_map.get(th.id, 0)
        total_capacity = (cap_map.get(th.id, 0) or 0) * max(shows, 1)
        booked = booked_map.get(th.id, 0)
        pct = round((booked / total_capacity) * 100, 1) if total_capacity else 0.0
        theater_occupancy.append({
            'theater': th.name,
            'booked_seats': booked,
            'capacity': total_capacity,
            'occupancy_pct': pct,
        })

    top_movies = (
        Booking.objects.filter(booked_at__date__gte=start_date, booked_at__date__lte=end_date)
        .values('movie_id', 'movie__name')
        .annotate(bookings=Count('id'), revenue=Sum('total_price'))
        .order_by('-bookings')[:10]
    )

    top_theaters = (
        Booking.objects.filter(booked_at__date__gte=start_date, booked_at__date__lte=end_date)
        .values('theater_id', 'theater__name')
        .annotate(bookings=Count('id'), revenue=Sum('total_price'))
        .order_by('-bookings')[:10]
    )

    peak_hours = (
        Booking.objects.filter(booked_at__date__gte=start_date, booked_at__date__lte=end_date)
        .annotate(hour=ExtractHour('booked_at'))
        .values('hour')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )

    payment_stats = (
        PaymentTransaction.objects.filter(created_at__date__gte=start_date, created_at__date__lte=end_date)
        .values('status')
        .annotate(count=Count('id'))
    )
    cancelled_bookings = BookingOrder.objects.filter(
        status=BookingOrder.Status.CANCELLED,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    ).count()
    refunds = PaymentTransaction.objects.filter(
        status=PaymentTransaction.Status.REFUNDED,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    ).count()

    user_growth = (
        User.objects.filter(date_joined__date__gte=start_date, date_joined__date__lte=end_date)
        .annotate(month=TruncMonth('date_joined'))
        .values('month')
        .annotate(new_users=Count('id'))
        .order_by('month')
    )

    return {
        'filter_start': start_date.isoformat(),
        'filter_end': end_date.isoformat(),
        'revenue_total': revenue_total,
        'revenue_daily': revenue_daily,
        'revenue_weekly': revenue_weekly,
        'revenue_monthly': revenue_monthly,
        'revenue_yearly': revenue_yearly,
        'booking_trends': list(booking_trends),
        'theater_occupancy': theater_occupancy,
        'top_movies': list(top_movies),
        'top_theaters': list(top_theaters),
        'peak_hours': list(peak_hours),
        'payment_stats': list(payment_stats),
        'cancelled_bookings': cancelled_bookings,
        'refunds': refunds,
        'user_growth': list(user_growth),
    }


def export_analytics_csv(request_get) -> str:
    ctx = build_dashboard_context(request_get)
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(['BookMySeat Analytics Export'])
    writer.writerow(['Period', ctx['filter_start'], 'to', ctx['filter_end']])
    writer.writerow([])
    writer.writerow(['Revenue (filtered period)', ctx['revenue_total']])
    writer.writerow(['Revenue (daily)', ctx['revenue_daily']])
    writer.writerow(['Revenue (weekly)', ctx['revenue_weekly']])
    writer.writerow(['Revenue (monthly)', ctx['revenue_monthly']])
    writer.writerow(['Revenue (yearly)', ctx['revenue_yearly']])
    writer.writerow([])
    writer.writerow(['Booking trends by day'])
    writer.writerow(['Date', 'Bookings', 'Revenue'])
    for row in ctx['booking_trends']:
        writer.writerow([row['day'], row['count'], row['revenue'] or 0])
    writer.writerow([])
    writer.writerow(['Theater occupancy'])
    writer.writerow(['Theater', 'Booked seats', 'Capacity', 'Occupancy %'])
    for row in ctx['theater_occupancy']:
        writer.writerow([row['theater'], row['booked_seats'], row['capacity'], row['occupancy_pct']])
    writer.writerow([])
    writer.writerow(['Top movies'])
    writer.writerow(['Movie', 'Bookings', 'Revenue'])
    for row in ctx['top_movies']:
        writer.writerow([row['movie__name'], row['bookings'], row['revenue'] or 0])
    writer.writerow([])
    writer.writerow(['Peak booking hours'])
    writer.writerow(['Hour (UTC)', 'Bookings'])
    for row in ctx['peak_hours']:
        writer.writerow([row['hour'], row['count']])
    return buf.getvalue()
