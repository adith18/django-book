from datetime import date, time
from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError

from .models import (
    Genre, Language, CastMember, Movie, MovieCast, MoviePoster,
    Theater, Seat, Booking, Review, ReviewReport,
    extract_youtube_id, validate_youtube_url, user_has_watched_movie
)
from .views import _recommendations_for


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


class ReviewAndBookingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='john', password='password123')
        self.movie = Movie.objects.create(name='Inception', duration_minutes=148)
        self.theater = Theater.objects.create(
            name='Grand Cinema',
            movie=self.movie,
            date=timezone.localdate(),
            time=time(14, 0)
        )
        self.seat = Seat.objects.create(theater=self.theater, seat_number='A1', is_booked=True)

    def test_user_has_not_watched_movie_without_booking(self):
        self.assertFalse(user_has_watched_movie(self.user, self.movie))

    def test_user_has_watched_movie_with_past_or_today_booking(self):
        Booking.objects.create(
            user=self.user,
            seat=self.seat,
            theater=self.theater,
            movie=self.movie
        )
        self.assertTrue(user_has_watched_movie(self.user, self.movie))

    def test_average_rating_and_verified_badge(self):
        Booking.objects.create(
            user=self.user,
            seat=self.seat,
            theater=self.theater,
            movie=self.movie
        )
        review = Review.objects.create(
            movie=self.movie,
            user=self.user,
            rating=5,
            comment='Awesome!'
        )
        self.assertEqual(self.movie.average_rating, 5.0)
        self.assertTrue(review.is_verified_viewer)


class RecommendationsTests(TestCase):
    def test_recommendations(self):
        genre = Genre.objects.create(name='Sci-Fi')
        lang = Language.objects.create(name='English')
        m1 = Movie.objects.create(name='Movie 1', language=lang, release_date=date(2025, 1, 1))
        m1.genres.add(genre)
        m2 = Movie.objects.create(name='Movie 2', language=lang, release_date=date(2025, 2, 1))
        m2.genres.add(genre)

        similar, trending, recent = _recommendations_for(m1)
        self.assertIn(m2, similar)


from django.test import TestCase, TransactionTestCase


class SmartSeatReservationTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username='alice', password='password123')
        self.user2 = User.objects.create_user(username='bob', password='password123')
        self.movie = Movie.objects.create(name='Avatar', duration_minutes=180)
        self.theater = Theater.objects.create(
            name='IMAX 3D',
            movie=self.movie,
            date=timezone.localdate(),
            time=time(18, 0)
        )
        self.seat1 = Seat.objects.create(theater=self.theater, seat_number='A1')
        self.seat2 = Seat.objects.create(theater=self.theater, seat_number='A2')
        self.seat3 = Seat.objects.create(theater=self.theater, seat_number='A3')

    def test_reserve_seats_creates_2_min_hold(self):
        self.client.login(username='alice', password='password123')
        response = self.client.post(
            f'/movies/theater/{self.theater.id}/seats/reserve/',
            data={'seats': [self.seat1.id, self.seat2.id]},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['remaining_seconds'], 120)

        # Check status API
        status_resp = self.client.get(f'/movies/theater/{self.theater.id}/seats/status/')
        status_data = status_resp.json()
        seats_by_id = {s['id']: s['status'] for s in status_data['seats']}
        self.assertEqual(seats_by_id[self.seat1.id], 'RESERVED_BY_YOU')
        self.assertEqual(seats_by_id[self.seat2.id], 'RESERVED_BY_YOU')
        self.assertEqual(seats_by_id[self.seat3.id], 'AVAILABLE')

    def test_prevent_other_user_reserving_held_seats(self):
        # Alice reserves A1
        self.client.login(username='alice', password='password123')
        self.client.post(
            f'/movies/theater/{self.theater.id}/seats/reserve/',
            data={'seats': [self.seat1.id]},
            content_type='application/json'
        )

        # Bob attempts to reserve A1 & A2
        self.client.logout()
        self.client.login(username='bob', password='password123')
        response = self.client.post(
            f'/movies/theater/{self.theater.id}/seats/reserve/',
            data={'seats': [self.seat1.id, self.seat2.id]},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertIn('temporarily reserved by another user', data['error'])

        # Bob status API check
        status_resp = self.client.get(f'/movies/theater/{self.theater.id}/seats/status/')
        seats_by_id = {s['id']: s['status'] for s in status_resp.json()['seats']}
        self.assertEqual(seats_by_id[self.seat1.id], 'RESERVED')
        self.assertEqual(seats_by_id[self.seat2.id], 'AVAILABLE')

    def test_modify_seat_selection_before_payment(self):
        self.client.login(username='alice', password='password123')
        # Reserve A1
        self.client.post(
            f'/movies/theater/{self.theater.id}/seats/reserve/',
            data={'seats': [self.seat1.id]},
            content_type='application/json'
        )
        # Modify to reserve A2 & A3 instead
        response = self.client.post(
            f'/movies/theater/{self.theater.id}/seats/reserve/',
            data={'seats': [self.seat2.id, self.seat3.id]},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)

        # Status check: A1 should be available, A2 & A3 reserved by Alice
        status_resp = self.client.get(f'/movies/theater/{self.theater.id}/seats/status/')
        seats_by_id = {s['id']: s['status'] for s in status_resp.json()['seats']}
        self.assertEqual(seats_by_id[self.seat1.id], 'AVAILABLE')
        self.assertEqual(seats_by_id[self.seat2.id], 'RESERVED_BY_YOU')
        self.assertEqual(seats_by_id[self.seat3.id], 'RESERVED_BY_YOU')

    def test_auto_release_expired_reservation(self):
        from .models import SeatReservation
        # Alice reserves A1
        self.client.login(username='alice', password='password123')
        self.client.post(
            f'/movies/theater/{self.theater.id}/seats/reserve/',
            data={'seats': [self.seat1.id]},
            content_type='application/json'
        )

        # Manually expire Alice's reservation
        res = SeatReservation.objects.get(user=self.user1, theater=self.theater, status='HELD')
        res.expires_at = timezone.now() - timezone.timedelta(seconds=10)
        res.save()

        # Bob checks status and should see A1 is AVAILABLE again
        self.client.logout()
        self.client.login(username='bob', password='password123')
        status_resp = self.client.get(f'/movies/theater/{self.theater.id}/seats/status/')
        seats_by_id = {s['id']: s['status'] for s in status_resp.json()['seats']}
        self.assertEqual(seats_by_id[self.seat1.id], 'AVAILABLE')

        # Bob can now reserve A1 successfully
        reserve_resp = self.client.post(
            f'/movies/theater/{self.theater.id}/seats/reserve/',
            data={'seats': [self.seat1.id]},
            content_type='application/json'
        )
        self.assertEqual(reserve_resp.status_code, 200)

    def test_confirm_booking_success(self):
        self.client.login(username='alice', password='password123')
        # Reserve A1
        self.client.post(
            f'/movies/theater/{self.theater.id}/seats/reserve/',
            data={'seats': [self.seat1.id]},
            content_type='application/json'
        )

        # Confirm & Pay
        response = self.client.post(
            f'/movies/theater/{self.theater.id}/seats/book/',
            data={'seats': [self.seat1.id]},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)

        # Verify Seat is booked
        self.seat1.refresh_from_db()
        self.assertTrue(self.seat1.is_booked)

        # Verify status API shows BOOKED
        status_resp = self.client.get(f'/movies/theater/{self.theater.id}/seats/status/')
        seats_by_id = {s['id']: s['status'] for s in status_resp.json()['seats']}
        self.assertEqual(seats_by_id[self.seat1.id], 'BOOKED')


class ConcurrentBookingTests(TransactionTestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username='alice_conc', password='password123')
        self.user2 = User.objects.create_user(username='bob_conc', password='password123')
        self.movie = Movie.objects.create(name='Concurrent Movie', duration_minutes=120)
        self.theater = Theater.objects.create(
            name='Screen 1',
            movie=self.movie,
            date=timezone.localdate(),
            time=time(20, 0)
        )
        self.seat1 = Seat.objects.create(theater=self.theater, seat_number='B1')

    def test_concurrent_booking_transaction_protection(self):
        from django.db import transaction, connection
        import threading

        results = []

        def attempt_booking(user, seat_id):
            connection.close()
            try:
                with transaction.atomic():
                    seat = Seat.objects.select_for_update().get(id=seat_id)
                    if seat.is_booked:
                        results.append((user.username, False, 'Already booked'))
                        return
                    Booking.objects.create(
                        user=user, seat=seat, theater=seat.theater, movie=seat.theater.movie
                    )
                    seat.is_booked = True
                    seat.save()
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
        failures = [r for r in results if r[1] is False]

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(Booking.objects.filter(seat=self.seat1).count(), 1)


class AdminDashboardTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='admin', password='password123', email='admin@example.com')
        self.normal_user = User.objects.create_user(username='regular', password='password123')
        self.movie = Movie.objects.create(name='Dashboard Test Movie', duration_minutes=120)
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




