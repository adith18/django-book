from django import forms
from django.contrib import admin
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html
from .models import (
    Genre, Language, CastMember, Movie, MovieCast, MoviePoster, MovieView,
    Theater, Screen, ShowTime, Seat, Booking, BookingOrder, PaymentTransaction,
    Review, ReviewReport, SeatReservation,
)


class BulkSeatForm(forms.Form):
    row_label = forms.CharField(
        max_length=5,
        help_text='Prefix for the seat numbers, e.g. "A" → A1, A2, A3… or "VIP" → VIP1, VIP2…',
    )
    start_number = forms.IntegerField(min_value=1, label='From seat number')
    end_number = forms.IntegerField(min_value=1, label='To seat number')
    seat_type = forms.ChoiceField(choices=Seat.SEAT_TYPE_CHOICES)
    price = forms.DecimalField(max_digits=8, decimal_places=2, initial=150.00)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start_number'), cleaned.get('end_number')
        if start and end and end < start:
            raise forms.ValidationError('"To seat number" must be greater than or equal to "From seat number".')
        return cleaned

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
                    'release_date', 'end_date', 'rating', 'booking_enabled', 'is_active', 'created_at']
    list_filter = ['language', 'genres', 'age_certification', 'release_date', 'end_date', 'booking_enabled']
    list_editable = ['booking_enabled']
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
                'booking_enabled', 'release_date', 'end_date', 'default_cleaning_buffer_minutes',
                'is_active', 'run_ended',
            ],
            'description': (
                'Uncheck "booking_enabled" to immediately block bookings and new showtime '
                'scheduling for this movie. "end_date" is shown to customers but no longer '
                'auto-blocks bookings once it passes — booking_enabled is now the real switch.'
            ),
        }),
    ]

    @admin.display(boolean=True, description='Active Run')
    def is_active(self, obj):
        return obj.is_active


@admin.register(Theater)
class TheaterAdmin(admin.ModelAdmin):
    list_display = ['name', 'city', 'screen_count']
    list_filter = ['city']
    search_fields = ['name', 'city']
    inlines = [ScreenInline]

    @admin.display(description='Screens')
    def screen_count(self, obj):
        return obj.screens.count()


@admin.register(Screen)
class ScreenAdmin(admin.ModelAdmin):
    list_display = ['name', 'theater', 'screen_type', 'total_seats', 'seat_count', 'bulk_add_link']
    list_filter = ['theater', 'screen_type']
    search_fields = ['name', 'theater__name']
    inlines = [SeatInline]

    @admin.display(description='Seats in DB')
    def seat_count(self, obj):
        return obj.seats.count()

    @admin.display(description='Add Seats')
    def bulk_add_link(self, obj):
        url = reverse('admin:movies_screen_bulk_add_seats', args=[obj.id])
        return format_html('<a class="button" href="{}">Bulk Add Seats</a>', url)

    def get_urls(self):
        custom_urls = [
            path(
                '<int:screen_id>/bulk-add-seats/',
                self.admin_site.admin_view(self.bulk_add_seats_view),
                name='movies_screen_bulk_add_seats',
            ),
        ]
        return custom_urls + super().get_urls()

    def bulk_add_seats_view(self, request, screen_id):
        screen = get_object_or_404(Screen, pk=screen_id)
        existing_count = screen.seats.count()
        remaining = max(0, screen.total_seats - existing_count)

        if request.method == 'POST':
            form = BulkSeatForm(request.POST)
            if form.is_valid():
                row_label = form.cleaned_data['row_label'].strip().upper()
                start = form.cleaned_data['start_number']
                end = form.cleaned_data['end_number']
                seat_type = form.cleaned_data['seat_type']
                price = form.cleaned_data['price']
                requested_count = end - start + 1

                if requested_count > remaining:
                    form.add_error(
                        None,
                        f'This would add {requested_count} seat(s), but this screen has capacity for only '
                        f'{remaining} more seat(s) (total_seats = {screen.total_seats}, '
                        f'{existing_count} already exist). Increase the screen\'s total_seats first, '
                        f'or reduce the range.',
                    )
                else:
                    created, skipped = 0, []
                    for n in range(start, end + 1):
                        seat_number = f'{row_label}{n}'
                        _, was_created = Seat.objects.get_or_create(
                            screen=screen,
                            seat_number=seat_number,
                            defaults={'seat_type': seat_type, 'price': price},
                        )
                        created += 1 if was_created else 0
                        if not was_created:
                            skipped.append(seat_number)

                    message = f'Created {created} seat(s) for {screen}.'
                    if skipped:
                        message += f' Skipped {len(skipped)} seat(s) that already existed: {", ".join(skipped)}.'
                    self.message_user(request, message)
                    return redirect(reverse('admin:movies_screen_change', args=[screen.id]))
        else:
            form = BulkSeatForm()

        return render(request, 'admin/movies/bulk_add_seats.html', {
            'form': form,
            'screen': screen,
            'existing_count': existing_count,
            'remaining': remaining,
            'opts': self.model._meta,
            'title': f'Bulk add seats — {screen}',
        })


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


@admin.register(BookingOrder)
class BookingOrderAdmin(admin.ModelAdmin):
    list_display = ['reference', 'user', 'show_time', 'total_amount', 'status', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['reference', 'user__username']


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'show_time', 'amount', 'status', 'provider_payment_id', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['provider_order_id', 'provider_payment_id', 'user__username']


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['user', 'seat', 'show_time', 'theater', 'total_price', 'booked_at', 'booking_order']
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