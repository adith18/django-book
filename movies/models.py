import re
import datetime

from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


YOUTUBE_ID_RE = re.compile(
    r'(?:youtube(?:-nocookie)?\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)'
    r'(?P<id>[A-Za-z0-9_-]{11})'
)


def extract_youtube_id(url):
    if not url:
        return None
    match = YOUTUBE_ID_RE.search(url)
    return match.group('id') if match else None


def validate_youtube_url(value):
    """Only allow real YouTube URLs (youtube.com / youtu.be) with a valid video id.

    This is a security control: it stops the trailer field being used to embed
    arbitrary third-party content or inject a non-YouTube iframe source.
    """
    if not value:
        return
    if not extract_youtube_id(value):
        raise ValidationError(
            'Enter a valid YouTube video URL (e.g. https://www.youtube.com/watch?v=... '
            'or https://youtu.be/...).'
        )


class Genre(models.Model):
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Language(models.Model):
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class CastMember(models.Model):
    name = models.CharField(max_length=200)
    photo = models.ImageField(upload_to='cast/', blank=True, null=True)
    bio = models.TextField(blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Movie(models.Model):
    AGE_CERTIFICATIONS = [
        ('U', 'U - Universal'),
        ('UA', 'UA - Parental Guidance'),
        ('UA13', 'UA13 - Parental Guidance (13+)'),
        ('UA16', 'UA16 - Parental Guidance (16+)'),
        ('A', 'A - Adults Only'),
        ('S', 'S - Special/Restricted'),
    ]

    name = models.CharField(max_length=250)
    image = models.ImageField(upload_to='movies/')
    rating = models.DecimalField(
        max_digits=3, decimal_places=1, blank=True, null=True,
        help_text='Optional critic/base rating out of 10. User rating is calculated from reviews.'
    )
    Cast = models.TextField(blank=True, help_text='Deprecated free-text cast list, kept for backward compatibility')
    description = models.TextField(blank=True, null=True)  # optional field

    genres = models.ManyToManyField(Genre, related_name='movies', blank=True)
    language = models.ForeignKey(
        Language, on_delete=models.SET_NULL, related_name='movies', null=True, blank=True
    )
    cast_members = models.ManyToManyField(
        CastMember, through='MovieCast', related_name='movies', blank=True
    )

    trailer_url = models.URLField(
        blank=True,
        validators=[validate_youtube_url],
        help_text='YouTube trailer URL (watch, youtu.be, or embed link).'
    )
    age_certification = models.CharField(max_length=10, choices=AGE_CERTIFICATIONS, blank=True)
    duration_minutes = models.PositiveIntegerField(blank=True, null=True, help_text='Runtime in minutes')
    release_date = models.DateField(
        blank=True, null=True,
        help_text='Available from (first bookable day, inclusive)',
    )
    end_date = models.DateField(
        blank=True, null=True,
        help_text='Available until (last bookable day, inclusive)',
    )
    default_cleaning_buffer_minutes = models.PositiveIntegerField(
        default=30,
        help_text='Default buffer/cleaning time between shows (minutes) for new showtimes',
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        validate_youtube_url(self.trailer_url)
        if self.release_date and self.end_date and self.end_date < self.release_date:
            raise ValidationError({'end_date': 'End date must be on or after the release date.'})

    @property
    def trailer_embed_url(self):
        """Safe, privacy-enhanced embed URL. Never render trailer_url directly in an iframe."""
        video_id = extract_youtube_id(self.trailer_url)
        if not video_id:
            return None
        return f'https://www.youtube-nocookie.com/embed/{video_id}'

    @property
    def duration_display(self):
        if not self.duration_minutes:
            return ''
        hours, minutes = divmod(self.duration_minutes, 60)
        if hours:
            return f'{hours}h {minutes}m'
        return f'{minutes}m'

    @property
    def average_rating(self):
        agg = self.reviews.filter(is_hidden=False).aggregate(avg=models.Avg('rating'))
        return agg['avg']

    @property
    def review_count(self):
        return self.reviews.filter(is_hidden=False).count()

    @property
    def ordered_cast(self):
        return self.movie_cast.select_related('cast_member').order_by('order')

    @property
    def is_active(self):
        """True when today falls within the movie's theatrical run."""
        today = timezone.localdate()
        if self.release_date and today < self.release_date:
            return False
        if self.end_date and today > self.end_date:
            return False
        return True

    @property
    def run_ended(self):
        """True when the theatrical run has passed."""
        if self.end_date:
            return timezone.localdate() > self.end_date
        return False

    def get_booking_dates(self):
        """Return list of dates from release_date to end_date (future-only window)."""
        today = timezone.localdate()
        start = self.release_date or today
        end = self.end_date or today
        if end < today:
            return []
        start = max(start, today)
        dates = []
        current = start
        while current <= end:
            dates.append(current)
            current += datetime.timedelta(days=1)
        return dates


class MovieCast(models.Model):
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='movie_cast')
    cast_member = models.ForeignKey(CastMember, on_delete=models.CASCADE, related_name='credits')
    character_name = models.CharField(max_length=200, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']
        unique_together = ('movie', 'cast_member')

    def __str__(self):
        role = f' as {self.character_name}' if self.character_name else ''
        return f'{self.cast_member.name}{role} in {self.movie.name}'


class MoviePoster(models.Model):
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='posters')
    image = models.ImageField(upload_to='movies/posters/')
    caption = models.CharField(max_length=200, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f'Poster for {self.movie.name}'


class Theater(models.Model):
    """Represents a physical cinema venue/location."""
    name = models.CharField(max_length=250)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Screen(models.Model):
    """A physical screen/auditorium inside a Theater venue."""
    SCREEN_TYPE_CHOICES = [
        ('standard', 'Standard'),
        ('imax', 'IMAX'),
        ('3d', '3D'),
        ('4dx', '4DX'),
        ('dolby', 'Dolby Atmos'),
    ]

    theater = models.ForeignKey(Theater, on_delete=models.CASCADE, related_name='screens')
    name = models.CharField(max_length=100, help_text='e.g. Screen 1, IMAX Hall')
    total_seats = models.PositiveIntegerField(default=100)
    screen_type = models.CharField(max_length=20, choices=SCREEN_TYPE_CHOICES, default='standard')

    class Meta:
        ordering = ['theater', 'name']
        unique_together = ('theater', 'name')

    def __str__(self):
        return f'{self.theater.name} — {self.name}'


class ShowTime(models.Model):
    """A single scheduled screening: movie + theater + screen + date + start time."""

    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='showtimes')
    theater = models.ForeignKey(Theater, on_delete=models.CASCADE, related_name='showtimes')
    screen = models.ForeignKey(Screen, on_delete=models.CASCADE, related_name='showtimes')
    date = models.DateField(help_text='Screening date')
    start_time = models.TimeField(help_text='Show start time')
    cleaning_buffer_minutes = models.PositiveIntegerField(
        default=20,
        help_text='Buffer time after show ends before next show can start (minutes)'
    )
    is_cancelled = models.BooleanField(default=False)

    class Meta:
        ordering = ['date', 'start_time']
        unique_together = ('screen', 'date', 'start_time')

    def __str__(self):
        return f'{self.movie.name} @ {self.theater.name}/{self.screen.name} on {self.date} at {self.start_time}'

    def save(self, *args, **kwargs):
        if self.screen_id:
            self.theater_id = self.screen.theater_id
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def duration_minutes(self):
        return self.movie.duration_minutes or 120

    @property
    def end_time(self):
        """Calculate end time including movie duration + cleaning buffer."""
        start_dt = datetime.datetime.combine(self.date, self.start_time)
        end_dt = start_dt + datetime.timedelta(minutes=self.duration_minutes + self.cleaning_buffer_minutes)
        return end_dt.time()

    @property
    def end_time_display(self):
        """End time without buffer (actual movie end)."""
        start_dt = datetime.datetime.combine(self.date, self.start_time)
        end_dt = start_dt + datetime.timedelta(minutes=self.duration_minutes)
        return end_dt.time()

    @property
    def starts_at(self):
        """Timezone-aware datetime of the show start."""
        dt = datetime.datetime.combine(self.date, self.start_time)
        if timezone.is_naive(dt):
            return timezone.make_aware(dt)
        return dt

    @property
    def has_started(self):
        """True once the show start time has passed."""
        try:
            return self.starts_at <= timezone.now()
        except Exception:
            return False

    def _occupied_seat_ids(self):
        """Seat IDs booked or actively held for this showtime."""
        booked = set(
            Booking.objects.filter(show_time=self).values_list('seat_id', flat=True)
        )
        now = timezone.now()
        held_reservations = SeatReservation.objects.filter(
            show_time=self,
            status='HELD',
            expires_at__gt=now,
        ).prefetch_related('seats')
        for reservation in held_reservations:
            booked.update(reservation.seats.values_list('id', flat=True))
        return booked

    @property
    def booking_status(self):
        """Returns one of: Available, Almost Full, Sold Out, Closed, Cancelled."""
        if self.is_cancelled:
            return 'Cancelled'
        if self.has_started:
            return 'Closed'
        total = self.seats_total
        if total == 0:
            return 'Available'
        occupied = len(self._occupied_seat_ids())
        ratio = occupied / total
        if ratio >= 1.0:
            return 'Sold Out'
        elif ratio >= 0.8:
            return 'Almost Full'
        return 'Available'

    @property
    def customer_status_label(self):
        """Label shown on the public booking page."""
        status = self.booking_status
        if status == 'Available':
            return 'Book Now'
        return status

    @property
    def seats_available(self):
        return max(0, self.seats_total - len(self._occupied_seat_ids()))

    @property
    def seats_total(self):
        if not self.screen_id:
            return 0
        count = self.screen.seats.count()
        if count:
            return count
        return self.screen.total_seats or 0

    def clean(self):
        """Prevent overlapping shows on the same screen on the same date."""
        super().clean()
        if self.screen_id:
            self.theater = self.screen.theater
        if self.screen_id and self.theater_id and self.screen.theater_id != self.theater_id:
            raise ValidationError({'screen': 'Selected screen does not belong to the chosen theater.'})
        if self.movie_id and self.date:
            movie = self.movie
            if movie.release_date and self.date < movie.release_date:
                raise ValidationError({
                    'date': f'Show date cannot be before the movie\'s available-from date ({movie.release_date}).',
                })
            if movie.end_date and self.date > movie.end_date:
                raise ValidationError({
                    'date': f'Show date cannot be after the movie\'s available-until date ({movie.end_date}).',
                })
        if not self.screen_id or not self.date or not self.start_time:
            return

        duration = self.duration_minutes
        buffer = self.cleaning_buffer_minutes
        my_start = datetime.datetime.combine(self.date, self.start_time)
        my_end = my_start + datetime.timedelta(minutes=duration + buffer)

        conflicts = ShowTime.objects.filter(
            screen=self.screen,
            date=self.date,
            is_cancelled=False,
        ).exclude(pk=self.pk)

        for other in conflicts:
            other_start = datetime.datetime.combine(other.date, other.start_time)
            other_end = other_start + datetime.timedelta(
                minutes=(other.movie.duration_minutes or 120) + other.cleaning_buffer_minutes
            )
            if my_start < other_end and my_end > other_start:
                raise ValidationError(
                    f'This show overlaps with "{other.movie.name}" on {self.screen} '
                    f'({other.start_time.strftime("%I:%M %p")} – {other_end.strftime("%I:%M %p")}).'
                )


class Seat(models.Model):
    SEAT_TYPE_CHOICES = [
        ('standard', 'Standard'),
        ('premium', 'Premium'),
    ]

    screen = models.ForeignKey(Screen, on_delete=models.CASCADE, related_name='seats', null=True, blank=True)
    seat_number = models.CharField(max_length=10)
    seat_type = models.CharField(max_length=10, choices=SEAT_TYPE_CHOICES, default='standard')
    price = models.DecimalField(max_digits=8, decimal_places=2, default=150.00)
    # is_booked tracks permanent bookings per show; for dynamic status use ShowTime.booking_status
    is_booked = models.BooleanField(default=False)

    class Meta:
        ordering = ['seat_number']

    def __str__(self):
        return f'Seat {self.seat_number} ({self.get_seat_type_display()}) in {self.screen}'


class SeatReservation(models.Model):
    STATUS_CHOICES = [
        ('HELD', 'Held'),
        ('COMPLETED', 'Completed'),
        ('EXPIRED', 'Expired'),
        ('CANCELLED', 'Cancelled'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='seat_reservations')
    show_time = models.ForeignKey(ShowTime, on_delete=models.CASCADE, related_name='seat_reservations', null=True, blank=True)
    seats = models.ManyToManyField(Seat, related_name='reservations')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='HELD')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ['-created_at']

    def is_active(self):
        return self.status == 'HELD' and timezone.now() < self.expires_at

    def release(self):
        if self.status == 'HELD':
            self.status = 'CANCELLED'
            self.save(update_fields=['status'])

    @classmethod
    def cleanup_expired(cls, show_time=None):
        """Mark past held reservations as EXPIRED."""
        now = timezone.now()
        qs = cls.objects.filter(status='HELD', expires_at__lte=now)
        if show_time:
            qs = qs.filter(show_time=show_time)
        return qs.update(status='EXPIRED')

    def __str__(self):
        return f"Reservation #{self.id} by {self.user.username} for {self.show_time}"


class Booking(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seat = models.ForeignKey(Seat, on_delete=models.CASCADE, null=True, blank=True)
    show_time = models.ForeignKey(ShowTime, on_delete=models.CASCADE, related_name='bookings', null=True, blank=True)
    theater = models.ForeignKey(Theater, on_delete=models.CASCADE, null=True, blank=True)
    booked_at = models.DateTimeField(auto_now_add=True)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, null=True, blank=True)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return f'Booking by {self.user.username} for {self.seat.seat_number} at {self.show_time}'

    @property
    def has_been_watched(self):
        return self.show_time.has_started


def user_has_watched_movie(user, movie):
    """A user is eligible to review a movie once they have a booking for a
    showtime that has already started (i.e. they've had the chance to watch it)."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return Booking.objects.filter(
        user=user, movie=movie, show_time__date__lte=timezone.localdate()
    ).exists()


class Review(models.Model):
    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]

    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_hidden = models.BooleanField(
        default=False, help_text='Hidden from public view after moderation of reports.'
    )

    class Meta:
        unique_together = ('movie', 'user')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.username} rated {self.movie.name}: {self.rating}/5'

    @property
    def is_verified_viewer(self):
        return user_has_watched_movie(self.user, self.movie)

    @property
    def report_count(self):
        return self.reports.count()


class ReviewReport(models.Model):
    REASON_CHOICES = [
        ('spam', 'Spam or advertising'),
        ('offensive', 'Offensive or abusive language'),
        ('spoiler', 'Unmarked spoilers'),
        ('irrelevant', 'Not relevant to the movie'),
        ('other', 'Other'),
    ]

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name='reports')
    reported_by = models.ForeignKey(User, on_delete=models.CASCADE)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default='other')
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('review', 'reported_by')
        ordering = ['-created_at']

    def __str__(self):
        return f'Report on review #{self.review_id} by {self.reported_by.username}'