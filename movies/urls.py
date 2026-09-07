from django.urls import path
from . import views
urlpatterns = [
    path('', views.movie_list, name='movie_list'),
    path('theater/<int:movie_id>/', views.theater_list, name='theater_list'),
    path('theater/<int:theater_id>/seats/status/', views.api_seat_status, name='api_seat_status'),
    path('theater/<int:theater_id>/seats/reserve/', views.reserve_seats, name='reserve_seats'),
    path('theater/<int:theater_id>/seats/release/', views.release_reservation, name='release_reservation'),
    path('theater/<int:theater_id>/seats/book/', views.book_seats, name='book_seats'),
    path('booking/<int:booking_id>/cancel/', views.cancel_booking, name='cancel_booking'),
    path('review/<int:review_id>/report/', views.report_review, name='report_review'),
]