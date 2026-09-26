import datetime
from datetime import date, time

from django.test import TestCase, TransactionTestCase
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError

from .models import (
    Genre, Language, CastMember, Movie, MovieCast, MoviePoster,
    Theater, Screen, ShowTime, Seat, Booking, Review, ReviewReport,
    SeatReservation,
    extract_youtube_id, validate_youtube_url, user_has_watched_movie
)
from .views import _recommendations_for


# ---------------------------------------------------------------------------
# YouTube URL validation
# ---------------------------------------------------------------------------

class YouTubeValidationTests(TestCase):
    def test_extract_youtube_id(self):
        url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
        self.assertEqual(extract_youtube_id(url), 'dQw4w9WgXcQ')

        short_url = 'https://youtu.be/dQw4w9WgXcQ'
        self.assertEqual(extract_youtube_id(short_url), 'dQw4w9WgXcQ')

        embed_url = 'https://www.youtube.com/embed/dQw4w9WgXcQ'
        self.assertEqual(extract_youtube_id(embed_url), 'dQw4w9WgXcQ')

    def test_validate_youtube_url_invalid(self):
        with self.assertRaises(ValidationError):
            validate_youtube_url('https://malicious-site.com/video')


# ---------------------------------------------------------------------------
# Movie model
# ---------------------------------------------------------------------------

class MovieModelTests(TestCase):
    def setUp(self):
        self.action = Genre.objects.create(name='Action')
        self.english = Language.objects.create(name='English')
        self.movie = Movie.objects.create(
            name='Test Movie',
            language=self.english,
            trailer_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            age_certification='UA',
            duration_minutes=145,
            release_date=date(2025, 1, 1),
            description='Test Description'
        )
        self.movie.genres.add(self.action)

    def test_trailer_embed_url(self):
        self.assertEqual(
            self.movie.trailer_embed_url,
            'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ'
        )

    def test_duration_display(self):
        self.assertEqual(self.movie.duration_display, '2h 25m')


# ---------------------------------------------------------------------------
# Helpers for building ShowTime-based fixtures
# ---------------------------------------------------------------------------

def _make_showtime_fixture(movie, show_date=None, start_hour=14, duration_minutes=None):
    """Create Theater → Screen → ShowTime and return all three objects."""
    theater = Theater.objects.create(name='Grand Cinema')
    screen  = Screen.objects.create(theater=theater, name='Screen 1', total_seats=50)
    if show_date is None:
        # Default to a future date so has_started=False
        show_date = timezone.localdate() + datetime.timedelta(days=1)
    if duration_minutes is not None:
        movie.duration_minutes = duration_minutes
        movie.save(update_fields=['duration_minutes'])
    show_time = ShowTime.objects.create(
        movie=movie,
        theater=theater,
        screen=screen,
        date=show_date,
        start_time=time(start_hour, 0),
        cleaning_buffer_minutes=20,
    )
    return theater, screen, show_time


# ---------------------------------------------------------------------------
# Review & booking
# ---------------------------------------------------------------------------

class ReviewAndBookingTests(TestCase):
    def setUp(self):
        self.user  = User.objects.create_user(username='john', password='password123')
        self.movie = Movie.objects.create(name='Inception', duration_minutes=148)
        self.theater, self.screen, self.show_time = _make_showtime_fixture(
            self.movie,
            show_date=timezone.localdate(),  # today (has_started may be True later)
            start_hour=0,                    # midnight → safely in the past for today
        )
        # Create a seat linked to the screen
        self.seat = Seat.objects.create(screen=self.screen, seat_number='A1', is_booked=False)

    def test_user_has_not_watched_movie_without_booking(self):
        self.assertFalse(user_has_watched_movie(self.user, self.movie))

    def test_user_has_watched_movie_with_past_or_today_booking(self):
        Booking.objects.create(
            user=self.user,
            seat=self.seat,
            show_time=self.show_time,
            theater=self.theater,
            movie=self.movie,
        )
        self.assertTrue(user_has_watched_movie(self.user, self.movie))

    def test_average_rating_and_verified_badge(self):
        Booking.objects.create(
            user=self.user,
            seat=self.seat,
            show_time=self.show_time,
            theater=self.theater,
            movie=self.movie,
        )
        review = Review.objects.create(
            movie=self.movie,
            user=self.user,
            rating=5,
            comment='Awesome!'
        )
        self.assertEqual(self.movie.average_rating, 5.0)
        self.assertTrue(review.is_verified_viewer)


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

class RecommendationsTests(TestCase):
    def test_recommendations(self):
        genre = Genre.objects.create(name='Sci-Fi')
        lang  = Language.objects.create(name='English')
        m1 = Movie.objects.create(name='Movie 1', language=lang, release_date=date(2025, 1, 1))
        m1.genres.add(genre)
        m2 = Movie.objects.create(name='Movie 2', language=lang, release_date=date(2025, 2, 1))
        m2.genres.add(genre)

        similar, trending, recent = _recommendations_for(m1)
        self.assertIn(m2, similar)


# ---------------------------------------------------------------------------
# ShowTime validation
# ---------------------------------------------------------------------------

class ShowTimeValidationTests(TestCase):
    """Test the date-range and overlap validation on ShowTime."""

    def setUp(self):
        self.movie = Movie.objects.create(
            name='Avengers',
            duration_minutes=150,
            release_date=date(2026, 9, 25),
            end_date=date(2026, 10, 5),
            default_cleaning_buffer_minutes=30,
        )
        self.theater = Theater.objects.create(name='PVR Cinemas')
        self.screen  = Screen.objects.create(theater=self.theater, name='Screen 1', total_seats=100)

    def test_show_date_before_release_is_rejected(self):
        st = ShowTime(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            date=date(2026, 9, 24),   # one day before release_date
            start_time=time(10, 0),
            cleaning_buffer_minutes=30,
        )
        with self.assertRaises(ValidationError):
            st.full_clean()

    def test_show_date_after_end_date_is_rejected(self):
        st = ShowTime(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            date=date(2026, 10, 6),   # one day after end_date
            start_time=time(10, 0),
            cleaning_buffer_minutes=30,
        )
        with self.assertRaises(ValidationError):
            st.full_clean()

    def test_overlapping_show_is_rejected(self):
        """10:00 AM show (150 min + 30 min buffer = ends 12:40) overlaps with 12:00 PM show."""
        ShowTime.objects.create(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            date=date(2026, 9, 25),
            start_time=time(10, 0),
            cleaning_buffer_minutes=30,
        )
        overlap = ShowTime(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            date=date(2026, 9, 25),
            start_time=time(12, 0),   # starts before previous show+buffer ends at 12:40
            cleaning_buffer_minutes=30,
        )
        with self.assertRaises(ValidationError):
            overlap.full_clean()

    def test_non_overlapping_show_is_accepted(self):
        """10:00 AM show (150 min + 30 min buffer = ends 12:40) → 13:00 PM is fine."""
        ShowTime.objects.create(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            date=date(2026, 9, 25),
            start_time=time(10, 0),
            cleaning_buffer_minutes=30,
        )
        # Should NOT raise
        st2 = ShowTime(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            date=date(2026, 9, 25),
            start_time=time(13, 0),
            cleaning_buffer_minutes=30,
        )
        st2.full_clean()   # no exception expected

    def test_end_time_calculated_correctly(self):
        """Movie 150 min → end_time_display = start + 150 min (no buffer)."""
        st = ShowTime(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            date=date(2026, 9, 25),
            start_time=time(10, 0),
            cleaning_buffer_minutes=30,
        )
        # 10:00 + 150min = 12:30
        self.assertEqual(st.end_time_display, time(12, 30))


# ---------------------------------------------------------------------------
# Seat reservation (SmartSeatReservationTests) — updated for ShowTime API
# ---------------------------------------------------------------------------

class SmartSeatReservationTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username='alice', password='password123')
        self.user2 = User.objects.create_user(username='bob',   password='password123')
        self.movie  = Movie.objects.create(
            name='Avatar',
            duration_minutes=180,
            release_date=timezone.localdate(),
            end_date=timezone.localdate() + datetime.timedelta(days=30),
        )
        self.theater, self.screen, self.show_time = _make_showtime_fixture(
            self.movie,
            # Future show so has_started=False and booking is open
            show_date=timezone.localdate() + datetime.timedelta(days=1),
            start_hour=18,
        )
        self.seat1 = Seat.objects.create(screen=self.screen, seat_number='A1', price=200)
        self.seat2 = Seat.objects.create(screen=self.screen, seat_number='A2', price=200)
        self.seat3 = Seat.objects.create(screen=self.screen, seat_number='A3', price=200)

    # Convenience URLs using the new showtime-based routes
    def _reserve_url(self): return f'/movies/showtime/{self.show_time.id}/seats/reserve/'
    def _status_url(self):  return f'/movies/showtime/{self.show_time.id}/seats/status/'
    def _book_url(self):    return f'/movies/showtime/{self.show_time.id}/seats/book/'

    def test_reserve_seats_creates_2_min_hold(self):
        self.client.login(username='alice', password='password123')
        response = self.client.post(
            self._reserve_url(),
            data={'seats': [self.seat1.id, self.seat2.id]},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['remaining_seconds'], 120)

        # Status API should show RESERVED_BY_YOU for alice's seats
        status_resp = self.client.get(self._status_url())
        status_data = status_resp.json()
        seats_by_id = {s['id']: s['status'] for s in status_data['seats']}
        self.assertEqual(seats_by_id[self.seat1.id], 'RESERVED_BY_YOU')
        self.assertEqual(seats_by_id[self.seat2.id], 'RESERVED_BY_YOU')
        self.assertEqual(seats_by_id[self.seat3.id], 'AVAILABLE')

    def test_prevent_other_user_reserving_held_seats(self):
        # Alice reserves A1
        self.client.login(username='alice', password='password123')
        self.client.post(
            self._reserve_url(),
            data={'seats': [self.seat1.id]},
            content_type='application/json'
        )

        # Bob attempts to reserve A1 & A2 → should be rejected
        self.client.logout()
        self.client.login(username='bob', password='password123')
        response = self.client.post(
            self._reserve_url(),
            data={'seats': [self.seat1.id, self.seat2.id]},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertIn('temporarily reserved by another user', data['error'])

        # Bob's status check: A1=RESERVED, A2=AVAILABLE
        status_resp = self.client.get(self._status_url())
        seats_by_id = {s['id']: s['status'] for s in status_resp.json()['seats']}
        self.assertEqual(seats_by_id[self.seat1.id], 'RESERVED')
        self.assertEqual(seats_by_id[self.seat2.id], 'AVAILABLE')

    def test_modify_seat_selection_before_payment(self):
        self.client.login(username='alice', password='password123')
        # Reserve A1
        self.client.post(
            self._reserve_url(),
            data={'seats': [self.seat1.id]},
            content_type='application/json'
        )
        # Modify to reserve A2 & A3 instead
        response = self.client.post(
            self._reserve_url(),
            data={'seats': [self.seat2.id, self.seat3.id]},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)

        # A1 should be freed, A2 & A3 reserved by Alice
        status_resp = self.client.get(self._status_url())
        seats_by_id = {s['id']: s['status'] for s in status_resp.json()['seats']}
        self.assertEqual(seats_by_id[self.seat1.id], 'AVAILABLE')
        self.assertEqual(seats_by_id[self.seat2.id], 'RESERVED_BY_YOU')
        self.assertEqual(seats_by_id[self.seat3.id], 'RESERVED_BY_YOU')

    def test_auto_release_expired_reservation(self):
        # Alice reserves A1
        self.client.login(username='alice', password='password123')
        self.client.post(
            self._reserve_url(),
            data={'seats': [self.seat1.id]},
            content_type='application/json'
        )

        # Manually expire Alice's reservation
        res = SeatReservation.objects.get(user=self.user1, show_time=self.show_time, status='HELD')
        res.expires_at = timezone.now() - datetime.timedelta(seconds=10)
        res.save()

        # Bob checks status → A1 should be AVAILABLE again
        self.client.logout()
        self.client.login(username='bob', password='password123')
        status_resp = self.client.get(self._status_url())
        seats_by_id = {s['id']: s['status'] for s in status_resp.json()['seats']}
        self.assertEqual(seats_by_id[self.seat1.id], 'AVAILABLE')

        # Bob can now successfully reserve A1
        reserve_resp = self.client.post(
            self._reserve_url(),
            data={'seats': [self.seat1.id]},
            content_type='application/json'
        )
        self.assertEqual(reserve_resp.status_code, 200)


# ---------------------------------------------------------------------------
# Concurrent booking protection
# ---------------------------------------------------------------------------

class ConcurrentBookingTests(TransactionTestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username='alice_conc', password='password123')
        self.user2 = User.objects.create_user(username='bob_conc',   password='password123')
        self.movie  = Movie.objects.create(name='Concurrent Movie', duration_minutes=120)
        self.theater = Theater.objects.create(name='Screen 1')
        self.screen  = Screen.objects.create(theater=self.theater, name='Main Screen', total_seats=50)
        self.show_time = ShowTime.objects.create(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            date=timezone.localdate() + datetime.timedelta(days=1),
            start_time=time(20, 0),
            cleaning_buffer_minutes=20,
        )
        self.seat1 = Seat.objects.create(screen=self.screen, seat_number='B1', price=150)

    def test_concurrent_booking_transaction_protection(self):
        from django.db import transaction, connection
        import threading

        results = []

        def attempt_booking(user, seat_id):
            connection.close()
            try:
                with transaction.atomic():
                    seat = Seat.objects.select_for_update().get(id=seat_id)
                    already_booked_ids = set(
                        Booking.objects.filter(show_time=self.show_time).values_list('seat_id', flat=True)
                    )
                    if seat.id in already_booked_ids:
                        results.append((user.username, False, 'Already booked'))
                        return
                    Booking.objects.create(
                        user=user,
                        seat=seat,
                        show_time=self.show_time,
                        theater=self.theater,
                        movie=self.movie,
                        total_price=seat.price,
                    )
                    results.append((user.username, True, 'Success'))
            except Exception as e:
                results.append((user.username, False, str(e)))
            finally:
                connection.close()

        t1 = threading.Thread(target=attempt_booking, args=(self.user1, self.seat1.id))
        t2 = threading.Thread(target=attempt_booking, args=(self.user2, self.seat1.id))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        successes = [r for r in results if r[1] is True]
        failures  = [r for r in results if r[1] is False]

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(Booking.objects.filter(seat=self.seat1, show_time=self.show_time).count(), 1)


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------

class AdminDashboardTests(TestCase):
    def setUp(self):
        self.admin       = User.objects.create_superuser(username='admin',   password='password123', email='admin@example.com')
        self.normal_user = User.objects.create_user(username='regular', password='password123')
        self.movie  = Movie.objects.create(name='Dashboard Test Movie', duration_minutes=120)
        self.review = Review.objects.create(movie=self.movie, user=self.normal_user, rating=4, comment='Good movie')
        self.report = ReviewReport.objects.create(review=self.review, reported_by=self.admin, reason='spam')

    def test_admin_dashboard_access_denied_for_non_staff(self):
        self.client.login(username='regular', password='password123')
        response = self.client.get('/movies/custom-admin/')
        self.assertEqual(response.status_code, 302)

    def test_admin_dashboard_access_granted_for_staff(self):
        self.client.login(username='admin', password='password123')
        response = self.client.get('/movies/custom-admin/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Dashboard Test Movie')

    def test_moderate_review_toggle_hide(self):
        self.client.login(username='admin', password='password123')
        response = self.client.post(
            f'/movies/custom-admin/review/{self.review.id}/moderate/',
            data={'action': 'toggle_hide'}
        )
        self.assertEqual(response.status_code, 302)
        self.review.refresh_from_db()
        self.assertTrue(self.review.is_hidden)

    def test_moderate_review_delete(self):
        self.client.login(username='admin', password='password123')
        response = self.client.post(
            f'/movies/custom-admin/review/{self.review.id}/moderate/',
            data={'action': 'delete'}
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Review.objects.filter(id=self.review.id).exists())
