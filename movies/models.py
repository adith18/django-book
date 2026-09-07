import re

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
    release_date = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        validate_youtube_url(self.trailer_url)

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
    """Represents a single scheduled screening (show) of a movie at a venue/screen."""
    name = models.CharField(max_length=250)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='theaters')
    date = models.DateField(default=timezone.localdate, help_text='Show date')
    time = models.TimeField()

    class Meta:
        ordering = ['date', 'time']

    def __str__(self):
        return f'{self.name} - {self.movie.name} on {self.date} at {self.time}'

    @property
    def starts_at(self):
        dt = timezone.datetime.combine(self.date, self.time)
        if timezone.is_naive(dt):
            return timezone.make_aware(dt)
        return dt

    @property
    def has_started(self):
        try:
            return self.starts_at <= timezone.now()
        except Exception:
            return False


class Seat(models.Model):
    theater = models.ForeignKey(Theater, on_delete=models.CASCADE, related_name='seats')
    seat_number = models.CharField(max_length=10)
    is_booked = models.BooleanField(default=False)

    def __str__(self):
        return f'Seat {self.seat_number} in {self.theater.name}'


class SeatReservation(models.Model):
    STATUS_CHOICES = [
        ('HELD', 'Held'),
        ('COMPLETED', 'Completed'),
        ('EXPIRED', 'Expired'),
        ('CANCELLED', 'Cancelled'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='seat_reservations')
    theater = models.ForeignKey(Theater, on_delete=models.CASCADE, related_name='seat_reservations')
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
    def cleanup_expired(cls, theater=None):
        """Mark past held reservations as EXPIRED."""
        now = timezone.now()
        qs = cls.objects.filter(status='HELD', expires_at__lte=now)
        if theater:
            qs = qs.filter(theater=theater)
        return qs.update(status='EXPIRED')

    def __str__(self):
        return f"Reservation #{self.id} by {self.user.username} for {self.theater.name}"



class Booking(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seat = models.OneToOneField(Seat, on_delete=models.CASCADE)
    theater = models.ForeignKey(Theater, on_delete=models.CASCADE)
    booked_at = models.DateTimeField(auto_now_add=True)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        return f'Booking by {self.user.username} for {self.seat.seat_number} at {self.theater.name}'

    @property
    def has_been_watched(self):
        return self.theater.has_started


def user_has_watched_movie(user, movie):
    """A user is eligible to review a movie once they have a booking for a
    showtime that has already started (i.e. they've had the chance to watch it)."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return Booking.objects.filter(
        user=user, movie=movie, theater__date__lte=timezone.localdate()
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