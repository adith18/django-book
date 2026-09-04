from django.urls import path
from . import views

urlpatterns = [
    path('', views.movie_list, name='movie_list'),
    path('theater/<int:movie_id>/', views.theater_list, name='theater_list'),
    path('theater/<int:theater_id>/seats/book/', views.book_seats, name='book_seats'),
    path('booking/<int:booking_id>/cancel/', views.cancel_booking, name='cancel_booking'),
    path('review/<int:review_id>/report/', views.report_review, name='report_review'),

    # Custom Admin Management Routes
    path('manage/', views.admin_dashboard, name='admin_dashboard'),
    path('manage/movie/save/', views.admin_save_movie, name='admin_save_movie'),
    path('manage/movie/<int:movie_id>/delete/', views.admin_delete_movie, name='admin_delete_movie'),
    path('manage/genre/save/', views.admin_save_genre, name='admin_save_genre'),
    path('manage/language/save/', views.admin_save_language, name='admin_save_language'),
    path('manage/cast/save/', views.admin_save_cast, name='admin_save_cast'),
    path('manage/theater/save/', views.admin_save_theater, name='admin_save_theater'),
    path('manage/review/<int:review_id>/toggle/', views.admin_toggle_review, name='admin_toggle_review'),
]