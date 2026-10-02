"""Optimized movie discovery queryset with filters, sort, and pagination."""
from django.db.models import Avg, Count, Min, Q
from django.utils import timezone

from .models import Booking, Genre, Language, Movie, MovieView, ShowTime, Theater


def recommended_for_user(user):
    if not user or not user.is_authenticated:
        return Movie.objects.none()

    booked_genres = Booking.objects.filter(user=user).values_list('movie__genres', flat=True)
    booked_langs = Booking.objects.filter(user=user).values_list('movie__language_id', flat=True)
    viewed_ids = MovieView.objects.filter(user=user).values_list('movie_id', flat=True)[:20]

    qs = Movie.objects.exclude(
        id__in=Booking.objects.filter(user=user).values_list('movie_id', flat=True)
    )
    qs = qs.filter(
        Q(genres__in=booked_genres) | Q(language_id__in=booked_langs) | Q(id__in=viewed_ids)
    ).distinct()
    qs = qs.annotate(booking_count=Count('showtimes__bookings')).order_by('-booking_count', '-release_date')
    return qs[:8]


def build_movie_queryset(request):
    today = timezone.localdate()
    params = request.GET

    qs = Movie.objects.select_related('language').prefetch_related('genres')

    search = (params.get('search') or '').strip()
    if search:
        qs = qs.filter(name__icontains=search)
    else:
        qs = qs.filter(Q(end_date__isnull=True) | Q(end_date__gte=today))

    genre_id = params.get('genre')
    if genre_id:
        qs = qs.filter(genres__id=genre_id)

    language_id = params.get('language')
    if language_id:
        qs = qs.filter(language_id=language_id)

    city = (params.get('city') or '').strip()
    if city:
        qs = qs.filter(showtimes__theater__city__icontains=city, showtimes__is_cancelled=False).distinct()

    theater_id = params.get('theater')
    if theater_id:
        qs = qs.filter(showtimes__theater_id=theater_id, showtimes__is_cancelled=False).distinct()

    release_from = params.get('release_from')
    release_to = params.get('release_to')
    if release_from:
        qs = qs.filter(release_date__gte=release_from)
    if release_to:
        qs = qs.filter(release_date__lte=release_to)

    show_date = params.get('show_date')
    show_from = params.get('show_from')
    show_to = params.get('show_to')
    if show_date or show_from or show_to:
        st_q = Q(showtimes__is_cancelled=False)
        if show_date:
            st_q &= Q(showtimes__date=show_date)
        if show_from:
            st_q &= Q(showtimes__start_time__gte=show_from)
        if show_to:
            st_q &= Q(showtimes__start_time__lte=show_to)
        qs = qs.filter(st_q).distinct()

    qs = qs.annotate(
        booking_count=Count('showtimes__bookings', distinct=True),
        min_ticket_price=Min('showtimes__screen__seats__price'),
        avg_user_rating=Avg('reviews__rating', filter=Q(reviews__is_hidden=False)),
    )

    min_rating = params.get('min_rating')
    if min_rating:
        try:
            qs = qs.filter(avg_user_rating__gte=float(min_rating))
        except ValueError:
            pass

    sort = params.get('sort', 'popularity')
    if sort == 'newest':
        qs = qs.order_by('-release_date', '-created_at')
    elif sort == 'rating':
        qs = qs.order_by('-avg_user_rating', '-release_date')
    elif sort == 'price':
        qs = qs.order_by('min_ticket_price', '-release_date')
    else:
        qs = qs.order_by('-booking_count', '-release_date')

    return qs.distinct()


def filter_options():
    return {
        'genres': Genre.objects.order_by('name'),
        'languages': Language.objects.order_by('name'),
        'theaters': Theater.objects.order_by('name'),
        'cities': list(
            Theater.objects.exclude(city='').values_list('city', flat=True).distinct().order_by('city')
        ),
    }
