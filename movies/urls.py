from django.urls import path
from . import views

urlpatterns = [
    path('', views.movie_list, name='movie_list'),
    path('theater/<int:movie_id>/', views.theater_list, name='theater_list'),

    # Dynamic showtime API: returns JSON showtimes for a given date
    path('theater/<int:movie_id>/showtimes/<str:show_date>/', views.api_showtimes_for_date, name='api_showtimes_for_date'),

    # ShowTime-based seat operations (replaces old theater_id routes)
    path('showtime/<int:showtime_id>/seats/status/', views.api_seat_status, name='api_seat_status'),
    path('showtime/<int:showtime_id>/seats/reserve/', views.reserve_seats, name='reserve_seats'),
    path('showtime/<int:showtime_id>/seats/release/', views.release_reservation, name='release_reservation'),
    path('showtime/<int:showtime_id>/seats/summary/', views.api_reservation_summary, name='api_reservation_summary'),
    path('showtime/<int:showtime_id>/seats/book/', views.book_seats, name='book_seats'),

    path('booking/<int:booking_id>/cancel/', views.cancel_booking, name='cancel_booking'),
    path('review/<int:review_id>/report/', views.report_review, name='report_review'),
    path('review/<int:review_id>/delete/', views.delete_review, name='delete_review'),
    path('custom-admin/', views.admin_dashboard, name='admin_dashboard'),
    path('custom-admin/movie/schedule/', views.admin_movie_schedule, name='admin_movie_schedule'),
    path('custom-admin/movie/<int:movie_id>/schedule/', views.admin_movie_schedule, name='admin_movie_schedule_edit'),
    path('custom-admin/api/screens/', views.admin_api_screens, name='admin_api_screens'),
    path('custom-admin/api/movie/<int:movie_id>/showtimes/', views.admin_api_movie_showtimes, name='admin_api_movie_showtimes'),
    path('custom-admin/api/movie/<int:movie_id>/showtime/add/', views.admin_api_showtime_add, name='admin_api_showtime_add'),
    path('custom-admin/api/showtime/<int:showtime_id>/update/', views.admin_api_showtime_update, name='admin_api_showtime_update'),
    path('custom-admin/api/showtime/<int:showtime_id>/delete/', views.admin_api_showtime_delete, name='admin_api_showtime_delete'),
    path('custom-admin/api/movie/<int:movie_id>/copy-schedule/', views.admin_api_copy_schedule, name='admin_api_copy_schedule'),
    path('custom-admin/review/<int:review_id>/moderate/', views.moderate_review, name='moderate_review'),
]
