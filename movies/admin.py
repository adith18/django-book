from django.contrib import admin
from .models import (
    Genre, Language, CastMember, Movie, MovieCast, MoviePoster,
    Theater, Seat, Booking, Review, ReviewReport
)


class MoviePosterInline(admin.TabularInline):
    model = MoviePoster
    extra = 1


class MovieCastInline(admin.TabularInline):
    model = MovieCast
    extra = 1


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ['name']
    search_fields = ['name']


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ['name']
    search_fields = ['name']


@admin.register(CastMember)
class CastMemberAdmin(admin.ModelAdmin):
    list_display = ['name', 'bio']
    search_fields = ['name']


@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ['name', 'language', 'age_certification', 'duration_minutes', 'release_date', 'rating', 'created_at']
    list_filter = ['language', 'genres', 'age_certification', 'release_date']
    search_fields = ['name', 'description']
    inlines = [MoviePosterInline, MovieCastInline]
    filter_horizontal = ['genres']


@admin.register(Theater)
class TheaterAdmin(admin.ModelAdmin):
    list_display = ['name', 'movie', 'date', 'time']
    list_filter = ['date', 'movie']
    search_fields = ['name', 'movie__name']


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ['seat_number', 'theater', 'is_booked']
    list_filter = ['is_booked', 'theater']
    search_fields = ['seat_number', 'theater__name']


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['user', 'seat', 'theater', 'booked_at']
    list_filter = ['booked_at', 'theater']
    search_fields = ['user__username', 'user__email', 'theater__name']


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ['movie', 'user', 'rating', 'is_hidden', 'created_at', 'updated_at']
    list_filter = ['rating', 'is_hidden', 'created_at']
    search_fields = ['user__username', 'movie__name', 'comment']


@admin.register(ReviewReport)
class ReviewReportAdmin(admin.ModelAdmin):
    list_display = ['review', 'reported_by', 'reason', 'created_at']
    list_filter = ['reason', 'created_at']
    search_fields = ['reported_by__username', 'review__movie__name', 'details']