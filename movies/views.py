from django.shortcuts import render, redirect, get_object_or_404
from .models import (
    Movie, Genre, Language, CastMember, MovieCast, MoviePoster,
    Theater, Seat, Booking, Review, ReviewReport, user_has_watched_movie,
)
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import IntegrityError
from django.db.models import Count, Q
from django.core.mail import send_mail
from django.conf import settings
from django.contrib import messages


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
def book_seats(request, theater_id):
    theaters = get_object_or_404(Theater, id=theater_id)
    seats = Seat.objects.filter(theater=theaters)

    if request.method == 'POST':
        selected_seats = request.POST.getlist('seats')
        error_seats = []
        booked_seat_numbers = []

        if not selected_seats:
            return render(request, 'movies/seat_selection.html', {
                'theater': theaters,
                'seats': seats,
                'error_message': 'Please select at least one seat.'
            })

        for seat_id in selected_seats:
            seat = get_object_or_404(Seat, id=seat_id, theater=theaters)
            if seat.is_booked:
                error_seats.append(seat.seat_number)
                continue
            try:
                Booking.objects.create(user=request.user, seat=seat, theater=theaters, movie=theaters.movie)
                seat.is_booked = True
                seat.save()
                booked_seat_numbers.append(seat.seat_number)
            except IntegrityError:
                error_seats.append(seat.seat_number)

        if error_seats:
            error_message = f"The following seats are already booked: {', '.join(error_seats)}"
            return render(request, 'movies/seat_selection.html', {
                'theater': theaters,
                'seats': seats,
                'error_message': error_message
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


# --- CUSTOM ADMIN INTERFACE VIEWS FOR STAFF USERS ---

@user_passes_test(lambda u: u.is_staff, login_url='login')
def admin_dashboard(request):
    movies = Movie.objects.all().prefetch_related('genres', 'posters').select_related('language')
    genres = Genre.objects.all()
    languages = Language.objects.all()
    cast_members = CastMember.objects.all()
    theaters = Theater.objects.select_related('movie').order_by('-date', '-time')
    reported_reviews = ReviewReport.objects.select_related('review__movie', 'review__user', 'reported_by').order_by('-created_at')

    return render(request, 'movies/admin_dashboard.html', {
        'movies': movies,
        'genres': genres,
        'languages': languages,
        'cast_members': cast_members,
        'theaters': theaters,
        'reported_reviews': reported_reviews,
    })


@user_passes_test(lambda u: u.is_staff, login_url='login')
def admin_save_movie(request):
    if request.method == 'POST':
        movie_id = request.POST.get('movie_id')
        name = request.POST.get('name', '').strip()
        language_id = request.POST.get('language')
        age_certification = request.POST.get('age_certification', '')
        duration_minutes = request.POST.get('duration_minutes')
        release_date = request.POST.get('release_date')
        trailer_url = request.POST.get('trailer_url', '').strip()
        description = request.POST.get('description', '').strip()
        genre_ids = request.POST.getlist('genres')

        if not name:
            messages.error(request, 'Movie title is required.')
            return redirect('admin_dashboard')

        if movie_id:
            movie = get_object_or_404(Movie, id=movie_id)
            movie.name = name
        else:
            if 'image' not in request.FILES:
                messages.error(request, 'Main poster image is required for new movies.')
                return redirect('admin_dashboard')
            movie = Movie(name=name)

        if 'image' in request.FILES:
            movie.image = request.FILES['image']

        movie.language_id = language_id if language_id else None
        movie.age_certification = age_certification
        movie.duration_minutes = int(duration_minutes) if duration_minutes else None
        movie.release_date = release_date if release_date else None
        movie.trailer_url = trailer_url
        movie.description = description

        try:
            movie.full_clean()
            movie.save()
            if genre_ids:
                movie.genres.set(genre_ids)

            # Upload additional posters
            extra_posters = request.FILES.getlist('extra_posters')
            for order, poster_file in enumerate(extra_posters, start=1):
                MoviePoster.objects.create(movie=movie, image=poster_file, order=order)

            messages.success(request, f'Movie "{movie.name}" saved successfully!')
        except Exception as e:
            messages.error(request, f'Error saving movie: {e}')

    return redirect('admin_dashboard')


@user_passes_test(lambda u: u.is_staff, login_url='login')
def admin_delete_movie(request, movie_id):
    if request.method == 'POST':
        movie = get_object_or_404(Movie, id=movie_id)
        movie_name = movie.name
        movie.delete()
        messages.success(request, f'Movie "{movie_name}" deleted.')
    return redirect('admin_dashboard')


@user_passes_test(lambda u: u.is_staff, login_url='login')
def admin_save_genre(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if name:
            g, created = Genre.objects.get_or_create(name=name)
            if created:
                messages.success(request, f'Genre "{name}" added.')
            else:
                messages.info(request, f'Genre "{name}" already exists.')
    return redirect('admin_dashboard')


@user_passes_test(lambda u: u.is_staff, login_url='login')
def admin_save_language(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if name:
            l, created = Language.objects.get_or_create(name=name)
            if created:
                messages.success(request, f'Language "{name}" added.')
            else:
                messages.info(request, f'Language "{name}" already exists.')
    return redirect('admin_dashboard')


@user_passes_test(lambda u: u.is_staff, login_url='login')
def admin_save_cast(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        bio = request.POST.get('bio', '').strip()
        photo = request.FILES.get('photo')
        if name:
            c = CastMember(name=name, bio=bio)
            if photo:
                c.photo = photo
            c.save()
            messages.success(request, f'Cast Member "{name}" saved.')
    return redirect('admin_dashboard')


@user_passes_test(lambda u: u.is_staff, login_url='login')
def admin_save_theater(request):
    if request.method == 'POST':
        movie_id = request.POST.get('movie_id')
        name = request.POST.get('name', '').strip()
        date = request.POST.get('date')
        time = request.POST.get('time')
        auto_generate_seats = request.POST.get('auto_generate_seats')

        movie = get_object_or_404(Movie, id=movie_id)
        theater = Theater.objects.create(movie=movie, name=name, date=date, time=time)

        if auto_generate_seats:
            rows = ['A', 'B', 'C']
            seats_to_create = [
                Seat(theater=theater, seat_number=f'{row}{col}')
                for row in rows
                for col in range(1, 11)
            ]
            Seat.objects.bulk_create(seats_to_create)

        messages.success(request, f'Show Schedule created for "{movie.name}" at {theater.name}.')
    return redirect('admin_dashboard')


@user_passes_test(lambda u: u.is_staff, login_url='login')
def admin_toggle_review(request, review_id):
    if request.method == 'POST':
        review = get_object_or_404(Review, id=review_id)
        review.is_hidden = not review.is_hidden
        review.save()
        status_text = 'hidden' if review.is_hidden else 'unhidden'
        messages.success(request, f'Review by {review.user.username} is now {status_text}.')
    return redirect('admin_dashboard')