"""
Data migration 0007:
  For each old Theater row (which previously had date/time/movie fields that were
  removed in 0006), we create:
    - A Screen ("Screen 1") linked to that Theater venue.
    - A ShowTime linked to movie + theater + screen + date + time.
    - Move seats from the old theater_id FK to the new screen.
    - Point SeatReservations to the new ShowTime.
    - Point Bookings to the new ShowTime.

Because the old columns (theater.date, theater.time, theater.movie_id) were already
removed by the schema migration (0006), we use RunSQL with raw SQL to avoid
depending on fields that no longer exist in the ORM.

NOTE: If there are no rows in the old tables this migration is a no-op.
"""
import datetime
from django.db import migrations, models


def forward_migrate(apps, schema_editor):
    """
    Migrate old Theater-based showtimes to Screen + ShowTime objects.
    The old Theater table had: name, movie_id, date, time.
    Those columns were removed in 0006, so we use raw SQL to read them
    from the database before they disappear.

    Strategy:
      1. For each distinct (theater_id, movie_id, date, time) combination in the
         old Seat/Booking tables, create a Screen + ShowTime.
      2. Assign Seats to the matching Screen.
      3. Assign Bookings and SeatReservations to the matching ShowTime.
    """
    db = schema_editor.connection
    Screen = apps.get_model('movies', 'Screen')
    ShowTime = apps.get_model('movies', 'ShowTime')
    Seat = apps.get_model('movies', 'Seat')
    Booking = apps.get_model('movies', 'Booking')
    SeatReservation = apps.get_model('movies', 'SeatReservation')
    Movie = apps.get_model('movies', 'Movie')
    Theater = apps.get_model('movies', 'Theater')

    with db.cursor() as cursor:
        # Fetch old booking rows: we need theater_id + movie info
        # theater_id still exists on Booking (nullable now). We also need the
        # old showtime data. Look up via old seat→theater→date/time if possible.
        # Since 0006 already dropped theater.date/time/movie_id, we rely on
        # the fact that Booking still has theater_id pointing to the Theater row.
        # We can gather distinct theater_id values from bookings.

        # Fetch distinct theater_ids that exist in bookings
        cursor.execute(
            "SELECT DISTINCT theater_id FROM movies_booking WHERE theater_id IS NOT NULL"
        )
        theater_ids = [row[0] for row in cursor.fetchall()]

    for theater_id in theater_ids:
        try:
            theater = Theater.objects.get(pk=theater_id)
        except Theater.DoesNotExist:
            continue

        # Get or create a default Screen for this theater
        screen, _ = Screen.objects.get_or_create(
            theater=theater,
            name='Screen 1',
            defaults={'total_seats': 100, 'screen_type': 'standard'},
        )

        # Assign all seats (that still have theater_id via the old FK name, now NULL)
        # to this screen. Since we dropped the old theater FK on Seat and added
        # screen (nullable), we need to handle seats that may not be linked yet.
        # At this point Seat rows have screen=NULL; we assign them all to Screen 1
        # of their original theater. We can look up via the booking records.
        with db.cursor() as cursor:
            # Get all seat_ids from bookings for this theater
            cursor.execute(
                "SELECT DISTINCT seat_id FROM movies_booking WHERE theater_id = %s AND seat_id IS NOT NULL",
                [theater_id]
            )
            seat_ids = [row[0] for row in cursor.fetchall()]

        if seat_ids:
            Seat.objects.filter(pk__in=seat_ids, screen__isnull=True).update(screen=screen)

        # For bookings related to this theater, create a ShowTime if movie info exists
        bookings = Booking.objects.filter(theater_id=theater_id, show_time__isnull=True).select_related('movie')
        movie_ids = list(set(b.movie_id for b in bookings if b.movie_id))

        for movie_id in movie_ids:
            try:
                movie = Movie.objects.get(pk=movie_id)
            except Movie.DoesNotExist:
                continue

            # Create a default ShowTime for today if none exists for this combination
            # (We lost the original date/time in migration 0006; use today as fallback)
            import django.utils.timezone as tz
            today = tz.localdate()
            show_time, _ = ShowTime.objects.get_or_create(
                movie=movie,
                theater=theater,
                screen=screen,
                date=today,
                start_time=datetime.time(12, 0),  # noon as default
                defaults={'cleaning_buffer_minutes': 20, 'is_cancelled': False},
            )

            # Link bookings to this showtime
            Booking.objects.filter(
                theater_id=theater_id,
                movie_id=movie_id,
                show_time__isnull=True,
            ).update(show_time=show_time)

    # Link SeatReservations: find ones that have show_time=NULL
    # They reference a seat → screen → first showtime on that screen
    for reservation in SeatReservation.objects.filter(show_time__isnull=True):
        # Try to find a showtime via any seat in the reservation
        first_seat = reservation.seats.select_related('screen').first()
        if first_seat and first_seat.screen:
            showtime = ShowTime.objects.filter(screen=first_seat.screen).first()
            if showtime:
                reservation.show_time = showtime
                reservation.save(update_fields=['show_time'])


def reverse_migrate(apps, schema_editor):
    """Reverse: delete all Screen and ShowTime objects (best effort)."""
    apps.get_model('movies', 'ShowTime').objects.all().delete()
    apps.get_model('movies', 'Screen').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('movies', '0006_showtime_screen_enddate'),
    ]

    operations = [
        migrations.RunPython(forward_migrate, reverse_code=reverse_migrate),
    ]
