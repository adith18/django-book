from django.contrib import admin
from .models import (
    Genre, Language, CastMember, Movie, MovieCast, MoviePoster,
    Theater, Screen, ShowTime, Seat, Booking, Review, ReviewReport, SeatReservation
)


class MoviePosterInline(admin.TabularInline):
    model = MoviePoster
    extra = 1


class MovieCastInline(admin.TabularInline):
    model = MovieCast
    extra = 1


class MovieShowTimeInline(admin.TabularInline):
    """Add individual showtimes per date (each date can have its own schedule)."""
    model = ShowTime
    fk_name = 'movie'
    extra = 2
    fields = ['theater', 'screen', 'date', 'start_time', 'cleaning_buffer_minutes', 'is_cancelled']
    ordering = ['date', 'start_time']
    autocomplete_fields = ['theater', 'screen']


class ScreenInline(admin.TabularInline):
    model = Screen
    extra = 1
    fields = ['name', 'screen_type', 'total_seats']


class ShowTimeInline(admin.TabularInline):
    model = ShowTime
    extra = 1
    fields = ['movie', 'screen', 'date', 'start_time', 'cleaning_buffer_minutes', 'is_cancelled']
    ordering = ['date', 'start_time']


class SeatInline(admin.TabularInline):
    model = Seat
    extra = 0
    fields = ['seat_number', 'seat_type', 'price', 'is_booked']
    readonly_fields = ['is_booked']


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
    list_display = ['name', 'language', 'age_certification', 'duration_minutes',
                    'release_date', 'end_date', 'rating', 'is_active', 'created_at']
    list_filter = ['language', 'genres', 'age_certification', 'release_date', 'end_date']
    search_fields = ['name', 'description']
    inlines = [MoviePosterInline, MovieCastInline, MovieShowTimeInline]
    filter_horizontal = ['genres']
    readonly_fields = ['is_active', 'run_ended']
    fieldsets = [
        (None, {
            'fields': ['name', 'image', 'description', 'rating', 'age_certification',
                       'duration_minutes', 'trailer_url', 'language', 'genres', 'Cast']
        }),
        ('Availability', {
            'fields': [
                'release_date', 'end_date', 'default_cleaning_buffer_minutes',
                'is_active', 'run_ended',
            ],
            'description': 'Available from/until dates control when customers can book. Use the custom admin scheduler for date-wise showtimes.',
        }),
    ]

    @admin.display(boolean=True, description='Active Run')
    def is_active(self, obj):
        return obj.is_active


@admin.register(Theater)
class TheaterAdmin(admin.ModelAdmin):
    list_display = ['name', 'screen_count']
    search_fields = ['name']
    inlines = [ScreenInline]

    @admin.display(description='Screens')
    def screen_count(self, obj):
        return obj.screens.count()


@admin.register(Screen)
class ScreenAdmin(admin.ModelAdmin):
    list_display = ['name', 'theater', 'screen_type', 'total_seats', 'seat_count']
    list_filter = ['theater', 'screen_type']
    search_fields = ['name', 'theater__name']
    inlines = [SeatInline]

    @admin.display(description='Seats in DB')
    def seat_count(self, obj):
        return obj.seats.count()


@admin.register(ShowTime)
class ShowTimeAdmin(admin.ModelAdmin):
    list_display = ['movie', 'theater', 'screen', 'date', 'start_time',
                    'end_time_display', 'cleaning_buffer_minutes', 'booking_status', 'is_cancelled']
    list_filter = ['date', 'movie', 'theater', 'screen', 'is_cancelled']
    search_fields = ['movie__name', 'theater__name', 'screen__name']
    ordering = ['date', 'start_time']
    date_hierarchy = 'date'
    list_editable = ['is_cancelled']
    autocomplete_fields = ['movie', 'theater', 'screen']
    readonly_fields = ['computed_end_time', 'computed_end_with_buffer']

    fieldsets = [
        (None, {
            'fields': ['movie', 'theater', 'screen', 'date', 'start_time'],
        }),
        ('Schedule', {
            'fields': [
                'cleaning_buffer_minutes',
                'computed_end_time',
                'computed_end_with_buffer',
                'is_cancelled',
            ],
            'description': (
                'End times are calculated from the movie runtime plus the cleaning buffer. '
                'Overlapping shows on the same screen are rejected.'
            ),
        }),
    ]

    @admin.display(description='Movie ends')
    def computed_end_time(self, obj):
        if not obj.pk and not obj.start_time:
            return '—'
        return obj.end_time_display.strftime('%I:%M %p')

    @admin.display(description='Next show can start after')
    def computed_end_with_buffer(self, obj):
        if not obj.pk and not obj.start_time:
            return '—'
        return obj.end_time.strftime('%I:%M %p')

    @admin.display(description='End Time')
    def end_time_display(self, obj):
        return obj.end_time_display.strftime('%I:%M %p')

    @admin.display(description='Status')
    def booking_status(self, obj):
        return obj.booking_status


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ['seat_number', 'seat_type', 'price', 'screen', 'is_booked']
    list_filter = ['is_booked', 'seat_type', 'screen__theater']
    search_fields = ['seat_number', 'screen__name', 'screen__theater__name']
    list_editable = ['seat_type', 'price']


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['user', 'seat', 'show_time', 'theater', 'total_price', 'booked_at']
    list_filter = ['booked_at', 'show_time__date', 'theater']
    search_fields = ['user__username', 'user__email', 'show_time__movie__name', 'theater__name']


@admin.register(SeatReservation)
class SeatReservationAdmin(admin.ModelAdmin):
    list_display = ['user', 'show_time', 'status', 'created_at', 'expires_at']
    list_filter = ['status', 'created_at']
    search_fields = ['user__username', 'show_time__movie__name']


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