import datetime
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile

from .models import (
    Genre, Language, CastMember, Movie, MovieCast, MoviePoster,
    Theater, Seat, Booking, Review, ReviewReport, validate_youtube_url
)


class MovieModelTests(TestCase):
    def setUp(self):
        self.genre_action = Genre.objects.create(name='Action')
        self.genre_scifi = Genre.objects.create(name='Sci-Fi')
        self.language_en = Language.objects.create(name='English')
        
        # 1x1 GIF dummy image
        small_gif = (
            b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00'
            b'\xff\xff\xff\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00'
            b'\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
        )
        self.image = SimpleUploadedFile('test.gif', small_gif, content_type='image/gif')

        self.movie = Movie.objects.create(
            name='Test Movie',
            image=self.image,
            language=self.language_en,
            duration_minutes=150,
            age_certification='UA',
            trailer_url='https://www.youtube.com/watch?v=YoHD9XEInc0',
            release_date=datetime.date(2025, 1, 1)
        )
        self.movie.genres.add(self.genre_action, self.genre_scifi)

    def test_youtube_validation_and_embed_url(self):
        # Valid trailer url embed conversion
        self.assertEqual(
            self.movie.trailer_embed_url,
            'https://www.youtube-nocookie.com/embed/YoHD9XEInc0'
        )

        # Invalid YouTube URL validation error
        invalid_movie = Movie(
            name='Invalid Trailer',
            image=self.image,
            trailer_url='https://malicious-site.com/video'
        )
        with self.assertRaises(ValidationError):
            invalid_movie.full_clean()

    def test_duration_display(self):
        self.assertEqual(self.movie.duration_display, '2h 30m')
        
        short_movie = Movie(name='Short', duration_minutes=45)
        self.assertEqual(short_movie.duration_display, '45m')

    def test_multiple_posters_and_cast(self):
        poster1 = MoviePoster.objects.create(movie=self.movie, image=self.image, caption='Poster 1', order=1)
        poster2 = MoviePoster.objects.create(movie=self.movie, image=self.image, caption='Poster 2', order=2)
        self.assertEqual(self.movie.posters.count(), 2)

        cast_member = CastMember.objects.create(name='John Doe', bio='Actor')
        credit = MovieCast.objects.create(movie=self.movie, cast_member=cast_member, character_name='Hero', order=1)
        self.assertEqual(self.movie.ordered_cast.first().cast_member.name, 'John Doe')


class ReviewAndBookingTests(TestCase):
    def setUp(self):
        small_gif = (
            b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00'
            b'\xff\xff\xff\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00'
            b'\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
        )
        self.image = SimpleUploadedFile('test.gif', small_gif, content_type='image/gif')

        self.user_unbooked = User.objects.create_user(username='unbooked', password='password123')
        self.user_viewer = User.objects.create_user(username='viewer', password='password123')
        self.user_reporter = User.objects.create_user(username='reporter', password='password123')
        self.staff_user = User.objects.create_superuser(username='admin', password='password123', email='admin@test.com')

        self.movie = Movie.objects.create(name='Blockbuster', image=self.image)
        
        # Showtime for today
        self.theater = Theater.objects.create(
            name='Cinema 1',
            movie=self.movie,
            date=timezone.localdate(),
            time=datetime.time(10, 0)
        )
        self.seat = Seat.objects.create(theater=self.theater, seat_number='A1', is_booked=True)
        self.booking = Booking.objects.create(
            user=self.user_viewer,
            seat=self.seat,
            theater=self.theater,
            movie=self.movie
        )

    def test_review_eligibility_and_verified_badge(self):
        client = Client()

        # Unbooked user cannot review
        client.login(username='unbooked', password='password123')
        response = client.post(reverse('theater_list', args=[self.movie.id]), {'rating': 5, 'comment': 'Great movie!'})
        self.assertEqual(Review.objects.count(), 0)

        # Booked user (viewer) can review
        client.login(username='viewer', password='password123')
        response = client.post(reverse('theater_list', args=[self.movie.id]), {'rating': 5, 'comment': 'Loved it!'})
        self.assertEqual(Review.objects.count(), 1)
        review = Review.objects.get(user=self.user_viewer, movie=self.movie)
        self.assertTrue(review.is_verified_viewer)
        self.assertEqual(self.movie.average_rating, 5.0)

        # Viewer can edit review
        response = client.post(reverse('theater_list', args=[self.movie.id]), {'rating': 4, 'comment': 'Updated review: Good!'})
        review.refresh_from_db()
        self.assertEqual(review.rating, 4)
        self.assertEqual(self.movie.average_rating, 4.0)

    def test_report_review_and_moderation(self):
        review = Review.objects.create(movie=self.movie, user=self.user_viewer, rating=1, comment='Bad!')

        client = Client()
        client.login(username='reporter', password='password123')
        
        # Report review
        response = client.post(reverse('report_review', args=[review.id]), {'reason': 'spam', 'details': 'Spam post'})
        self.assertEqual(ReviewReport.objects.count(), 1)
        report = ReviewReport.objects.first()
        self.assertEqual(report.reason, 'spam')

        # Moderation by staff
        client.login(username='admin', password='password123')
        response = client.post(reverse('admin_toggle_review', args=[review.id]))
        review.refresh_from_db()
        self.assertTrue(review.is_hidden)

        # Hidden review is excluded from average rating
        self.assertIsNone(self.movie.average_rating)


class RecommendationTests(TestCase):
    def setUp(self):
        small_gif = (
            b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00'
            b'\xff\xff\xff\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00'
            b'\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
        )
        self.image = SimpleUploadedFile('test.gif', small_gif, content_type='image/gif')

        self.genre_action = Genre.objects.create(name='Action')
        self.lang_en = Language.objects.create(name='English')

        self.movie1 = Movie.objects.create(name='Movie 1', image=self.image, language=self.lang_en, release_date=datetime.date(2025, 1, 1))
        self.movie1.genres.add(self.genre_action)

        self.movie2 = Movie.objects.create(name='Movie 2 (Similar)', image=self.image, language=self.lang_en, release_date=datetime.date(2025, 2, 1))
        self.movie2.genres.add(self.genre_action)

        self.movie3 = Movie.objects.create(name='Movie 3 (Recent)', image=self.image, release_date=datetime.date(2025, 3, 1))

    def test_recommendation_algorithm(self):
        from .views import _recommendations_for
        similar, trending, recent = _recommendations_for(self.movie1)

        self.assertIn(self.movie2, similar)
        self.assertIn(self.movie3, recent)


class CustomAdminAccessTests(TestCase):
    def setUp(self):
        small_gif = (
            b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00'
            b'\xff\xff\xff\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00'
            b'\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
        )
        self.image = SimpleUploadedFile('test.gif', small_gif, content_type='image/gif')

        self.regular_user = User.objects.create_user(username='regular', password='password123')
        self.staff_user = User.objects.create_user(username='staff', password='password123', email='staff@test.com', is_staff=True)

    def test_custom_admin_access(self):
        client = Client()

        # Regular user cannot access custom admin dashboard
        client.login(username='regular', password='password123')
        response = client.get(reverse('admin_dashboard'))
        self.assertNotEqual(response.status_code, 200)

        # Staff user can access custom admin dashboard
        client.login(username='staff', password='password123')
        response = client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Custom Admin Dashboard')

