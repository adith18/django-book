import json
import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.db import transaction
from django.db.models import Count, Q
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError as DjangoValidationError

from .forms import MovieAvailabilityForm, AdminShowTimeForm
from .models import (
    Movie, Theater, Screen, ShowTime, Seat, Booking, Review, ReviewReport,
    SeatReservation, Genre, Language, CastMember, user_has_watched_movie,
)


# ---------------------------------------------------------------------------
# Movie list
# ---------------------------------------------------------------------------

def movie_list(request):
    search_query = request.GET.get('search')
    today = timezone.localdate()
    movies = Movie.objects.all()
    if search_query:
        movies = movies.filter(name__icontains=search_query)
    else:
        # Hide movies whose theatrical run has ended from the default browse list
        movies = movies.filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
    return render(request, 'movies/movie_list.html', {'movies': movies, 'today': today})


# ---------------------------------------------------------------------------
# Recommendation helpers
# ---------------------------------------------------------------------------

def _recommendations_for(movie):
    """Similar (by genre/language), trending (most bookings), and recently released movies."""
    genre_ids = list(movie.genres.values_list('id', flat=True))

    similar = Movie.objects.exclude(id=movie.id)
    if genre_ids or movie.language_id:
        similar = similar.filter(
            Q(genres__id__in=genre_ids) | Q(language_id=movie.language_id)
        ).distinct()
    similar = similar.annotate(
        shared_genres=Count('genres', filter=Q(genres__id__in=genre_ids))
    ).order_by('-shared_genres', '-release_date')[:6]

    trending = (
        Movie.objects.exclude(id=movie.id)
        .annotate(booking_count=Count('showtimes__bookings'))
        .order_by('-booking_count', '-release_date')[:6]
    )

    recent = (
        Movie.objects.exclude(id=movie.id)
        .exclude(release_date__isnull=True)
        .order_by('-release_date')[:6]
    )

    return similar, trending, recent


# ---------------------------------------------------------------------------
# Showtime serialization (shared by page + API)
# ---------------------------------------------------------------------------

def _serialize_showtime(st):
    start = st.start_time.strftime('%I:%M %p')
    end = st.end_time_display.strftime('%I:%M %p')
    status = st.booking_status
    return {
        'id': st.id,
        'screen': st.screen.name,
        'screen_type': st.screen.get_screen_type_display(),
        'start_time': start,
        'end_time': end,
        'time_range': f'{start} - {end}',
        'status': status,
        'status_label': st.customer_status_label,
        'seats_available': st.seats_available,
        'seats_total': st.seats_total,
        'has_started': st.has_started,
        'bookable': status in ('Available', 'Almost Full'),
    }


def _serialize_admin_showtime(st):
    start = st.start_time.strftime('%I:%M %p')
    end = st.end_time_display.strftime('%I:%M %p')
    return {
        'id': st.id,
        'theater_id': st.theater_id,
        'theater_name': st.theater.name,
        'screen_id': st.screen_id,
        'screen_name': st.screen.name,
        'date': st.date.isoformat(),
        'start_time': st.start_time.strftime('%H:%M'),
        'start_time_display': start,
        'end_time_display': end,
        'time_range': f'{start} - {end}',
        'cleaning_buffer_minutes': st.cleaning_buffer_minutes,
        'booking_count': st.bookings.count(),
    }


def _staff_only(request):
    return request.user.is_authenticated and request.user.is_staff


def _theaters_payload_from_showtimes(showtimes_qs):
    theaters_data = {}
    for st in showtimes_qs:
        tid = st.theater_id
        if tid not in theaters_data:
            theaters_data[tid] = {
                'id': tid,
                'name': st.theater.name,
                'showtimes': [],
            }
        theaters_data[tid]['showtimes'].append(_serialize_showtime(st))
    return list(theaters_data.values())


# ---------------------------------------------------------------------------
# Theater / Movie detail page
# ---------------------------------------------------------------------------

def theater_list(request, movie_id):
    """Movie detail page: trailer, cast, date-wise showtimes, reviews, and recommendations."""
    movie = get_object_or_404(Movie, id=movie_id)
    reviews = movie.reviews.filter(is_hidden=False).select_related('user').all()
    avg_rating = movie.average_rating

    user_review = None
    can_review = False
    if request.user.is_authenticated:
        user_review = movie.reviews.filter(user=request.user).first()
        can_review = user_has_watched_movie(request.user, movie)

    if request.method == 'POST' and request.user.is_authenticated:
        if not can_review:
            messages.error(
                request,
                'You can only review a movie after booking and watching it.'
            )
            return redirect('theater_list', movie_id=movie.id)

        rating = request.POST.get('rating')
        comment = request.POST.get('comment', '').strip()
        if rating:
            Review.objects.update_or_create(
                movie=movie,
                user=request.user,
                defaults={'rating': rating, 'comment': comment, 'is_hidden': False},
            )
            messages.success(request, 'Thanks for your review!')
            return redirect('theater_list', movie_id=movie.id)

    similar_movies, trending_movies, recent_movies = _recommendations_for(movie)

    today = timezone.localdate()
    booking_dates = movie.get_booking_dates()

    # Group showtimes by date for this movie (all theaters/screens)
    showtimes_qs = (
        ShowTime.objects
        .filter(movie=movie, is_cancelled=False)
        .select_related('theater', 'screen', 'movie')
        .order_by('date', 'start_time')
    )

    # Build a date → {theater: [showtimes]} nested dict
    dates_showtime_map = {}
    for st in showtimes_qs:
        date_key = st.date
        if date_key not in dates_showtime_map:
            dates_showtime_map[date_key] = {}
        theater_key = st.theater
        if theater_key not in dates_showtime_map[date_key]:
            dates_showtime_map[date_key][theater_key] = []
        dates_showtime_map[date_key][theater_key].append(st)

    # Build list of all theaters that have at least one showtime for this movie
    theaters = list(Theater.objects.filter(
        showtimes__movie=movie,
        showtimes__is_cancelled=False
    ).distinct().order_by('name'))

    # Default selected date = today if in range, else first booking date
    selected_date = today
    if booking_dates and today not in booking_dates:
        selected_date = booking_dates[0]

    initial_showtimes = {'theaters': []}
    if selected_date in dates_showtime_map:
        day_showtimes = ShowTime.objects.filter(
            movie=movie,
            date=selected_date,
            is_cancelled=False,
        ).select_related('theater', 'screen', 'movie').order_by('theater__name', 'start_time')
        initial_showtimes = {'theaters': _theaters_payload_from_showtimes(day_showtimes)}

    return render(request, 'movies/theater_list.html', {
        'movie': movie,
        'reviews': reviews,
        'avg_rating': avg_rating,
        'user_review': user_review,
        'can_review': can_review,
        'similar_movies': similar_movies,
        'trending_movies': trending_movies,
        'recent_movies': recent_movies,
        'booking_dates': booking_dates,
        'dates_showtime_map': dates_showtime_map,
        'theaters': theaters,
        'today': today,
        'selected_date': selected_date,
        'initial_showtimes_json': json.dumps(initial_showtimes),
    })


# ---------------------------------------------------------------------------
# AJAX: showtimes for a specific date
# ---------------------------------------------------------------------------

def api_showtimes_for_date(request, movie_id, show_date):
    """Return JSON list of theaters + showtimes for a movie on a given date."""
    movie = get_object_or_404(Movie, id=movie_id)

    try:
        target_date = datetime.date.fromisoformat(show_date)
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Invalid date format.'}, status=400)

    # Verify the date is within the movie's theatrical run
    today = timezone.localdate()
    if movie.end_date and target_date > movie.end_date:
        return JsonResponse({'success': False, 'error': 'Movie run has ended.'}, status=400)
    if movie.release_date and target_date < movie.release_date:
        return JsonResponse({'success': False, 'error': 'Date is before release.'}, status=400)
    if target_date < today:
        return JsonResponse({'success': False, 'error': 'Date is in the past.'}, status=400)

    showtimes_qs = (
        ShowTime.objects
        .filter(movie=movie, date=target_date, is_cancelled=False)
        .select_related('theater', 'screen', 'movie')
        .order_by('theater__name', 'start_time')
    )

    return JsonResponse({
        'success': True,
        'date': show_date,
        'theaters': _theaters_payload_from_showtimes(showtimes_qs),
    })


# ---------------------------------------------------------------------------
# Report review
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def report_review(request, review_id):
    review = get_object_or_404(Review, id=review_id)

    if review.user_id == request.user.id:
        messages.error(request, "You can't report your own review.")
        return redirect('theater_list', movie_id=review.movie_id)

    if request.method == 'POST':
        reason = request.POST.get('reason', 'other')
        details = request.POST.get('details', '').strip()
        _, created = ReviewReport.objects.get_or_create(
            review=review,
            reported_by=request.user,
            defaults={'reason': reason, 'details': details},
        )
        if created:
            messages.success(request, 'Thanks — this review has been reported for moderation.')
        else:
            messages.info(request, "You've already reported this review.")
        return redirect('theater_list', movie_id=review.movie_id)

    return render(request, 'movies/report_review.html', {'review': review})


# ---------------------------------------------------------------------------
# Seat status API
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def api_seat_status(request, showtime_id):
    show_time = get_object_or_404(ShowTime, id=showtime_id)
    # Lazy cleanup of expired reservations
    SeatReservation.cleanup_expired(show_time=show_time)

    seats = Seat.objects.filter(screen=show_time.screen)

    # Get all active HELD reservations for this showtime
    active_reservations = SeatReservation.objects.filter(
        show_time=show_time,
        status='HELD',
        expires_at__gt=timezone.now()
    ).prefetch_related('seats')

    reserved_seat_map = {}
    for res in active_reservations:
        for s in res.seats.all():
            reserved_seat_map[s.id] = res.user_id

    # Which seats are booked for THIS show specifically
    booked_seat_ids = set(
        Booking.objects.filter(show_time=show_time).values_list('seat_id', flat=True)
    )

    user_reservation_data = None
    if request.user.is_authenticated:
        user_res = active_reservations.filter(user=request.user).first()
        if user_res:
            rem_sec = max(0, int((user_res.expires_at - timezone.now()).total_seconds()))
            held_seats = list(user_res.seats.values('id', 'seat_number', 'seat_type', 'price'))
            total = sum(float(s['price']) for s in held_seats)
            user_reservation_data = {
                'id': user_res.id,
                'seat_ids': [s['id'] for s in held_seats],
                'seats': held_seats,
                'total_price': round(total, 2),
                'remaining_seconds': rem_sec,
                'expires_at': user_res.expires_at.isoformat(),
            }

    seat_list = []
    for s in seats:
        if s.id in booked_seat_ids:
            st = 'BOOKED'
        elif s.id in reserved_seat_map:
            if request.user.is_authenticated and reserved_seat_map[s.id] == request.user.id:
                st = 'RESERVED_BY_YOU'
            else:
                st = 'RESERVED'
        else:
            st = 'AVAILABLE'

        seat_list.append({
            'id': s.id,
            'seat_number': s.seat_number,
            'seat_type': s.seat_type,
            'price': float(s.price),
            'status': st,
        })

    return JsonResponse({
        'success': True,
        'showtime_id': show_time.id,
        'seats': seat_list,
        'user_reservation': user_reservation_data,
    })


# ---------------------------------------------------------------------------
# Reserve seats
# ---------------------------------------------------------------------------

@login_required(login_url='login')
@require_POST
def reserve_seats(request, showtime_id):
    show_time = get_object_or_404(ShowTime, id=showtime_id)

    if show_time.has_started:
        return JsonResponse({'success': False, 'error': 'This show has already started.'}, status=400)

    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
            seat_ids = data.get('seats', [])
        except Exception:
            return JsonResponse({'success': False, 'error': 'Invalid JSON data.'}, status=400)
    else:
        seat_ids = request.POST.getlist('seats')

    try:
        seat_ids = [int(sid) for sid in seat_ids]
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid seat IDs provided.'}, status=400)

    if not seat_ids:
        return JsonResponse({'success': False, 'error': 'Please select at least one seat.'}, status=400)

    booked_seat_ids = set(
        Booking.objects.filter(show_time=show_time).values_list('seat_id', flat=True)
    )

    with transaction.atomic():
        SeatReservation.cleanup_expired(show_time=show_time)

        locked_seats = list(
            Seat.objects.select_for_update().filter(id__in=seat_ids, screen=show_time.screen)
        )

        if len(locked_seats) != len(seat_ids):
            return JsonResponse({'success': False, 'error': 'One or more selected seats were not found.'}, status=400)

        already_booked = [s.seat_number for s in locked_seats if s.id in booked_seat_ids]
        if already_booked:
            return JsonResponse({
                'success': False,
                'error': f"The following seat(s) are already booked: {', '.join(already_booked)}"
            }, status=400)

        other_active_res = SeatReservation.objects.select_for_update().filter(
            show_time=show_time,
            status='HELD',
            expires_at__gt=timezone.now(),
            seats__in=locked_seats
        ).exclude(user=request.user).distinct()

        if other_active_res.exists():
            conflict_seat_ids = set()
            for r in other_active_res:
                conflict_seat_ids.update(r.seats.values_list('id', flat=True))
            conflict_names = [s.seat_number for s in locked_seats if s.id in conflict_seat_ids]
            return JsonResponse({
                'success': False,
                'error': f"The following seat(s) are temporarily reserved by another user: {', '.join(conflict_names)}"
            }, status=400)

        # Release existing active reservations by current user for this showtime
        existing_res = SeatReservation.objects.filter(
            show_time=show_time, user=request.user, status='HELD'
        )
        existing_res.update(status='CANCELLED')

        expires_at = timezone.now() + datetime.timedelta(minutes=2)
        reservation = SeatReservation.objects.create(
            user=request.user,
            show_time=show_time,
            expires_at=expires_at,
            status='HELD'
        )
        reservation.seats.set(locked_seats)

    return JsonResponse({
        'success': True,
        'message': 'Seats temporarily reserved for 2 minutes.',
        'reservation_id': reservation.id,
        'remaining_seconds': 120,
        'expires_at': expires_at.isoformat(),
        'reserved_seat_ids': seat_ids
    })


# ---------------------------------------------------------------------------
# Release reservation
# ---------------------------------------------------------------------------

@login_required(login_url='login')
@require_POST
def release_reservation(request, showtime_id):
    show_time = get_object_or_404(ShowTime, id=showtime_id)
    with transaction.atomic():
        SeatReservation.objects.filter(
            show_time=show_time, user=request.user, status='HELD'
        ).update(status='CANCELLED')
    return JsonResponse({'success': True, 'message': 'Reservation released.'})


# ---------------------------------------------------------------------------
# Reservation summary
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def api_reservation_summary(request, showtime_id):
    """Return the current user's active HELD reservation details for the summary panel."""
    show_time = get_object_or_404(ShowTime, id=showtime_id)
    SeatReservation.cleanup_expired(show_time=show_time)

    user_res = SeatReservation.objects.filter(
        show_time=show_time,
        user=request.user,
        status='HELD',
        expires_at__gt=timezone.now()
    ).prefetch_related('seats').first()

    if not user_res:
        return JsonResponse({'success': True, 'reservation': None})

    held_seats = list(user_res.seats.values('id', 'seat_number', 'seat_type', 'price'))
    total = sum(float(s['price']) for s in held_seats)
    rem_sec = max(0, int((user_res.expires_at - timezone.now()).total_seconds()))

    return JsonResponse({
        'success': True,
        'reservation': {
            'id': user_res.id,
            'seats': held_seats,
            'total_price': round(total, 2),
            'remaining_seconds': rem_sec,
            'expires_at': user_res.expires_at.isoformat(),
        }
    })


# ---------------------------------------------------------------------------
# Book seats
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def book_seats(request, showtime_id):
    show_time = get_object_or_404(ShowTime, id=showtime_id)
    seats = Seat.objects.filter(screen=show_time.screen)
    booked_seat_ids = set(
        Booking.objects.filter(show_time=show_time).values_list('seat_id', flat=True)
    )

    if show_time.has_started:
        messages.error(request, 'This show has already started. Bookings are closed.')
        return redirect('theater_list', movie_id=show_time.movie_id)

    if request.method == 'POST':
        is_ajax = (
            request.headers.get('x-requested-with') == 'XMLHttpRequest' or
            request.content_type == 'application/json'
        )

        if request.content_type == 'application/json':
            try:
                data = json.loads(request.body)
                selected_seats = data.get('seats', [])
            except Exception:
                selected_seats = []
        else:
            selected_seats = request.POST.getlist('seats')

        try:
            selected_seat_ids = [int(sid) for sid in selected_seats]
        except (ValueError, TypeError):
            selected_seat_ids = []

        if not selected_seat_ids:
            msg = 'Please select at least one seat.'
            if is_ajax:
                return JsonResponse({'success': False, 'error': msg}, status=400)
            return render(request, 'movies/seat_selection.html', {
                'show_time': show_time,
                'seats': seats,
                'booked_seat_ids': booked_seat_ids,
                'error_message': msg
            })

        booked_seat_numbers = []
        total_order_price = 0

        try:
            with transaction.atomic():
                SeatReservation.cleanup_expired(show_time=show_time)

                locked_seats = list(
                    Seat.objects.select_for_update().filter(id__in=selected_seat_ids, screen=show_time.screen)
                )

                if len(locked_seats) != len(selected_seat_ids):
                    raise ValueError('One or more selected seats were not found.')

                # Re-fetch booked seat IDs inside atomic block
                booked_ids_now = set(
                    Booking.objects.filter(show_time=show_time).values_list('seat_id', flat=True)
                )
                already_booked = [s.seat_number for s in locked_seats if s.id in booked_ids_now]
                if already_booked:
                    raise ValueError(f"The following seat(s) are already booked: {', '.join(already_booked)}")

                other_holds = SeatReservation.objects.select_for_update().filter(
                    show_time=show_time,
                    status='HELD',
                    expires_at__gt=timezone.now(),
                    seats__in=locked_seats
                ).exclude(user=request.user).distinct()

                if other_holds.exists():
                    raise ValueError("One or more selected seats are temporarily reserved by another user.")

                user_res = SeatReservation.objects.select_for_update().filter(
                    show_time=show_time,
                    user=request.user,
                    status='HELD',
                    expires_at__gt=timezone.now()
                ).first()

                if not user_res:
                    raise ValueError("Your seat hold has expired. Please select and hold seats again before paying.")

                held_ids = set(user_res.seats.values_list('id', flat=True))
                submitted_ids = set(selected_seat_ids)
                if held_ids != submitted_ids:
                    raise ValueError("Submitted seats do not match your held reservation. Please refresh and try again.")

                for seat in locked_seats:
                    Booking.objects.create(
                        user=request.user,
                        seat=seat,
                        show_time=show_time,
                        theater=show_time.theater,
                        movie=show_time.movie,
                        total_price=seat.price,
                    )
                    booked_seat_numbers.append(seat.seat_number)
                    total_order_price += float(seat.price)

                user_res.status = 'COMPLETED'
                user_res.save(update_fields=['status'])

        except ValueError as e:
            error_msg = str(e)
            if is_ajax:
                return JsonResponse({'success': False, 'error': error_msg}, status=400)
            return render(request, 'movies/seat_selection.html', {
                'show_time': show_time,
                'seats': seats,
                'booked_seat_ids': booked_seat_ids,
                'error_message': error_msg
            })

        if booked_seat_numbers and request.user.email:
            send_mail(
                subject=f'Booking confirmed: {show_time.movie.name}',
                message=(
                    f"Hi {request.user.username},\n\n"
                    f"Your booking is confirmed.\n\n"
                    f"Movie: {show_time.movie.name}\n"
                    f"Theater: {show_time.theater.name}\n"
                    f"Screen: {show_time.screen.name}\n"
                    f"Date: {show_time.date}\n"
                    f"Time: {show_time.start_time.strftime('%I:%M %p')}\n"
                    f"Seats: {', '.join(booked_seat_numbers)}\n"
                    f"Total Paid: \u20b9{total_order_price:.2f}\n\n"
                    f"Enjoy the show!"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[request.user.email],
                fail_silently=True,
            )

        messages.success(request, f'Booking confirmed for {len(booked_seat_numbers)} seat(s)! A confirmation email has been sent.')
        if is_ajax:
            return JsonResponse({
                'success': True,
                'message': f'Booking confirmed! Total: \u20b9{total_order_price:.2f}',
                'redirect_url': '/user/profile/'
            })

        return redirect('profile')

    return render(request, 'movies/seat_selection.html', {
        'show_time': show_time,
        'seats': seats,
        'booked_seat_ids': booked_seat_ids,
    })


# ---------------------------------------------------------------------------
# Cancel booking
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def cancel_booking(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id, user=request.user)

    if request.method == 'POST':
        seat = booking.seat
        movie_name = booking.movie.name if booking.movie else ''
        seat_number = seat.seat_number if seat else ''
        movie_name = booking.movie.name if booking.movie else ''
        booking.delete()
        messages.success(request, f'Booking for {movie_name} (seat {seat_number}) has been cancelled.')
        return redirect('profile')

    return render(request, 'movies/cancel_booking.html', {'booking': booking})


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def admin_dashboard(request):
    """Custom admin interface for managing movies, genres, languages, cast, theaters, and review moderation."""
    if not request.user.is_staff:
        messages.error(request, "Access restricted to administrators.")
        return redirect('movie_list')

    total_movies = Movie.objects.count()
    total_genres = Genre.objects.count()
    total_languages = Language.objects.count()
    total_cast = CastMember.objects.count()
    total_theaters = Theater.objects.count()
    total_screens = Screen.objects.count()
    total_showtimes = ShowTime.objects.count()
    total_bookings = Booking.objects.count()
    total_reviews = Review.objects.count()

    recent_reports = ReviewReport.objects.select_related(
        'review', 'review__movie', 'review__user', 'reported_by'
    ).order_by('-created_at')[:20]
    recent_movies = Movie.objects.order_by('-created_at')[:10]
    recent_showtimes = ShowTime.objects.select_related(
        'movie', 'theater', 'screen'
    ).order_by('-date', '-start_time')[:10]

    return render(request, 'movies/admin_dashboard.html', {
        'total_movies': total_movies,
        'total_genres': total_genres,
        'total_languages': total_languages,
        'total_cast': total_cast,
        'total_theaters': total_theaters,
        'total_screens': total_screens,
        'total_showtimes': total_showtimes,
        'total_bookings': total_bookings,
        'total_reviews': total_reviews,
        'recent_reports': recent_reports,
        'recent_movies': recent_movies,
        'recent_showtimes': recent_showtimes,
    })


@login_required(login_url='login')
def moderate_review(request, review_id):
    """Toggle visibility or delete a reported review from the custom admin interface."""
    if not request.user.is_staff:
        messages.error(request, "Access restricted to administrators.")
        return redirect('movie_list')

    review = get_object_or_404(Review, id=review_id)
    action = request.POST.get('action')

    if action == 'toggle_hide':
        review.is_hidden = not review.is_hidden
        review.save()
        status_str = "hidden" if review.is_hidden else "visible"
        messages.success(request, f"Review #{review.id} status updated to {status_str}.")
    elif action == 'delete':
        review.delete()
        messages.success(request, f"Review #{review_id} deleted successfully.")

    return redirect('admin_dashboard')


# ---------------------------------------------------------------------------
# Custom admin: movie availability + date-wise showtime scheduling
# ---------------------------------------------------------------------------

@login_required(login_url='login')
def admin_movie_schedule(request, movie_id=None):
    """Create or edit a movie and manage per-date showtimes (custom admin UI)."""
    if not _staff_only(request):
        messages.error(request, 'Access restricted to administrators.')
        return redirect('movie_list')

    movie = None
    if movie_id:
        movie = get_object_or_404(Movie, pk=movie_id)

    if request.method == 'POST' and request.POST.get('form_type') == 'movie':
        form = MovieAvailabilityForm(request.POST, request.FILES, instance=movie)
        if form.is_valid():
            movie = form.save()
            messages.success(request, f'Movie "{movie.name}" saved.')
            return redirect('admin_movie_schedule_edit', movie_id=movie.id)
        messages.error(request, 'Please fix the errors below and try again.')
    else:
        form = MovieAvailabilityForm(instance=movie)

    theaters = Theater.objects.prefetch_related('screens').order_by('name')
    schedule_dates = []
    showtimes_by_date = {}
    if movie and movie.pk:
        schedule_dates = movie.get_booking_dates()
        # Admin preview: full run including past dates within release/end
        if movie.release_date and movie.end_date:
            schedule_dates = []
            current = movie.release_date
            while current <= movie.end_date:
                schedule_dates.append(current)
                current += datetime.timedelta(days=1)
        qs = (
            ShowTime.objects.filter(movie=movie)
            .select_related('theater', 'screen')
            .order_by('date', 'start_time')
        )
        for st in qs:
            key = st.date.isoformat()
            showtimes_by_date.setdefault(key, []).append(_serialize_admin_showtime(st))

    return render(request, 'movies/admin_movie_schedule.html', {
        'form': form,
        'movie': movie,
        'theaters': theaters,
        'schedule_dates': schedule_dates,
        'showtimes_by_date_json': json.dumps(showtimes_by_date),
        'languages': Language.objects.order_by('name'),
    })


@login_required(login_url='login')
def admin_api_screens(request):
    if not _staff_only(request):
        return JsonResponse({'success': False, 'error': 'Forbidden.'}, status=403)
    theater_id = request.GET.get('theater_id')
    if not theater_id:
        return JsonResponse({'success': False, 'error': 'theater_id required.'}, status=400)
    screens = Screen.objects.filter(theater_id=theater_id).order_by('name')
    return JsonResponse({
        'success': True,
        'screens': [{'id': s.id, 'name': s.name, 'type': s.get_screen_type_display()} for s in screens],
    })


@login_required(login_url='login')
def admin_api_movie_showtimes(request, movie_id):
    if not _staff_only(request):
        return JsonResponse({'success': False, 'error': 'Forbidden.'}, status=403)
    movie = get_object_or_404(Movie, pk=movie_id)
    qs = ShowTime.objects.filter(movie=movie).select_related('theater', 'screen').order_by('date', 'start_time')
    by_date = {}
    for st in qs:
        key = st.date.isoformat()
        by_date.setdefault(key, []).append(_serialize_admin_showtime(st))
    return JsonResponse({'success': True, 'movie_id': movie.id, 'showtimes_by_date': by_date})


@login_required(login_url='login')
@require_POST
def admin_api_showtime_add(request, movie_id):
    if not _staff_only(request):
        return JsonResponse({'success': False, 'error': 'Forbidden.'}, status=403)
    movie = get_object_or_404(Movie, pk=movie_id)
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        data = request.POST

    theater_id = data.get('theater_id')
    screen_id = data.get('screen_id')
    show_date = data.get('date')
    start_time_str = data.get('start_time')
    buffer_mins = data.get('cleaning_buffer_minutes', movie.default_cleaning_buffer_minutes)

    try:
        show_date_parsed = datetime.date.fromisoformat(show_date)
        start_time_parsed = datetime.datetime.strptime(start_time_str, '%H:%M').time()
        buffer_mins = int(buffer_mins)
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid date, time, or buffer.'}, status=400)

    showtime = ShowTime(
        movie=movie,
        theater_id=theater_id,
        screen_id=screen_id,
        date=show_date_parsed,
        start_time=start_time_parsed,
        cleaning_buffer_minutes=buffer_mins,
    )
    try:
        showtime.save()
    except DjangoValidationError as exc:
        return JsonResponse({'success': False, 'error': '; '.join(exc.messages)}, status=400)

    return JsonResponse({'success': True, 'showtime': _serialize_admin_showtime(showtime)})


@login_required(login_url='login')
@require_POST
def admin_api_showtime_update(request, showtime_id):
    if not _staff_only(request):
        return JsonResponse({'success': False, 'error': 'Forbidden.'}, status=403)
    showtime = get_object_or_404(ShowTime, pk=showtime_id)
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        data = request.POST

    if 'date' in data:
        showtime.date = datetime.date.fromisoformat(data['date'])
    if 'start_time' in data:
        showtime.start_time = datetime.datetime.strptime(data['start_time'], '%H:%M').time()
    if 'cleaning_buffer_minutes' in data:
        showtime.cleaning_buffer_minutes = int(data['cleaning_buffer_minutes'])
    if 'theater_id' in data:
        showtime.theater_id = int(data['theater_id'])
    if 'screen_id' in data:
        showtime.screen_id = int(data['screen_id'])

    try:
        showtime.save()
    except DjangoValidationError as exc:
        return JsonResponse({'success': False, 'error': '; '.join(exc.messages)}, status=400)

    return JsonResponse({'success': True, 'showtime': _serialize_admin_showtime(showtime)})


@login_required(login_url='login')
@require_POST
def admin_api_showtime_delete(request, showtime_id):
    if not _staff_only(request):
        return JsonResponse({'success': False, 'error': 'Forbidden.'}, status=403)
    showtime = get_object_or_404(ShowTime, pk=showtime_id)
    if showtime.bookings.exists():
        return JsonResponse({
            'success': False,
            'error': 'Cannot delete: this showtime has existing bookings.',
        }, status=400)
    showtime.delete()
    return JsonResponse({'success': True})


@login_required(login_url='login')
@require_POST
def admin_api_copy_schedule(request, movie_id):
    """Copy all showtimes from one date to another (same theater/screen/times)."""
    if not _staff_only(request):
        return JsonResponse({'success': False, 'error': 'Forbidden.'}, status=403)
    movie = get_object_or_404(Movie, pk=movie_id)
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        data = request.POST

    source_date = data.get('source_date')
    target_date = data.get('target_date')
    try:
        src = datetime.date.fromisoformat(source_date)
        tgt = datetime.date.fromisoformat(target_date)
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid source or target date.'}, status=400)

    if src == tgt:
        return JsonResponse({'success': False, 'error': 'Source and target dates must differ.'}, status=400)

    source_shows = list(
        ShowTime.objects.filter(movie=movie, date=src, is_cancelled=False).select_related('screen', 'theater')
    )
    if not source_shows:
        return JsonResponse({'success': False, 'error': 'No showtimes on the source date to copy.'}, status=400)

    created = []
    errors = []
    with transaction.atomic():
        for st in source_shows:
            clone = ShowTime(
                movie=movie,
                theater=st.theater,
                screen=st.screen,
                date=tgt,
                start_time=st.start_time,
                cleaning_buffer_minutes=st.cleaning_buffer_minutes,
            )
            try:
                clone.save()
                created.append(_serialize_admin_showtime(clone))
            except DjangoValidationError as exc:
                errors.append(f'{st.start_time.strftime("%H:%M")}: {"; ".join(exc.messages)}')

    if errors and not created:
        return JsonResponse({'success': False, 'error': ' '.join(errors)}, status=400)

    return JsonResponse({
        'success': True,
        'created': created,
        'warnings': errors,
    })
