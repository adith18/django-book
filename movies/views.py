import json
import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.db import transaction, IntegrityError
from django.db.models import Count, Q
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.conf import settings
from django.contrib import messages
from .models import (
    Movie, Theater, Seat, Booking, Review, ReviewReport, SeatReservation, user_has_watched_movie,
)



def movie_list(request):
    search_query = request.GET.get('search')
    if search_query:
        movies = Movie.objects.filter(name__icontains=search_query)
    else:
        movies = Movie.objects.all()
    return render(request, 'movies/movie_list.html', {'movies': movies})


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
        .annotate(booking_count=Count('theaters__seats__booking'))
        .order_by('-booking_count', '-release_date')[:6]
    )

    recent = (
        Movie.objects.exclude(id=movie.id)
        .exclude(release_date__isnull=True)
        .order_by('-release_date')[:6]
    )

    return similar, trending, recent


def theater_list(request, movie_id):
    """Movie detail page: trailer, cast, showtimes, reviews, and recommendations."""
    movie = get_object_or_404(Movie, id=movie_id)
    theaters = Theater.objects.filter(movie=movie)
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

    return render(request, 'movies/theater_list.html', {
        'movie': movie,
        'theaters': theaters,
        'reviews': reviews,
        'avg_rating': avg_rating,
        'user_review': user_review,
        'can_review': can_review,
        'similar_movies': similar_movies,
        'trending_movies': trending_movies,
        'recent_movies': recent_movies,
    })


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


@login_required(login_url='login')
def api_seat_status(request, theater_id):
    theater = get_object_or_404(Theater, id=theater_id)
    # Lazy cleanup of expired reservations
    SeatReservation.cleanup_expired(theater=theater)

    seats = Seat.objects.filter(theater=theater)

    # Get all active HELD reservations for this theater
    active_reservations = SeatReservation.objects.filter(
        theater=theater,
        status='HELD',
        expires_at__gt=timezone.now()
    ).prefetch_related('seats')

    reserved_seat_map = {}
    for res in active_reservations:
        for s in res.seats.all():
            reserved_seat_map[s.id] = res.user_id

    user_reservation_data = None
    if request.user.is_authenticated:
        user_res = active_reservations.filter(user=request.user).first()
        if user_res:
            rem_sec = max(0, int((user_res.expires_at - timezone.now()).total_seconds()))
            user_reservation_data = {
                'id': user_res.id,
                'seat_ids': list(user_res.seats.values_list('id', flat=True)),
                'remaining_seconds': rem_sec,
                'expires_at': user_res.expires_at.isoformat(),
            }

    seat_list = []
    for s in seats:
        if s.is_booked:
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
            'status': st,
        })

    return JsonResponse({
        'success': True,
        'theater_id': theater.id,
        'seats': seat_list,
        'user_reservation': user_reservation_data,
    })


@login_required(login_url='login')
@require_POST
def reserve_seats(request, theater_id):
    theater = get_object_or_404(Theater, id=theater_id)

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

    with transaction.atomic():
        SeatReservation.cleanup_expired(theater=theater)

        locked_seats = list(
            Seat.objects.select_for_update().filter(id__in=seat_ids, theater=theater)
        )

        if len(locked_seats) != len(seat_ids):
            return JsonResponse({'success': False, 'error': 'One or more selected seats were not found.'}, status=400)

        booked_seats = [s.seat_number for s in locked_seats if s.is_booked]
        if booked_seats:
            return JsonResponse({
                'success': False,
                'error': f"The following seat(s) are already booked: {', '.join(booked_seats)}"
            }, status=400)

        other_active_res = SeatReservation.objects.select_for_update().filter(
            theater=theater,
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

        # Release existing active reservations by current user for this theater (supports modifying selection)
        existing_res = SeatReservation.objects.filter(
            theater=theater, user=request.user, status='HELD'
        )
        existing_res.update(status='CANCELLED')

        expires_at = timezone.now() + datetime.timedelta(minutes=2)
        reservation = SeatReservation.objects.create(
            user=request.user,
            theater=theater,
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


@login_required(login_url='login')
@require_POST
def release_reservation(request, theater_id):
    theater = get_object_or_404(Theater, id=theater_id)
    with transaction.atomic():
        SeatReservation.objects.filter(
            theater=theater, user=request.user, status='HELD'
        ).update(status='CANCELLED')
    return JsonResponse({'success': True, 'message': 'Reservation released.'})


@login_required(login_url='login')
def book_seats(request, theater_id):
    theaters = get_object_or_404(Theater, id=theater_id)
    seats = Seat.objects.filter(theater=theaters)

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
                'theater': theaters,
                'seats': seats,
                'error_message': msg
            })

        booked_seat_numbers = []

        try:
            with transaction.atomic():
                SeatReservation.cleanup_expired(theater=theaters)

                locked_seats = list(
                    Seat.objects.select_for_update().filter(id__in=selected_seat_ids, theater=theaters)
                )

                if len(locked_seats) != len(selected_seat_ids):
                    raise ValueError('One or more selected seats were not found.')

                already_booked = [s.seat_number for s in locked_seats if s.is_booked]
                if already_booked:
                    raise ValueError(f"The following seat(s) are already booked: {', '.join(already_booked)}")

                other_holds = SeatReservation.objects.select_for_update().filter(
                    theater=theaters,
                    status='HELD',
                    expires_at__gt=timezone.now(),
                    seats__in=locked_seats
                ).exclude(user=request.user).distinct()

                if other_holds.exists():
                    raise ValueError("One or more selected seats are temporarily reserved by another user.")

                user_res = SeatReservation.objects.select_for_update().filter(
                    theater=theaters,
                    user=request.user,
                    status='HELD',
                    expires_at__gt=timezone.now()
                ).first()

                for seat in locked_seats:
                    Booking.objects.create(
                        user=request.user,
                        seat=seat,
                        theater=theaters,
                        movie=theaters.movie
                    )
                    seat.is_booked = True
                    seat.save()
                    booked_seat_numbers.append(seat.seat_number)

                if user_res:
                    user_res.status = 'COMPLETED'
                    user_res.save(update_fields=['status'])

        except ValueError as e:
            error_msg = str(e)
            if is_ajax:
                return JsonResponse({'success': False, 'error': error_msg}, status=400)
            return render(request, 'movies/seat_selection.html', {
                'theater': theaters,
                'seats': seats,
                'error_message': error_msg
            })

        if booked_seat_numbers and request.user.email:
            send_mail(
                subject=f'Booking confirmed: {theaters.movie.name}',
                message=(
                    f"Hi {request.user.username},\n\n"
                    f"Your booking is confirmed.\n\n"
                    f"Movie: {theaters.movie.name}\n"
                    f"Theater: {theaters.name}\n"
                    f"Date: {theaters.date}\n"
                    f"Time: {theaters.time}\n"
                    f"Seats: {', '.join(booked_seat_numbers)}\n\n"
                    f"Enjoy the show!"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[request.user.email],
                fail_silently=True,
            )

        messages.success(request, 'Booking confirmed! A confirmation email has been sent.')
        if is_ajax:
            return JsonResponse({
                'success': True,
                'message': 'Booking confirmed!',
                'redirect_url': '/user/profile/'
            })

        return redirect('profile')

    return render(request, 'movies/seat_selection.html', {'theater': theaters, 'seats': seats})



@login_required(login_url='login')
def cancel_booking(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id, user=request.user)

    if request.method == 'POST':
        seat = booking.seat
        movie_name = booking.movie.name if booking.movie else ''
        seat_number = seat.seat_number
        seat.is_booked = False
        seat.save()
        booking.delete()
        messages.success(request, f'Booking for {movie_name} (seat {seat_number}) has been cancelled.')
        return redirect('profile')

    return render(request, 'movies/cancel_booking.html', {'booking': booking})