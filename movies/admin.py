from django.contrib import admin
from .models import (
    Genre, Language, CastMember, Movie, MovieCast, MoviePoster,
    Theater, Seat, Booking, Review, ReviewReport
)


class MovieCastInline(admin.TabularInline):
    model = MovieCast
    extra = 1
    autocomplete_fields = ['cast_member']


class MoviePosterInline(admin.TabularInline):
    model = MoviePoster
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
    list_display = [
        'name', 'language', 'age_certification', 'duration_display',
        'release_date', 'rating', 'created_at'
    ]
    list_filter = ['language', 'genres', 'age_certification', 'release_date']
    search_fields = ['name', 'description']
    inlines = [MovieCastInline, MoviePosterInline]
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
    list_display = ['user', 'movie', 'theater', 'seat', 'booked_at']
    list_filter = ['booked_at', 'movie']
    search_fields = ['user__username', 'movie__name', 'theater__name', 'seat__seat_number']


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ['movie', 'user', 'rating', 'is_verified_viewer', 'is_hidden', 'created_at', 'report_count']
    list_filter = ['is_hidden', 'rating', 'created_at']
    search_fields = ['user__username', 'movie__name', 'comment']
    actions = ['hide_reviews', 'unhide_reviews']

    @admin.action(description='Hide selected reviews (inappropriate content)')
    def hide_reviews(self, request, queryset):
        count = queryset.update(is_hidden=True)
        self.message_user(request, f'{count} review(s) hidden successfully.')

    @admin.action(description='Unhide selected reviews')
    def unhide_reviews(self, request, queryset):
        count = queryset.update(is_hidden=False)
        self.message_user(request, f'{count} review(s) unhidden successfully.')


@admin.register(ReviewReport)
class ReviewReportAdmin(admin.ModelAdmin):
    list_display = ['review', 'reported_by', 'reason', 'created_at']
    list_filter = ['reason', 'created_at']
    search_fields = ['review__movie__name', 'review__user__username', 'reported_by__username', 'details']